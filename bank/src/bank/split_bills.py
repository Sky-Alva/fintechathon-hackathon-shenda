"""Smart Transfers: Split-bill Requests (#12).

A Customer asks several Payees to each pay a share of a shared expense. It is Yellow and
needs Confirmation because it reaches out to other people on the Customer's behalf, but no
money leaves the Customer's account: balances and the Daily Transfer Total never change.

The shares must add up to the total, each Payee gets one share, and every Payee must be the
Customer's own. These are checked when the Plan is tiered, so a Customer is never asked to
confirm a request that could not be sent.
"""

import sqlite3
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from bank import transfers
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

SCHEMA = """
CREATE TABLE split_bill_requests (
    split_bill_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers (customer_id),
    total_cents INTEGER NOT NULL CHECK (total_cents > 0),
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE split_bill_shares (
    split_bill_id TEXT NOT NULL REFERENCES split_bill_requests (split_bill_id),
    payee_id TEXT NOT NULL REFERENCES payees (payee_id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    position INTEGER NOT NULL,
    PRIMARY KEY (split_bill_id, payee_id)
);
"""

MIN_SHARES = 2  # a split among fewer than two Payees is just a payment request
MAX_SHARES = 20


class Share(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payee_id: str
    amount: Amount = Field(description="What this Payee is asked to pay.")


class ShareView(BaseModel):
    payee_id: str
    payee_name: str
    amount: Decimal


class SplitBillRequest(BaseModel):
    split_bill_id: str
    total: Decimal
    note: str
    shares: list[ShareView]


def _view(conn: sqlite3.Connection, request: sqlite3.Row) -> SplitBillRequest:
    rows = conn.execute(
        "SELECT s.payee_id, p.name, s.amount_cents FROM split_bill_shares s"
        " JOIN payees p ON p.payee_id = s.payee_id"
        " WHERE s.split_bill_id = ? ORDER BY s.position",
        (request["split_bill_id"],),
    ).fetchall()
    return SplitBillRequest(
        split_bill_id=request["split_bill_id"],
        total=from_cents(request["total_cents"]),
        note=request["note"],
        shares=[
            ShareView(
                payee_id=row["payee_id"],
                payee_name=row["name"],
                amount=from_cents(row["amount_cents"]),
            )
            for row in rows
        ],
    )


# --- Request a split (Yellow) -------------------------------------------------------


class RequestSplitBillInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total: Amount = Field(description="The whole shared expense.")
    shares: list[Share] = Field(
        min_length=MIN_SHARES,
        max_length=MAX_SHARES,
        description="One share per Payee. The shares must add up to the total.",
    )
    note: str = Field(default="", max_length=100, description="What the expense was for.")


class RequestSplitBillOutput(BaseModel):
    split_bill_request: SplitBillRequest


def _validate(ctx: OperationContext, input: RequestSplitBillInput) -> None:
    payee_ids = [share.payee_id for share in input.shares]
    duplicates = sorted({payee_id for payee_id in payee_ids if payee_ids.count(payee_id) > 1})
    if duplicates:
        raise Refusal(
            RefusalReason.INVALID_INPUT,
            "Each Payee can have only one share",
            {"duplicate_payee_ids": duplicates},
        )
    shares_cents = sum(to_cents(share.amount) for share in input.shares)
    if shares_cents != to_cents(input.total):
        raise Refusal(
            RefusalReason.INVALID_INPUT,
            "The shares must add up to the total",
            {"total": str(input.total), "shares_total": str(from_cents(shares_cents))},
        )
    for payee_id in payee_ids:
        transfers.payee_of(ctx, payee_id)  # not_found for an unknown or another Customer's Payee


def request_split_bill_tier(input: RequestSplitBillInput, ctx: OperationContext) -> Tier:
    _validate(ctx, input)
    return Tier.YELLOW


def request_split_bill(
    ctx: OperationContext, input: RequestSplitBillInput
) -> RequestSplitBillOutput:
    _validate(ctx, input)
    count = ctx.conn.execute("SELECT COUNT(*) FROM split_bill_requests").fetchone()[0]
    split_bill_id = f"SPL-{count + 1:06d}"  # Bank-wide, like transfer IDs
    ctx.conn.execute(
        "INSERT INTO split_bill_requests VALUES (?, ?, ?, ?, ?)",
        (split_bill_id, ctx.customer_id, to_cents(input.total), input.note, ctx.now.isoformat()),
    )
    ctx.conn.executemany(
        "INSERT INTO split_bill_shares VALUES (?, ?, ?, ?)",
        [
            (split_bill_id, share.payee_id, to_cents(share.amount), position)
            for position, share in enumerate(input.shares)
        ],
    )
    request = ctx.conn.execute(
        "SELECT * FROM split_bill_requests WHERE split_bill_id = ?", (split_bill_id,)
    ).fetchone()
    return RequestSplitBillOutput(split_bill_request=_view(ctx.conn, request))


# --- List (Green) -------------------------------------------------------------------


class ListSplitBillRequestsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListSplitBillRequestsOutput(BaseModel):
    split_bill_requests: list[SplitBillRequest]


def list_split_bill_requests(
    ctx: OperationContext, input: ListSplitBillRequestsInput
) -> ListSplitBillRequestsOutput:
    rows = ctx.conn.execute(
        "SELECT * FROM split_bill_requests WHERE customer_id = ? ORDER BY split_bill_id",
        (ctx.customer_id,),
    ).fetchall()
    return ListSplitBillRequestsOutput(
        split_bill_requests=[_view(ctx.conn, row) for row in rows]
    )


MODULE = BankModule(
    name="split_bills",
    schema=SCHEMA,
    operations=[
        Operation(
            name="request_split_bill",
            description_zh=(
                "向多位收款人发出分摊账单请求，请每人支付一份；需客户确认，不会从客户账户转出任何资金。"
            ),
            description_en=(
                "Send a Split-bill Request asking several Payees to each pay a share of a"
                " shared expense; it needs Confirmation and no money leaves the Customer's"
                " account."
            ),
            input_model=RequestSplitBillInput,
            output_model=RequestSplitBillOutput,
            tier=TierRule(
                description_zh=(
                    "一律为黄色（需确认），与总额大小无关；份额之和必须等于总额，每位收款人只能有一份。"
                ),
                description_en=(
                    "Always Yellow (Confirmation), however large the total, because no money"
                    " leaves the account; the shares must add up to the total, one per Payee."
                ),
                resolve=request_split_bill_tier,
            ),
            # Payees have already been asked, so a request cannot be taken back.
            reversibility=Irreversible(),
            handler=request_split_bill,
        ),
        Operation(
            name="list_split_bill_requests",
            description_zh="列出当前客户发出的分摊账单请求及各收款人的份额。",
            description_en=(
                "List the Split-bill Requests the current Customer has sent, with each"
                " Payee's share."
            ),
            input_model=ListSplitBillRequestsInput,
            output_model=ListSplitBillRequestsOutput,
            tier=Tier.GREEN,
            reversibility=ReadOnly(),
            handler=list_split_bill_requests,
        ),
    ],
)
