"""Smart Transfers: Reserved Funds (#13).

A Customer sets money aside in their current account for a stated purpose and can release
it early. It stays theirs and stays in the balance, but it is not *available*: nothing can
spend it except an order from a Merchant the reservation allows (merchants.py), up to what is
left. Reserving and releasing change the available balance, never the total balance.

The table and `customers.reserved_cents` live in customers.py with the accounts, because every
check of what an account can pay has to know about them (see `transfers.funded_current_account`).
"""

import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from bank import customers
from bank.money import Amount, from_cents, to_cents
from bank.registry import (
    BankModule,
    Operation,
    OperationContext,
    OwnedBy,
    ReadOnly,
    Refusal,
    RefusalReason,
    ReversedBy,
    Tier,
    TierRule,
)

# The kinds of Merchant a reservation can be limited to (see merchants.py).
MerchantCategory = Literal["flowers", "cake"]
Status = Literal["active", "used", "released"]

# One of the session's Customer's reservations. The gate refuses another Customer's.
ReservationId = Annotated[str, OwnedBy("reserved_funds", "reservation_id")]


class Reservation(BaseModel):
    reservation_id: str
    account_id: str
    amount: Decimal = Field(description="What was set aside.")
    spent: Decimal = Field(description="How much of it has been spent on orders.")
    remaining: Decimal = Field(description="Still set aside; zero once used or released.")
    purpose: str
    merchant_categories: list[MerchantCategory] | None = Field(
        description="The kinds of Merchant it may pay; null means any Merchant."
    )
    status: Status


def categories_of(row: sqlite3.Row) -> list[MerchantCategory] | None:
    """The reservation's allowed Merchant categories, or None for any Merchant."""
    stored: str | None = row["merchant_categories"]
    if stored is None:
        return None
    return cast(list[MerchantCategory], [c for c in stored.split(",") if c])


def view(row: sqlite3.Row) -> Reservation:
    remaining = row["amount_cents"] - row["spent_cents"] if row["status"] == "active" else 0
    return Reservation(
        reservation_id=row["reservation_id"],
        account_id=row["account_id"],
        amount=from_cents(row["amount_cents"]),
        spent=from_cents(row["spent_cents"]),
        remaining=from_cents(remaining),
        purpose=row["purpose"],
        merchant_categories=categories_of(row),
        status=row["status"],
    )


def reservation_row(conn: sqlite3.Connection, reservation_id: str) -> sqlite3.Row:
    row: sqlite3.Row = conn.execute(
        "SELECT * FROM reserved_funds WHERE reservation_id = ?", (reservation_id,)
    ).fetchone()
    return row


def active_reservation(ctx: OperationContext, reservation_id: str) -> sqlite3.Row:
    """The Customer's reservation, if there is still money set aside in it."""
    row: sqlite3.Row | None = ctx.conn.execute(
        "SELECT * FROM reserved_funds WHERE reservation_id = ? AND customer_id = ?",
        (reservation_id, ctx.customer_id),
    ).fetchone()
    if row is None:
        raise Refusal(
            RefusalReason.NOT_FOUND, "No such Reserved Funds", {"reservation_id": reservation_id}
        )
    if row["status"] != "active":
        raise Refusal(
            RefusalReason.INVALID_INPUT,
            f"These Reserved Funds are already {row['status']}",
            {"reservation_id": reservation_id, "status": row["status"]},
        )
    return row


def spend(conn: sqlite3.Connection, row: sqlite3.Row, cents: int, now: datetime) -> None:
    """Use up part of a reservation; it is closed as used when nothing is left."""
    spent = row["spent_cents"] + cents
    used_up = spent == row["amount_cents"]
    conn.execute(
        "UPDATE reserved_funds SET spent_cents = ?, status = ?, closed_at = ?"
        " WHERE reservation_id = ?",
        (spent, "used" if used_up else "active", now.isoformat() if used_up else None,
         row["reservation_id"]),
    )


# --- Reserve (Yellow) ---------------------------------------------------------------


class ReserveFundsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: Amount
    purpose: str = Field(min_length=1, max_length=100, description="What the money is for.")
    merchant_categories: list[MerchantCategory] | None = Field(
        default=None,
        description=(
            "The kinds of Merchant this money may pay, e.g. [\"flowers\", \"cake\"] for a"
            " birthday gift. Leave out to allow any Merchant; [] to allow none."
        ),
    )


class ReserveFundsOutput(BaseModel):
    reservation: Reservation
    available_balance: Decimal = Field(description="What can be spent now, after this.")


def _available_cents(ctx: OperationContext, amount: Decimal) -> tuple[sqlite3.Row, int]:
    """The current account and what is available in it, or a refusal if `amount` is more."""
    account = customers.current_account(ctx.conn, ctx.customer_id)
    available = account["balance_cents"] - customers.reserved_cents(
        ctx.conn, account["account_id"]
    )
    if available < to_cents(amount):
        raise Refusal(
            RefusalReason.INSUFFICIENT_FUNDS,
            "Not enough available money in the current account to reserve",
            {
                "account_id": account["account_id"],
                "balance": str(from_cents(account["balance_cents"])),
                "available_balance": str(from_cents(available)),
            },
        )
    return account, available


def reserve_funds_tier(input: ReserveFundsInput, ctx: OperationContext) -> Tier:
    _available_cents(ctx, input.amount)  # refuse what can never run before asking to confirm
    return Tier.YELLOW


def reserve_funds(ctx: OperationContext, input: ReserveFundsInput) -> ReserveFundsOutput:
    account, available = _available_cents(ctx, input.amount)
    count = ctx.conn.execute("SELECT COUNT(*) FROM reserved_funds").fetchone()[0]
    reservation_id = f"RSV-{count + 1:06d}"  # Bank-wide, like transfer IDs
    categories = None
    if input.merchant_categories is not None:
        categories = ",".join(sorted(set(input.merchant_categories)))  # "" means no Merchant
    ctx.conn.execute(
        "INSERT INTO reserved_funds (reservation_id, customer_id, account_id, amount_cents,"
        " purpose, merchant_categories, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, 'active', ?)",
        (
            reservation_id,
            ctx.customer_id,
            account["account_id"],
            to_cents(input.amount),
            input.purpose,
            categories,
            ctx.now.isoformat(),
        ),
    )
    return ReserveFundsOutput(
        reservation=view(reservation_row(ctx.conn, reservation_id)),
        available_balance=from_cents(available - to_cents(input.amount)),
    )


# --- Release (Yellow) ---------------------------------------------------------------


class ReleaseReservedFundsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reservation_id: ReservationId


class ReleaseReservedFundsOutput(BaseModel):
    reservation: Reservation
    released: Decimal = Field(description="What went back to the available balance.")
    available_balance: Decimal = Field(description="What can be spent now, after this.")


def release_reserved_funds_tier(
    input: ReleaseReservedFundsInput, ctx: OperationContext
) -> Tier:
    active_reservation(ctx, input.reservation_id)  # already used or released: refuse early
    return Tier.YELLOW


def release_reserved_funds(
    ctx: OperationContext, input: ReleaseReservedFundsInput
) -> ReleaseReservedFundsOutput:
    row = active_reservation(ctx, input.reservation_id)
    released_cents = row["amount_cents"] - row["spent_cents"]
    ctx.conn.execute(
        "UPDATE reserved_funds SET status = 'released', closed_at = ? WHERE reservation_id = ?",
        (ctx.now.isoformat(), input.reservation_id),
    )
    account = customers.current_account(ctx.conn, ctx.customer_id)
    available = account["balance_cents"] - customers.reserved_cents(
        ctx.conn, account["account_id"]
    )
    return ReleaseReservedFundsOutput(
        reservation=view(reservation_row(ctx.conn, input.reservation_id)),
        released=from_cents(released_cents),
        available_balance=from_cents(available),
    )


# --- List (Green) -------------------------------------------------------------------


class ListReservedFundsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Status | None = Field(default=None, description="Only those in this status.")


class ListReservedFundsOutput(BaseModel):
    reservations: list[Reservation]


def list_reserved_funds(
    ctx: OperationContext, input: ListReservedFundsInput
) -> ListReservedFundsOutput:
    rows = ctx.conn.execute(
        "SELECT * FROM reserved_funds WHERE customer_id = ? AND (? IS NULL OR status = ?)"
        " ORDER BY reservation_id",
        (ctx.customer_id, input.status, input.status),
    ).fetchall()
    return ListReservedFundsOutput(reservations=[view(row) for row in rows])


MODULE = BankModule(
    name="reserved_funds",
    schema="",  # the table lives in customers.py, with the accounts
    operations=[
        Operation(
            name="reserve_funds",
            description_zh=(
                "从活期账户中预留一笔钱用于指定用途（如买生日礼物）；预留的钱仍属于客户，"
                "但不能用于其他支出。只改变可用余额，不改变总余额。"
            ),
            description_en=(
                "Set money aside in the current account for a stated purpose, such as a"
                " birthday gift; it stays the Customer's but can be spent on nothing else."
                " It changes the available balance, never the total balance."
            ),
            input_model=ReserveFundsInput,
            output_model=ReserveFundsOutput,
            tier=TierRule(
                description_zh="一律为黄色（需确认）；可用余额不足以预留时直接拒绝。",
                description_en=(
                    "Always Yellow (Confirmation); refused when the available balance is"
                    " not enough to reserve."
                ),
                resolve=reserve_funds_tier,
            ),
            reversibility=ReversedBy(
                "release_reserved_funds",
                lambda input, output: ReleaseReservedFundsInput(
                    reservation_id=output.reservation.reservation_id
                ),
            ),
            handler=reserve_funds,
        ),
        Operation(
            name="release_reserved_funds",
            description_zh="提前释放一笔预留资金，剩余未用部分回到可用余额。",
            description_en=(
                "Release Reserved Funds early; whatever has not been spent goes back to the"
                " available balance."
            ),
            input_model=ReleaseReservedFundsInput,
            output_model=ReleaseReservedFundsOutput,
            tier=TierRule(
                description_zh="一律为黄色（需确认）；已用完或已释放的预留资金会被拒绝。",
                description_en=(
                    "Always Yellow (Confirmation); Reserved Funds already used up or"
                    " released are refused."
                ),
                resolve=release_reserved_funds_tier,
            ),
            reversibility=ReversedBy(
                "reserve_funds",
                lambda input, output: ReserveFundsInput(
                    amount=output.released,
                    purpose=output.reservation.purpose,
                    merchant_categories=output.reservation.merchant_categories,
                ),
            ),
            handler=release_reserved_funds,
        ),
        Operation(
            name="list_reserved_funds",
            description_zh="列出当前客户的预留资金：用途、已用金额、剩余金额和状态。",
            description_en=(
                "List the current Customer's Reserved Funds: purpose, amount spent, amount"
                " remaining and status."
            ),
            input_model=ListReservedFundsInput,
            output_model=ListReservedFundsOutput,
            tier=Tier.GREEN,
            reversibility=ReadOnly(),
            handler=list_reserved_funds,
        ),
    ],
)
