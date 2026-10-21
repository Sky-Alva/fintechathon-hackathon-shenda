"""Customers, their accounts, and the balance query.

Seed: a married couple. 李明 (liming) has a balance close to the ¥1,000 boundary;
韩梅梅 (hanmei)'s birthday on her profile drives the birthday demo.
"""

import sqlite3
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from bank.money import from_cents
from bank.registry import BankModule, Operation, OperationContext, OwnedBy, ReadOnly, Tier
from bank.sessions import hash_password

SCHEMA = """
CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    birthday TEXT NOT NULL,
    spouse_customer_id TEXT REFERENCES customers (customer_id)
);
CREATE TABLE accounts (
    account_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers (customer_id),
    kind TEXT NOT NULL CHECK (kind IN ('current', 'savings')),
    name TEXT NOT NULL,
    balance_cents INTEGER NOT NULL
);
-- Money set aside in an account for a stated purpose (operations: reserved_funds.py).
-- It stays in the balance but is not available to spend, except through its purpose.
CREATE TABLE reserved_funds (
    reservation_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers (customer_id),
    account_id TEXT NOT NULL REFERENCES accounts (account_id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    spent_cents INTEGER NOT NULL DEFAULT 0 CHECK (spent_cents BETWEEN 0 AND amount_cents),
    purpose TEXT NOT NULL,
    merchant_categories TEXT,
    status TEXT NOT NULL CHECK (status IN ('active', 'used', 'released')),
    created_at TEXT NOT NULL,
    closed_at TEXT
);
"""

DEMO_PASSWORD = "demo1234"

# One of the session's Customer's accounts. The gate refuses another Customer's.
AccountId = Annotated[str, OwnedBy("accounts", "account_id")]


def seed(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("CUST-LM", "liming", hash_password(DEMO_PASSWORD), "李明", "13800000001",
             "1992-03-15", "CUST-HM"),
            ("CUST-HM", "hanmei", hash_password(DEMO_PASSWORD), "韩梅梅", "13800000002",
             "1994-10-28", "CUST-LM"),
        ],
    )
    conn.executemany(
        "INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
        [
            ("ACC-LM-001", "CUST-LM", "current", "活期账户 Current account", 118000),
            ("ACC-HM-001", "CUST-HM", "current", "活期账户 Current account", 865000),
            ("ACC-HM-002", "CUST-HM", "savings", "储蓄账户 Savings account", 5000000),
        ],
    )


def exists(conn: sqlite3.Connection, customer_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM customers WHERE customer_id = ?", (customer_id,))
    return row.fetchone() is not None


def phone_number(conn: sqlite3.Connection, customer_id: str) -> str:
    row = conn.execute("SELECT phone FROM customers WHERE customer_id = ?", (customer_id,))
    phone: str = row.fetchone()["phone"]
    return phone


def current_account(conn: sqlite3.Connection, customer_id: str) -> sqlite3.Row:
    """The Customer's current account, which transfers are paid from."""
    row: sqlite3.Row = conn.execute(
        "SELECT account_id, balance_cents FROM accounts"
        " WHERE customer_id = ? AND kind = 'current' ORDER BY account_id LIMIT 1",
        (customer_id,),
    ).fetchone()
    return row


def reserved_cents(conn: sqlite3.Connection, account_id: str) -> int:
    """What is still set aside in the account: reserved and not yet spent or released."""
    total: int = conn.execute(
        "SELECT COALESCE(SUM(amount_cents - spent_cents), 0) FROM reserved_funds"
        " WHERE account_id = ? AND status = 'active'",
        (account_id,),
    ).fetchone()[0]
    return total


def credit(conn: sqlite3.Connection, account_id: str, cents: int) -> None:
    """Pay money in. An account outside the Bank is simply not found and nothing happens."""
    conn.execute(
        "UPDATE accounts SET balance_cents = balance_cents + ? WHERE account_id = ?",
        (cents, account_id),
    )


def debit(conn: sqlite3.Connection, account_id: str, cents: int) -> None:
    conn.execute(
        "UPDATE accounts SET balance_cents = balance_cents - ? WHERE account_id = ?",
        (cents, account_id),
    )


class QueryBalanceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: AccountId | None = Field(
        default=None, description="Only this account; every account if left out."
    )


class AccountBalance(BaseModel):
    account_id: str
    kind: Literal["current", "savings"]
    name: str
    currency: Literal["CNY"] = "CNY"
    balance: Decimal = Field(description="Everything in the account, Reserved Funds included.")
    available_balance: Decimal = Field(
        description="What can be spent now: the balance minus Reserved Funds."
    )


class QueryBalanceOutput(BaseModel):
    accounts: list[AccountBalance]


def query_balance(ctx: OperationContext, input: QueryBalanceInput) -> QueryBalanceOutput:
    rows = ctx.conn.execute(
        "SELECT account_id, kind, name, balance_cents FROM accounts"
        " WHERE customer_id = ? AND (? IS NULL OR account_id = ?) ORDER BY account_id",
        (ctx.customer_id, input.account_id, input.account_id),
    ).fetchall()
    return QueryBalanceOutput(
        accounts=[
            AccountBalance(
                account_id=row["account_id"],
                kind=row["kind"],
                name=row["name"],
                balance=from_cents(row["balance_cents"]),
                available_balance=from_cents(
                    row["balance_cents"] - reserved_cents(ctx.conn, row["account_id"])
                ),
            )
            for row in rows
        ]
    )


MODULE = BankModule(
    name="customers",
    schema=SCHEMA,
    seed=seed,
    operations=[
        Operation(
            name="query_balance",
            description_zh="查询当前客户名下所有账户（或其中指定一个账户）的余额。",
            description_en=(
                "Get the balance of every account the current Customer holds, or of one"
                " of them."
            ),
            input_model=QueryBalanceInput,
            output_model=QueryBalanceOutput,
            tier=Tier.GREEN,
            reversibility=ReadOnly(),
            handler=query_balance,
        )
    ],
)
