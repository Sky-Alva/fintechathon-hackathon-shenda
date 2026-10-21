"""Smart Transfers: Scheduled Transfers (#11).

A Customer schedules a transfer for a future Bank-clock date and can cancel it before it
runs. It is tiered when created, against the Daily Transfer Total of its *run day* (money
already sent that day plus this amount), and re-checked when it runs: the Bank runs every
due Scheduled Transfer whenever the Bank clock is set or advanced (`run_due`, called by the
admin clock endpoints).

At run time a transfer that has become Red (it was Yellow when scheduled) is *held*: neither
sent nor dropped. It shows as awaiting verification until the Customer passes Strong
Verification for `send_held_transfer`, or cancels it. A transfer that can't be paid (not
enough money) is marked failed, never silently dropped.

Pending Scheduled Transfers don't count toward another one's tier at creation, because they
can still be cancelled; the run-time re-check is what catches their sum.
"""

import sqlite3
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from bank import audit, clock, limits, transfers
from bank.audit import Event
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
    ReversedBy,
    Tier,
    TierRule,
)

SCHEMA = """
CREATE TABLE scheduled_transfers (
    scheduled_transfer_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers (customer_id),
    payee_id TEXT NOT NULL REFERENCES payees (payee_id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    note TEXT NOT NULL,
    run_date TEXT NOT NULL,
    created_tier TEXT NOT NULL CHECK (created_tier IN ('yellow', 'red')),
    created_session_id TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('scheduled', 'held', 'executed', 'cancelled', 'failed')),
    created_at TEXT NOT NULL,
    held_at TEXT,
    executed_at TEXT,
    cancelled_at TEXT,
    transfer_id TEXT,
    failure_reason TEXT
);
"""

MAX_DAYS_AHEAD = 365
RUN_OPERATION = "run_scheduled_transfer"  # how a run shows in the audit trail

Status = Literal["scheduled", "held", "executed", "cancelled", "failed"]
OPEN_STATUSES = ("scheduled", "held")  # not yet sent, and can still be cancelled

# One of the session's Customer's Scheduled Transfers. The gate refuses another Customer's.
ScheduledTransferId = Annotated[str, OwnedBy("scheduled_transfers", "scheduled_transfer_id")]


class ScheduledTransfer(BaseModel):
    scheduled_transfer_id: str
    payee_id: str
    payee_name: str
    amount: Decimal
    run_date: date
    note: str
    status: Status
    awaiting_verification: bool = Field(
        description="True while the transfer is held: it needs Strong Verification to be sent."
    )
    transfer_id: str | None = Field(description="The transfer it became, once executed.")
    failure_reason: str | None = Field(description="Why it could not be paid, when failed.")


def _view(conn: sqlite3.Connection, row: sqlite3.Row) -> ScheduledTransfer:
    payee = conn.execute(
        "SELECT name FROM payees WHERE payee_id = ?", (row["payee_id"],)
    ).fetchone()
    return ScheduledTransfer(
        scheduled_transfer_id=row["scheduled_transfer_id"],
        payee_id=row["payee_id"],
        payee_name=payee["name"],
        amount=from_cents(row["amount_cents"]),
        run_date=date.fromisoformat(row["run_date"]),
        note=row["note"],
        status=row["status"],
        awaiting_verification=row["status"] == "held",
        transfer_id=row["transfer_id"],
        failure_reason=row["failure_reason"],
    )


def _row(ctx: OperationContext, scheduled_transfer_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = ctx.conn.execute(
        "SELECT * FROM scheduled_transfers WHERE scheduled_transfer_id = ? AND customer_id = ?",
        (scheduled_transfer_id, ctx.customer_id),
    ).fetchone()
    if row is None:
        raise Refusal(
            RefusalReason.NOT_FOUND,
            "No such Scheduled Transfer",
            {"scheduled_transfer_id": scheduled_transfer_id},
        )
    return row


def _open_row(ctx: OperationContext, scheduled_transfer_id: str) -> sqlite3.Row:
    """A Scheduled Transfer that has not been sent yet (scheduled or held)."""
    row = _row(ctx, scheduled_transfer_id)
    if row["status"] not in OPEN_STATUSES:
        raise Refusal(
            RefusalReason.INVALID_INPUT,
            f"This Scheduled Transfer is already {row['status']}",
            {"scheduled_transfer_id": scheduled_transfer_id, "status": row["status"]},
        )
    return row


def _check_run_date(ctx: OperationContext, run_date: date) -> None:
    today = ctx.now.date()
    if run_date <= today or run_date > today + timedelta(days=MAX_DAYS_AHEAD):
        raise Refusal(
            RefusalReason.INVALID_INPUT,
            f"A Scheduled Transfer runs on a date after {today.isoformat()}"
            f" and within {MAX_DAYS_AHEAD} days",
            {"run_date": run_date.isoformat(), "today": today.isoformat()},
        )


def _tier_on_run_day(ctx: OperationContext, amount: Decimal, run_date: date) -> Tier:
    """Yellow if what the Customer has sent on the run day, plus `amount`, stays within the
    Daily Transfer Total limit; otherwise Red."""
    on_run_day = replace(
        ctx, daily_transfer_total=limits.daily_transfer_total(ctx.conn, ctx.customer_id, run_date)
    )
    return limits.tier_for_outgoing(amount, on_run_day)


# --- Schedule a transfer ------------------------------------------------------------


class ScheduleTransferInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payee_id: str
    amount: Amount
    run_date: date = Field(description="The Bank-clock date it runs on; after today.")
    note: str = Field(default="", max_length=100)


class ScheduleTransferOutput(BaseModel):
    scheduled_transfer: ScheduledTransfer


def schedule_transfer_tier(input: ScheduleTransferInput, ctx: OperationContext) -> Tier:
    _check_run_date(ctx, input.run_date)
    transfers.payee_of(ctx, input.payee_id)
    return _tier_on_run_day(ctx, input.amount, input.run_date)


def schedule_transfer(
    ctx: OperationContext, input: ScheduleTransferInput
) -> ScheduleTransferOutput:
    _check_run_date(ctx, input.run_date)
    payee = transfers.payee_of(ctx, input.payee_id)
    tier = _tier_on_run_day(ctx, input.amount, input.run_date)
    count = ctx.conn.execute("SELECT COUNT(*) FROM scheduled_transfers").fetchone()[0]
    scheduled_transfer_id = f"SCH-{count + 1:06d}"  # Bank-wide, like transfer IDs
    ctx.conn.execute(
        "INSERT INTO scheduled_transfers (scheduled_transfer_id, customer_id, payee_id,"
        " amount_cents, note, run_date, created_tier, created_session_id, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', ?)",
        (
            scheduled_transfer_id,
            ctx.customer_id,
            payee["payee_id"],
            to_cents(input.amount),
            input.note,
            input.run_date.isoformat(),
            tier.value,
            ctx.session_id,
            ctx.now.isoformat(),
        ),
    )
    return ScheduleTransferOutput(
        scheduled_transfer=_view(ctx.conn, _row(ctx, scheduled_transfer_id))
    )


# --- Cancel (Yellow) ----------------------------------------------------------------


class CancelScheduledTransferInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scheduled_transfer_id: ScheduledTransferId


class CancelScheduledTransferOutput(BaseModel):
    scheduled_transfer: ScheduledTransfer


def cancel_scheduled_transfer_tier(
    input: CancelScheduledTransferInput, ctx: OperationContext
) -> Tier:
    # Refuse one that can never be cancelled before the Customer is asked to confirm.
    _open_row(ctx, input.scheduled_transfer_id)
    return Tier.YELLOW


def cancel_scheduled_transfer(
    ctx: OperationContext, input: CancelScheduledTransferInput
) -> CancelScheduledTransferOutput:
    _open_row(ctx, input.scheduled_transfer_id)
    ctx.conn.execute(
        "UPDATE scheduled_transfers SET status = 'cancelled', cancelled_at = ?"
        " WHERE scheduled_transfer_id = ?",
        (ctx.now.isoformat(), input.scheduled_transfer_id),
    )
    return CancelScheduledTransferOutput(
        scheduled_transfer=_view(ctx.conn, _row(ctx, input.scheduled_transfer_id))
    )


# --- List (Green) -------------------------------------------------------------------


class ListScheduledTransfersInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Status | None = Field(default=None, description="Only those in this status.")


class ListScheduledTransfersOutput(BaseModel):
    scheduled_transfers: list[ScheduledTransfer]


def list_scheduled_transfers(
    ctx: OperationContext, input: ListScheduledTransfersInput
) -> ListScheduledTransfersOutput:
    rows = ctx.conn.execute(
        "SELECT * FROM scheduled_transfers WHERE customer_id = ? AND (? IS NULL OR status = ?)"
        " ORDER BY run_date, scheduled_transfer_id",
        (ctx.customer_id, input.status, input.status),
    ).fetchall()
    return ListScheduledTransfersOutput(
        scheduled_transfers=[_view(ctx.conn, row) for row in rows]
    )


# --- Send a held transfer (Red) -----------------------------------------------------


class SendHeldTransferInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scheduled_transfer_id: ScheduledTransferId
    amount: Amount = Field(description="Must equal the held transfer's amount.")


class SendHeldTransferOutput(BaseModel):
    scheduled_transfer: ScheduledTransfer
    transfer: transfers.TransferOutput


def _held_row(ctx: OperationContext, input: SendHeldTransferInput) -> sqlite3.Row:
    row = _row(ctx, input.scheduled_transfer_id)
    if row["status"] != "held":
        raise Refusal(
            RefusalReason.INVALID_INPUT,
            f"Only a held Scheduled Transfer can be sent; this one is {row['status']}",
            {"scheduled_transfer_id": input.scheduled_transfer_id, "status": row["status"]},
        )
    if row["amount_cents"] != to_cents(input.amount):
        raise Refusal(
            RefusalReason.INVALID_INPUT,
            "The amount is not the held transfer's amount",
            {
                "scheduled_transfer_id": input.scheduled_transfer_id,
                "amount": str(from_cents(row["amount_cents"])),
            },
        )
    return row


def send_held_transfer_tier(input: SendHeldTransferInput, ctx: OperationContext) -> Tier:
    # A transfer is only ever held because it was Red, so sending it needs Strong Verification.
    _held_row(ctx, input)
    transfers.funded_current_account(ctx, input.amount)
    return Tier.RED


def send_held_transfer(
    ctx: OperationContext, input: SendHeldTransferInput
) -> SendHeldTransferOutput:
    row = _held_row(ctx, input)
    sent = transfers.transfer(
        ctx,
        transfers.TransferInput(
            payee_id=row["payee_id"], amount=input.amount, note=row["note"]
        ),
    )
    ctx.conn.execute(
        "UPDATE scheduled_transfers SET status = 'executed', executed_at = ?, transfer_id = ?"
        " WHERE scheduled_transfer_id = ?",
        (ctx.now.isoformat(), sent.transfer_id, input.scheduled_transfer_id),
    )
    return SendHeldTransferOutput(
        scheduled_transfer=_view(ctx.conn, _row(ctx, input.scheduled_transfer_id)),
        transfer=sent,
    )


# --- Running due transfers, when the Bank clock reaches their date ------------------


def run_due(conn: sqlite3.Connection) -> None:
    """Run every Scheduled Transfer whose run date the Bank clock has reached, oldest first.

    Each one counts toward the Daily Transfer Total of its run day and is tiered again
    there. Called inside the transaction that sets or advances the Bank clock.
    """
    now = clock.now(conn)
    due = conn.execute(
        "SELECT * FROM scheduled_transfers WHERE status = 'scheduled' AND run_date <= ?"
        " ORDER BY run_date, scheduled_transfer_id",
        (now.date().isoformat(),),
    ).fetchall()
    for row in due:
        _run(conn, row, now)


def _run(conn: sqlite3.Connection, row: sqlite3.Row, now: datetime) -> None:
    scheduled_transfer_id = row["scheduled_transfer_id"]
    day = date.fromisoformat(row["run_date"])
    run_at = datetime.combine(day, time(0, 0), tzinfo=clock.BANK_TZ)
    amount = from_cents(row["amount_cents"])
    ctx = OperationContext(
        conn=conn,
        customer_id=row["customer_id"],
        session_id=row["created_session_id"],
        now=run_at,
        daily_transfer_total=limits.daily_transfer_total(conn, row["customer_id"], day),
    )
    tier = limits.tier_for_outgoing(amount, ctx)
    detail = {"scheduled_transfer_id": scheduled_transfer_id, "run_date": row["run_date"]}

    if tier is Tier.RED and row["created_tier"] == "yellow":
        # It has become Red since it was scheduled: hold it, neither send nor drop it.
        conn.execute(
            "UPDATE scheduled_transfers SET status = 'held', held_at = ?"
            " WHERE scheduled_transfer_id = ?",
            (now.isoformat(), scheduled_transfer_id),
        )
        _audit_run(conn, row, now, tier, RefusalReason.NEEDS_VERIFICATION, {**detail, "held": True})
        return

    conn.execute("SAVEPOINT scheduled_run")
    try:
        sent = transfers.transfer(
            ctx,
            transfers.TransferInput(
                payee_id=row["payee_id"], amount=amount, note=row["note"]
            ),
        )
    except Refusal as refusal:
        conn.execute("ROLLBACK TO scheduled_run")
        conn.execute("RELEASE scheduled_run")
        conn.execute(
            "UPDATE scheduled_transfers SET status = 'failed', failure_reason = ?"
            " WHERE scheduled_transfer_id = ?",
            (refusal.reason.value, scheduled_transfer_id),
        )
        _audit_run(conn, row, now, tier, refusal.reason, {**detail, **refusal.detail})
        return
    conn.execute("RELEASE scheduled_run")
    limits.record(conn, row["customer_id"], "scheduled_transfer", amount, run_at)
    conn.execute(
        "UPDATE scheduled_transfers SET status = 'executed', executed_at = ?, transfer_id = ?"
        " WHERE scheduled_transfer_id = ?",
        (now.isoformat(), sent.transfer_id, scheduled_transfer_id),
    )
    _audit_run(conn, row, now, tier, None, {**detail, "transfer_id": sent.transfer_id})


def _audit_run(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    now: datetime,
    tier: Tier,
    reason: RefusalReason | None,
    detail: dict[str, object],
) -> None:
    """Record a run in the trail of the session that scheduled it. A transfer that was
    held or failed shows as a refusal, so it never counts as having run."""
    audit.record(
        conn,
        session_id=row["created_session_id"],
        at=now,
        event=Event.OPERATION,
        operation=RUN_OPERATION,
        input={"scheduled_transfer_id": row["scheduled_transfer_id"]},
        tier=tier,
        reason=reason,
        detail=detail,
        refusal_detail=detail if reason else None,
    )


MODULE = BankModule(
    name="scheduled_transfers",
    schema=SCHEMA,
    operations=[
        Operation(
            name="schedule_transfer",
            description_zh=(
                "预约一笔未来日期的转账；创建时按执行当日的累计转账额定级，到期执行时会再次检查。"
            ),
            description_en=(
                "Schedule a transfer to a Payee for a future Bank date; it is tiered when"
                " created against the Daily Transfer Total of its run day and checked again"
                " when it runs."
            ),
            input_model=ScheduleTransferInput,
            output_model=ScheduleTransferOutput,
            tier=TierRule(
                description_zh="执行当日累计转账额（含本笔）≤ ¥1,000 为黄色，否则为红色。",
                description_en=(
                    "Yellow if the Daily Transfer Total of the run day, including this"
                    " transfer, stays at or below ¥1,000, otherwise Red."
                ),
                resolve=schedule_transfer_tier,
            ),
            reversibility=ReversedBy(
                "cancel_scheduled_transfer",
                lambda input, output: CancelScheduledTransferInput(
                    scheduled_transfer_id=output.scheduled_transfer.scheduled_transfer_id
                ),
            ),
            handler=schedule_transfer,
        ),
        Operation(
            name="cancel_scheduled_transfer",
            description_zh="在预约转账执行前取消它（待执行或被暂扣的都可以取消）。",
            description_en=(
                "Cancel a Scheduled Transfer before it is sent, whether it is waiting or held."
            ),
            input_model=CancelScheduledTransferInput,
            output_model=CancelScheduledTransferOutput,
            tier=TierRule(
                description_zh="取消尚未执行的预约转账为黄色；已执行、已取消或失败的不可取消。",
                description_en=(
                    "Yellow for a Scheduled Transfer that has not been sent; one already"
                    " sent, cancelled or failed is refused."
                ),
                resolve=cancel_scheduled_transfer_tier,
            ),
            reversibility=Irreversible(),
            handler=cancel_scheduled_transfer,
        ),
        Operation(
            name="list_scheduled_transfers",
            description_zh="列出当前客户的预约转账及其状态，包括因变为红色而待验证的暂扣转账。",
            description_en=(
                "List the current Customer's Scheduled Transfers and their status, including"
                " held ones awaiting Strong Verification."
            ),
            input_model=ListScheduledTransfersInput,
            output_model=ListScheduledTransfersOutput,
            tier=Tier.GREEN,
            reversibility=ReadOnly(),
            handler=list_scheduled_transfers,
        ),
        Operation(
            name="send_held_transfer",
            description_zh="经强验证后，发出一笔因执行时变为红色而被暂扣的预约转账。",
            description_en=(
                "Send a Scheduled Transfer that was held because it became Red when it ran,"
                " after Strong Verification."
            ),
            input_model=SendHeldTransferInput,
            output_model=SendHeldTransferOutput,
            tier=TierRule(
                description_zh="只有被暂扣的预约转账可发出，且一律为红色（需强验证）。",
                description_en=(
                    "Only a held Scheduled Transfer can be sent, and it is always Red"
                    " (Strong Verification)."
                ),
                resolve=send_held_transfer_tier,
            ),
            reversibility=Irreversible(),
            handler=send_held_transfer,
            daily_transfer_amount=lambda input: input.amount,
        ),
    ],
)
