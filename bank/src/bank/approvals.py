"""Approval tokens: proof that the Customer approved one exact Operation call.

There are two kinds. A Confirmation token covers a Yellow Operation the Customer
confirmed; a verification token covers an Operation whose Strong Verification the
Customer passed. Either kind is bound to the Customer, the session, the Operation name
and a fingerprint of its validated input. It works once and expires after a few
Bank-clock minutes. Only a hash of the token is stored, and the audit log refers to it
by its ID.
"""

import hashlib
import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel

from bank.registry import BankModule, Refusal, RefusalReason

TOKEN_LIFETIME = timedelta(minutes=5)

SCHEMA = """
CREATE TABLE approval_tokens (
    token_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('confirmation', 'verification')),
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


class TokenKind(StrEnum):
    CONFIRMATION = "confirmation"
    VERIFICATION = "verification"


ID_PREFIX = {TokenKind.CONFIRMATION: "CNF", TokenKind.VERIFICATION: "VER"}


@dataclass(frozen=True)
class Binding:
    """Exactly what a token covers."""

    session_id: str
    customer_id: str
    operation: str
    fingerprint: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Binding":
        return cls(row["session_id"], row["customer_id"], row["operation"], row["fingerprint"])

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
    token_id: str
    token: str
    expires_at: datetime


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue(
    conn: sqlite3.Connection, kind: TokenKind, binding: Binding, now: datetime
) -> IssuedToken:
    issued = IssuedToken(
        token_id=f"{ID_PREFIX[kind]}-{secrets.token_hex(6)}",
        token=secrets.token_urlsafe(32),
        expires_at=now + TOKEN_LIFETIME,
    )
    conn.execute(
        "INSERT INTO approval_tokens VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
        (
            issued.token_id,
            kind.value,
            _hash(issued.token),
            binding.session_id,
            binding.customer_id,
            binding.operation,
            binding.fingerprint,
            now.isoformat(),
            issued.expires_at.isoformat(),
        ),
    )
    return issued


def redeem(
    conn: sqlite3.Connection, kind: TokenKind, token: str, binding: Binding, now: datetime
) -> str:
    """Use up a token of this kind for exactly this Operation call; return its ID.

    The token is marked used inside the caller's transaction, so if the call is then
    refused and rolled back the token stays valid: nothing it approved has happened.
    """
    row = conn.execute(
        "SELECT * FROM approval_tokens WHERE token_hash = ?", (_hash(token),)
    ).fetchone()
    if (
        row is None
        or row["kind"] != kind.value
        or Binding.from_row(row) != binding
        or row["used_at"] is not None
        or now >= datetime.fromisoformat(row["expires_at"])
    ):
        # One reason for every mismatch, so a caller can't probe which part was wrong.
        raise Refusal(
            RefusalReason.TOKEN_INVALID,
            f"This {kind.value} token does not cover this exact Operation,"
            " or has expired or been used",
            audit_detail={f"{row['kind']}_id": row["token_id"]} if row else None,
        )
    conn.execute(
        "UPDATE approval_tokens SET used_at = ? WHERE token_id = ?",
        (now.isoformat(), row["token_id"]),
    )
    token_id: str = row["token_id"]
    return token_id


MODULE = BankModule(name="approvals", schema=SCHEMA)
