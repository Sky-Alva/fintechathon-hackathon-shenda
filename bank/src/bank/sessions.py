"""Sessions & identity: login, the link from a session to exactly one Customer, and
password change (Red: it would let whoever holds the chat take over the login). A
password change ends the Customer's other sessions; an ended session is kept for its
audit trail but can no longer authenticate.

Login also returns a phone token: the session's simulated phone, which reads the
Customer's SMS and answers verification challenges (see `bank.phone`). The Channel keeps
it and never hands it to the Agent.
"""

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from bank import clock
from bank.registry import (
    BankModule,
    Irreversible,
    Operation,
    OperationContext,
    Refusal,
    RefusalReason,
    Secret,
    Tier,
)

SCHEMA = """
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    session_token TEXT NOT NULL UNIQUE,
    phone_token TEXT NOT NULL UNIQUE,
    customer_id TEXT NOT NULL REFERENCES customers (customer_id),
    created_at TEXT NOT NULL,
    ended_at TEXT
);
"""

_SALT = b"fintechathon-demo-bank"


def hash_password(password: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), _SALT, 100_000).hex()


@dataclass(frozen=True)
class Session:
    session_id: str
    session_token: str
    phone_token: str
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
        phone_token=secrets.token_urlsafe(32),
        customer_id=row["customer_id"],
        customer_name=row["name"],
    )
    conn.execute(
        "INSERT INTO sessions"
        " (session_id, session_token, phone_token, customer_id, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (session.session_id, session.session_token, session.phone_token, session.customer_id,
         clock.now(conn).isoformat()),
    )
    return session


def authenticate(conn: sqlite3.Connection, session_token: str | None) -> Session:
    row = None
    if session_token:
        row = conn.execute(
            "SELECT s.session_id, s.phone_token, s.customer_id, c.name FROM sessions s"
            " JOIN customers c USING (customer_id)"
            " WHERE s.session_token = ? AND s.ended_at IS NULL",
            (session_token,),
        ).fetchone()
    if row is None:
        raise Refusal(RefusalReason.NOT_AUTHENTICATED, "No valid session; log in first")
    return Session(
        row["session_id"], session_token or "", row["phone_token"], row["customer_id"], row["name"]
    )


def authenticate_phone(conn: sqlite3.Connection, phone_token: str | None) -> Session:
    """The session whose simulated phone this phone token is."""
    row = None
    if phone_token:
        row = conn.execute(
            "SELECT s.session_id, s.session_token, s.customer_id, c.name FROM sessions s"
            " JOIN customers c USING (customer_id)"
            " WHERE s.phone_token = ? AND s.ended_at IS NULL",
            (phone_token,),
        ).fetchone()
    if row is None:
        raise Refusal(RefusalReason.NOT_AUTHENTICATED, "No valid phone token; log in first")
    return Session(
        row["session_id"], row["session_token"], phone_token or "", row["customer_id"], row["name"]
    )


def by_id(conn: sqlite3.Connection, session_id: str) -> Session:
    """A session, ended or not, for what the Bank does later on its behalf (e.g. running a
    Scheduled Transfer it approved)."""
    row = conn.execute(
        "SELECT s.session_id, s.session_token, s.phone_token, s.customer_id, c.name"
        " FROM sessions s JOIN customers c USING (customer_id) WHERE s.session_id = ?",
        (session_id,),
    ).fetchone()
    return Session(
        row["session_id"], row["session_token"], row["phone_token"], row["customer_id"],
        row["name"],
    )


def exists(conn: sqlite3.Connection, session_id: str) -> bool:
    return (
        conn.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        is not None
    )


class ChangePasswordInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_password: Annotated[str, Field(min_length=8, max_length=64), Secret()]


class ChangePasswordOutput(BaseModel):
    changed: bool
    other_sessions_ended: int


def change_password(ctx: OperationContext, input: ChangePasswordInput) -> ChangePasswordOutput:
    ctx.conn.execute(
        "UPDATE customers SET password_hash = ? WHERE customer_id = ?",
        (hash_password(input.new_password), ctx.customer_id),
    )
    ended = ctx.conn.execute(
        "UPDATE sessions SET ended_at = ?"
        " WHERE customer_id = ? AND session_id != ? AND ended_at IS NULL",
        (ctx.now.isoformat(), ctx.customer_id, ctx.session_id),
    ).rowcount
    return ChangePasswordOutput(changed=True, other_sessions_ended=ended)


MODULE = BankModule(
    name="sessions",
    schema=SCHEMA,
    operations=[
        Operation(
            name="change_password",
            description_zh="修改当前客户的登录密码（8–64 个字符），并退出该客户的其他登录会话。",
            description_en=(
                "Change the current Customer's login password (8–64 characters) and end"
                " their other sessions."
            ),
            input_model=ChangePasswordInput,
            output_model=ChangePasswordOutput,
            tier=Tier.RED,
            reversibility=Irreversible(),
            handler=change_password,
        )
    ],
)
