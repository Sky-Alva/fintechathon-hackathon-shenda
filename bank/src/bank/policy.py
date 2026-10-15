"""The policy gate: every Operation call passes through here.

Scenario handlers hold only business logic. The gate resolves the tier, demands the
approval that tier needs, runs the handler and writes the audit-log entry, whether
the call ran or was refused.
"""

import sqlite3
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from bank import audit, clock
from bank.registry import (
    OperationContext,
    Refusal,
    RefusalReason,
    Registry,
    Tier,
)
from bank.sessions import Session


class OperationResult(BaseModel):
    operation: str
    tier: Tier
    output: dict[str, Any]


def run(
    conn: sqlite3.Connection,
    registry: Registry,
    session: Session,
    name: str,
    raw_input: Any,
) -> OperationResult:
    """Run one Operation for the session's Customer, or raise a Refusal. Always audited."""
    now = clock.now(conn)
    tier: Tier | None = None
    conn.execute("SAVEPOINT operation")
    try:
        operation = registry.get(name)
        if isinstance(operation.tier, Tier):
            tier = operation.tier
        if not isinstance(raw_input, dict):
            raise Refusal(RefusalReason.INVALID_INPUT, f"Input for {name} must be an object")
        try:
            input = operation.input_model.model_validate(raw_input)
        except ValidationError as error:
            raise Refusal(
                RefusalReason.INVALID_INPUT,
                f"Invalid input for {name}",
                {"errors": error.errors(include_url=False, include_context=False)},
            ) from None
        ctx = OperationContext(
            conn=conn,
            customer_id=session.customer_id,
            session_token=session.session_token,
            now=now,
        )
        if not isinstance(operation.tier, Tier):
            tier = operation.tier.resolve(input, ctx)
        # Confirmation (#4) and Strong Verification (#5) are not built yet, so nothing
        # above Green can run.
        if tier is Tier.YELLOW:
            raise Refusal(RefusalReason.NEEDS_CONFIRMATION, f"{name} needs Confirmation")
        if tier is Tier.RED:
            raise Refusal(RefusalReason.NEEDS_VERIFICATION, f"{name} needs Strong Verification")
        output = operation.handler(ctx, input)
    except Refusal as refusal:
        conn.execute("ROLLBACK TO operation")
        conn.execute("RELEASE operation")
        _audit(conn, session, now, name, raw_input, tier, refusal.reason)
        raise
    conn.execute("RELEASE operation")
    _audit(conn, session, now, name, raw_input, tier, None)
    assert tier is not None
    return OperationResult(operation=name, tier=tier, output=output.model_dump(mode="json"))


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
