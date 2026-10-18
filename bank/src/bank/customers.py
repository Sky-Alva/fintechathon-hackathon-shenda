"""Customers, their accounts, the balance query and the profile query.

Seed: a married couple. 李明 (liming) has a balance close to the ¥1,000 boundary;
韩梅梅 (hanmei)'s birthday on her profile drives the birthday demo: 李明 reads it
as his spouse's birthday in his own profile (query_profile).
"""

import sqlite3
from datetime import date
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
    balance: Decimal


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
            )
            for row in rows
        ]
    )


class QueryProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SpouseProfile(BaseModel):
    name: str
    birthday: date


class QueryProfileOutput(BaseModel):
    name: str
    birthday: date
    spouse: SpouseProfile | None = Field(description="None if the Customer has no spouse.")


def query_profile(ctx: OperationContext, input: QueryProfileInput) -> QueryProfileOutput:
    # The spouse's name and birthday are part of the Customer's own profile; nothing
    # else about the spouse (accounts, cards, transactions) is read here.
    row = ctx.conn.execute(
        "SELECT c.name, c.birthday, s.name AS spouse_name, s.birthday AS spouse_birthday"
        " FROM customers c LEFT JOIN customers s ON s.customer_id = c.spouse_customer_id"
        " WHERE c.customer_id = ?",
        (ctx.customer_id,),
    ).fetchone()
    spouse = (
        SpouseProfile(name=row["spouse_name"], birthday=row["spouse_birthday"])
        if row["spouse_name"] is not None
        else None
    )
    return QueryProfileOutput(name=row["name"], birthday=row["birthday"], spouse=spouse)


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
        ),
        Operation(
            name="query_profile",
            description_zh="查询当前客户本人的资料：姓名和生日，以及配偶的姓名和生日。",
            description_en=(
                "Get the current Customer's own profile: their name and birthday, and their"
                " spouse's name and birthday."
            ),
            input_model=QueryProfileInput,
            output_model=QueryProfileOutput,
            tier=Tier.GREEN,
            reversibility=ReadOnly(),
            handler=query_profile,
        ),
    ],
)
