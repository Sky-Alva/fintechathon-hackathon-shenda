"""Transfers to Payees, tiered by the Daily Transfer Total.

Seed: only the Payees the reference slice needs (老妈, and each spouse as the other's
Payee). Smart Transfers (#10 onwards) extends this module with Payee lookup and the
rest of the seed.
"""

import sqlite3
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from bank.money import Amount, from_cents, to_cents
from bank.registry import (
    BankModule,
    Irreversible,
    Operation,
    OperationContext,
    Refusal,
    RefusalReason,
    Tier,
    TierRule,
)

DAILY_TRANSFER_LIMIT_CENTS = 100_000  # ¥1,000: above this the Daily Transfer Total is Red

SCHEMA = """
CREATE TABLE payees (
    payee_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers (customer_id),
    name TEXT NOT NULL,
    nickname TEXT,
    phone TEXT,
    account_id TEXT NOT NULL
);
CREATE TABLE transfers (
    transfer_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers (customer_id),
    from_account_id TEXT NOT NULL REFERENCES accounts (account_id),
    payee_id TEXT NOT NULL REFERENCES payees (payee_id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    note TEXT NOT NULL,
    at TEXT NOT NULL
);
"""


def seed(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT INTO payees VALUES (?, ?, ?, ?, ?, ?)",
        [
            # account_id is a Bank account for the spouses; 老妈 banks elsewhere.
            ("PAY-LM-001", "CUST-LM", "韩梅梅", "老婆", "13800000002", "ACC-HM-001"),
            ("PAY-LM-002", "CUST-LM", "王秀英", "老妈", "13900000003", "6222020200112233"),
            ("PAY-HM-001", "CUST-HM", "李明", "老公", "13800000001", "ACC-LM-001"),
        ],
    )


def daily_transfer_total(conn: sqlite3.Connection, customer_id: str, day: date) -> int:
    """The Customer's outgoing transfers on one Bank-clock day, in cents."""
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM transfers"
        " WHERE customer_id = ? AND substr(at, 1, 10) = ?",
        (customer_id, day.isoformat()),
    ).fetchone()
    total: int = row[0]
    return total


class TransferInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payee_id: str
    amount: Amount
    note: str = Field(default="", max_length=100)


class TransferOutput(BaseModel):
    transfer_id: str
    payee_id: str
    payee_name: str
    amount: Decimal
    from_account_id: str
    balance_after: Decimal
    daily_transfer_total: Decimal


def _payee(ctx: OperationContext, payee_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = ctx.conn.execute(
        "SELECT * FROM payees WHERE payee_id = ? AND customer_id = ?",
        (payee_id, ctx.customer_id),
    ).fetchone()
    if row is None:
        raise Refusal(RefusalReason.NOT_FOUND, "No such Payee", {"payee_id": payee_id})
    return row


def _funded_current_account(ctx: OperationContext, amount_cents: int) -> sqlite3.Row:
    account: sqlite3.Row = ctx.conn.execute(
        "SELECT account_id, balance_cents FROM accounts"
        " WHERE customer_id = ? AND kind = 'current' ORDER BY account_id LIMIT 1",
        (ctx.customer_id,),
    ).fetchone()
    if account["balance_cents"] < amount_cents:
        raise Refusal(
            RefusalReason.INSUFFICIENT_FUNDS,
            "Not enough money in the current account",
            {
                "account_id": account["account_id"],
                "balance": str(from_cents(account["balance_cents"])),
            },
        )
    return account


def transfer_tier(input: TransferInput, ctx: OperationContext) -> Tier:
    # Refuse a transfer that cannot run before the Customer is asked to approve it.
    _payee(ctx, input.payee_id)
    amount_cents = to_cents(input.amount)
    _funded_current_account(ctx, amount_cents)
    total = daily_transfer_total(ctx.conn, ctx.customer_id, ctx.now.date()) + amount_cents
    return Tier.YELLOW if total <= DAILY_TRANSFER_LIMIT_CENTS else Tier.RED


def transfer(ctx: OperationContext, input: TransferInput) -> TransferOutput:
    payee = _payee(ctx, input.payee_id)
    amount_cents = to_cents(input.amount)
    account = _funded_current_account(ctx, amount_cents)
    ctx.conn.execute(
        "UPDATE accounts SET balance_cents = balance_cents - ? WHERE account_id = ?",
        (amount_cents, account["account_id"]),
    )
    # A Payee who banks with the Bank is credited; anyone else is paid out of the Bank.
    ctx.conn.execute(
        "UPDATE accounts SET balance_cents = balance_cents + ? WHERE account_id = ?",
        (amount_cents, payee["account_id"]),
    )
    count = ctx.conn.execute("SELECT COUNT(*) FROM transfers").fetchone()[0]
    transfer_id = f"TRF-{count + 1:06d}"
    ctx.conn.execute(
        "INSERT INTO transfers VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            transfer_id,
            ctx.customer_id,
            account["account_id"],
            payee["payee_id"],
            amount_cents,
            input.note,
            ctx.now.isoformat(),
        ),
    )
    return TransferOutput(
        transfer_id=transfer_id,
        payee_id=payee["payee_id"],
        payee_name=payee["name"],
        amount=input.amount,
        from_account_id=account["account_id"],
        balance_after=from_cents(account["balance_cents"] - amount_cents),
        daily_transfer_total=from_cents(
            daily_transfer_total(ctx.conn, ctx.customer_id, ctx.now.date())
        ),
    )


MODULE = BankModule(
    name="transfers",
    schema=SCHEMA,
    seed=seed,
    operations=[
        Operation(
            name="transfer",
            description_zh="从当前客户的活期账户向一位收款人转账。",
            description_en="Transfer money from the current Customer's current account to a Payee.",
            input_model=TransferInput,
            output_model=TransferOutput,
            tier=TierRule(
                description_zh="转账后当日累计转账额 ≤ ¥1,000 为黄色，否则为红色。",
                description_en=(
                    "Yellow if the Daily Transfer Total including this transfer stays"
                    " at or below ¥1,000, otherwise Red."
                ),
                resolve=transfer_tier,
            ),
            reversibility=Irreversible(),
            handler=transfer,
        )
    ],
)
