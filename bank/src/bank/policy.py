"""The policy gate: every Operation call, Confirmation request and Strong Verification
step passes through here.

Scenario handlers hold only business logic. The gate validates the input, resolves the
tier, demands the approval that tier needs, runs the handler, keeps the Daily Transfer
Total, and writes the audit-log entry whether the call ran or was refused.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bank import approvals, audit, clock, limits, verification
from bank.approvals import Binding, TokenKind
from bank.audit import Event
from bank.registry import (
    Operation,
    OperationContext,
    Refusal,
    RefusalReason,
    Registry,
    Tier,
)
from bank.sessions import Session

MAX_CONFIRMATIONS_PER_REQUEST = 20

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


class OperationResult(BaseModel):
    operation: str
    tier: Tier
    output: dict[str, Any]


class PlannedOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str
    input: Any = Field(default_factory=dict)


class ConfirmationRequest(BaseModel):
    """The body of a Confirmation request: the Yellow Operations on one Plan card, in order."""

    model_config = ConfigDict(extra="forbid")
    operations: list[PlannedOperation] = Field(
        min_length=1, max_length=MAX_CONFIRMATIONS_PER_REQUEST
    )


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
    """What the audit log needs about one call; `tier` is filled in once it is known."""

    name: str | None
    raw_input: Any
    tier: Tier | None = None


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
    _audit(conn, session, now, Event.OPERATION, call, None, detail)
    output_json = output.model_dump(mode="json")
    return OperationResult(operation=name, tier=resolved.tier, output=output_json)


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
    with _audited(conn, session, now, Event.CONFIRMATION_ISSUED, _Call(None, body)):
        request = _parse(ConfirmationRequest, body)
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
    call = _Call(None, body)
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

    A wrong code is counted outside the audited block, so the count survives the refusal.
    """
    now = clock.now(conn)
    # Only Red Operations get challenges.
    call = _Call(None, {"challenge_id": challenge_id}, Tier.RED)
    with _audited(conn, session, now, Event.VERIFICATION_ATTEMPT, call):
        request = _parse(CodeRequest, body)
        challenge = verification.find_open(conn, challenge_id, session.session_id, now)
        call.name = challenge.binding.operation
    correct = verification.check_code(conn, challenge, request.code)
    with _audited(conn, session, now, Event.VERIFICATION_ATTEMPT, call):
        if not correct:
            detail = {
                "challenge_id": challenge_id,
                "failed_attempts": verification.failed_attempts(conn, session.session_id),
            }
            raise Refusal(
                RefusalReason.TOKEN_INVALID, "Wrong verification code", detail,
                audit_detail=detail,
            )
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
    if not isinstance(operation.tier, Tier):
        call.tier = operation.tier.resolve(validated, ctx)
    assert call.tier is not None
    return _Resolved(operation, validated, ctx, call.tier)


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
            _audit(
                self.conn, self.session, self.now, self.event, self.call, error.reason,
                error.audit_detail,
            )
            if self.index is not None:
                error.detail = {**error.detail, "index": self.index}


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
) -> None:
    audit.record(
        conn,
        session_id=session.session_id,
        at=now,
        event=event,
        operation=call.name,
        input=call.raw_input,
        tier=call.tier,
        reason=reason,
        detail=detail,
    )
