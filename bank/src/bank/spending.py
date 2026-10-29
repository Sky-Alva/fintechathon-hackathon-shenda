"""Seeded transaction history and Green spending/report Operations."""

from __future__ import annotations

import sqlite3
import calendar
from datetime import date
from decimal import Decimal
from statistics import median
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bank import customers
from bank.money import from_cents
from bank.registry import (
    BankModule, Irreversible, Operation, OperationContext, OwnedBy, ReadOnly, Refusal,
    RefusalReason, Tier,
)

SpendingCategory = Literal[
    "income", "housing", "food_delivery", "transport", "subscriptions",
    "shopping", "gifts", "utilities", "dining", "entertainment", "healthcare",
]
# Money moved between people, not spent or earned: kept out of spending and income totals,
# Unusual Transaction rules and Recurring Charges (docs/adr/0002).
TRANSFER = "transfer"
NOT_A_TRANSFER = f"category != '{TRANSFER}'"
"""The clause that keeps transfers out of a spending or income query."""
GIFTS = "gifts"
"""A Merchant purchase's category."""
Category = SpendingCategory | Literal["transfer"]
AccountId = customers.AccountId

SCHEMA = """
CREATE TABLE transactions (
    transaction_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers (customer_id),
    account_id TEXT NOT NULL REFERENCES accounts (account_id),
    occurred_at TEXT NOT NULL,
    category TEXT NOT NULL,
    merchant TEXT NOT NULL,
    description TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    location TEXT NOT NULL DEFAULT 'Shenzhen, CN'
);
CREATE INDEX transactions_customer_date ON transactions (customer_id, occurred_at);
CREATE INDEX transactions_customer_category_date ON transactions (customer_id, category, occurred_at);
CREATE TABLE subscriptions (
    subscription_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers (customer_id),
    merchant TEXT NOT NULL,
    service_category TEXT NOT NULL,
    signed_up_on TEXT NOT NULL,
    original_amount_cents INTEGER NOT NULL CHECK (original_amount_cents > 0),
    current_amount_cents INTEGER NOT NULL CHECK (current_amount_cents > 0),
    trial_converted INTEGER NOT NULL DEFAULT 0 CHECK (trial_converted IN (0, 1)),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')),
    UNIQUE (customer_id, merchant)
);
"""

# Amounts are positive integer cents; `direction` determines whether funds entered or
# left the account. Dates cover roughly six months through the initial Bank clock, plus
# last year's birthday gift to make the orchestration reference scenario inspectable.
_ROWS: tuple[tuple[str, str, str, str, str, str, str, int], ...] = (
    ("2025-10-26", "CUST-LM", "ACC-LM-001", "gifts", "Bloom & Co.", "Flowers for spouse's birthday", "debit", 26800),
    ("2026-04-01", "CUST-LM", "ACC-LM-001", "income", "Shenzhen Technology Co.", "Monthly salary", "credit", 1850000),
    ("2026-04-02", "CUST-LM", "ACC-LM-001", "housing", "Evergreen Residence", "April rent", "debit", 420000),
    ("2026-04-03", "CUST-LM", "ACC-LM-001", "subscriptions", "CloudMusic", "Monthly music membership", "debit", 1800),
    ("2026-04-04", "CUST-LM", "ACC-LM-001", "subscriptions", "TuneBox", "Monthly music membership", "debit", 2200),
    ("2026-04-05", "CUST-LM", "ACC-LM-001", "food_delivery", "Meituan", "Lunch delivery", "debit", 3800),
    ("2026-04-08", "CUST-LM", "ACC-LM-001", "transport", "Shenzhen Metro", "Metro travel", "debit", 620),
    ("2026-04-12", "CUST-LM", "ACC-LM-001", "utilities", "Shenzhen Water", "Water bill", "debit", 6800),
    ("2026-04-27", "CUST-LM", "ACC-LM-001", "food_delivery", "Ele.me", "Dinner delivery", "debit", 5260),
    ("2026-04-14", "CUST-LM", "ACC-LM-001", "transport", "DiDi", "Ride to office", "debit", 3200),
    ("2026-04-24", "CUST-LM", "ACC-LM-001", "dining", "Nanshan Kitchen", "Dinner", "debit", 15600),
    ("2026-04-28", "CUST-LM", "ACC-LM-001", "shopping", "JD.com", "Household supplies", "debit", 12900),
    ("2026-05-01", "CUST-LM", "ACC-LM-001", "income", "Shenzhen Technology Co.", "Monthly salary", "credit", 1850000),
    ("2026-05-02", "CUST-LM", "ACC-LM-001", "housing", "Evergreen Residence", "May rent", "debit", 420000),
    ("2026-05-03", "CUST-LM", "ACC-LM-001", "subscriptions", "CloudMusic", "Monthly music membership", "debit", 1800),
    ("2026-05-04", "CUST-LM", "ACC-LM-001", "subscriptions", "TuneBox", "Monthly music membership", "debit", 2200),
    ("2026-05-19", "CUST-LM", "ACC-LM-001", "food_delivery", "Meituan", "Lunch delivery", "debit", 4150),
    ("2026-05-21", "CUST-LM", "ACC-LM-001", "transport", "Shenzhen Metro", "Metro travel", "debit", 760),
    ("2026-05-12", "CUST-LM", "ACC-LM-001", "utilities", "Shenzhen Power", "Electricity bill", "debit", 14300),
    ("2026-05-08", "CUST-LM", "ACC-LM-001", "food_delivery", "Ele.me", "Dinner delivery", "debit", 4870),
    ("2026-05-03", "CUST-LM", "ACC-LM-001", "transport", "DiDi", "Ride to office", "debit", 4100),
    ("2026-05-24", "CUST-LM", "ACC-LM-001", "dining", "Canton Table", "Weekend meal", "debit", 20800),
    ("2026-05-28", "CUST-LM", "ACC-LM-001", "shopping", "Taobao", "Home supplies", "debit", 18600),
    ("2026-06-01", "CUST-LM", "ACC-LM-001", "income", "Shenzhen Technology Co.", "Monthly salary", "credit", 1850000),
    ("2026-06-02", "CUST-LM", "ACC-LM-001", "housing", "Evergreen Residence", "June rent", "debit", 420000),
    ("2026-06-03", "CUST-LM", "ACC-LM-001", "subscriptions", "CloudMusic", "Monthly music membership", "debit", 1800),
    ("2026-06-04", "CUST-LM", "ACC-LM-001", "subscriptions", "TuneBox", "Monthly music membership", "debit", 2200),
    ("2026-06-02", "CUST-LM", "ACC-LM-001", "food_delivery", "Meituan", "Lunch delivery", "debit", 3650),
    ("2026-06-03", "CUST-LM", "ACC-LM-001", "transport", "Shenzhen Metro", "Metro travel", "debit", 840),
    ("2026-06-12", "CUST-LM", "ACC-LM-001", "utilities", "Shenzhen Water", "Water bill", "debit", 7200),
    ("2026-06-25", "CUST-LM", "ACC-LM-001", "food_delivery", "Ele.me", "Dinner delivery", "debit", 5920),
    ("2026-06-20", "CUST-LM", "ACC-LM-001", "transport", "DiDi", "Airport ride", "debit", 16800),
    ("2026-06-24", "CUST-LM", "ACC-LM-001", "dining", "Nanshan Kitchen", "Dinner", "debit", 17200),
    ("2026-06-28", "CUST-LM", "ACC-LM-001", "shopping", "JD.com", "Phone accessories", "debit", 23900),
    ("2026-07-01", "CUST-LM", "ACC-LM-001", "income", "Shenzhen Technology Co.", "Monthly salary", "credit", 1850000),
    ("2026-07-02", "CUST-LM", "ACC-LM-001", "housing", "Evergreen Residence", "July rent", "debit", 420000),
    ("2026-07-03", "CUST-LM", "ACC-LM-001", "subscriptions", "CloudMusic", "Monthly music membership", "debit", 1800),
    ("2026-07-04", "CUST-LM", "ACC-LM-001", "subscriptions", "TuneBox", "Monthly music membership", "debit", 2200),
    ("2026-07-05", "CUST-LM", "ACC-LM-001", "subscriptions", "StreamPlus", "Trial converted to monthly subscription", "debit", 2500),
    ("2026-07-23", "CUST-LM", "ACC-LM-001", "food_delivery", "Meituan", "Lunch delivery", "debit", 4380),
    ("2026-07-26", "CUST-LM", "ACC-LM-001", "transport", "Shenzhen Metro", "Metro travel", "debit", 930),
    ("2026-07-12", "CUST-LM", "ACC-LM-001", "utilities", "Shenzhen Power", "Electricity bill", "debit", 16100),
    ("2026-07-11", "CUST-LM", "ACC-LM-001", "food_delivery", "Ele.me", "Dinner delivery", "debit", 5260),
    ("2026-07-09", "CUST-LM", "ACC-LM-001", "transport", "DiDi", "Ride to office", "debit", 3800),
    ("2026-07-24", "CUST-LM", "ACC-LM-001", "dining", "Canton Table", "Dinner", "debit", 19600),
    ("2026-07-28", "CUST-LM", "ACC-LM-001", "shopping", "Taobao", "Kitchen items", "debit", 14500),
    ("2026-08-01", "CUST-LM", "ACC-LM-001", "income", "Shenzhen Technology Co.", "Monthly salary", "credit", 1850000),
    ("2026-08-02", "CUST-LM", "ACC-LM-001", "housing", "Evergreen Residence", "August rent", "debit", 420000),
    ("2026-08-03", "CUST-LM", "ACC-LM-001", "subscriptions", "CloudMusic", "Monthly membership; price increased from CNY 18 to CNY 25", "debit", 2500),
    ("2026-08-04", "CUST-LM", "ACC-LM-001", "subscriptions", "TuneBox", "Monthly music membership", "debit", 2200),
    ("2026-08-05", "CUST-LM", "ACC-LM-001", "subscriptions", "StreamPlus", "Monthly subscription after trial", "debit", 2500),
    ("2026-08-09", "CUST-LM", "ACC-LM-001", "food_delivery", "Meituan", "Lunch delivery", "debit", 3950),
    ("2026-08-12", "CUST-LM", "ACC-LM-001", "transport", "Shenzhen Metro", "Metro travel", "debit", 720),
    ("2026-08-12", "CUST-LM", "ACC-LM-001", "utilities", "Shenzhen Water", "Water bill", "debit", 6900),
    ("2026-08-29", "CUST-LM", "ACC-LM-001", "food_delivery", "Ele.me", "Dinner delivery", "debit", 6150),
    ("2026-08-27", "CUST-LM", "ACC-LM-001", "transport", "DiDi", "Ride to office", "debit", 4400),
    ("2026-08-24", "CUST-LM", "ACC-LM-001", "dining", "Nanshan Kitchen", "Dinner", "debit", 18400),
    ("2026-08-28", "CUST-LM", "ACC-LM-001", "shopping", "JD.com", "Household supplies", "debit", 12900),
    ("2026-09-01", "CUST-LM", "ACC-LM-001", "income", "Shenzhen Technology Co.", "Monthly salary", "credit", 1850000),
    ("2026-09-02", "CUST-LM", "ACC-LM-001", "housing", "Evergreen Residence", "September rent", "debit", 420000),
    ("2026-09-03", "CUST-LM", "ACC-LM-001", "subscriptions", "CloudMusic", "Monthly music membership", "debit", 2500),
    ("2026-09-04", "CUST-LM", "ACC-LM-001", "subscriptions", "TuneBox", "Monthly music membership", "debit", 2200),
    ("2026-09-05", "CUST-LM", "ACC-LM-001", "subscriptions", "StreamPlus", "Monthly subscription after trial", "debit", 2500),
    ("2026-09-27", "CUST-LM", "ACC-LM-001", "food_delivery", "Meituan", "Lunch delivery", "debit", 4590),
    ("2026-09-29", "CUST-LM", "ACC-LM-001", "transport", "Shenzhen Metro", "Metro travel", "debit", 810),
    ("2026-09-12", "CUST-LM", "ACC-LM-001", "utilities", "Shenzhen Power", "Electricity bill", "debit", 15400),
    ("2026-09-14", "CUST-LM", "ACC-LM-001", "food_delivery", "Ele.me", "Dinner delivery", "debit", 5420),
    ("2026-09-15", "CUST-LM", "ACC-LM-001", "transport", "DiDi", "Ride to office", "debit", 3500),
    ("2026-09-24", "CUST-LM", "ACC-LM-001", "dining", "Canton Table", "Dinner", "debit", 22600),
    ("2026-09-28", "CUST-LM", "ACC-LM-001", "shopping", "Taobao", "Home supplies", "debit", 17400),
    ("2026-10-01", "CUST-LM", "ACC-LM-001", "income", "Shenzhen Technology Co.", "Monthly salary", "credit", 1850000),
    ("2026-10-02", "CUST-LM", "ACC-LM-001", "housing", "Evergreen Residence", "October rent", "debit", 420000),
    ("2026-10-03", "CUST-LM", "ACC-LM-001", "subscriptions", "CloudMusic", "Monthly music membership", "debit", 2500),
    ("2026-10-04", "CUST-LM", "ACC-LM-001", "subscriptions", "TuneBox", "Monthly music membership", "debit", 2200),
    ("2026-10-05", "CUST-LM", "ACC-LM-001", "subscriptions", "StreamPlus", "Monthly subscription after trial", "debit", 2500),
    ("2026-10-03", "CUST-LM", "ACC-LM-001", "food_delivery", "Meituan", "Lunch delivery", "debit", 4250),
    ("2026-10-02", "CUST-LM", "ACC-LM-001", "transport", "Shenzhen Metro", "Metro travel", "debit", 680),
    ("2026-10-12", "CUST-LM", "ACC-LM-001", "utilities", "Shenzhen Water", "Water bill", "debit", 7350),
    ("2026-10-16", "CUST-LM", "ACC-LM-001", "food_delivery", "Ele.me", "Dinner delivery", "debit", 5180),
    ("2026-10-18", "CUST-LM", "ACC-LM-001", "transport", "DiDi", "Ride to office", "debit", 3900),
    ("2026-10-10", "CUST-LM", "ACC-LM-001", "shopping", "HomePro Electronics", "Large home appliance purchase", "debit", 328000),
    ("2026-10-14", "CUST-LM", "ACC-LM-001", "food_delivery", "QuickBite 24", "Late-night food order", "debit", 6800),
    ("2026-10-19", "CUST-LM", "ACC-LM-001", "shopping", "Tokyo Duty Free", "Airport purchase", "debit", 12800),
)


def seed(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO transactions (transaction_id, customer_id, account_id, occurred_at, "
        "category, merchant, description, direction, amount_cents) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (f"TXN-LM-{index:04d}", customer_id, account_id,
             f"{day}T{'02:17:00' if merchant == 'QuickBite 24' else '12:00:00'}+08:00",
             category, merchant, description, direction, cents)
            for index, (day, customer_id, account_id, category, merchant, description,
                        direction, cents) in enumerate(_ROWS, start=1)
        ],
    )
    conn.execute("UPDATE transactions SET location = 'Tokyo, JP' WHERE merchant = 'Tokyo Duty Free'")
    seed_subscriptions(conn)
    for (transfer_id,) in conn.execute("SELECT transfer_id FROM transfers").fetchall():
        record_transfer(conn, transfer_id)


def record_transfer(conn: sqlite3.Connection, transfer_id: str) -> None:
    """Put a completed transfer in the transaction history: a debit for the sender, with
    the Payee as the counterparty and the note as the description, and a credit for the
    receiving Customer when the Payee's account is in the Bank."""
    row = conn.execute(
        "SELECT t.customer_id, t.from_account_id, t.amount_cents, t.note, t.at,"
        " p.name AS payee_name, p.account_id AS payee_account_id, c.name AS sender_name"
        " FROM transfers t JOIN payees p ON p.payee_id = t.payee_id"
        " JOIN customers c ON c.customer_id = t.customer_id WHERE t.transfer_id = ?",
        (transfer_id,),
    ).fetchone()
    entries = [(f"TXN-{transfer_id}", row["customer_id"], row["from_account_id"],
                row["payee_name"], "debit")]
    receiver = conn.execute(
        "SELECT customer_id FROM accounts WHERE account_id = ?", (row["payee_account_id"],)
    ).fetchone()
    if receiver is not None:
        entries.append((f"TXN-{transfer_id}-IN", receiver["customer_id"],
                        row["payee_account_id"], row["sender_name"], "credit"))
    conn.executemany(
        "INSERT OR IGNORE INTO transactions (transaction_id, customer_id, account_id, "
        "occurred_at, category, merchant, description, direction, amount_cents) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(txn_id, customer_id, account_id, row["at"], TRANSFER, counterparty, row["note"],
          direction, row["amount_cents"])
         for txn_id, customer_id, account_id, counterparty, direction in entries],
    )


def record_merchant_payment(conn: sqlite3.Connection, order_id: str) -> None:
    """Put a paid Merchant order in the transaction history: a purchase, so it is gift
    spending (flowers and cakes), unlike a transfer to a Payee."""
    conn.execute(
        "INSERT OR IGNORE INTO transactions (transaction_id, customer_id, account_id, "
        "occurred_at, category, merchant, description, direction, amount_cents) "
        f"SELECT 'TXN-' || o.order_id, o.customer_id, r.account_id, o.paid_at, '{GIFTS}', m.name,"
        " CASE WHEN o.note != '' THEN o.note ELSE p.name END, 'debit', o.amount_cents"
        " FROM merchant_orders o JOIN merchants m ON m.merchant_id = o.merchant_id"
        " JOIN merchant_products p ON p.product_id = o.product_id"
        " JOIN reserved_funds r ON r.reservation_id = o.reservation_id WHERE o.order_id = ?",
        (order_id,),
    )


def seed_subscriptions(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO subscriptions "
        "(subscription_id, customer_id, merchant, service_category, signed_up_on, "
        "original_amount_cents, current_amount_cents, trial_converted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SUB-LM-001", "CUST-LM", "CloudMusic", "music", "2025-04-03", 1800, 2500, 0),
            ("SUB-LM-002", "CUST-LM", "TuneBox", "music", "2025-04-04", 2200, 2200, 0),
            ("SUB-LM-003", "CUST-LM", "StreamPlus", "video", "2026-07-05", 2500, 2500, 1),
        ],
    )


def migrate(conn: sqlite3.Connection) -> None:
    """Add seeded transaction history to existing Bank databases without data loss."""
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()}
    if "transactions" not in tables:
        for statement in SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)
    else:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(transactions)").fetchall()}
        if "location" not in columns:
            conn.execute(
                "ALTER TABLE transactions ADD COLUMN location TEXT NOT NULL DEFAULT 'Shenzhen, CN'"
            )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS subscriptions ("
        "subscription_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(customer_id), "
        "merchant TEXT NOT NULL, service_category TEXT NOT NULL, signed_up_on TEXT NOT NULL, "
        "original_amount_cents INTEGER NOT NULL CHECK (original_amount_cents > 0), "
        "current_amount_cents INTEGER NOT NULL CHECK (current_amount_cents > 0), "
        "trial_converted INTEGER NOT NULL DEFAULT 0 CHECK (trial_converted IN (0, 1)), "
        "status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')), "
        "UNIQUE (customer_id, merchant))"
    )
    subscription_columns = {row[1] for row in conn.execute(
        "PRAGMA table_info(subscriptions)"
    ).fetchall()}
    if "status" not in subscription_columns:
        conn.execute(
            "ALTER TABLE subscriptions ADD COLUMN status TEXT NOT NULL DEFAULT 'active' "
            "CHECK (status IN ('active', 'cancelled'))"
        )
    seed(conn)


class TransactionFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: AccountId | None = None
    start_date: date | None = Field(default=None, description="Inclusive start date")
    end_date: date | None = Field(default=None, description="Inclusive end date")
    category: Category | None = None
    merchant: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(
        default=None, min_length=1, max_length=100,
        description="Text in the description, such as a transfer's note (家用); any case.",
    )

    @model_validator(mode="after")
    def dates_are_ordered(self) -> TransactionFilter:
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        return self


class ListTransactionsInput(TransactionFilter):
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10000)


class Transaction(BaseModel):
    transaction_id: str
    account_id: str
    occurred_at: str
    category: str
    merchant: str
    description: str
    direction: Literal["debit", "credit"]
    amount: Decimal
    location: str


class ListTransactionsOutput(BaseModel):
    transactions: list[Transaction]
    total: int
    limit: int
    offset: int


def _where(ctx: OperationContext, filters: TransactionFilter) -> tuple[str, list[object]]:
    clauses = ["customer_id = ?"]
    params: list[object] = [ctx.customer_id]
    for column, value in (
        ("account_id", filters.account_id),
        ("category", filters.category),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    if filters.start_date:
        clauses.append("date(occurred_at) >= ?")
        params.append(filters.start_date.isoformat())
    if filters.end_date:
        clauses.append("date(occurred_at) <= ?")
        params.append(filters.end_date.isoformat())
    if filters.merchant:
        clauses.append("instr(lower(merchant), lower(?)) > 0")
        params.append(filters.merchant.strip())
    if filters.description:
        clauses.append("instr(lower(description), lower(?)) > 0")
        params.append(filters.description.strip())
    return " AND ".join(clauses), params


def list_transactions(ctx: OperationContext, input: ListTransactionsInput) -> ListTransactionsOutput:
    where, params = _where(ctx, input)
    total = ctx.conn.execute(f"SELECT COUNT(*) FROM transactions WHERE {where}", params).fetchone()[0]
    rows = ctx.conn.execute(
        f"SELECT transaction_id, account_id, occurred_at, category, merchant, description, "
        f"direction, amount_cents, location FROM transactions WHERE {where} "
        "ORDER BY occurred_at DESC, transaction_id DESC LIMIT ? OFFSET ?",
        [*params, input.limit, input.offset],
    ).fetchall()
    return ListTransactionsOutput(
        transactions=[Transaction(
            transaction_id=row["transaction_id"], account_id=row["account_id"],
            occurred_at=row["occurred_at"], category=row["category"], merchant=row["merchant"],
            description=row["description"], direction=row["direction"],
            amount=from_cents(row["amount_cents"]),
            location=row["location"],
        ) for row in rows],
        total=total, limit=input.limit, offset=input.offset,
    )


class UnusualTransactionsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: AccountId | None = None
    start_date: date | None = Field(default=None, description="Inclusive start date")
    end_date: date | None = Field(default=None, description="Inclusive end date")

    @model_validator(mode="after")
    def dates_are_ordered(self) -> UnusualTransactionsInput:
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        return self


class UnusualTransaction(BaseModel):
    transaction: Transaction
    rule_id: Literal[
        "amount_far_above_category", "unfamiliar_merchant_unusual_hour",
        "new_overseas_location",
    ]
    rule: str


class UnusualTransactionsOutput(BaseModel):
    start_date: date | None
    end_date: date | None
    flags: list[UnusualTransaction]


def detect_unusual_transactions(
    ctx: OperationContext, input: UnusualTransactionsInput
) -> UnusualTransactionsOutput:
    """Deterministic Bank rules; no model or probabilistic classifier is involved."""
    clauses = ["customer_id = ?"]
    params: list[object] = [ctx.customer_id]
    if input.account_id:
        clauses.append("account_id = ?")
        params.append(input.account_id)
    clauses.append(NOT_A_TRANSFER)
    rows = ctx.conn.execute(
        "SELECT transaction_id, account_id, occurred_at, category, merchant, description, "
        "direction, amount_cents, location FROM transactions "
        f"WHERE {' AND '.join(clauses)} ORDER BY occurred_at, transaction_id",
        params,
    ).fetchall()

    category_history: dict[str, list[int]] = {}
    merchants_seen: set[str] = set()
    locations_seen: set[str] = set()
    flags: list[UnusualTransaction] = []
    for row in rows:
        occurred = str(row["occurred_at"])
        event_date = date.fromisoformat(occurred[:10])
        if event_date > ctx.now.date():
            continue
        direction, category = row["direction"], str(row["category"])
        merchant_key = str(row["merchant"]).strip().casefold()
        location = str(row["location"])
        amount = int(row["amount_cents"])
        rule_ids: list[tuple[str, str]] = []
        if direction == "debit":
            history = category_history.get(category, [])
            if len(history) >= 3:
                typical = int(median(history[-12:]))
                if amount >= max(typical * 5, 50000):
                    rule_ids.append((
                        "amount_far_above_category",
                        "Amount is at least five times the recent median for this spending category.",
                    ))
            hour = int(occurred[11:13])
            if merchant_key not in merchants_seen and (hour < 5 or hour >= 23):
                rule_ids.append((
                    "unfamiliar_merchant_unusual_hour",
                    "First transaction at this merchant, made between 11 PM and 5 AM.",
                ))
            country = location.rsplit(",", 1)[-1].strip().upper()
            location_key = location.strip().casefold()
            if country not in {"CN", "CHINA"} and location_key not in locations_seen:
                rule_ids.append((
                    "new_overseas_location",
                    "Transaction occurred in an overseas location not seen before for this Customer.",
                ))
        if (not input.start_date or event_date >= input.start_date) and (
            not input.end_date or event_date <= input.end_date
        ):
            transaction = Transaction(
                transaction_id=row["transaction_id"], account_id=row["account_id"],
                occurred_at=occurred, category=category, merchant=row["merchant"],
                description=row["description"], direction=direction,
                amount=from_cents(amount), location=location,
            )
            flags.extend(UnusualTransaction(transaction=transaction, rule_id=rule_id, rule=rule)
                         for rule_id, rule in rule_ids)
        if direction == "debit":
            category_history.setdefault(category, []).append(amount)
        merchants_seen.add(merchant_key)
        locations_seen.add(location.strip().casefold())
    return UnusualTransactionsOutput(
        start_date=input.start_date, end_date=input.end_date, flags=flags
    )


class RecurringChargesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: AccountId | None = None
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def dates_are_ordered(self) -> RecurringChargesInput:
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        return self


class RecurringCharge(BaseModel):
    merchant: str
    category: str
    occurrence_count: int
    frequency: Literal["weekly", "monthly", "other"]
    average_interval_days: Decimal
    last_charge_date: date
    typical_amount: Decimal


class RecurringChargesOutput(BaseModel):
    charges: list[RecurringCharge]


def detect_recurring_charges(
    ctx: OperationContext, input: RecurringChargesInput
) -> RecurringChargesOutput:
    """Find merchant charges with at least three regular debit occurrences."""
    clauses = ["customer_id = ?", "direction = 'debit'", NOT_A_TRANSFER, "date(occurred_at) <= ?"]
    params: list[object] = [ctx.customer_id, ctx.now.date().isoformat()]
    if input.account_id:
        clauses.append("account_id = ?")
        params.append(input.account_id)
    if input.start_date:
        clauses.append("date(occurred_at) >= ?")
        params.append(input.start_date.isoformat())
    if input.end_date:
        clauses.append("date(occurred_at) <= ?")
        params.append(input.end_date.isoformat())
    rows = ctx.conn.execute(
        "SELECT merchant, category, date(occurred_at) AS charge_date, amount_cents "
        "FROM transactions WHERE " + " AND ".join(clauses)
        + " ORDER BY merchant, charge_date",
        params,
    ).fetchall()
    grouped: dict[str, list[Any]] = {}
    for row in rows:
        grouped.setdefault(str(row["merchant"]).casefold(), []).append(row)
    charges: list[RecurringCharge] = []
    for records in grouped.values():
        # A merchant should normally map to one category; do not merge unrelated
        # charge types if the seeded/loaded transaction data disagrees.
        if len({row["category"] for row in records}) != 1:
            continue
        dates = [date.fromisoformat(row["charge_date"]) for row in records]
        intervals = [(later - earlier).days for earlier, later in zip(dates, dates[1:])]
        if len(dates) < 3 or not intervals:
            continue
        typical_interval = median(intervals)
        if not all(abs(interval - typical_interval) <= 4 for interval in intervals):
            continue
        if not (6 <= typical_interval <= 8 or 27 <= typical_interval <= 35):
            continue
        frequency: Literal["weekly", "monthly", "other"] = (
            "weekly" if typical_interval <= 8 else "monthly"
        )
        charges.append(RecurringCharge(
            merchant=str(records[-1]["merchant"]),
            category=str(records[-1]["category"]),
            occurrence_count=len(records),
            frequency=frequency,
            average_interval_days=Decimal(str(round(sum(intervals) / len(intervals), 1))),
            last_charge_date=dates[-1],
            typical_amount=from_cents(int(median([int(row["amount_cents"]) for row in records]))),
        ))
    charges.sort(key=lambda charge: (charge.category, charge.merchant.casefold()))
    return RecurringChargesOutput(charges=charges)


class SubscriptionReason(BaseModel):
    reason: Literal["same_category", "price_increase", "trial_converted"]
    detail: str


class Subscription(BaseModel):
    subscription_id: str
    merchant: str
    category: str
    original_amount: Decimal
    current_amount: Decimal
    signed_up_on: date
    status: Literal["active", "cancelled"]
    next_renewal_date: date | None
    reasons_it_may_not_be_needed: list[SubscriptionReason]


class ListSubscriptionsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListSubscriptionsOutput(BaseModel):
    subscriptions: list[Subscription]


class CancelSubscriptionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subscription_id: Annotated[str, OwnedBy("subscriptions", "subscription_id")]


class CancelSubscriptionOutput(BaseModel):
    subscription_id: str
    merchant: str
    status: Literal["cancelled"]
    message: str


def cancel_subscription(
    ctx: OperationContext, input: CancelSubscriptionInput
) -> CancelSubscriptionOutput:
    row = ctx.conn.execute(
        "SELECT merchant, status FROM subscriptions WHERE subscription_id = ? AND customer_id = ?",
        (input.subscription_id, ctx.customer_id),
    ).fetchone()
    if row is None or row["status"] != "active":
        raise Refusal(
            RefusalReason.NOT_FOUND,
            "No active subscription with that ID was found",
            {"subscription_id": input.subscription_id},
        )
    ctx.conn.execute(
        "UPDATE subscriptions SET status = 'cancelled' "
        "WHERE subscription_id = ? AND customer_id = ? AND status = 'active'",
        (input.subscription_id, ctx.customer_id),
    )
    return CancelSubscriptionOutput(
        subscription_id=input.subscription_id,
        merchant=row["merchant"],
        status="cancelled",
        message=f"{row['merchant']} subscription cancelled.",
    )


def _next_monthly_renewal(last_charge: date, today: date) -> date:
    year, month = last_charge.year, last_charge.month
    while True:
        month += 1
        if month == 13:
            year += 1
            month = 1
        day = min(last_charge.day, calendar.monthrange(year, month)[1])
        candidate = date(year, month, day)
        if candidate >= today:
            return candidate


def _subscription_rows(ctx: OperationContext) -> list[sqlite3.Row]:
    return ctx.conn.execute(
        "SELECT s.subscription_id, s.merchant, s.service_category, s.signed_up_on, s.status, "
        "s.original_amount_cents, s.current_amount_cents, s.trial_converted, "
        "MAX(date(t.occurred_at)) AS last_charge_date "
        "FROM subscriptions s LEFT JOIN transactions t "
        "ON t.customer_id = s.customer_id AND lower(t.merchant) = lower(s.merchant) "
        "AND t.direction = 'debit' AND date(t.occurred_at) <= ? WHERE s.customer_id = ? "
        "GROUP BY s.subscription_id ORDER BY s.merchant",
        (ctx.now.date().isoformat(), ctx.customer_id),
    ).fetchall()


def _subscription_reason_map(rows: list[sqlite3.Row]) -> dict[str, list[SubscriptionReason]]:
    by_category: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        if row["status"] != "active":
            continue
        by_category.setdefault(str(row["service_category"]), []).append(row)
    reasons: dict[str, list[SubscriptionReason]] = {}
    for row in rows:
        merchant_key = str(row["merchant"]).casefold()
        items: list[SubscriptionReason] = []
        if row["status"] != "active":
            reasons[merchant_key] = items
            continue
        same_category = by_category[str(row["service_category"])]
        if len(same_category) > 1:
            peers = [str(peer["merchant"]) for peer in same_category
                     if str(peer["merchant"]).casefold() != merchant_key]
            items.append(SubscriptionReason(
                reason="same_category",
                detail=f"You also subscribe to {', '.join(peers)} in the {row['service_category']} category.",
            ))
        if int(row["current_amount_cents"]) > int(row["original_amount_cents"]):
            items.append(SubscriptionReason(
                reason="price_increase",
                detail="The recurring price is higher than it was at sign-up.",
            ))
        if bool(row["trial_converted"]):
            items.append(SubscriptionReason(
                reason="trial_converted",
                detail="A free trial converted to a paid subscription.",
            ))
        reasons[merchant_key] = items
    return reasons


def list_subscriptions(ctx: OperationContext, _: ListSubscriptionsInput) -> ListSubscriptionsOutput:
    rows = _subscription_rows(ctx)
    reasons = _subscription_reason_map(rows)
    subscriptions: list[Subscription] = []
    for row in rows:
        last = row["last_charge_date"]
        next_renewal = (
            _next_monthly_renewal(
                date.fromisoformat(last) if last else date.fromisoformat(row["signed_up_on"]),
                ctx.now.date(),
            )
            if row["status"] == "active" else None
        )
        subscriptions.append(Subscription(
            subscription_id=row["subscription_id"], merchant=row["merchant"],
            category=row["service_category"],
            original_amount=from_cents(row["original_amount_cents"]),
            current_amount=from_cents(row["current_amount_cents"]),
            signed_up_on=date.fromisoformat(row["signed_up_on"]),
            status=row["status"],
            next_renewal_date=next_renewal,
            reasons_it_may_not_be_needed=reasons[str(row["merchant"]).casefold()],
        ))
    # Cancelled Subscriptions have no renewal date; list them after the active ones.
    subscriptions.sort(key=lambda item: (
        item.next_renewal_date is None, item.next_renewal_date or date.max,
        item.merchant.casefold(),
    ))
    return ListSubscriptionsOutput(subscriptions=subscriptions)


class RenewalRemindersInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days_ahead: int = Field(default=7, ge=0, le=31)


class RenewalReminder(BaseModel):
    subscription: Subscription
    days_until_renewal: int


class RenewalRemindersOutput(BaseModel):
    as_of: date
    days_ahead: int
    reminders: list[RenewalReminder]


def renewal_reminders(ctx: OperationContext, input: RenewalRemindersInput) -> RenewalRemindersOutput:
    today = ctx.now.date()
    listed = list_subscriptions(ctx, ListSubscriptionsInput())
    reminders = []
    for subscription in listed.subscriptions:
        if subscription.status != "active" or subscription.next_renewal_date is None:
            continue
        days = (subscription.next_renewal_date - today).days
        if 0 <= days <= input.days_ahead:
            reminders.append(RenewalReminder(subscription=subscription, days_until_renewal=days))
    return RenewalRemindersOutput(as_of=today, days_ahead=input.days_ahead, reminders=reminders)


class SpendingByCategoryInput(TransactionFilter):
    account_id: AccountId | None = None
    category: SpendingCategory | None = None  # transfers are not spending
    merchant: str | None = None


class CategoryTotal(BaseModel):
    category: str
    amount: Decimal
    transaction_count: int


class SpendingByCategoryOutput(BaseModel):
    start_date: date | None
    end_date: date | None
    total_spending: Decimal
    categories: list[CategoryTotal]


def spending_by_category(ctx: OperationContext, input: SpendingByCategoryInput) -> SpendingByCategoryOutput:
    where, params = _where(ctx, input)
    rows = ctx.conn.execute(
        f"SELECT category, SUM(amount_cents) AS cents, COUNT(*) AS count "
        f"FROM transactions WHERE {where} AND direction = 'debit' AND {NOT_A_TRANSFER} "
        "GROUP BY category ORDER BY category",
        params,
    ).fetchall()
    totals = [CategoryTotal(category=row["category"], amount=from_cents(row["cents"]),
                            transaction_count=row["count"]) for row in rows]
    return SpendingByCategoryOutput(
        start_date=input.start_date, end_date=input.end_date,
        total_spending=sum((item.amount for item in totals), Decimal("0.00")),
        categories=totals,
    )


class MonthlyReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    account_id: AccountId | None = None


class ReportSeries(BaseModel):
    category: str
    values: list[Decimal]


class MonthlyReportOutput(BaseModel):
    year: int
    month: int
    labels: list[str]
    categories: list[CategoryTotal]
    total_income: Decimal
    total_spending: Decimal
    net: Decimal
    series: list[ReportSeries]


def monthly_report(ctx: OperationContext, input: MonthlyReportInput) -> MonthlyReportOutput:
    start = date(input.year, input.month, 1)
    end = date(input.year + (input.month == 12), input.month % 12 + 1, 1)
    clauses = [
        "customer_id = ?", NOT_A_TRANSFER, "date(occurred_at) >= ?", "date(occurred_at) < ?"
    ]
    params: list[object] = [ctx.customer_id, start.isoformat(), end.isoformat()]
    if input.account_id:
        clauses.append("account_id = ?")
        params.append(input.account_id)
    rows = ctx.conn.execute(
        "SELECT category, direction, SUM(amount_cents) AS cents, COUNT(*) AS count "
        f"FROM transactions WHERE {' AND '.join(clauses)} GROUP BY category, direction",
        params,
    ).fetchall()
    by_category: dict[str, int] = {}
    totals: list[CategoryTotal] = []
    income = spending = 0
    for row in rows:
        cents = int(row["cents"])
        if row["direction"] == "credit":
            income += cents
            continue
        spending += cents
        by_category[row["category"]] = cents
        totals.append(CategoryTotal(category=row["category"], amount=from_cents(cents),
                                    transaction_count=row["count"]))
    totals.sort(key=lambda item: item.category)
    return MonthlyReportOutput(
        year=input.year, month=input.month, labels=[start.strftime("%Y-%m")],
        categories=totals, total_income=from_cents(income), total_spending=from_cents(spending),
        net=from_cents(income - spending),
        series=[ReportSeries(category=category,
                             values=[from_cents(by_category.get(category, 0))])
                for category in sorted({"housing", "food_delivery", "transport", "subscriptions",
                                        "shopping", "gifts", "utilities", "dining",
                                        "entertainment", "healthcare"})],
    )


class AnnualReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    year: int = Field(ge=2000, le=2100)
    account_id: AccountId | None = None


class AnnualReportOutput(BaseModel):
    year: int
    labels: list[str]
    monthly_income: list[Decimal]
    monthly_spending: list[Decimal]
    monthly_net: list[Decimal]
    series: list[ReportSeries]
    total_income: Decimal
    total_spending: Decimal
    net: Decimal


def annual_report(ctx: OperationContext, input: AnnualReportInput) -> AnnualReportOutput:
    clauses = [
        "customer_id = ?", NOT_A_TRANSFER, "date(occurred_at) >= ?", "date(occurred_at) < ?"
    ]
    params: list[object] = [
        ctx.customer_id, f"{input.year:04d}-01-01", f"{input.year + 1:04d}-01-01"
    ]
    if input.account_id:
        clauses.append("account_id = ?")
        params.append(input.account_id)
    rows = ctx.conn.execute(
        "SELECT CAST(strftime('%m', occurred_at) AS INTEGER) AS month, category, direction, "
        "SUM(amount_cents) AS cents FROM transactions "
        f"WHERE {' AND '.join(clauses)} GROUP BY month, category, direction ORDER BY month, category",
        params,
    ).fetchall()
    income = [0] * 12
    spending = [0] * 12
    categories = {category: [0] * 12 for category in (
        "housing", "food_delivery", "transport", "subscriptions", "shopping", "gifts",
        "utilities", "dining", "entertainment", "healthcare",
    )}
    for row in rows:
        index, cents = int(row["month"]) - 1, int(row["cents"])
        if row["direction"] == "credit":
            income[index] += cents
        else:
            spending[index] += cents
            categories.setdefault(row["category"], [0] * 12)[index] += cents
    monthly_income = [from_cents(value) for value in income]
    monthly_spending = [from_cents(value) for value in spending]
    monthly_net = [from_cents(i - s) for i, s in zip(income, spending)]
    return AnnualReportOutput(
        year=input.year,
        labels=[f"{input.year:04d}-{month:02d}" for month in range(1, 13)],
        monthly_income=monthly_income, monthly_spending=monthly_spending, monthly_net=monthly_net,
        series=[ReportSeries(category=category, values=[from_cents(value) for value in values])
                for category, values in sorted(categories.items())],
        total_income=from_cents(sum(income)), total_spending=from_cents(sum(spending)),
        net=from_cents(sum(income) - sum(spending)),
    )


MODULE = BankModule(
    name="spending",
    schema=SCHEMA,
    seed=seed,
    operations=[
        Operation(
            name="list_transactions",
            description_zh=(
                "按日期范围、类别、商户（对方）或描述（如转账备注“家用”）筛选并查看当前客户自己的交易记录；"
                "已完成的转账也在其中，类别为 transfer。"
            ),
            description_en=(
                "List the current Customer's own transactions, filterable by date range, category,"
                " merchant (counterparty), description (e.g. a transfer's note, 家用) and account;"
                " completed transfers are included under the `transfer` category."
            ),
            input_model=ListTransactionsInput, output_model=ListTransactionsOutput,
            tier=Tier.GREEN, reversibility=ReadOnly(), handler=list_transactions,
        ),
        Operation(
            name="detect_unusual_transactions",
            description_zh="使用银行内置的固定规则检查当前客户的交易，并返回异常交易及触发原因。",
            description_en="Check the current Customer's transactions with deterministic Bank rules and return each unusual transaction with the rule that fired.",
            input_model=UnusualTransactionsInput, output_model=UnusualTransactionsOutput,
            tier=Tier.GREEN, reversibility=ReadOnly(), handler=detect_unusual_transactions,
        ),
        Operation(
            name="detect_recurring_charges",
            description_zh="根据当前客户的借记交易，按商户和规律间隔识别至少出现三次的重复收费。",
            description_en="Find the current Customer's debit charges that occur at least three times with a regular interval at the same merchant.",
            input_model=RecurringChargesInput, output_model=RecurringChargesOutput,
            tier=Tier.GREEN, reversibility=ReadOnly(), handler=detect_recurring_charges,
        ),
        Operation(
            name="list_subscriptions",
            description_zh="列出当前客户的订阅、下次续费日期，以及可能不需要该订阅的原因。",
            description_en="List the current Customer's subscriptions, next renewal dates, and reasons a subscription may not be needed.",
            input_model=ListSubscriptionsInput, output_model=ListSubscriptionsOutput,
            tier=Tier.GREEN, reversibility=ReadOnly(), handler=list_subscriptions,
        ),
        Operation(
            name="renewal_reminders",
            description_zh="根据银行时钟，提醒当前客户在指定天数内即将续费的订阅。",
            description_en="Use the Bank clock to remind the current Customer about subscriptions renewing within the requested number of days.",
            input_model=RenewalRemindersInput, output_model=RenewalRemindersOutput,
            tier=Tier.GREEN, reversibility=ReadOnly(), handler=renewal_reminders,
        ),
        Operation(
            name="cancel_subscription",
            description_zh="取消当前客户名下的一项有效订阅。此操作需要客户确认，且无法撤销。",
            description_en="Cancel one active subscription belonging to the current Customer. This action requires confirmation and cannot be reversed.",
            input_model=CancelSubscriptionInput, output_model=CancelSubscriptionOutput,
            tier=Tier.YELLOW, reversibility=Irreversible(), handler=cancel_subscription,
        ),
        Operation(
            name="spending_by_category",
            description_zh="汇总当前客户在指定日期范围内按消费类别划分的支出；转账不算支出。",
            description_en="Summarize the current Customer's spending by category for a date range, with totals that reconcile to matching debit transactions; transfers are not spending.",
            input_model=SpendingByCategoryInput, output_model=SpendingByCategoryOutput,
            tier=Tier.GREEN, reversibility=ReadOnly(), handler=spending_by_category,
        ),
        Operation(
            name="monthly_report",
            description_zh="生成当前客户指定月份的收入、支出和分类汇总，可直接用于图表。",
            description_en="Return the current Customer's income, spending, category totals, and chart-ready series for one calendar month.",
            input_model=MonthlyReportInput, output_model=MonthlyReportOutput,
            tier=Tier.GREEN, reversibility=ReadOnly(), handler=monthly_report,
        ),
        Operation(
            name="annual_report",
            description_zh="生成当前客户指定年度的逐月收入、支出、净额和分类序列，可直接用于图表。",
            description_en="Return the current Customer's monthly income, spending, net values, and category series for one calendar year, ready for charts.",
            input_model=AnnualReportInput, output_model=AnnualReportOutput,
            tier=Tier.GREEN, reversibility=ReadOnly(), handler=annual_report,
        ),
    ],
)
