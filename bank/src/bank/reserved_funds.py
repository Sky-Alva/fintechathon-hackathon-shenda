"""Smart Transfers: Reserved Funds (#13).

A Customer sets money aside in their current account for a stated purpose and can release
it early. It stays theirs and stays in the balance, but it is not *available*: nothing can
spend it except an order from the kinds of Merchant the purpose names (merchants.py), up to
what is left. Every reservation names at least one kind, so none can pay just anything.
Reserving and releasing change the available balance, never the total balance.

An order is paid on its delivery date, so until then its amount is *committed*: still set
aside, no longer remaining. Releasing gives back only what is neither spent nor committed;
the money for orders already placed stays set aside until they are paid.

The table and `customers.available_cents` live in customers.py with the accounts, because every
check of what an account can pay has to know about them (see `transfers.funded_current_account`).
"""

import sqlite3
from decimal import Decimal
from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from bank import customers, transfers
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
    amount: Decimal = Field(description="What is set aside, less any part released.")
    spent: Decimal = Field(description="How much of it has been spent on orders.")
    committed: Decimal = Field(
        description="Promised to Merchant orders that are paid on their delivery date."
    )
    remaining: Decimal = Field(
        description="Still free for new orders; zero once used or released."
    )
    purpose: str
    merchant_categories: list[MerchantCategory] = Field(
        description="The kinds of Merchant it may pay, and nothing else."
    )
    status: Status


def categories_of(row: sqlite3.Row) -> list[MerchantCategory]:
    """The kinds of Merchant the reservation may pay."""
    stored: str = row["merchant_categories"]
    return cast(list[MerchantCategory], stored.split(","))


def committed_cents(conn: sqlite3.Connection, reservation_id: str) -> int:
    """What the Merchant orders waiting to be paid from these Reserved Funds will take (the
    orders table is merchants.py's)."""
    committed: int = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM merchant_orders"
        " WHERE reservation_id = ? AND status = 'pending'",
        (reservation_id,),
    ).fetchone()[0]
    return committed


def free_cents(conn: sqlite3.Connection, row: sqlite3.Row) -> int:
    """What is left in an active reservation for new orders, or to release."""
    committed = committed_cents(conn, row["reservation_id"])
    free: int = row["amount_cents"] - row["spent_cents"] - committed
    return free


def view(conn: sqlite3.Connection, row: sqlite3.Row) -> Reservation:
    active = row["status"] == "active"
    committed = committed_cents(conn, row["reservation_id"]) if active else 0
    return Reservation(
        reservation_id=row["reservation_id"],
        account_id=row["account_id"],
        amount=from_cents(row["amount_cents"]),
        spent=from_cents(row["spent_cents"]),
        committed=from_cents(committed),
        remaining=from_cents(free_cents(conn, row) if active else 0),
        purpose=row["purpose"],
        merchant_categories=categories_of(row),
        status=row["status"],
    )


def reservation(ctx: OperationContext, reservation_id: str) -> sqlite3.Row:
    """One of the Customer's Reserved Funds, in any status."""
    row: sqlite3.Row | None = ctx.conn.execute(
        "SELECT * FROM reserved_funds WHERE reservation_id = ? AND customer_id = ?",
        (reservation_id, ctx.customer_id),
    ).fetchone()
    if row is None:
        raise Refusal(
            RefusalReason.NOT_FOUND, "No such Reserved Funds", {"reservation_id": reservation_id}
        )
    return row


def active_reservation(ctx: OperationContext, reservation_id: str) -> sqlite3.Row:
    """The Customer's reservation, if there is still money set aside in it."""
    row = reservation(ctx, reservation_id)
    if row["status"] != "active":
        raise Refusal(
            RefusalReason.INVALID_INPUT,
            f"These Reserved Funds are already {row['status']}",
            {"reservation_id": reservation_id, "status": row["status"]},
        )
    return row


def check_can_pay(row: sqlite3.Row, category: str, cents: int, committed: int = 0) -> None:
    """Refuse paying `cents` to a Merchant of `category` from this active reservation,
    unless its purpose allows that kind of Merchant and enough is left once the `committed`
    orders waiting on it are paid."""
    allowed = categories_of(row)
    if category not in allowed:
        raise Refusal(
            RefusalReason.INVALID_INPUT,
            "These Reserved Funds cannot be spent at this kind of Merchant",
            {
                "reservation_id": row["reservation_id"],
                "purpose": row["purpose"],
                "allowed_merchant_categories": allowed,
                "merchant_category": category,
            },
        )
    remaining = row["amount_cents"] - row["spent_cents"] - committed
    if remaining < cents:
        raise Refusal(
            RefusalReason.INSUFFICIENT_FUNDS,
            "Not enough left in these Reserved Funds",
            {"reservation_id": row["reservation_id"], "remaining": str(from_cents(remaining))},
        )


def spend(ctx: OperationContext, row: sqlite3.Row, cents: int) -> None:
    """Use up part of a reservation; it is closed as used when nothing is left."""
    spent = row["spent_cents"] + cents
    used_up = spent == row["amount_cents"]
    ctx.conn.execute(
        "UPDATE reserved_funds SET spent_cents = ?, status = ?, closed_at = ?"
        " WHERE reservation_id = ? AND customer_id = ?",
        (spent, "used" if used_up else "active", ctx.now.isoformat() if used_up else None,
         row["reservation_id"], ctx.customer_id),
    )


# --- Reserve (Yellow) ---------------------------------------------------------------


class ReserveFundsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: Amount
    purpose: str = Field(min_length=1, max_length=100, description="What the money is for.")
    merchant_categories: list[MerchantCategory] = Field(
        min_length=1,
        description=(
            "The kinds of Merchant the purpose needs, e.g. [\"flowers\", \"cake\"] for a"
            " birthday gift. The money can be spent on nothing else."
        ),
    )


class ReserveFundsOutput(BaseModel):
    reservation: Reservation
    available_balance: Decimal = Field(description="What can be spent now, after this.")


def reserve_funds_tier(input: ReserveFundsInput, ctx: OperationContext) -> Tier:
    # Refuse what can never run before asking to confirm: only available money is reserved.
    transfers.funded_current_account(ctx, input.amount)
    return Tier.YELLOW


def reserve_funds(ctx: OperationContext, input: ReserveFundsInput) -> ReserveFundsOutput:
    account = transfers.funded_current_account(ctx, input.amount)
    count = ctx.conn.execute("SELECT COUNT(*) FROM reserved_funds").fetchone()[0]
    reservation_id = f"RSV-{count + 1:06d}"  # Bank-wide, like transfer IDs
    categories = ",".join(sorted(set(input.merchant_categories)))
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
        reservation=view(ctx.conn, reservation(ctx, reservation_id)),
        available_balance=from_cents(customers.available_cents(ctx.conn, account)),
    )


# --- Release (Yellow) ---------------------------------------------------------------


class ReleaseReservedFundsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reservation_id: ReservationId


class ReleaseReservedFundsOutput(BaseModel):
    reservation: Reservation = Field(
        description="Still active, holding what pending orders need, if there are any."
    )
    released: Decimal = Field(description="What went back to the available balance.")
    available_balance: Decimal = Field(description="What can be spent now, after this.")


def releasable(ctx: OperationContext, reservation_id: str) -> int:
    """What releasing the active reservation gives back: refused if pending orders take all
    of what is left."""
    free = free_cents(ctx.conn, active_reservation(ctx, reservation_id))
    if free == 0:
        raise Refusal(
            RefusalReason.INVALID_INPUT,
            "Everything left in these Reserved Funds pays orders not yet delivered",
            {
                "reservation_id": reservation_id,
                "committed": str(from_cents(committed_cents(ctx.conn, reservation_id))),
            },
        )
    return free


def release_reserved_funds_tier(
    input: ReleaseReservedFundsInput, ctx: OperationContext
) -> Tier:
    releasable(ctx, input.reservation_id)  # used, released or all committed: refuse early
    return Tier.YELLOW


def release_reserved_funds(
    ctx: OperationContext, input: ReleaseReservedFundsInput
) -> ReleaseReservedFundsOutput:
    released_cents = releasable(ctx, input.reservation_id)
    if committed_cents(ctx.conn, input.reservation_id):
        # Keep what the pending orders need set aside; they close it as used when paid.
        ctx.conn.execute(
            "UPDATE reserved_funds SET amount_cents = amount_cents - ?"
            " WHERE reservation_id = ? AND customer_id = ?",
            (released_cents, input.reservation_id, ctx.customer_id),
        )
    else:
        ctx.conn.execute(
            "UPDATE reserved_funds SET status = 'released', closed_at = ?"
            " WHERE reservation_id = ? AND customer_id = ?",
            (ctx.now.isoformat(), input.reservation_id, ctx.customer_id),
        )
    account = customers.current_account(ctx.conn, ctx.customer_id)
    return ReleaseReservedFundsOutput(
        reservation=view(ctx.conn, reservation(ctx, input.reservation_id)),
        released=from_cents(released_cents),
        available_balance=from_cents(customers.available_cents(ctx.conn, account)),
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
    return ListReservedFundsOutput(reservations=[view(ctx.conn, row) for row in rows])


MODULE = BankModule(
    name="reserved_funds",
    schema="",  # the table lives in customers.py, with the accounts
    operations=[
        Operation(
            name="reserve_funds",
            description_zh=(
                "从活期账户中预留一笔钱用于指定用途（如买生日礼物），并指明可向哪类商户付款；"
                "预留的钱仍属于客户，但不能用于其他支出。只改变可用余额，不改变总余额。"
            ),
            description_en=(
                "Set money aside in the current account for a stated purpose, such as a"
                " birthday gift, naming the kinds of Merchant it may pay; it stays the"
                " Customer's but can be spent on nothing else. It changes the available"
                " balance, never the total balance."
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
            description_zh=(
                "提前释放一笔预留资金，未用且未被待付款订单占用的部分回到可用余额；"
                "待付款订单所需的钱继续预留，直到付款。"
            ),
            description_en=(
                "Release Reserved Funds early; whatever is neither spent nor committed to"
                " orders still to be paid goes back to the available balance. The money"
                " for those orders stays set aside until they are paid."
            ),
            input_model=ReleaseReservedFundsInput,
            output_model=ReleaseReservedFundsOutput,
            tier=TierRule(
                description_zh=(
                    "一律为黄色（需确认）；已用完、已释放或余额全部被待付款订单占用的预留资金"
                    "会被拒绝。"
                ),
                description_en=(
                    "Always Yellow (Confirmation); Reserved Funds already used up or"
                    " released, or whose rest is all committed to pending orders, are"
                    " refused."
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
            description_zh=(
                "列出当前客户的预留资金：用途、已用金额、待付款订单占用的金额、剩余金额和状态。"
            ),
            description_en=(
                "List the current Customer's Reserved Funds: purpose, amount spent, amount"
                " committed to orders still to be paid, amount remaining and status."
            ),
            input_model=ListReservedFundsInput,
            output_model=ListReservedFundsOutput,
            tier=Tier.GREEN,
            reversibility=ReadOnly(),
            handler=list_reserved_funds,
        ),
    ],
)
