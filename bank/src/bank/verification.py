"""Strong Verification challenges: a simulated SMS code for one exact Red Operation call.

A challenge is bound like an approval token (Customer, session, Operation name, input
fingerprint). Its code goes to the Customer's simulated phone; the right code, from the
same session's phone before the challenge expires, earns one verification token for
exactly that call. Three wrong codes close a challenge, and wrong codes are counted per
session for the Safety Lock. Only a hash of the code is stored.
"""

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from bank import phone
from bank.approvals import Binding
from bank.registry import BankModule, Refusal, RefusalReason

CHALLENGE_LIFETIME = timedelta(minutes=5)
MAX_FAILED_ATTEMPTS = 3  # per challenge; then the Customer must request a new code

SCHEMA = """
CREATE TABLE verification_challenges (
    challenge_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    answered_at TEXT,
    failed_attempts INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass(frozen=True)
class Challenge:
    challenge_id: str
    binding: Binding
    expires_at: datetime


@dataclass(frozen=True)
class SentChallenge:
    challenge: Challenge
    sent_to: str


def _hash(challenge_id: str, code: str) -> str:
    return hashlib.sha256(f"{challenge_id}:{code}".encode()).hexdigest()


def start(
    conn: sqlite3.Connection, binding: Binding, description_zh: str, now: datetime
) -> SentChallenge:
    """Open a challenge and text its code to the Customer's simulated phone."""
    challenge = Challenge(
        challenge_id=f"CHL-{secrets.token_hex(6)}",
        binding=binding,
        expires_at=now + CHALLENGE_LIFETIME,
    )
    code = f"{secrets.randbelow(1_000_000):06d}"
    conn.execute(
        "INSERT INTO verification_challenges"
        " (challenge_id, session_id, customer_id, operation, fingerprint, code_hash,"
        " issued_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            challenge.challenge_id,
            binding.session_id,
            binding.customer_id,
            binding.operation,
            binding.fingerprint,
            _hash(challenge.challenge_id, code),
            now.isoformat(),
            challenge.expires_at.isoformat(),
        ),
    )
    minutes = int(CHALLENGE_LIFETIME.total_seconds() // 60)
    text = (
        f"【模拟银行】验证码 {code}，用于授权「{description_zh.rstrip('。')}」"
        f"（{binding.operation}），{minutes} 分钟内有效。银行工作人员不会向您索取验证码，"
        "请勿告诉任何人。"
    )
    sent_to = phone.send_sms(conn, binding.customer_id, text, now)
    return SentChallenge(challenge, sent_to)


def find_open(
    conn: sqlite3.Connection, challenge_id: str, session_id: str, now: datetime
) -> Challenge:
    """The challenge this session may still answer, or a `token_invalid` refusal."""
    row = conn.execute(
        "SELECT * FROM verification_challenges WHERE challenge_id = ?", (challenge_id,)
    ).fetchone()
    if (
        row is None
        or row["session_id"] != session_id
        or row["answered_at"] is not None
        or row["failed_attempts"] >= MAX_FAILED_ATTEMPTS
        or now >= datetime.fromisoformat(row["expires_at"])
    ):
        raise Refusal(
            RefusalReason.TOKEN_INVALID,
            "This challenge is not open for this session: request a new one",
            {"challenge_id": challenge_id},
            audit_detail={"challenge_id": challenge_id},
        )
    return Challenge(
        challenge_id, Binding.from_row(row), datetime.fromisoformat(row["expires_at"])
    )


def check_code(conn: sqlite3.Connection, challenge: Challenge, code: str) -> bool:
    """Whether `code` answers the challenge. A wrong code is counted against it."""
    row = conn.execute(
        "SELECT code_hash FROM verification_challenges WHERE challenge_id = ?",
        (challenge.challenge_id,),
    ).fetchone()
    if secrets.compare_digest(row["code_hash"], _hash(challenge.challenge_id, code)):
        return True
    conn.execute(
        "UPDATE verification_challenges SET failed_attempts = failed_attempts + 1"
        " WHERE challenge_id = ?",
        (challenge.challenge_id,),
    )
    return False


def mark_answered(conn: sqlite3.Connection, challenge: Challenge, now: datetime) -> None:
    conn.execute(
        "UPDATE verification_challenges SET answered_at = ? WHERE challenge_id = ?",
        (now.isoformat(), challenge.challenge_id),
    )


def failed_attempts(conn: sqlite3.Connection, session_id: str) -> int:
    """Wrong codes entered in this session, across all its challenges."""
    row = conn.execute(
        "SELECT COALESCE(SUM(failed_attempts), 0) FROM verification_challenges"
        " WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    count: int = row[0]
    return count


MODULE = BankModule(name="verification", schema=SCHEMA)
