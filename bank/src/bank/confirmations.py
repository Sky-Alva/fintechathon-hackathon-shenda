"""Confirmation tokens: proof that the Customer confirmed one exact Yellow Operation.

A token is bound to the Customer, the session, the Operation name and a fingerprint of
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
class Binding:
    """Exactly what a token covers."""

    session_id: str
    customer_id: str
    operation: str
    fingerprint: str

    @classmethod
    def of(
        cls, *, session_id: str, customer_id: str, operation: str, validated_input: BaseModel
    ) -> "Binding":
        canonical = json.dumps(
            {"operation": operation, "input": validated_input.model_dump(mode="json")},
            sort_keys=True,
            ensure_ascii=False,
        )
        return cls(
            session_id, customer_id, operation, hashlib.sha256(canonical.encode()).hexdigest()
        )


@dataclass(frozen=True)
class IssuedToken:
    confirmation_id: str
    confirmation_token: str
    expires_at: datetime


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue(conn: sqlite3.Connection, binding: Binding, now: datetime) -> IssuedToken:
    issued = IssuedToken(
        confirmation_id=f"CNF-{secrets.token_hex(6)}",
        confirmation_token=secrets.token_urlsafe(32),
        expires_at=now + CONFIRMATION_LIFETIME,
    )
    conn.execute(
        "INSERT INTO confirmations VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
        (
            issued.confirmation_id,
            _hash(issued.confirmation_token),
            binding.session_id,
            binding.customer_id,
            binding.operation,
            binding.fingerprint,
            now.isoformat(),
            issued.expires_at.isoformat(),
        ),
    )
    return issued


def redeem(conn: sqlite3.Connection, token: str, binding: Binding, now: datetime) -> str:
    """Use up a token for exactly this Operation call; return its confirmation ID.

    The token is marked used inside the caller's transaction, so if the call is then
    refused and rolled back the token stays valid: nothing it confirmed has happened.
    """
    row = conn.execute(
        "SELECT * FROM confirmations WHERE token_hash = ?", (_hash(token),)
    ).fetchone()
    if (
        row is None
        or Binding(row["session_id"], row["customer_id"], row["operation"], row["fingerprint"])
        != binding
        or row["used_at"] is not None
        or now >= datetime.fromisoformat(row["expires_at"])
    ):
        # One reason for every mismatch, so a caller can't probe which part was wrong.
        raise Refusal(
            RefusalReason.TOKEN_INVALID,
            "This Confirmation does not cover this exact Operation, or has expired or been used",
            audit_detail={"confirmation_id": row["confirmation_id"]} if row else None,
        )
    conn.execute(
        "UPDATE confirmations SET used_at = ? WHERE confirmation_id = ?",
        (now.isoformat(), row["confirmation_id"]),
    )
    confirmation_id: str = row["confirmation_id"]
    return confirmation_id


MODULE = BankModule(name="confirmations", schema=SCHEMA)
