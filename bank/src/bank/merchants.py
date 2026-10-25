"""Smart Transfers: Merchants (#13).

Simulated businesses inside the Bank: a flower shop and a cake shop. A Customer orders goods
for a delivery date and pays from Reserved Funds, so an order only works with a reservation
that allows that kind of Merchant and has enough left once its other pending orders are paid.
An order may leave its Reserved Funds unnamed: then it is paid from the Customer's oldest
active ones that qualify, found when the order is placed, so one Plan (and one approval)
can reserve the money and place the orders paid from it.

An order is accepted now and paid at 09:00 on its delivery date, like a Scheduled Transfer:
nothing is debited and no Reserved Funds are spent before then. It is tiered when ordered like
a transfer of the same amount on its delivery day, against that day's *projected* Daily
Transfer Total (`limits.projected_total`), so one that takes it above ¥1,000 is
Red. The Bank pays every due order whenever the Bank clock is set or advanced (`run_due`,
called by the admin clock endpoints), through the policy gate as the payment the Customer
approved when ordering: the Safety Lock, the tier and the Reserved Funds are checked again
then, and the payment counts toward the Daily Transfer Total of the day it is paid. An order
that can't be paid then is marked failed with the reason, never silently dropped. A payment
cannot be taken back.

Merchants keep no account of their own: the Customer's account is debited and that is all.
"""

import sqlite3
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from bank import clock, customers, limits, policy, reserved_funds, sessions
from bank.money import Amount, from_cents, to_cents
from bank.registry import (
    BankModule,
    Irreversible,
    Operation,
    OperationContext,
    OwnedBy,
    ReadOnly,
    Refusal,
    RefusalReason,
    Registry,
    Tier,
    TierRule,
)
from bank.reserved_funds import MerchantCategory, Reservation, ReservationId

SCHEMA = """
CREATE TABLE merchants (
    merchant_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('flowers', 'cake'))
);
CREATE TABLE merchant_products (
    product_id TEXT PRIMARY KEY,
    merchant_id TEXT NOT NULL REFERENCES merchants (merchant_id),
    name TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents > 0)
);
CREATE TABLE merchant_orders (
    order_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers (customer_id),
    merchant_id TEXT NOT NULL REFERENCES merchants (merchant_id),
    product_id TEXT NOT NULL REFERENCES merchant_products (product_id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    delivery_date TEXT NOT NULL,
    reservation_id TEXT NOT NULL REFERENCES reserved_funds (reservation_id),
    note TEXT NOT NULL,
    approved_tier TEXT NOT NULL CHECK (approved_tier IN ('yellow', 'red')),
    created_session_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'paid', 'failed')),
    created_at TEXT NOT NULL,
    paid_at TEXT,
    failure_reason TEXT
);
"""

MAX_DAYS_AHEAD = 365
PAY_TIME = time(9, 0)  # Bank-clock time an order is paid on its delivery date

Status = Literal["pending", "paid", "failed"]

# One of the session's Customer's Merchant orders. The gate refuses another Customer's.
OrderId = Annotated[str, OwnedBy("merchant_orders", "order_id")]


def seed(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT INTO merchants VALUES (?, ?, ?)",
        [
            ("MER-FLOWER-001", "花语鲜花店 Petal Florist", "flowers"),
            ("MER-CAKE-001", "甜心蛋糕店 Sweet Heart Bakery", "cake"),
        ],
    )
    conn.executemany(
        "INSERT INTO merchant_products VALUES (?, ?, ?, ?)",
        [
            ("PRD-FLOWER-001", "MER-FLOWER-001", "红玫瑰花束 Red rose bouquet", 29900),
            ("PRD-FLOWER-002", "MER-FLOWER-001", "向日葵花束 Sunflower bouquet", 19900),
            ("PRD-CAKE-001", "MER-CAKE-001", "生日蛋糕 6寸 Birthday cake, 6 inch", 26800),
            ("PRD-CAKE-002", "MER-CAKE-001", "生日蛋糕 8寸 Birthday cake, 8 inch", 38800),
        ],
    )


# --- List Merchants (Green) ---------------------------------------------------------


class Product(BaseModel):
    product_id: str
    name: str
    price: Decimal


class MerchantView(BaseModel):
    merchant_id: str
    name: str
    category: MerchantCategory
    products: list[Product]


class ListMerchantsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: MerchantCategory | None = Field(
        default=None, description="Only Merchants of this kind."
    )


class ListMerchantsOutput(BaseModel):
    merchants: list[MerchantView]


def list_merchants(ctx: OperationContext, input: ListMerchantsInput) -> ListMerchantsOutput:
    merchants = ctx.conn.execute(
        "SELECT * FROM merchants WHERE (? IS NULL OR category = ?) ORDER BY merchant_id",
        (input.category, input.category),
    ).fetchall()
    result = []
    for merchant in merchants:
        products = ctx.conn.execute(
            "SELECT * FROM merchant_products WHERE merchant_id = ? ORDER BY product_id",
            (merchant["merchant_id"],),
        ).fetchall()
        result.append(
            MerchantView(
                merchant_id=merchant["merchant_id"],
                name=merchant["name"],
                category=merchant["category"],
                products=[
                    Product(
                        product_id=p["product_id"],
                        name=p["name"],
                        price=from_cents(p["price_cents"]),
                    )
                    for p in products
                ],
            )
        )
    return ListMerchantsOutput(merchants=result)


# --- Order, to be paid from Reserved Funds on the delivery date ---------------------


class OrderFromMerchantInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    merchant_id: str
    product_id: str
    quantity: int = Field(default=1, ge=1, le=20)
    amount: Amount = Field(description="The listed price times the quantity.")
    delivery_date: date = Field(
        description="The Bank-clock date it is delivered and paid, at 09:00; after today."
    )
    reservation_id: ReservationId | None = Field(
        default=None,
        description=(
            "The Reserved Funds to pay from. Left out, the Customer's oldest active Reserved"
            " Funds that may pay this kind of Merchant and have enough left, found when the"
            " order is placed: so a Plan can reserve them in an earlier step."
        ),
    )
    note: str = Field(default="", max_length=100)


class Order(BaseModel):
    order_id: str
    merchant_id: str
    merchant_name: str
    product_id: str
    product_name: str
    quantity: int
    amount: Decimal
    delivery_date: date
    reservation_id: str
    note: str
    status: Status = Field(
        description="pending until paid at 09:00 on the delivery date; failed if it couldn't be."
    )
    paid_at: datetime | None
    failure_reason: str | None = Field(description="Why it could not be paid, when failed.")


class OrderFromMerchantOutput(BaseModel):
    order: Order
    reservation: Reservation = Field(description="Nothing is spent from it until the order is paid.")


def _validate(
    ctx: OperationContext, input: OrderFromMerchantInput
) -> tuple[sqlite3.Row, sqlite3.Row]:
    """The Merchant and product of an order that can be accepted now."""
    merchant: sqlite3.Row | None = ctx.conn.execute(
        "SELECT * FROM merchants WHERE merchant_id = ?", (input.merchant_id,)
    ).fetchone()
    if merchant is None:
        raise Refusal(
            RefusalReason.NOT_FOUND, "No such Merchant", {"merchant_id": input.merchant_id}
        )
    product: sqlite3.Row | None = ctx.conn.execute(
        "SELECT * FROM merchant_products WHERE product_id = ? AND merchant_id = ?",
        (input.product_id, input.merchant_id),
    ).fetchone()
    if product is None:
        raise Refusal(
            RefusalReason.NOT_FOUND,
            "This Merchant does not sell that product",
            {"merchant_id": input.merchant_id, "product_id": input.product_id},
        )
    today = ctx.now.date()
    if not today < input.delivery_date <= today + timedelta(days=MAX_DAYS_AHEAD):
        raise Refusal(
            RefusalReason.INVALID_INPUT,
            f"The delivery date must be after {today.isoformat()} and within"
            f" {MAX_DAYS_AHEAD} days",
            {"delivery_date": input.delivery_date.isoformat(), "today": today.isoformat()},
        )
    price_cents = product["price_cents"] * input.quantity
    if to_cents(input.amount) != price_cents:
        raise Refusal(
            RefusalReason.INVALID_INPUT,
            "The amount is not the listed price times the quantity",
            {"amount": str(input.amount), "price_total": str(from_cents(price_cents))},
        )
    return merchant, product


def _committed(ctx: OperationContext, reservation_id: str) -> int:
    """What the orders waiting to be paid from these Reserved Funds will take."""
    committed: int = ctx.conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM merchant_orders"
        " WHERE reservation_id = ? AND status = 'pending'",
        (reservation_id,),
    ).fetchone()[0]
    return committed


def _reservation_for(
    ctx: OperationContext, input: OrderFromMerchantInput, category: str
) -> sqlite3.Row:
    """The Reserved Funds the order will be paid from: the ones it names, or else the
    Customer's oldest active ones that may pay this kind of Merchant and have enough left,
    once the orders already waiting on them are paid."""
    cents = to_cents(input.amount)
    if input.reservation_id is not None:
        reservation = reserved_funds.active_reservation(ctx, input.reservation_id)
        committed = _committed(ctx, input.reservation_id)
        reserved_funds.check_can_pay(reservation, category, cents + committed)
        return reservation
    active = ctx.conn.execute(
        "SELECT * FROM reserved_funds WHERE customer_id = ? AND status = 'active'"
        " ORDER BY reservation_id",
        (ctx.customer_id,),
    ).fetchall()
    allowed: list[sqlite3.Row] = [
        row for row in active if category in reserved_funds.categories_of(row)
    ]
    if not allowed:
        raise Refusal(
            RefusalReason.INVALID_INPUT,
            "No Reserved Funds may be spent at this kind of Merchant",
            {"merchant_category": category},
        )
    for row in allowed:
        left = row["amount_cents"] - row["spent_cents"] - _committed(ctx, row["reservation_id"])
        if left >= cents:
            return row
    raise Refusal(
        RefusalReason.INSUFFICIENT_FUNDS,
        "Not enough left in the Reserved Funds for this kind of Merchant",
        {"merchant_category": category},
    )


def _tier_on_delivery_day(ctx: OperationContext, input: OrderFromMerchantInput) -> Tier:
    """Tiered like a transfer of the same amount on the day it will be paid."""
    projected = limits.projected_total(ctx.conn, ctx.customer_id, input.delivery_date)
    return limits.tier_for_day_total(projected, input.amount)


@limits.counts_as_pending
def pending_total(conn: sqlite3.Connection, customer_id: str, day: date) -> Decimal:
    """The Customer's Merchant orders still to be paid on `day`."""
    cents = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM merchant_orders"
        " WHERE customer_id = ? AND delivery_date = ? AND status = 'pending'",
        (customer_id, day.isoformat()),
    ).fetchone()[0]
    return from_cents(cents)


def order_from_merchant_tier(input: OrderFromMerchantInput, ctx: OperationContext) -> Tier:
    merchant, _ = _validate(ctx, input)
    if input.reservation_id is not None:
        # Named Reserved Funds must exist already. Without a name, a Plan may reserve them
        # in an earlier step, so they are found only when the order is placed.
        _reservation_for(ctx, input, merchant["category"])
    return _tier_on_delivery_day(ctx, input)


def order_from_merchant(
    ctx: OperationContext, input: OrderFromMerchantInput
) -> OrderFromMerchantOutput:
    merchant, product = _validate(ctx, input)
    reservation = _reservation_for(ctx, input, merchant["category"])
    tier = _tier_on_delivery_day(ctx, input)
    count = ctx.conn.execute("SELECT COUNT(*) FROM merchant_orders").fetchone()[0]
    order_id = f"ORD-{count + 1:06d}"  # Bank-wide, like transfer IDs
    ctx.conn.execute(
        "INSERT INTO merchant_orders (order_id, customer_id, merchant_id, product_id, quantity,"
        " amount_cents, delivery_date, reservation_id, note, approved_tier, created_session_id,"
        " status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
        (
            order_id,
            ctx.customer_id,
            merchant["merchant_id"],
            product["product_id"],
            input.quantity,
            to_cents(input.amount),
            input.delivery_date.isoformat(),
            reservation["reservation_id"],
            input.note,
            tier.value,
            ctx.session_id,
            ctx.now.isoformat(),
        ),
    )
    return OrderFromMerchantOutput(
        order=_order_view(ctx.conn, order_id),
        reservation=reserved_funds.view(
            reserved_funds.reservation(ctx, reservation["reservation_id"])
        ),
    )


def _order_view(conn: sqlite3.Connection, order_id: str) -> Order:
    row = conn.execute(
        "SELECT o.*, m.name AS merchant_name, p.name AS product_name FROM merchant_orders o"
        " JOIN merchants m ON m.merchant_id = o.merchant_id"
        " JOIN merchant_products p ON p.product_id = o.product_id"
        " WHERE o.order_id = ?",
        (order_id,),
    ).fetchone()
    return Order(
        order_id=row["order_id"],
        merchant_id=row["merchant_id"],
        merchant_name=row["merchant_name"],
        product_id=row["product_id"],
        product_name=row["product_name"],
        quantity=row["quantity"],
        amount=from_cents(row["amount_cents"]),
        delivery_date=date.fromisoformat(row["delivery_date"]),
        reservation_id=row["reservation_id"],
        note=row["note"],
        status=row["status"],
        paid_at=row["paid_at"],
        failure_reason=row["failure_reason"],
    )


# --- List orders (Green) ------------------------------------------------------------


class ListMerchantOrdersInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListMerchantOrdersOutput(BaseModel):
    orders: list[Order]


def list_merchant_orders(
    ctx: OperationContext, input: ListMerchantOrdersInput
) -> ListMerchantOrdersOutput:
    ids = ctx.conn.execute(
        "SELECT order_id FROM merchant_orders WHERE customer_id = ? ORDER BY order_id",
        (ctx.customer_id,),
    ).fetchall()
    return ListMerchantOrdersOutput(orders=[_order_view(ctx.conn, r["order_id"]) for r in ids])


# --- Paying due orders, when the Bank clock reaches their delivery date -------------


class PayMerchantOrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: OrderId
    amount: Amount


class PayMerchantOrderOutput(BaseModel):
    order: Order


def _payable(ctx: OperationContext, input: PayMerchantOrderInput) -> sqlite3.Row:
    """The order's Reserved Funds, if they can still pay it."""
    order = ctx.conn.execute(
        "SELECT o.reservation_id, m.category FROM merchant_orders o"
        " JOIN merchants m ON m.merchant_id = o.merchant_id WHERE o.order_id = ?",
        (input.order_id,),
    ).fetchone()
    reservation = reserved_funds.active_reservation(ctx, order["reservation_id"])
    reserved_funds.check_can_pay(reservation, order["category"], to_cents(input.amount))
    return reservation


def pay_merchant_order_tier(input: PayMerchantOrderInput, ctx: OperationContext) -> Tier:
    _payable(ctx, input)
    return limits.tier_for_outgoing(input.amount, ctx)  # on the day it is paid


def pay_merchant_order(
    ctx: OperationContext, input: PayMerchantOrderInput
) -> PayMerchantOrderOutput:
    reservation = _payable(ctx, input)
    cents = to_cents(input.amount)
    customers.debit(ctx.conn, reservation["account_id"], cents)
    reserved_funds.spend(ctx, reservation, cents)
    ctx.conn.execute(
        "UPDATE merchant_orders SET status = 'paid', paid_at = ? WHERE order_id = ?",
        (ctx.now.isoformat(), input.order_id),
    )
    return PayMerchantOrderOutput(order=_order_view(ctx.conn, input.order_id))


# Not in the Bank's catalogue: only `run_due` pays an order, as the Customer approved it.
PAYMENTS = Registry(
    [
        BankModule(
            name="merchant_payments",
            schema="",
            operations=[
                Operation(
                    name="pay_merchant_order",
                    description_zh="在送达日9:00用预留资金支付一笔待付款的商户订单。",
                    description_en=(
                        "Pay a pending Merchant order from its Reserved Funds at 09:00 on"
                        " its delivery date."
                    ),
                    input_model=PayMerchantOrderInput,
                    output_model=PayMerchantOrderOutput,
                    tier=TierRule(
                        description_zh="与付款当日同等金额的转账分级相同。",
                        description_en="Tiered like a transfer of the same amount that day.",
                        resolve=pay_merchant_order_tier,
                    ),
                    reversibility=Irreversible(),
                    handler=pay_merchant_order,
                    daily_transfer_amount=lambda input: input.amount,
                )
            ],
        )
    ]
)


def _pay_at(delivery_date: date) -> datetime:
    return datetime.combine(delivery_date, PAY_TIME, clock.BANK_TZ)


def run_due(conn: sqlite3.Connection) -> None:
    """Pay every pending order whose payment time the Bank clock has reached, oldest first.

    Each one goes through the policy gate as the payment approved when it was ordered, at
    its payment time, so it counts toward that day's Daily Transfer Total and is tiered
    again there. Called inside the transaction that sets or advances the Bank clock, after
    the Scheduled Transfers due at the same time.
    """
    now = clock.now(conn)
    on_or_before_today = conn.execute(
        "SELECT * FROM merchant_orders WHERE status = 'pending' AND delivery_date <= ?"
        " ORDER BY delivery_date, order_id",
        (now.date().isoformat(),),
    ).fetchall()
    for row in on_or_before_today:
        pay_at = _pay_at(date.fromisoformat(row["delivery_date"]))
        if pay_at > now:
            continue
        try:
            policy.run_approved(
                conn,
                PAYMENTS,
                sessions.by_id(conn, row["created_session_id"]),
                "pay_merchant_order",
                {"order_id": row["order_id"], "amount": str(from_cents(row["amount_cents"]))},
                approved=Tier(row["approved_tier"]),
                at=pay_at,  # also the time its Safety Lock and Daily Transfer Total are read
                detail={"order_id": row["order_id"], "delivery_date": row["delivery_date"]},
            )
        except Refusal as refusal:
            conn.execute(
                "UPDATE merchant_orders SET status = 'failed', failure_reason = ?"
                " WHERE order_id = ?",
                (refusal.reason.value, row["order_id"]),
            )


MODULE = BankModule(
    name="merchants",
    schema=SCHEMA,
    seed=seed,
    operations=[
        Operation(
            name="list_merchants",
            description_zh="列出可下单的商户（如鲜花店、蛋糕店）及其商品和价格。",
            description_en=(
                "List the Merchants that take orders, such as a flower shop and a cake shop,"
                " with their products and prices."
            ),
            input_model=ListMerchantsInput,
            output_model=ListMerchantsOutput,
            tier=Tier.GREEN,
            reversibility=ReadOnly(),
            handler=list_merchants,
        ),
        Operation(
            name="order_from_merchant",
            description_zh=(
                "向商户下单并指定送达日期（明天起），用预留资金付款；金额等于商品单价乘以数量。"
                "下单时不扣款，银行于送达日9:00从预留资金付款，届时再次检查；付款不可撤销。"
            ),
            description_en=(
                "Order goods from a Merchant for a delivery date after today, paid from"
                " Reserved Funds; the amount is the listed price times the quantity. Nothing"
                " is paid when ordering: the Bank pays it from the Reserved Funds at 09:00 on"
                " the delivery date, checking again then, and the payment cannot be taken back."
            ),
            input_model=OrderFromMerchantInput,
            output_model=OrderFromMerchantOutput,
            tier=TierRule(
                description_zh=(
                    "与送达日同等金额的转账分级相同：送达日预计累计转账额（已转出 + 当日待执行的"
                    "预约转账和待付款订单 + 本笔）≤ ¥1,000 为黄色，否则为红色；"
                    "预留资金（扣除待付款订单后）不够或用途不符会被拒绝。"
                ),
                description_en=(
                    "Tiered like a transfer of the same amount on the delivery day: Yellow if"
                    " that day's projected Daily Transfer Total (already sent, plus Scheduled"
                    " Transfers and pending orders for that day, plus this one) stays at or"
                    " below ¥1,000, otherwise Red; refused when the Reserved Funds, less their"
                    " pending orders, are too small or not for this kind of Merchant."
                ),
                resolve=order_from_merchant_tier,
            ),
            reversibility=Irreversible(),
            handler=order_from_merchant,
        ),
        Operation(
            name="list_merchant_orders",
            description_zh=(
                "列出当前客户向商户下的订单、送达日期、金额和付款状态（待付款、已付款或失败）。"
            ),
            description_en=(
                "List the current Customer's Merchant orders with delivery dates, amounts and"
                " whether each is pending, paid or failed."
            ),
            input_model=ListMerchantOrdersInput,
            output_model=ListMerchantOrdersOutput,
            tier=Tier.GREEN,
            reversibility=ReadOnly(),
            handler=list_merchant_orders,
        ),
    ],
)
