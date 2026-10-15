"""Confirmation tokens: the Customer's approval of one exact Yellow Operation.

A token is tied to the Customer, the session, the Operation name and a fingerprint of
its validated input. It works once and expires after a few Bank-clock minutes. Only a
hash of the token is stored, and the audit log refers to it by its confirmation ID.
"""

import hashlib
import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import BaseModel

from bank.registry import BankModule, Refusal, RefusalReason

CONFIRMATION_LIFETIME = timedelta(minutes=5)

SCHEMA = """
CREATE TABLE confirmations (
    confirmation_id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);
"""


@dataclass(frozen=True)
class IssuedConfirmation:
    confirmation_id: str
    confirmation_token: str
    expires_at: datetime


def fingerprint(operation: str, validated_input: BaseModel) -> str:
    canonical = json.dumps(
        {"operation": operation, "input": validated_input.model_dump(mode="json")},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    customer_id: str,
    operation: str,
    validated_input: BaseModel,
    now: datetime,
) -> IssuedConfirmation:
    issued = IssuedConfirmation(
        confirmation_id=f"CNF-{secrets.token_hex(6)}",
        confirmation_token=secrets.token_urlsafe(32),
        expires_at=now + CONFIRMATION_LIFETIME,
    )
    conn.execute(
        "INSERT INTO confirmations VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
        (
            issued.confirmation_id,
            _hash(issued.confirmation_token),
            session_id,
            customer_id,
            operation,
            fingerprint(operation, validated_input),
            now.isoformat(),
            issued.expires_at.isoformat(),
        ),
    )
    return issued


def redeem(
    conn: sqlite3.Connection,
    token: str,
    *,
    session_id: str,
    customer_id: str,
    operation: str,
    validated_input: BaseModel,
    now: datetime,
) -> str:
    """Use up a token for exactly this Operation call; return its confirmation ID."""
    row = conn.execute(
        "SELECT * FROM confirmations WHERE token_hash = ?", (_hash(token),)
    ).fetchone()
    if (
        row is None
        or row["session_id"] != session_id
        or row["customer_id"] != customer_id
        or row["operation"] != operation
        or row["fingerprint"] != fingerprint(operation, validated_input)
        or row["used_at"] is not None
        or now >= datetime.fromisoformat(row["expires_at"])
    ):
        # One reason for every mismatch, so a caller can't probe which part was wrong.
        raise Refusal(
            RefusalReason.TOKEN_INVALID,
            "This Confirmation does not cover this exact Operation, or has expired or been used",
        )
    conn.execute(
        "UPDATE confirmations SET used_at = ? WHERE confirmation_id = ?",
        (now.isoformat(), row["confirmation_id"]),
    )
    confirmation_id: str = row["confirmation_id"]
    return confirmation_id


MODULE = BankModule(name="confirmations", schema=SCHEMA)
