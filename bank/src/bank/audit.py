"""The audit log: every Operation call, its tier decision and outcome, per session."""

import json
import sqlite3
from datetime import datetime
from typing import Any

from bank.registry import BankModule, RefusalReason, Tier

SCHEMA = """
CREATE TABLE audit_log (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    at TEXT NOT NULL,
    operation TEXT NOT NULL,
    input TEXT NOT NULL,
    tier TEXT,
    outcome TEXT NOT NULL CHECK (outcome IN ('ok', 'refused')),
    reason TEXT
);
"""


def record(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    at: datetime,
    operation: str,
    input: Any,
    tier: Tier | None,
    reason: RefusalReason | None,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (session_id, at, operation, input, tier, outcome, reason)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            session_id,
            at.isoformat(),
            operation,
            json.dumps(input, ensure_ascii=False, default=str),
            tier.value if tier else None,
            "refused" if reason else "ok",
            reason.value if reason else None,
        ),
    )


def trail(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM audit_log WHERE session_id = ? ORDER BY entry_id", (session_id,)
    ).fetchall()
    return [{**dict(row), "input": json.loads(row["input"])} for row in rows]


def _no_seed(conn: sqlite3.Connection) -> None:
    pass


MODULE = BankModule(name="audit", schema=SCHEMA, seed=_no_seed)
