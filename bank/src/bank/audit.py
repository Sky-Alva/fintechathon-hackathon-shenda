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
    detail TEXT NOT NULL,
    refusal_detail TEXT
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
    refusal_detail: dict[str, Any] | None = None,
) -> None:
    """`detail` is what Staff Members see; for a refusal, `refusal_detail` is what the
    caller was told, which is all the session itself sees of it."""
    conn.execute(
        "INSERT INTO audit_log"
        " (session_id, at, event, operation, input, tier, outcome, reason, detail,"
        " refusal_detail)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            json.dumps(refusal_detail or {}, ensure_ascii=False, default=str) if reason else None,
        ),
    )


def trail(
    conn: sqlite3.Connection, session_id: str, *, as_caller: bool = False
) -> list[dict[str, Any]]:
    """One session's entries in order: as a Staff Member sees them, or `as_caller`, with
    each refusal showing only what the caller was told (never e.g. which token a refused
    one turned out to be)."""
    rows = conn.execute(
        "SELECT * FROM audit_log WHERE session_id = ? ORDER BY entry_id", (session_id,)
    ).fetchall()
    entries = []
    for row in rows:
        entry = {**dict(row), "input": json.loads(row["input"])}
        shown = row["refusal_detail"] if as_caller and row["outcome"] == "refused" else None
        entry["detail"] = json.loads(shown or row["detail"])
        del entry["refusal_detail"]
        entries.append(entry)
    return entries


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


def read_audit_trail(ctx: OperationContext, input: ReadAuditTrailInput) -> ReadAuditTrailOutput:
    entries = trail(ctx.conn, ctx.session_id, as_caller=True)
    return ReadAuditTrailOutput(
        session_id=ctx.session_id,
        entries=[AuditEntry.model_validate(entry) for entry in entries],
    )


MODULE = BankModule(
    name="audit",
    schema=SCHEMA,
    operations=[
        Operation(
            name="read_audit_trail",
            description_zh=(
                "按顺序读取当前会话的审计记录：每次请求、权限等级判定、确认、强验证尝试及结果。"
            ),
            description_en=(
                "Read the current session's audit trail in order: every request, Permission"
                " Tier decision, Confirmation, Strong Verification attempt and outcome."
            ),
            input_model=ReadAuditTrailInput,
            output_model=ReadAuditTrailOutput,
            tier=Tier.GREEN,
            reversibility=ReadOnly(),
            handler=read_audit_trail,
        )
    ],
)
