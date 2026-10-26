"""The Daily Transfer Total: the sum of a Customer's outgoing money on one Bank-clock day.

The policy gate records every Operation that declares a `daily_transfer_amount`, so
transfers, Merchant payments from Reserved Funds and Scheduled Transfers (on the day
they run) all count the same way. Scenario modules only read it, through
`ctx.daily_transfer_total`, and tier with `tier_for_outgoing`.
"""

import sqlite3
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal

from bank.money import from_cents, to_cents
from bank.registry import BankModule, OperationContext, Tier

DAILY_TRANSFER_LIMIT = Decimal("1000.00")  # a total above this makes a transfer Red

SCHEMA = """
CREATE TABLE daily_transfer_ledger (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL,
    day TEXT NOT NULL,
    operation TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    at TEXT NOT NULL
);
"""


def daily_transfer_total(conn: sqlite3.Connection, customer_id: str, day: date) -> Decimal:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM daily_transfer_ledger"
        " WHERE customer_id = ? AND day = ?",
        (customer_id, day.isoformat()),
    ).fetchone()
    return from_cents(row[0])


PendingTotal = Callable[[sqlite3.Connection, str, date], Decimal]
"""What a Customer has set to leave their account on a day but isn't paid yet."""

_pending_totals: list[PendingTotal] = []


def counts_as_pending(total: PendingTotal) -> PendingTotal:
    """Declare money set to leave on a day (Scheduled Transfers, Merchant orders): each
    module owns its own, and `projected_total` adds them all up."""
    _pending_totals.append(total)
    return total


def projected_total(conn: sqlite3.Connection, customer_id: str, day: date) -> Decimal:
    """The Customer's projected Daily Transfer Total for `day`: what they have already sent
    that day, plus everything still set to be paid on it."""
    pending = sum((total(conn, customer_id, day) for total in _pending_totals), Decimal(0))
    return daily_transfer_total(conn, customer_id, day) + pending


def record(
    conn: sqlite3.Connection, customer_id: str, operation: str, amount: Decimal, at: datetime
) -> None:
    conn.execute(
        "INSERT INTO daily_transfer_ledger (customer_id, day, operation, amount_cents, at)"
        " VALUES (?, ?, ?, ?, ?)",
        (customer_id, at.date().isoformat(), operation, to_cents(amount), at.isoformat()),
    )


def tier_for_outgoing(amount: Decimal, ctx: OperationContext) -> Tier:
    """Yellow while the Daily Transfer Total including `amount` stays ≤ ¥1,000, else Red."""
    return tier_for_day_total(ctx.daily_transfer_total, amount)


def tier_for_day_total(day_total: Decimal, amount: Decimal) -> Tier:
    """The same rule for a day other than today, given what that day's total is (or is set
    to be) before `amount`: how a Scheduled Transfer is tiered against its run day."""
    return Tier.YELLOW if day_total + amount <= DAILY_TRANSFER_LIMIT else Tier.RED


MODULE = BankModule(name="limits", schema=SCHEMA)
