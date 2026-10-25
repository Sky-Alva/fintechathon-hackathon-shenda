"""Smart Transfers: Merchants (#13).

Simulated businesses inside the Bank: a flower shop and a cake shop. A Customer orders goods
for a delivery date and pays from Reserved Funds, so an order only works with a reservation
that allows that kind of Merchant and has enough left. The payment is tiered like a transfer
of the same amount and counts toward the Daily Transfer Total, so one that takes the total
above ¥1,000 is Red. A payment cannot be taken back.

Merchants keep no account of their own: the Customer's account is debited and that is all.
"""

import sqlite3
from datetime import date, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from bank import customers, limits, reserved_funds
from bank.money import Amount, from_cents, to_cents
from bank.registry import (
    BankModule,
    Irreversible,
    Operation,
    OperationContext,
    ReadOnly,
    Refusal,
    RefusalReason,
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
    created_at TEXT NOT NULL
);
"""

MAX_DAYS_AHEAD = 365


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


# --- Order and pay from Reserved Funds ----------------------------------------------


class OrderFromMerchantInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    merchant_id: str
    product_id: str
    quantity: int = Field(default=1, ge=1, le=20)
    amount: Amount = Field(description="The listed price times the quantity.")
    delivery_date: date = Field(description="The Bank-clock date it is delivered; not in the past.")
    reservation_id: ReservationId  # the Reserved Funds to pay from
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


class OrderFromMerchantOutput(BaseModel):
    order: Order
    reservation: Reservation
    balance_after: Decimal
    daily_transfer_total: Decimal


def _validate(
    ctx: OperationContext, input: OrderFromMerchantInput
) -> tuple[sqlite3.Row, sqlite3.Row, sqlite3.Row]:
    """The Merchant, product and reservation of an order that could be paid right now."""
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
    if not today <= input.delivery_date <= today + timedelta(days=MAX_DAYS_AHEAD):
        raise Refusal(
            RefusalReason.INVALID_INPUT,
            f"The delivery date must be from {today.isoformat()} and within"
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
    reservation = reserved_funds.active_reservation(ctx, input.reservation_id)
    reserved_funds.check_can_pay(reservation, merchant["category"], price_cents)
    return merchant, product, reservation


def order_from_merchant_tier(input: OrderFromMerchantInput, ctx: OperationContext) -> Tier:
    _validate(ctx, input)
    return limits.tier_for_outgoing(input.amount, ctx)  # tiered like a transfer of that amount


def order_from_merchant(
    ctx: OperationContext, input: OrderFromMerchantInput
) -> OrderFromMerchantOutput:
    merchant, product, reservation = _validate(ctx, input)
    cents = to_cents(input.amount)
    customers.debit(ctx.conn, reservation["account_id"], cents)
    reserved_funds.spend(ctx, reservation, cents)
    count = ctx.conn.execute("SELECT COUNT(*) FROM merchant_orders").fetchone()[0]
    order_id = f"ORD-{count + 1:06d}"  # Bank-wide, like transfer IDs
    ctx.conn.execute(
        "INSERT INTO merchant_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            order_id,
            ctx.customer_id,
            merchant["merchant_id"],
            product["product_id"],
            input.quantity,
            cents,
            input.delivery_date.isoformat(),
            input.reservation_id,
            input.note,
            ctx.now.isoformat(),
        ),
    )
    account = customers.current_account(ctx.conn, ctx.customer_id)  # what Reserved Funds are in
    return OrderFromMerchantOutput(
        order=_order_view(ctx, order_id),
        reservation=reserved_funds.view(
            reserved_funds.reservation(ctx, input.reservation_id)
        ),
        balance_after=from_cents(account["balance_cents"]),
        daily_transfer_total=ctx.daily_transfer_total + input.amount,
    )


def _order_view(ctx: OperationContext, order_id: str) -> Order:
    row = ctx.conn.execute(
        "SELECT o.*, m.name AS merchant_name, p.name AS product_name FROM merchant_orders o"
        " JOIN merchants m ON m.merchant_id = o.merchant_id"
        " JOIN merchant_products p ON p.product_id = o.product_id"
        " WHERE o.order_id = ? AND o.customer_id = ?",
        (order_id, ctx.customer_id),
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
    return ListMerchantOrdersOutput(orders=[_order_view(ctx, r["order_id"]) for r in ids])


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
                "向商户下单并指定送达日期，用预留资金付款；金额等于商品单价乘以数量，付款不可撤销。"
            ),
            description_en=(
                "Order goods from a Merchant for a delivery date and pay from Reserved Funds;"
                " the amount is the listed price times the quantity, and the payment cannot"
                " be taken back."
            ),
            input_model=OrderFromMerchantInput,
            output_model=OrderFromMerchantOutput,
            tier=TierRule(
                description_zh=(
                    "与同等金额的转账分级相同：当日累计转账额（含本笔）≤ ¥1,000 为黄色，否则为红色；"
                    "预留资金不够或用途不符会被拒绝。"
                ),
                description_en=(
                    "Tiered like a transfer of the same amount: Yellow if today's Daily"
                    " Transfer Total, including this payment, stays at or below ¥1,000,"
                    " otherwise Red; refused when the Reserved Funds are too small or not"
                    " for this kind of Merchant."
                ),
                resolve=order_from_merchant_tier,
            ),
            reversibility=Irreversible(),
            handler=order_from_merchant,
            daily_transfer_amount=lambda input: input.amount,
        ),
        Operation(
            name="list_merchant_orders",
            description_zh="列出当前客户向商户下的订单、送达日期和付款金额。",
            description_en=(
                "List the current Customer's Merchant orders with delivery dates and amounts paid."
            ),
            input_model=ListMerchantOrdersInput,
            output_model=ListMerchantOrdersOutput,
            tier=Tier.GREEN,
            reversibility=ReadOnly(),
            handler=list_merchant_orders,
        ),
    ],
)
