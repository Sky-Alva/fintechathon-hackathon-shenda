"""Card Management: list cards, virtual card, Card Limit, Card Lock and Loss Report.

Seed: 李明 has two debit cards and one credit card, one debit card already under a Card
Lock (overseas). 韩梅梅 has one debit card, so "someone else's card" can be tested.

Tiers (plan.md §3), anything that makes the account less safe is Red:
- Green: list cards.
- Yellow (#14): apply for a virtual card, lower a Card Limit, add a Card Lock.
- Red (#15): raise a Card Limit, lift a Card Lock, file a Loss Report, withdraw a Loss
  Report. Each needs Strong Verification; the Bank refuses it without one.
"""

import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

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

CardKind = Literal["debit", "credit", "virtual"]
CardStatus = Literal["active", "disabled"]  # "disabled" while a Loss Report stands (#15)
LimitPeriod = Literal["daily", "monthly"]
LockScope = Literal["overseas", "online", "all"]

# One of the session's Customer's cards. The gate refuses another Customer's card with
# `forbidden_other_customer` and an unknown one with `not_found`, before any tier rule runs.
CardId = Annotated[str, OwnedBy("cards", "card_id")]

# A virtual card starts with a small, fixed Card Limit. The Customer cannot pick a bigger
# one when applying, because raising a Card Limit is Red.
VIRTUAL_CARD_LIMIT_CENTS = 100000  # ¥1,000 a day
MAX_VIRTUAL_CARDS = 3

SCHEMA = """
CREATE TABLE cards (
    card_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers (customer_id),
    account_id TEXT NOT NULL REFERENCES accounts (account_id),
    kind TEXT NOT NULL CHECK (kind IN ('debit', 'credit', 'virtual')),
    name TEXT NOT NULL,
    last4 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    card_limit_cents INTEGER NOT NULL CHECK (card_limit_cents > 0),
    limit_period TEXT NOT NULL CHECK (limit_period IN ('daily', 'monthly'))
);
CREATE TABLE card_locks (
    card_id TEXT NOT NULL REFERENCES cards (card_id),
    scope TEXT NOT NULL CHECK (scope IN ('overseas', 'online', 'all')),
    locked_at TEXT NOT NULL,
    PRIMARY KEY (card_id, scope)
);
CREATE TABLE loss_reports (
    loss_report_id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id TEXT NOT NULL REFERENCES cards (card_id),
    reported_at TEXT NOT NULL,
    withdrawn_at TEXT
)
"""


def seed(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("CARD-LM-001", "CUST-LM", "ACC-LM-001", "debit", "工商借记卡 Debit card",
             "1234", "active", 500000, "daily"),
            ("CARD-LM-002", "CUST-LM", "ACC-LM-001", "debit", "招商借记卡 Debit card",
             "5678", "active", 200000, "daily"),
            ("CARD-LM-003", "CUST-LM", "ACC-LM-001", "credit", "信用卡 Credit card",
             "9012", "active", 2000000, "monthly"),
            ("CARD-HM-001", "CUST-HM", "ACC-HM-001", "debit", "工商借记卡 Debit card",
             "3456", "active", 500000, "daily"),
        ],
    )
    # CARD-LM-002 is already under a Card Lock: no overseas transactions.
    conn.execute(
        "INSERT INTO card_locks VALUES (?, ?, ?)",
        ("CARD-LM-002", "overseas", "2026-09-01T10:00:00+08:00"),
    )


# --- Shared helpers (also used by the Red card Operations, #15) ------------------------


def own_card(ctx: OperationContext, card_id: str) -> sqlite3.Row:
    """The Customer's card. The gate has already refused another Customer's card and an
    unknown one (`CardId`); the query is still scoped by the Customer, as every handler's is."""
    row: sqlite3.Row | None = ctx.conn.execute(
        "SELECT * FROM cards WHERE card_id = ? AND customer_id = ?", (card_id, ctx.customer_id)
    ).fetchone()
    if row is None:
        raise Refusal(RefusalReason.NOT_FOUND, "No such card", {"card_id": card_id})
    return row


def locks_on(conn: sqlite3.Connection, card_id: str) -> list[LockScope]:
    rows = conn.execute(
        "SELECT scope FROM card_locks WHERE card_id = ?"
        " ORDER BY CASE scope WHEN 'overseas' THEN 1 WHEN 'online' THEN 2 ELSE 3 END",
        (card_id,),
    ).fetchall()
    return [row["scope"] for row in rows]


# --- Models ----------------------------------------------------------------------------


class CardView(BaseModel):
    card_id: str
    kind: CardKind
    name: str
    masked_number: str  # only the last 4 digits are ever shown
    status: CardStatus
    card_limit: Decimal
    limit_period: LimitPeriod
    locks: list[LockScope]  # empty when the card is not under a Card Lock
    linked_account_id: str


def _view(conn: sqlite3.Connection, row: sqlite3.Row) -> CardView:
    return CardView(
        card_id=row["card_id"],
        kind=row["kind"],
        name=row["name"],
        masked_number=f"**** **** **** {row['last4']}",
        status=row["status"],
        card_limit=from_cents(row["card_limit_cents"]),
        limit_period=row["limit_period"],
        locks=locks_on(conn, row["card_id"]),
        linked_account_id=row["account_id"],
    )


class ListCardsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListCardsOutput(BaseModel):
    cards: list[CardView]


class ApplyVirtualCardInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="虚拟卡 Virtual card", min_length=1, max_length=30)


class ApplyVirtualCardOutput(BaseModel):
    card: CardView


class LowerCardLimitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card_id: CardId
    new_limit: Amount


class LowerCardLimitOutput(BaseModel):
    card_id: str
    previous_limit: Decimal
    card_limit: Decimal
    limit_period: LimitPeriod


class AddCardLockInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card_id: CardId
    scope: LockScope


class AddCardLockOutput(BaseModel):
    card_id: str
    scope: LockScope
    locks: list[LockScope]  # every lock now on the card


# Red Operations (#15). They undo the Yellow ones above, and each other.


class RaiseCardLimitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card_id: CardId
    new_limit: Amount


class RaiseCardLimitOutput(BaseModel):
    card_id: str
    previous_limit: Decimal
    card_limit: Decimal
    limit_period: LimitPeriod


class LiftCardLockInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card_id: CardId
    scope: LockScope


class LiftCardLockOutput(BaseModel):
    card_id: str
    scope: LockScope
    locks: list[LockScope]  # every lock still on the card


class FileLossReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card_id: CardId


class FileLossReportOutput(BaseModel):
    card_id: str
    status: CardStatus  # "disabled" from now until the Loss Report is withdrawn
    reported_at: datetime


class WithdrawLossReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card_id: CardId


class WithdrawLossReportOutput(BaseModel):
    card_id: str
    status: CardStatus  # "active" again


# --- Operations ------------------------------------------------------------------------


def list_cards(ctx: OperationContext, _: ListCardsInput) -> ListCardsOutput:
    rows = ctx.conn.execute(
        "SELECT * FROM cards WHERE customer_id = ? ORDER BY card_id", (ctx.customer_id,)
    ).fetchall()
    return ListCardsOutput(cards=[_view(ctx.conn, row) for row in rows])


def _virtual_card_count(ctx: OperationContext) -> int:
    row = ctx.conn.execute(
        "SELECT COUNT(*) FROM cards WHERE customer_id = ? AND kind = 'virtual'",
        (ctx.customer_id,),
    ).fetchone()
    return int(row[0])


def _check_can_apply(ctx: OperationContext) -> sqlite3.Row:
    account = ctx.conn.execute(
        "SELECT account_id FROM accounts WHERE customer_id = ? AND kind = 'current'"
        " ORDER BY account_id LIMIT 1",
        (ctx.customer_id,),
    ).fetchone()
    if _virtual_card_count(ctx) >= MAX_VIRTUAL_CARDS:
        raise Refusal(
            RefusalReason.INVALID_INPUT,
            f"A Customer can hold at most {MAX_VIRTUAL_CARDS} virtual cards",
            {"max_virtual_cards": MAX_VIRTUAL_CARDS},
        )
    result: sqlite3.Row = account
    return result


def apply_virtual_card_tier(input: ApplyVirtualCardInput, ctx: OperationContext) -> Tier:
    # Refuse an application that can never succeed before the Customer is asked to confirm.
    _check_can_apply(ctx)
    return Tier.YELLOW


def apply_virtual_card(
    ctx: OperationContext, input: ApplyVirtualCardInput
) -> ApplyVirtualCardOutput:
    account = _check_can_apply(ctx)
    count = ctx.conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    card_id = f"CARD-V-{count + 1:06d}"  # IDs are Bank-wide, so this counts every card
    last4 = f"{(7000 + count * 37) % 10000:04d}"
    ctx.conn.execute(
        "INSERT INTO cards VALUES (?, ?, ?, 'virtual', ?, ?, 'active', ?, 'daily')",
        (
            card_id,
            ctx.customer_id,
            account["account_id"],
            input.name,
            last4,
            VIRTUAL_CARD_LIMIT_CENTS,
        ),
    )
    row = ctx.conn.execute("SELECT * FROM cards WHERE card_id = ?", (card_id,)).fetchone()
    return ApplyVirtualCardOutput(card=_view(ctx.conn, row))


def _check_direction(card: sqlite3.Row, new_limit: Decimal, *, raising: bool) -> None:
    """A Card Limit change must go the way its Operation says: lowering is Yellow, raising
    is Red, so neither may be used for the other."""
    current = card["card_limit_cents"]
    wrong_way = to_cents(new_limit) <= current if raising else to_cents(new_limit) >= current
    if wrong_way:
        word, other = ("higher", "lowering") if raising else ("lower", "raising")
        raise Refusal(
            RefusalReason.INVALID_INPUT,
            f"The new Card Limit must be {word} than the current one;"
            f" {other} a Card Limit is a different Operation",
            {
                "card_id": card["card_id"],
                "card_limit": str(from_cents(current)),
                "new_limit": str(new_limit),
            },
        )


def _set_limit(ctx: OperationContext, card: sqlite3.Row, new_limit: Decimal) -> None:
    ctx.conn.execute(
        "UPDATE cards SET card_limit_cents = ? WHERE card_id = ? AND customer_id = ?",
        (to_cents(new_limit), card["card_id"], ctx.customer_id),
    )


def lower_card_limit_tier(input: LowerCardLimitInput, ctx: OperationContext) -> Tier:
    _check_direction(own_card(ctx, input.card_id), input.new_limit, raising=False)
    return Tier.YELLOW


def lower_card_limit(
    ctx: OperationContext, input: LowerCardLimitInput
) -> LowerCardLimitOutput:
    card = own_card(ctx, input.card_id)
    # The limit may have changed since Confirmation, so check again.
    _check_direction(card, input.new_limit, raising=False)
    _set_limit(ctx, card, input.new_limit)
    return LowerCardLimitOutput(
        card_id=card["card_id"],
        previous_limit=from_cents(card["card_limit_cents"]),
        card_limit=input.new_limit,
        limit_period=card["limit_period"],
    )


def _check_not_already_locked(ctx: OperationContext, input: AddCardLockInput) -> sqlite3.Row:
    card = own_card(ctx, input.card_id)
    existing = locks_on(ctx.conn, input.card_id)
    if input.scope in existing or "all" in existing:
        raise Refusal(
            RefusalReason.INVALID_INPUT,
            "The card is already locked for that scope",
            {"card_id": input.card_id, "scope": input.scope, "locks": existing},
        )
    return card


def add_card_lock_tier(input: AddCardLockInput, ctx: OperationContext) -> Tier:
    _check_not_already_locked(ctx, input)
    return Tier.YELLOW


def add_card_lock(ctx: OperationContext, input: AddCardLockInput) -> AddCardLockOutput:
    _check_not_already_locked(ctx, input)
    ctx.conn.execute(
        "INSERT INTO card_locks VALUES (?, ?, ?)",
        (input.card_id, input.scope, ctx.now.isoformat()),
    )
    return AddCardLockOutput(
        card_id=input.card_id, scope=input.scope, locks=locks_on(ctx.conn, input.card_id)
    )


# --- Red Operations (#15) --------------------------------------------------------------
# Each tier rule refuses what can never run (unknown card, nothing to lift, ...), so the
# Customer is never sent a verification code for it. The handlers re-check, because state
# can change between Strong Verification and execution.


def _check_card_in_use(card: sqlite3.Row) -> None:
    """A card under a Loss Report is disabled. Loosening it (a higher limit, a lifted lock)
    waits until the report is withdrawn; tightening it (lower limit, new lock) is fine."""
    if card["status"] == "disabled":
        raise Refusal(
            RefusalReason.INVALID_INPUT,
            "The card is disabled by a Loss Report; withdraw the Loss Report first",
            {"card_id": card["card_id"], "status": card["status"]},
        )


def _check_raise(card: sqlite3.Row, new_limit: Decimal) -> None:
    _check_card_in_use(card)
    _check_direction(card, new_limit, raising=True)


def raise_card_limit_tier(input: RaiseCardLimitInput, ctx: OperationContext) -> Tier:
    _check_raise(own_card(ctx, input.card_id), input.new_limit)
    return Tier.RED


def raise_card_limit(
    ctx: OperationContext, input: RaiseCardLimitInput
) -> RaiseCardLimitOutput:
    card = own_card(ctx, input.card_id)
    _check_raise(card, input.new_limit)
    _set_limit(ctx, card, input.new_limit)
    return RaiseCardLimitOutput(
        card_id=card["card_id"],
        previous_limit=from_cents(card["card_limit_cents"]),
        card_limit=input.new_limit,
        limit_period=card["limit_period"],
    )


def _check_locked(ctx: OperationContext, input: LiftCardLockInput) -> None:
    _check_card_in_use(own_card(ctx, input.card_id))
    existing = locks_on(ctx.conn, input.card_id)
    if input.scope not in existing:
        raise Refusal(
            RefusalReason.NOT_FOUND,
            "The card has no such Card Lock",
            {"card_id": input.card_id, "scope": input.scope, "locks": existing},
        )


def lift_card_lock_tier(input: LiftCardLockInput, ctx: OperationContext) -> Tier:
    _check_locked(ctx, input)
    return Tier.RED


def lift_card_lock(ctx: OperationContext, input: LiftCardLockInput) -> LiftCardLockOutput:
    _check_locked(ctx, input)
    ctx.conn.execute(
        "DELETE FROM card_locks WHERE card_id = ? AND scope = ?", (input.card_id, input.scope)
    )
    return LiftCardLockOutput(
        card_id=input.card_id, scope=input.scope, locks=locks_on(ctx.conn, input.card_id)
    )


def _has_open_loss_report(conn: sqlite3.Connection, card_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM loss_reports WHERE card_id = ? AND withdrawn_at IS NULL", (card_id,)
    ).fetchone()
    return row is not None


def _check_can_report(ctx: OperationContext, card_id: str) -> sqlite3.Row:
    card = own_card(ctx, card_id)
    if _has_open_loss_report(ctx.conn, card_id):
        raise Refusal(
            RefusalReason.INVALID_INPUT,
            "A Loss Report is already filed for that card",
            {"card_id": card_id},
        )
    return card


def _check_can_withdraw(ctx: OperationContext, card_id: str) -> sqlite3.Row:
    card = own_card(ctx, card_id)
    if not _has_open_loss_report(ctx.conn, card_id):
        raise Refusal(
            RefusalReason.INVALID_INPUT,
            "There is no Loss Report to withdraw for that card",
            {"card_id": card_id},
        )
    return card


def file_loss_report_tier(input: FileLossReportInput, ctx: OperationContext) -> Tier:
    _check_can_report(ctx, input.card_id)
    return Tier.RED


def file_loss_report(
    ctx: OperationContext, input: FileLossReportInput
) -> FileLossReportOutput:
    card = _check_can_report(ctx, input.card_id)
    # The card is disabled the moment the report is filed.
    ctx.conn.execute(
        "UPDATE cards SET status = 'disabled' WHERE card_id = ? AND customer_id = ?",
        (card["card_id"], ctx.customer_id),
    )
    ctx.conn.execute(
        "INSERT INTO loss_reports (card_id, reported_at) VALUES (?, ?)",
        (card["card_id"], ctx.now.isoformat()),
    )
    return FileLossReportOutput(
        card_id=card["card_id"], status="disabled", reported_at=ctx.now
    )


def withdraw_loss_report_tier(input: WithdrawLossReportInput, ctx: OperationContext) -> Tier:
    _check_can_withdraw(ctx, input.card_id)
    return Tier.RED


def withdraw_loss_report(
    ctx: OperationContext, input: WithdrawLossReportInput
) -> WithdrawLossReportOutput:
    card = _check_can_withdraw(ctx, input.card_id)
    ctx.conn.execute(
        "UPDATE loss_reports SET withdrawn_at = ? WHERE card_id = ? AND withdrawn_at IS NULL",
        (ctx.now.isoformat(), card["card_id"]),
    )
    ctx.conn.execute(
        "UPDATE cards SET status = 'active' WHERE card_id = ? AND customer_id = ?",
        (card["card_id"], ctx.customer_id),
    )
    return WithdrawLossReportOutput(card_id=card["card_id"], status="active")


MODULE = BankModule(
    name="cards",
    schema=SCHEMA,
    seed=seed,
    operations=[
        Operation(
            name="list_cards",
            description_zh="列出当前客户名下所有银行卡的状态、卡片锁定情况和额度。",
            description_en=(
                "List every card the current Customer holds with its status, Card Locks"
                " and Card Limit."
            ),
            input_model=ListCardsInput,
            output_model=ListCardsOutput,
            tier=Tier.GREEN,
            reversibility=ReadOnly(),
            handler=list_cards,
        ),
        Operation(
            name="apply_virtual_card",
            description_zh=(
                "为当前客户申请一张虚拟卡，关联活期账户，初始额度为每日 ¥1,000。"
                "最多持有 3 张虚拟卡。"
            ),
            description_en=(
                "Apply for a virtual card linked to the current account, starting with a"
                " Card Limit of ¥1,000 a day. At most 3 virtual cards."
            ),
            input_model=ApplyVirtualCardInput,
            output_model=ApplyVirtualCardOutput,
            tier=TierRule(
                description_zh="始终为黄色（需要客户确认）。",
                description_en="Always Yellow (needs the Customer's Confirmation).",
                resolve=apply_virtual_card_tier,
            ),
            # There is no Operation to cancel a virtual card in the competition scope.
            reversibility=Irreversible(),
            handler=apply_virtual_card,
        ),
        Operation(
            name="lower_card_limit",
            description_zh=(
                "调低一张银行卡的额度。新额度必须低于当前额度；调高额度是另一项红色操作。"
            ),
            description_en=(
                "Lower a card's Card Limit. The new limit must be below the current one;"
                " raising a Card Limit is a separate Red Operation."
            ),
            input_model=LowerCardLimitInput,
            output_model=LowerCardLimitOutput,
            tier=TierRule(
                description_zh="调低额度为黄色；新额度不低于当前额度会被拒绝（invalid_input）。",
                description_en=(
                    "Yellow when the new limit is lower; refused with invalid_input when"
                    " it is not lower."
                ),
                resolve=lower_card_limit_tier,
            ),
            reversibility=ReversedBy(
                "raise_card_limit",
                lambda input, output: RaiseCardLimitInput(
                    card_id=output.card_id, new_limit=output.previous_limit
                ),
            ),
            handler=lower_card_limit,
        ),
        Operation(
            name="add_card_lock",
            description_zh=(
                "为一张银行卡添加卡片锁定，限制境外、线上或全部交易。可由客户解除（红色操作）。"
            ),
            description_en=(
                "Add a Card Lock that blocks overseas, online or all transactions on a card."
                " The Customer can lift it later (a Red Operation)."
            ),
            input_model=AddCardLockInput,
            output_model=AddCardLockOutput,
            tier=TierRule(
                description_zh="始终为黄色；该范围已被锁定时会被拒绝（invalid_input）。",
                description_en=(
                    "Always Yellow; refused with invalid_input when the card is already"
                    " locked for that scope."
                ),
                resolve=add_card_lock_tier,
            ),
            reversibility=ReversedBy(
                "lift_card_lock",
                lambda input, output: LiftCardLockInput(
                    card_id=output.card_id, scope=output.scope
                ),
            ),
            handler=add_card_lock,
        ),
        Operation(
            name="raise_card_limit",
            description_zh=(
                "调高一张银行卡的额度。新额度必须高于当前额度。属于红色操作，需要强验证（短信验证码）。"
            ),
            description_en=(
                "Raise a card's Card Limit. The new limit must be above the current one."
                " A Red Operation: needs Strong Verification (an SMS code)."
            ),
            input_model=RaiseCardLimitInput,
            output_model=RaiseCardLimitOutput,
            tier=TierRule(
                description_zh=(
                    "始终为红色；新额度不高于当前额度，或该卡已挂失，会被拒绝（invalid_input）。"
                ),
                description_en=(
                    "Always Red; refused with invalid_input when the new limit is not higher"
                    " or the card is disabled by a Loss Report."
                ),
                resolve=raise_card_limit_tier,
            ),
            reversibility=ReversedBy(
                "lower_card_limit",
                lambda input, output: LowerCardLimitInput(
                    card_id=output.card_id, new_limit=output.previous_limit
                ),
            ),
            handler=raise_card_limit,
        ),
        Operation(
            name="lift_card_lock",
            description_zh=(
                "解除一张银行卡上的卡片锁定（境外、线上或全部）。属于红色操作，需要强验证。"
            ),
            description_en=(
                "Lift a Card Lock (overseas, online or all) from a card."
                " A Red Operation: needs Strong Verification."
            ),
            input_model=LiftCardLockInput,
            output_model=LiftCardLockOutput,
            tier=TierRule(
                description_zh=(
                    "始终为红色；该卡没有此锁定时会被拒绝（not_found），该卡已挂失时会被拒绝（invalid_input）。"
                ),
                description_en=(
                    "Always Red; refused with not_found when the card has no such Card Lock,"
                    " and with invalid_input when the card is disabled by a Loss Report."
                ),
                resolve=lift_card_lock_tier,
            ),
            reversibility=ReversedBy(
                "add_card_lock",
                lambda input, output: AddCardLockInput(
                    card_id=output.card_id, scope=output.scope
                ),
            ),
            handler=lift_card_lock,
        ),
        Operation(
            name="file_loss_report",
            description_zh=(
                "为一张银行卡挂失，卡片立即停用，直到撤销挂失。属于红色操作，需要强验证。"
            ),
            description_en=(
                "File a Loss Report for a card. The card is disabled immediately until the"
                " report is withdrawn. A Red Operation: needs Strong Verification."
            ),
            input_model=FileLossReportInput,
            output_model=FileLossReportOutput,
            tier=TierRule(
                description_zh="始终为红色；该卡已挂失时会被拒绝（invalid_input）。",
                description_en=(
                    "Always Red; refused with invalid_input when a Loss Report already stands."
                ),
                resolve=file_loss_report_tier,
            ),
            reversibility=ReversedBy(
                "withdraw_loss_report",
                lambda input, output: WithdrawLossReportInput(card_id=output.card_id),
            ),
            handler=file_loss_report,
        ),
        Operation(
            name="withdraw_loss_report",
            description_zh="撤销一张银行卡的挂失，卡片恢复可用。属于红色操作，需要强验证。",
            description_en=(
                "Withdraw a card's Loss Report so the card works again."
                " A Red Operation: needs Strong Verification."
            ),
            input_model=WithdrawLossReportInput,
            output_model=WithdrawLossReportOutput,
            tier=TierRule(
                description_zh="始终为红色；该卡没有挂失时会被拒绝（invalid_input）。",
                description_en=(
                    "Always Red; refused with invalid_input when the card has no Loss Report."
                ),
                resolve=withdraw_loss_report_tier,
            ),
            reversibility=ReversedBy(
                "file_loss_report",
                lambda input, output: FileLossReportInput(card_id=output.card_id),
            ),
            handler=withdraw_loss_report,
        ),
    ],
)
