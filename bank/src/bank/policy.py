"""The policy gate: every Operation call and Confirmation request passes through here.

Scenario handlers hold only business logic. The gate validates the input, resolves the
tier, demands the approval that tier needs, runs the handler and writes the audit-log
entry, whether the call ran or was refused.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bank import audit, clock, confirmations
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


class OperationRequest(BaseModel):
    """The body of an Operation call."""

    model_config = ConfigDict(extra="forbid")
    input: Any = Field(default_factory=dict)
    confirmation_token: str | None = None


class OperationResult(BaseModel):
    operation: str
    tier: Tier
    output: dict[str, Any]


class PlannedOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str
    input: Any = Field(default_factory=dict)


class ConfirmationRequest(BaseModel):
    """The body of a Confirmation request: the Yellow Operations on one Plan card."""

    model_config = ConfigDict(extra="forbid")
    operations: list[PlannedOperation] = Field(
        min_length=1, max_length=MAX_CONFIRMATIONS_PER_REQUEST
    )


class IssuedConfirmation(BaseModel):
    operation: str
    tier: Tier
    confirmation_id: str
    confirmation_token: str
    expires_at: datetime


class ConfirmationResult(BaseModel):
    confirmations: list[IssuedConfirmation]


@dataclass
class _Call:
    """One Operation call as the gate understands it, filled in step by step."""

    name: str
    raw_input: Any
    tier: Tier | None = None
    operation: Operation | None = None
    validated: BaseModel | None = None
    ctx: OperationContext | None = None


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
    conn.execute("SAVEPOINT operation")
    try:
        request = _parse(OperationRequest, {} if body is None else body)
        operation, validated, ctx, tier = _resolve(call, conn, registry, session, now)
        if tier is Tier.YELLOW:
            if request.confirmation_token is None:
                raise _needs(RefusalReason.NEEDS_CONFIRMATION, name, tier)
            detail["confirmation_id"] = confirmations.redeem(
                conn,
                request.confirmation_token,
                session_id=session.session_id,
                customer_id=session.customer_id,
                operation=name,
                validated_input=validated,
                now=now,
            )
        elif tier is Tier.RED:
            # Strong Verification (#5) is not built yet, so no Red Operation can run.
            raise _needs(RefusalReason.NEEDS_VERIFICATION, name, tier)
        output = operation.handler(ctx, validated)
    except Refusal as refusal:
        _rollback(conn)
        _audit(conn, session, now, Event.OPERATION, call, refusal.reason)
        raise
    conn.execute("RELEASE operation")
    _audit(conn, session, now, Event.OPERATION, call, None, detail)
    return OperationResult(operation=name, tier=tier, output=output.model_dump(mode="json"))


def issue_confirmations(
    conn: sqlite3.Connection,
    registry: Registry,
    session: Session,
    body: Any,
) -> ConfirmationResult:
    """Issue one Confirmation token per Yellow Operation, all or none. Always audited."""
    now = clock.now(conn)
    conn.execute("SAVEPOINT operation")
    try:
        request = _parse(ConfirmationRequest, body)
    except Refusal as refusal:
        _rollback(conn)
        _audit(conn, session, now, Event.CONFIRMATION_ISSUED, _Call("", body), refusal.reason)
        raise
    issued: list[tuple[_Call, IssuedConfirmation]] = []
    for index, planned in enumerate(request.operations):
        call = _Call(planned.operation, planned.input)
        try:
            operation, validated, _, tier = _resolve(call, conn, registry, session, now)
            if tier is Tier.GREEN:
                raise Refusal(
                    RefusalReason.INVALID_INPUT,
                    f"{call.name} is Green and needs no Confirmation",
                    {"operation": call.name, "tier": tier.value},
                )
            if tier is Tier.RED:
                raise _needs(RefusalReason.NEEDS_VERIFICATION, call.name, tier)
            confirmation = confirmations.issue(
                conn,
                session_id=session.session_id,
                customer_id=session.customer_id,
                operation=operation.name,
                validated_input=validated,
                now=now,
            )
        except Refusal as refusal:
            _rollback(conn)
            _audit(conn, session, now, Event.CONFIRMATION_ISSUED, call, refusal.reason)
            refusal.detail = {**refusal.detail, "index": index}
            raise
        issued.append(
            (
                call,
                IssuedConfirmation(
                    operation=operation.name,
                    tier=tier,
                    confirmation_id=confirmation.confirmation_id,
                    confirmation_token=confirmation.confirmation_token,
                    expires_at=confirmation.expires_at,
                ),
            )
        )
    conn.execute("RELEASE operation")
    for call, granted in issued:
        detail = {
            "confirmation_id": granted.confirmation_id,
            "expires_at": granted.expires_at.isoformat(),
        }
        _audit(conn, session, now, Event.CONFIRMATION_ISSUED, call, None, detail)
    return ConfirmationResult(confirmations=[granted for _, granted in issued])


def _resolve(
    call: _Call, conn: sqlite3.Connection, registry: Registry, session: Session, now: datetime
) -> tuple[Operation, BaseModel, OperationContext, Tier]:
    """Look up the Operation, validate its input and resolve its tier, recording each on
    `call` as it becomes known so a refusal is audited with as much as was learned."""
    operation = call.operation = registry.get(call.name)
    if isinstance(operation.tier, Tier):
        call.tier = operation.tier
    if not isinstance(call.raw_input, dict):
        raise Refusal(RefusalReason.INVALID_INPUT, f"Input for {call.name} must be an object")
    validated = call.validated = _parse(operation.input_model, call.raw_input)
    ctx = call.ctx = OperationContext(
        conn=conn, customer_id=session.customer_id, session_id=session.session_id, now=now
    )
    if isinstance(operation.tier, Tier):
        tier = operation.tier
    else:
        tier = call.tier = operation.tier.resolve(validated, ctx)
    return operation, validated, ctx, tier


def _parse[M: BaseModel](model: type[M], data: Any) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as error:
        raise Refusal(
            RefusalReason.INVALID_INPUT,
            "Invalid input",
            {"errors": error.errors(include_url=False, include_context=False, include_input=False)},
        ) from None


def _needs(reason: RefusalReason, name: str, tier: Tier) -> Refusal:
    approval = (
        "Confirmation" if reason is RefusalReason.NEEDS_CONFIRMATION else "Strong Verification"
    )
    detail = {"operation": name, "tier": tier.value}
    return Refusal(reason, f"{name} is {tier.value} and needs {approval}", detail)


def _rollback(conn: sqlite3.Connection) -> None:
    conn.execute("ROLLBACK TO operation")
    conn.execute("RELEASE operation")


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
        operation=call.name or None,
        input=call.raw_input,
        tier=call.tier,
        reason=reason,
        detail=detail,
    )
