"""SQLite access: one connection per request, explicit transactions, and reset."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from bank.registry import BankModule


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Run a request as one write transaction, committed only if it completes."""
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()


def is_initialised(db_path: Path) -> bool:
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'bank_clock'"
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def reset(conn: sqlite3.Connection, modules: list[BankModule]) -> None:
    """Drop every table, then recreate and seed each module in order."""
    conn.execute("PRAGMA defer_foreign_keys = ON")
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    for (table,) in tables:
        conn.execute(f'DROP TABLE "{table}"')
    for module in modules:
        for statement in module.schema.split(";"):
            if statement.strip():
                conn.execute(statement)
        module.seed(conn)
