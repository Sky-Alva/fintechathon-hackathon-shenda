"""The Bank clock: the single source of "now" for the whole Bank, stored with the data."""

import sqlite3
from datetime import datetime, timedelta, timezone

from bank.registry import BankModule

BANK_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
SEED_NOW = datetime(2026, 10, 20, 9, 0, tzinfo=BANK_TZ)

SCHEMA = """
CREATE TABLE bank_clock (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    now TEXT NOT NULL
);
"""


def seed(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO bank_clock (id, now) VALUES (1, ?)", (SEED_NOW.isoformat(),))


def now(conn: sqlite3.Connection) -> datetime:
    row = conn.execute("SELECT now FROM bank_clock WHERE id = 1").fetchone()
    return datetime.fromisoformat(row[0]).astimezone(BANK_TZ)


def set_now(conn: sqlite3.Connection, value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=BANK_TZ)
    value = value.astimezone(BANK_TZ)
    conn.execute("UPDATE bank_clock SET now = ? WHERE id = 1", (value.isoformat(),))
    return value


def advance(conn: sqlite3.Connection, by: timedelta) -> datetime:
    return set_now(conn, now(conn) + by)


MODULE = BankModule(name="clock", schema=SCHEMA, seed=seed)
