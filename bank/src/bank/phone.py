"""The Customer's simulated phone: an SMS inbox the Bank writes verification codes to.

The Channel shows it as a phone in the UI, and the staff console can read it. It is
read, and codes are typed back into it, with the phone token from login, never the
session token, so a caller that holds only the session (the Agent) can neither read nor
answer the codes that approve its own Red Operations.
"""

import sqlite3
from datetime import datetime
from typing import Any

from bank import customers
from bank.registry import BankModule

SCHEMA = """
CREATE TABLE phone_messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL REFERENCES customers (customer_id),
    sent_to TEXT NOT NULL,
    at TEXT NOT NULL,
    text TEXT NOT NULL
);
"""


def masked(phone: str) -> str:
    """How the Bank shows a phone number outside the phone itself: 138****0002."""
    return f"{phone[:3]}****{phone[-4:]}"


def send_sms(conn: sqlite3.Connection, customer_id: str, text: str, now: datetime) -> str:
    """Deliver an SMS to the Customer's phone; return the masked number it went to."""
    sent_to = masked(customers.phone_number(conn, customer_id))
    conn.execute(
        "INSERT INTO phone_messages (customer_id, sent_to, at, text) VALUES (?, ?, ?, ?)",
        (customer_id, sent_to, now.isoformat(), text),
    )
    return sent_to


def messages(conn: sqlite3.Connection, customer_id: str) -> list[dict[str, Any]]:
    """Every SMS on the Customer's phone, oldest first."""
    rows = conn.execute(
        "SELECT message_id, sent_to, at, text FROM phone_messages"
        " WHERE customer_id = ? ORDER BY message_id",
        (customer_id,),
    ).fetchall()
    return [dict(row) for row in rows]


MODULE = BankModule(name="phone", schema=SCHEMA)
