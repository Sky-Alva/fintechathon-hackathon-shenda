"""The policy gate: every Operation call passes through here.

Scenario handlers hold only business logic. The gate resolves the tier, demands the
approval that tier needs, runs the handler and writes the audit-log entry, whether
the call ran or was refused.
"""

import sqlite3
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bank import audit, clock
from bank.registry import (
    OperationContext,
    Refusal,
    RefusalReason,
    Registry,
    Tier,
)
from bank.sessions import Session


class OperationRequest(BaseModel):
    """The body of an Operation call."""

    model_config = ConfigDict(extra="forbid")
    input: Any = Field(default_factory=dict)


class OperationResult(BaseModel):
    operation: str
    tier: Tier
    output: dict[str, Any]


def run(
    conn: sqlite3.Connection,
    registry: Registry,
    session: Session,
    name: str,
    body: Any,
) -> OperationResult:
    """Run one Operation call for the session's Customer, or raise a Refusal. Always audited."""
    now = clock.now(conn)
    tier: Tier | None = None
    raw_input = body.get("input", {}) if isinstance(body, dict) else body
    conn.execute("SAVEPOINT operation")
    try:
        try:
            OperationRequest.model_validate({} if body is None else body)
        except ValidationError as error:
            raise _invalid_input("Invalid request", error) from None
        operation = registry.get(name)
        if isinstance(operation.tier, Tier):
            tier = operation.tier
        if not isinstance(raw_input, dict):
            raise Refusal(RefusalReason.INVALID_INPUT, f"Input for {name} must be an object")
        try:
            validated = operation.input_model.model_validate(raw_input)
        except ValidationError as error:
            raise _invalid_input(f"Invalid input for {name}", error) from None
        ctx = OperationContext(
            conn=conn, customer_id=session.customer_id, session_id=session.session_id, now=now
        )
        if not isinstance(operation.tier, Tier):
            tier = operation.tier.resolve(validated, ctx)
        # Confirmation (#4) and Strong Verification (#5) are not built yet, so nothing
        # above Green can run.
        if tier is Tier.YELLOW:
            raise Refusal(RefusalReason.NEEDS_CONFIRMATION, f"{name} needs Confirmation")
        if tier is Tier.RED:
            raise Refusal(RefusalReason.NEEDS_VERIFICATION, f"{name} needs Strong Verification")
        output = operation.handler(ctx, validated)
    except Refusal as refusal:
        conn.execute("ROLLBACK TO operation")
        conn.execute("RELEASE operation")
        _audit(conn, session, now, name, raw_input, tier, refusal.reason)
        raise
    conn.execute("RELEASE operation")
    _audit(conn, session, now, name, raw_input, tier, None)
    assert tier is not None
    return OperationResult(operation=name, tier=tier, output=output.model_dump(mode="json"))


def _invalid_input(message: str, error: ValidationError) -> Refusal:
    return Refusal(
        RefusalReason.INVALID_INPUT,
        message,
        {"errors": error.errors(include_url=False, include_context=False, include_input=False)},
    )


def _audit(
    conn: sqlite3.Connection,
    session: Session,
    now: datetime,
    name: str,
    raw_input: Any,
    tier: Tier | None,
    reason: RefusalReason | None,
) -> None:
    audit.record(
        conn,
        session_id=session.session_id,
        at=now,
        operation=name,
        input=raw_input,
        tier=tier,
        reason=reason,
    )
