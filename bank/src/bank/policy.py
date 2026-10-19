"""The policy gate: every Operation call, Confirmation request and Strong Verification
step passes through here.

Scenario handlers hold only business logic. The gate validates the input, resolves the
tier, refuses anything but Green under a Safety Lock, demands the approval that tier
needs, runs the handler, keeps the Daily Transfer Total, counts what can trip a Safety
Lock, and writes the audit-log entry whether the call ran or was refused.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bank import approvals, audit, clock, limits, safety_lock, verification
from bank.approvals import Binding, TokenKind
from bank.audit import Event
from bank.registry import (
    SECRET_MASK,
    Operation,
    OperationContext,
    OwnedBy,
    Refusal,
    RefusalReason,
    Registry,
    ReversedBy,
    Secret,
    Tier,
    markers,
)
from bank.safety_lock import Signal
from bank.sessions import Session

MAX_PLAN_STEPS = 20  # per tier preview or Confirmation request
STAFF = "staff"  # who lifts a Safety Lock: any holder of the admin key, the staff console

# What a call of each tier needs before it runs, and the refusal when it is missing.
REQUIRED_APPROVAL = {
    Tier.YELLOW: (RefusalReason.NEEDS_CONFIRMATION, "Confirmation"),
    Tier.RED: (RefusalReason.NEEDS_VERIFICATION, "Strong Verification"),
}


class OperationRequest(BaseModel):
    """The body of an Operation call."""

    model_config = ConfigDict(extra="forbid")
    input: Any = Field(default_factory=dict)
    confirmation_token: str | None = None
    verification_token: str | None = None


class Reversal(BaseModel):
    """The call that would undo an Operation that ran: the Operation its catalogue entry
    names as `reversed_by`, with the input built from this call's input and output."""

    operation: str
    input: dict[str, Any]


class OperationResult(BaseModel):
    operation: str
    tier: Tier
    output: dict[str, Any]
    reversal: Reversal | None = None
    """How to undo it, for a reversible Operation; None for an irreversible or read-only
    one. The Agent only offers it: undoing is a new call, approved like any other."""


class PlannedOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str
    input: Any = Field(default_factory=dict)


class PlanRequest(BaseModel):
    """Operations of one Plan, in order: a whole proposed Plan for a tier preview, or the
    Yellow Operations on its Plan card for a Confirmation request."""

    model_config = ConfigDict(extra="forbid")
    operations: list[PlannedOperation] = Field(min_length=1, max_length=MAX_PLAN_STEPS)


class PreviewStep(BaseModel):
    """One step's tier, or why it can't run (then it has no tier)."""

    index: int
    operation: str
    tier: Tier | None = None
    refusal: dict[str, Any] | None = None


class TierPreviewResult(BaseModel):
    steps: list[PreviewStep]


class Confirmation(BaseModel):
    operation: str
    tier: Tier
    confirmation_id: str
    confirmation_token: str
    expires_at: datetime


class ConfirmationResult(BaseModel):
    confirmations: list[Confirmation]


class VerificationRequest(BaseModel):
    """The body of a Strong Verification request: one Red Operation call."""

    model_config = ConfigDict(extra="forbid")
    operation: str
    input: Any = Field(default_factory=dict)


class VerificationChallenge(BaseModel):
    challenge_id: str
    operation: str
    tier: Tier
    sent_to: str
    expires_at: datetime


class CodeRequest(BaseModel):
    """The body of an answer to a challenge: the code from the simulated SMS."""

    model_config = ConfigDict(extra="forbid")
    code: str = Field(max_length=12)


class Verification(BaseModel):
    operation: str
    verification_id: str
    verification_token: str
    expires_at: datetime


@dataclass
class _Call:
    """What the audit log needs about one call; `tier` is filled in once it is known, and
    `secrets` (input fields never logged) once the Operation is."""

    name: str | None
    raw_input: Any
    tier: Tier | None = None
    secrets: frozenset[str] = frozenset()

    def logged_input(self) -> Any:
        if not self.secrets or not isinstance(self.raw_input, dict):
            return self.raw_input
        return {
            key: SECRET_MASK if key in self.secrets else value
            for key, value in self.raw_input.items()
        }


@dataclass(frozen=True)
class _Resolved:
    operation: Operation
    validated: BaseModel
    ctx: OperationContext
    tier: Tier

    def binding(self) -> Binding:
        return Binding.of(
            session_id=self.ctx.session_id,
            customer_id=self.ctx.customer_id,
            operation=self.operation.name,
            validated_input=self.validated,
        )

    def daily_transfer_amount(self) -> Decimal:
        amount = self.operation.daily_transfer_amount
        return amount(self.validated) if amount else Decimal("0.00")


def run(
    conn: sqlite3.Connection,
    registry: Registry,
    session: Session,
    name: str,
    body: Any,
) -> OperationResult:
    """Run one Operation call for the session's Customer, or raise a Refusal. Always audited."""
    now = clock.now(conn)
    call = _Call(name, body.get("input", {}) if isinstance(body, dict) else body)
    if name in registry.operations:  # so a malformed request never logs a secret either
        call.secrets = _secret_fields(registry.operations[name])
    detail: dict[str, Any] = {}
    with _audited(conn, session, now, Event.OPERATION, call):
        request = _parse(OperationRequest, {} if body is None else body)
        resolved = _resolve(call, conn, registry, session, now)
        if resolved.tier is Tier.GREEN:
            pass
        elif resolved.tier is Tier.YELLOW and request.confirmation_token is not None:
            detail["confirmation_id"] = approvals.redeem(
                conn, TokenKind.CONFIRMATION, request.confirmation_token, resolved.binding(), now
            )
        # A verification token also covers a call that has become Yellow since.
        elif request.verification_token is not None:
            detail["verification_id"] = approvals.redeem(
                conn, TokenKind.VERIFICATION, request.verification_token, resolved.binding(), now
            )
        else:
            raise _needs_approval(name, resolved.tier)
        output = resolved.operation.handler(resolved.ctx, resolved.validated)
        if resolved.operation.daily_transfer_amount:
            limits.record(conn, session.customer_id, name, resolved.daily_transfer_amount(), now)
        if resolved.tier is Tier.RED:
            safety_lock.record(conn, session.session_id, Signal.RED_OPERATION, now)
    _audit(conn, session, now, Event.OPERATION, call, None, detail)
    _trip_safety_lock(conn, session, now)
    output_json = output.model_dump(mode="json")
    reversal = None
    if isinstance(reversibility := resolved.operation.reversibility, ReversedBy):
        undo = reversibility.build_input(resolved.validated, output)
        reversal = Reversal(operation=reversibility.operation, input=undo.model_dump(mode="json"))
    return OperationResult(
        operation=name, tier=resolved.tier, output=output_json, reversal=reversal
    )


def preview_tiers(
    conn: sqlite3.Connection,
    registry: Registry,
    session: Session,
    body: Any,
) -> TierPreviewResult:
    """Tier each step of a proposed Plan in order, each counting the earlier ones toward
    the Daily Transfer Total, without changing anything. Green itself, and audited as one
    entry.

    A step that can't run gets its own refusal and adds nothing to the total; the rest are
    still tiered.
    """
    now = clock.now(conn)
    envelope = _Call(None, _masked_request(registry, body), Tier.GREEN)
    with _audited(conn, session, now, Event.TIER_PREVIEW, envelope):
        request = _parse(PlanRequest, body)
    calls: list[_Call] = []
    steps: list[PreviewStep] = []
    planned_total = Decimal("0.00")
    # Tier rules must not write; the savepoint makes sure a preview never does.
    conn.execute("SAVEPOINT preview")
    try:
        for index, planned in enumerate(request.operations):
            call = _Call(planned.operation, planned.input)
            calls.append(call)
            step = PreviewStep(index=index, operation=planned.operation)
            try:
                resolved = _resolve(call, conn, registry, session, now, planned_total)
            except Refusal as refusal:
                step.refusal = refusal.body()
            else:
                step.tier = resolved.tier
                planned_total += resolved.daily_transfer_amount()
            steps.append(step)
    finally:
        conn.execute("ROLLBACK TO preview")
        conn.execute("RELEASE preview")
    audited = _Call(
        None,
        {"operations": [{"operation": c.name, "input": c.logged_input()} for c in calls]},
        Tier.GREEN,
    )
    detail = {"steps": [step.model_dump(mode="json", exclude_none=True) for step in steps]}
    _audit(conn, session, now, Event.TIER_PREVIEW, audited, None, detail)
    return TierPreviewResult(steps=steps)


def issue_confirmations(
    conn: sqlite3.Connection,
    registry: Registry,
    session: Session,
    body: Any,
) -> ConfirmationResult:
    """Issue one Confirmation token per Yellow Operation, all or none. Always audited.

    Operations are tiered in order, each counting the earlier ones toward the Daily
    Transfer Total, so a Plan card can't hold Yellow tokens that add up to a Red total.
    """
    now = clock.now(conn)
    envelope = _Call(None, _masked_request(registry, body))
    with _audited(conn, session, now, Event.CONFIRMATION_ISSUED, envelope):
        request = _parse(PlanRequest, body)
    issued: list[tuple[_Call, Confirmation]] = []
    planned_total = Decimal("0.00")
    for index, planned in enumerate(request.operations):
        call = _Call(planned.operation, planned.input)
        with _audited(conn, session, now, Event.CONFIRMATION_ISSUED, call, index=index):
            resolved = _resolve(call, conn, registry, session, now, planned_total)
            if resolved.tier is Tier.GREEN:
                raise Refusal(
                    RefusalReason.INVALID_INPUT,
                    f"{call.name} is Green and needs no Confirmation",
                    {"operation": call.name, "tier": resolved.tier.value},
                )
            if resolved.tier is Tier.RED:
                raise _needs_approval(planned.operation, resolved.tier)
            token = approvals.issue(conn, TokenKind.CONFIRMATION, resolved.binding(), now)
            planned_total += resolved.daily_transfer_amount()
        confirmation = Confirmation(
            operation=planned.operation,
            tier=resolved.tier,
            confirmation_id=token.token_id,
            confirmation_token=token.token,
            expires_at=token.expires_at,
        )
        issued.append((call, confirmation))
    for call, confirmation in issued:
        detail = {
            "confirmation_id": confirmation.confirmation_id,
            "expires_at": confirmation.expires_at.isoformat(),
        }
        _audit(conn, session, now, Event.CONFIRMATION_ISSUED, call, None, detail)
    return ConfirmationResult(confirmations=[confirmation for _, confirmation in issued])


def start_verification(
    conn: sqlite3.Connection,
    registry: Registry,
    session: Session,
    body: Any,
) -> VerificationChallenge:
    """Open a Strong Verification challenge for one Red Operation call and text its code
    to the Customer's simulated phone. Always audited."""
    now = clock.now(conn)
    call = _Call(None, _masked_request(registry, body))
    with _audited(conn, session, now, Event.VERIFICATION_CHALLENGE, call):
        request = _parse(VerificationRequest, body)
        call.name, call.raw_input = request.operation, request.input
        resolved = _resolve(call, conn, registry, session, now)
        if resolved.tier is not Tier.RED:
            raise Refusal(
                RefusalReason.INVALID_INPUT,
                f"{call.name} is {resolved.tier.value} and needs no Strong Verification",
                {"operation": call.name, "tier": resolved.tier.value},
            )
        sent = verification.start(
            conn, resolved.binding(), resolved.operation.description_zh, now
        )
    challenge = sent.challenge
    detail = {
        "challenge_id": challenge.challenge_id,
        "sent_to": sent.sent_to,
        "expires_at": challenge.expires_at.isoformat(),
    }
    _audit(conn, session, now, Event.VERIFICATION_CHALLENGE, call, None, detail)
    return VerificationChallenge(
        challenge_id=challenge.challenge_id,
        operation=challenge.binding.operation,
        tier=Tier.RED,
        sent_to=sent.sent_to,
        expires_at=challenge.expires_at,
    )


def answer_challenge(
    conn: sqlite3.Connection,
    session: Session,
    challenge_id: str,
    body: Any,
) -> Verification:
    """Check a code typed into the session's simulated phone against its challenge; the
    right code earns one verification token for exactly the challenged call. Always
    audited, never the code.

    A wrong code is counted outside the audited block, so the count survives the refusal,
    and may trip the Safety Lock; a locked session can't answer at all.
    """
    now = clock.now(conn)
    # Only Red Operations get challenges.
    call = _Call(None, {"challenge_id": challenge_id}, Tier.RED)
    with _audited(conn, session, now, Event.VERIFICATION_ATTEMPT, call):
        request = _parse(CodeRequest, body)
        _check_safety_lock(conn, session, now)
        challenge = verification.find_open(conn, challenge_id, session.session_id, now)
        call.name = challenge.binding.operation
    correct = verification.check_code(conn, challenge, request.code)
    if not correct:
        safety_lock.record(conn, session.session_id, Signal.WRONG_CODE, now)
        detail = {
            "challenge_id": challenge_id,
            "failed_attempts": verification.failed_attempts(conn, session.session_id),
        }
        wrong = Refusal(
            RefusalReason.TOKEN_INVALID, "Wrong verification code", detail, audit_detail=detail
        )
        _audit(
            conn, session, now, Event.VERIFICATION_ATTEMPT, call, wrong.reason, detail, detail
        )
        lock = _trip_safety_lock(conn, session, now)
        if lock is not None:
            wrong.detail = {**detail, "locked_until": lock.expires_at.isoformat()}
        raise wrong
    with _audited(conn, session, now, Event.VERIFICATION_ATTEMPT, call):
        verification.mark_answered(conn, challenge, now)
        token = approvals.issue(conn, TokenKind.VERIFICATION, challenge.binding, now)
    detail = {
        "challenge_id": challenge_id,
        "verification_id": token.token_id,
        "expires_at": token.expires_at.isoformat(),
    }
    _audit(conn, session, now, Event.VERIFICATION_ATTEMPT, call, None, detail)
    return Verification(
        operation=challenge.binding.operation,
        verification_id=token.token_id,
        verification_token=token.token,
        expires_at=token.expires_at,
    )


def _resolve(
    call: _Call,
    conn: sqlite3.Connection,
    registry: Registry,
    session: Session,
    now: datetime,
    planned_total: Decimal = Decimal("0.00"),
) -> _Resolved:
    """Look up the Operation, validate its input and resolve its tier. `planned_total` is
    what earlier steps of the same Plan would add to the Daily Transfer Total."""
    operation = registry.get(call.name or "")
    call.secrets = _secret_fields(operation)
    if isinstance(operation.tier, Tier):
        call.tier = operation.tier
    if not isinstance(call.raw_input, dict):
        raise Refusal(RefusalReason.INVALID_INPUT, f"Input for {call.name} must be an object")
    validated = _parse(operation.input_model, call.raw_input)
    ctx = OperationContext(
        conn=conn,
        customer_id=session.customer_id,
        session_id=session.session_id,
        now=now,
        daily_transfer_total=limits.daily_transfer_total(conn, session.customer_id, now.date())
        + planned_total,
    )
    _check_ownership(validated, ctx)
    if not isinstance(operation.tier, Tier):
        call.tier = operation.tier.resolve(validated, ctx)
    assert call.tier is not None
    if call.tier is not Tier.GREEN:
        _check_safety_lock(conn, session, now)
    return _Resolved(operation, validated, ctx, call.tier)


def _check_safety_lock(conn: sqlite3.Connection, session: Session, now: datetime) -> None:
    """Refuse with `session_locked` while a Safety Lock holds the session."""
    lock = safety_lock.active(conn, session.session_id, now)
    if lock is not None:
        detail = {"trigger": lock.trigger.value, "locked_until": lock.expires_at.isoformat()}
        raise Refusal(
            RefusalReason.SESSION_LOCKED,
            "This session is under a Safety Lock: only Green Operations run until"
            f" {lock.expires_at.isoformat()} or until a Staff Member lifts it",
            detail,
            audit_detail=detail,
        )


def _trip_safety_lock(
    conn: sqlite3.Connection, session: Session, now: datetime
) -> safety_lock.Lock | None:
    """Lock the session if what it has done so far reaches a trigger; audited."""
    lock = safety_lock.trip_if_due(conn, session.session_id, now)
    if lock is not None:
        detail = {
            "lock_id": lock.lock_id,
            "trigger": lock.trigger.value,
            "locked_until": lock.expires_at.isoformat(),
        }
        _audit(conn, session, now, Event.SAFETY_LOCK, _Call(None, {}), None, detail)
    return lock


def lift_safety_lock(conn: sqlite3.Connection, session_id: str) -> safety_lock.Lock:
    """A Staff Member ends the session's Safety Lock now. Audited in the session's trail."""
    now = clock.now(conn)
    lock = safety_lock.lift(conn, session_id, now, STAFF)
    if lock is None:
        raise Refusal(
            RefusalReason.NOT_FOUND,
            "This session is not under a Safety Lock",
            {"session_id": session_id},
        )
    audit.record(
        conn,
        session_id=session_id,
        at=now,
        event=Event.SAFETY_LOCK_LIFTED,
        operation=None,
        input={},
        tier=None,
        reason=None,
        detail={"lock_id": lock.lock_id, "lifted_by": STAFF},
    )
    return lock


def _secret_fields(operation: Operation) -> frozenset[str]:
    fields = operation.input_model.model_fields
    return frozenset(name for name, field in fields.items() if markers(field, Secret))


def _masked_request(registry: Registry, body: Any) -> Any:
    """A Plan or verification request body as the audit log may store it, for when it is
    refused before its steps are parsed: every known Operation's secrets masked."""
    if not isinstance(body, dict):
        return body
    if isinstance(body.get("operations"), list):
        steps = [_masked_request(registry, step) for step in body["operations"]]
        return {**body, "operations": steps}
    name = body.get("operation")
    operation = registry.operations.get(name) if isinstance(name, str) else None
    if operation is None:
        return body
    step = _Call(operation.name, body.get("input"), secrets=_secret_fields(operation))
    return {**body, "input": step.logged_input()}


def _check_ownership(model: BaseModel, ctx: OperationContext) -> None:
    """Refuse any ID in the input that belongs to another Customer, or to nobody."""
    for name, field in type(model).model_fields.items():
        value = getattr(model, name)
        values = value if isinstance(value, list) else [value]
        for owned in markers(field, OwnedBy):
            for owned_id in values:
                if owned_id is not None:
                    _check_one_owner(owned, name, owned_id, ctx)
        for nested in values:
            if isinstance(nested, BaseModel):
                _check_ownership(nested, ctx)


def _check_one_owner(owned: OwnedBy, field: str, id: str, ctx: OperationContext) -> None:
    # Table and column names come from the Operation's declaration, never the caller.
    row = ctx.conn.execute(
        f"SELECT customer_id FROM {owned.table} WHERE {owned.key} = ?", (id,)
    ).fetchone()
    if row is None:
        raise Refusal(RefusalReason.NOT_FOUND, f"No such {field}", {"field": field, "id": id})
    if row["customer_id"] != ctx.customer_id:
        raise Refusal(
            RefusalReason.FORBIDDEN_OTHER_CUSTOMER,
            f"This {field} belongs to another Customer; a session acts only for its own",
            {"field": field},
        )


class _audited:
    """Run a block inside a savepoint. On a Refusal, undo the block, audit the refusal
    and re-raise it (adding `index` to its detail for one step of a Plan card)."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        session: Session,
        now: datetime,
        event: Event,
        call: _Call,
        index: int | None = None,
    ):
        self.conn, self.session, self.now = conn, session, now
        self.event, self.call, self.index = event, call, index

    def __enter__(self) -> None:
        self.conn.execute("SAVEPOINT gate")

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, _: Any
    ) -> None:
        if error is None:
            self.conn.execute("RELEASE gate")
            return
        self.conn.execute("ROLLBACK TO gate")
        self.conn.execute("RELEASE gate")
        if isinstance(error, Refusal):
            if self.index is not None:
                error.detail = {**error.detail, "index": self.index}
            _audit(
                self.conn, self.session, self.now, self.event, self.call, error.reason,
                error.audit_detail, error.detail,
            )


def _parse[M: BaseModel](model: type[M], data: Any) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as error:
        raise Refusal(
            RefusalReason.INVALID_INPUT,
            "Invalid input",
            {"errors": error.errors(include_url=False, include_context=False, include_input=False)},
        ) from None


def _needs_approval(name: str, tier: Tier) -> Refusal:
    reason, approval = REQUIRED_APPROVAL[tier]
    detail = {"operation": name, "tier": tier.value}
    return Refusal(reason, f"{name} is {tier.value} and needs {approval}", detail)


def _audit(
    conn: sqlite3.Connection,
    session: Session,
    now: datetime,
    event: Event,
    call: _Call,
    reason: RefusalReason | None,
    detail: dict[str, Any] | None = None,
    refusal_detail: dict[str, Any] | None = None,
) -> None:
    audit.record(
        conn,
        session_id=session.session_id,
        at=now,
        event=event,
        operation=call.name,
        input=call.logged_input(),
        tier=call.tier,
        reason=reason,
        detail=detail,
        refusal_detail=refusal_detail,
    )
