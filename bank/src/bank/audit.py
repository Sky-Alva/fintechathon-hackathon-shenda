"""The audit log: every Operation call, tier preview, Confirmation request, verification
challenge and code attempt, Safety Lock and lift, its tier decision and outcome, per session, in order.

A session reads its own trail with `read_audit_trail` (Green, for the Agent's decision
trace panel, so it works under a Safety Lock); a Staff Member reads any session's with
the admin endpoint.
"""

import json
import sqlite3
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from bank.registry import (
    BankModule,
    Operation,
    OperationContext,
    ReadOnly,
    RefusalReason,
    Tier,
)

SCHEMA = """
CREATE TABLE audit_log (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    at TEXT NOT NULL,
    event TEXT NOT NULL,
    operation TEXT,
    input TEXT NOT NULL,
    tier TEXT,
    outcome TEXT NOT NULL CHECK (outcome IN ('ok', 'refused')),
    reason TEXT,
    detail TEXT NOT NULL
);
"""


class Event(StrEnum):
    OPERATION = "operation"
    TIER_PREVIEW = "tier_preview"
    CONFIRMATION_ISSUED = "confirmation_issued"
    VERIFICATION_CHALLENGE = "verification_challenge"
    VERIFICATION_ATTEMPT = "verification_attempt"
    SAFETY_LOCK = "safety_lock"
    SAFETY_LOCK_LIFTED = "safety_lock_lifted"


def record(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    at: datetime,
    event: Event,
    operation: str | None,
    input: Any,
    tier: Tier | None,
    reason: RefusalReason | None,
    detail: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_log"
        " (session_id, at, event, operation, input, tier, outcome, reason, detail)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            session_id,
            at.isoformat(),
            event.value,
            operation,
            json.dumps(input, ensure_ascii=False, default=str),
            tier.value if tier else None,
            "refused" if reason else "ok",
            reason.value if reason else None,
            json.dumps(detail or {}, ensure_ascii=False, default=str),
        ),
    )


def trail(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM audit_log WHERE session_id = ? ORDER BY entry_id", (session_id,)
    ).fetchall()
    return [
        {**dict(row), "input": json.loads(row["input"]), "detail": json.loads(row["detail"])}
        for row in rows
    ]


class ReadAuditTrailInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuditEntry(BaseModel):
    entry_id: int
    at: datetime
    event: Event
    operation: str | None
    input: Any
    tier: Tier | None
    outcome: Literal["ok", "refused"]
    reason: RefusalReason | None
    detail: dict[str, Any]


class ReadAuditTrailOutput(BaseModel):
    session_id: str
    entries: list[AuditEntry]


def read_audit_trail(ctx: OperationContext, _: ReadAuditTrailInput) -> ReadAuditTrailOutput:
    entries = [AuditEntry.model_validate(entry) for entry in trail(ctx.conn, ctx.session_id)]
    for entry in entries:
        # A refused entry's detail is the refusal's audit-only detail (e.g. which token a
        # refused one turned out to be), which the caller is never shown; Staff Members are.
        if entry.outcome == "refused":
            entry.detail = {}
    return ReadAuditTrailOutput(session_id=ctx.session_id, entries=entries)


MODULE = BankModule(
    name="audit",
    schema=SCHEMA,
    operations=[
        Operation(
            name="read_audit_trail",
            description_zh=(
                "按顺序读取当前会话的审计记录：每次请求、权限等级判定、确认、验证尝试及结果。"
            ),
            description_en=(
                "Read the current session's audit trail in order: every request, tier"
                " decision, Confirmation, verification attempt and outcome."
            ),
            input_model=ReadAuditTrailInput,
            output_model=ReadAuditTrailOutput,
            tier=Tier.GREEN,
            reversibility=ReadOnly(),
            handler=read_audit_trail,
        )
    ],
)
