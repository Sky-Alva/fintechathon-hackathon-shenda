"""Sessions & identity: login, and the link from a session to exactly one Customer."""

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass

from bank import clock
from bank.registry import BankModule, Refusal, RefusalReason

SCHEMA = """
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    session_token TEXT NOT NULL UNIQUE,
    customer_id TEXT NOT NULL REFERENCES customers (customer_id),
    created_at TEXT NOT NULL
);
"""

_SALT = b"fintechathon-demo-bank"


def hash_password(password: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), _SALT, 100_000).hex()


@dataclass(frozen=True)
class Session:
    session_id: str
    session_token: str
    customer_id: str
    customer_name: str


def login(conn: sqlite3.Connection, username: str, password: str) -> Session:
    row = conn.execute(
        "SELECT customer_id, name, password_hash FROM customers WHERE username = ?", (username,)
    ).fetchone()
    if row is None or not secrets.compare_digest(row["password_hash"], hash_password(password)):
        raise Refusal(RefusalReason.NOT_AUTHENTICATED, "Wrong username or password")
    session = Session(
        session_id=f"SES-{secrets.token_hex(6)}",
        session_token=secrets.token_urlsafe(32),
        customer_id=row["customer_id"],
        customer_name=row["name"],
    )
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?)",
        (session.session_id, session.session_token, session.customer_id,
         clock.now(conn).isoformat()),
    )
    return session


def authenticate(conn: sqlite3.Connection, session_token: str | None) -> Session:
    row = None
    if session_token:
        row = conn.execute(
            "SELECT s.session_id, s.customer_id, c.name FROM sessions s"
            " JOIN customers c USING (customer_id) WHERE s.session_token = ?",
            (session_token,),
        ).fetchone()
    if row is None:
        raise Refusal(RefusalReason.NOT_AUTHENTICATED, "No valid session; log in first")
    return Session(row["session_id"], session_token or "", row["customer_id"], row["name"])


def exists(conn: sqlite3.Connection, session_id: str) -> bool:
    return (
        conn.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        is not None
    )


MODULE = BankModule(name="sessions", schema=SCHEMA)
