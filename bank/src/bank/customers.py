"""Customers, their accounts, and the balance query.

Seed: a married couple. 李明 (liming) has a balance close to the ¥1,000 boundary;
韩梅梅 (hanmei)'s birthday on her profile drives the birthday demo.
"""

import sqlite3
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from bank.registry import BankModule, Operation, OperationContext, ReadOnly, Tier
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


class QueryBalanceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountBalance(BaseModel):
    account_id: str
    kind: Literal["current", "savings"]
    name: str
    currency: Literal["CNY"] = "CNY"
    balance: Decimal


class QueryBalanceOutput(BaseModel):
    accounts: list[AccountBalance]


def query_balance(ctx: OperationContext, _: QueryBalanceInput) -> QueryBalanceOutput:
    rows = ctx.conn.execute(
        "SELECT account_id, kind, name, balance_cents FROM accounts"
        " WHERE customer_id = ? ORDER BY account_id",
        (ctx.customer_id,),
    ).fetchall()
    return QueryBalanceOutput(
        accounts=[
            AccountBalance(
                account_id=row["account_id"],
                kind=row["kind"],
                name=row["name"],
                balance=Decimal(row["balance_cents"]).scaleb(-2),
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
            description_zh="查询当前客户名下所有账户的余额。",
            description_en="Get the balance of every account the current Customer holds.",
            input_model=QueryBalanceInput,
            output_model=QueryBalanceOutput,
            tier=Tier.GREEN,
            reversibility=ReadOnly(),
            handler=query_balance,
        )
    ],
)
