"""The audit log: every Operation call and Confirmation request, its tier decision and
outcome, per session, in order."""

import json
import sqlite3
from datetime import datetime
from enum import StrEnum
from typing import Any

from bank.registry import BankModule, RefusalReason, Tier

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
    CONFIRMATION_ISSUED = "confirmation_issued"


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


MODULE = BankModule(name="audit", schema=SCHEMA)
