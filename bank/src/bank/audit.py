"""The audit log: every Operation call, tier preview, Confirmation request, verification
challenge and code attempt, Safety Lock and lift, its tier decision and outcome, per session, in order.

A session reads its own trail with `read_audit_trail` (Green, for the Agent's decision
trace panel, so it works under a Safety Lock), plus the Safety Lock entries from the
Customer's other sessions, without the back-office identifiers; a
Staff Member reads any session's, identifiers and all, with
the admin endpoint.
"""

import hashlib
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
    caller_detail TEXT,  -- what the session itself sees instead of `detail`, if not all of it
    prev_hash TEXT NOT NULL,  -- the previous entry hash, or GENESIS_HASH for the first
    hash TEXT NOT NULL  -- SHA-256 of prev_hash + the canonical JSON of every other column
);
"""


GENESIS_HASH = "0" * 64
"""The `prev_hash` of the first entry in the Bank's one chain."""

CHAINED_COLUMNS = (
    "entry_id",
    "session_id",
    "at",
    "event",
    "operation",
    "input",
    "tier",
    "outcome",
    "reason",
    "detail",
    "caller_detail",
)


def entry_hash(prev_hash: str, row: dict[str, Any]) -> str:
    """SHA-256 of `prev_hash` followed by the canonical JSON (sorted keys, no whitespace) of
    every stored column except the two hashes."""
    canonical = json.dumps(
        {column: row[column] for column in CHAINED_COLUMNS},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256((prev_hash + canonical).encode()).hexdigest()


BACK_OFFICE_IDS = frozenset({"lock_id", "confirmation_id", "verification_id", "challenge_id"})
"""Identifiers for Staff Members only: a Safety Lock's, an approval token's, a challenge's.
The session's own trail leaves them out; what happened, when and why stays."""


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
    caller_detail: dict[str, Any] | None = None,
) -> None:
    """`detail` is what Staff Members see; `caller_detail` is all the session itself sees
    of it: for a refusal, what the caller was told, and for a Safety Lock set by hand, the
    lock without who set it or why."""
    # One chain for the whole Bank. The caller's write transaction (BEGIN IMMEDIATE) makes
    # reading the last hash and appending the next entry atomic.
    last = conn.execute("SELECT hash FROM audit_log ORDER BY entry_id DESC LIMIT 1").fetchone()
    prev_hash = last["hash"] if last else GENESIS_HASH
    cursor = conn.execute(
        "INSERT INTO audit_log"
        " (session_id, at, event, operation, input, tier, outcome, reason, detail,"
        " caller_detail, prev_hash, hash)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '')",
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
            json.dumps(caller_detail or {}, ensure_ascii=False, default=str)
            if reason or caller_detail is not None
            else None,
            prev_hash,
        ),
    )
    row = conn.execute("SELECT * FROM audit_log WHERE entry_id = ?", (cursor.lastrowid,)).fetchone()
    conn.execute(
        "UPDATE audit_log SET hash = ? WHERE entry_id = ?",
        (entry_hash(prev_hash, dict(row)), cursor.lastrowid),
    )


class ChainVerification(BaseModel):
    ok: bool
    entries_checked: int
    first_broken_entry_id: int | None


def verify_chain(conn: sqlite3.Connection) -> ChainVerification:
    """Walk the whole log in order: each entry must name the previous one's hash and carry
    the hash of its own columns. A deleted entry shows at the one after it; an edited or
    inserted entry shows at itself."""
    expected_prev = GENESIS_HASH
    checked = 0
    for row in conn.execute("SELECT * FROM audit_log ORDER BY entry_id"):
        checked += 1
        values = dict(row)
        if values["prev_hash"] != expected_prev or values["hash"] != entry_hash(
            expected_prev, values
        ):
            return ChainVerification(
                ok=False, entries_checked=checked, first_broken_entry_id=values["entry_id"]
            )
        expected_prev = values["hash"]
    return ChainVerification(ok=True, entries_checked=checked, first_broken_entry_id=None)


def trail(
    conn: sqlite3.Connection, session_id: str, *, as_caller: bool = False
) -> list[dict[str, Any]]:
    """One session's entries in order: as a Staff Member sees them, or `as_caller`, with
    the Safety Lock entries of the Customer's other sessions too and each refusal showing only what the caller was told (never e.g. which token a refused
    one turned out to be), a lock set by hand without who set it or why, and no
    back-office identifiers (`BACK_OFFICE_IDS`)."""
    if as_caller:
        # A Safety Lock holds the Customer, so every session of theirs shows its set and lift.
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE session_id = ? OR (event IN (?, ?) AND session_id IN"
            " (SELECT session_id FROM sessions WHERE customer_id ="
            " (SELECT customer_id FROM sessions WHERE session_id = ?))) ORDER BY entry_id",
            (session_id, Event.SAFETY_LOCK.value, Event.SAFETY_LOCK_LIFTED.value, session_id),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE session_id = ? ORDER BY entry_id", (session_id,)
        ).fetchall()
    entries = []
    for row in rows:
        entry = {**dict(row), "input": json.loads(row["input"])}
        shown = row["caller_detail"] if as_caller else None
        entry["detail"] = json.loads(shown or row["detail"])
        del entry["caller_detail"], entry["prev_hash"]
        if as_caller:
            entry["input"] = _without_ids(entry["input"])
            entry["detail"] = _without_ids(entry["detail"])
        entries.append(entry)
    return entries


def _without_ids(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _without_ids(v) for k, v in value.items() if k not in BACK_OFFICE_IDS}
    if isinstance(value, list):
        return [_without_ids(item) for item in value]
    return value


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
    hash: str


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
