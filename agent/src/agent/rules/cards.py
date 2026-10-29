"""Card Management in the rules Planner: list the cards, add or lift a Card Lock, lower or
raise a Card Limit, make or withdraw a Loss Report.

The Customer names a card the way the Bank listed it: by kind ("信用卡"), bank ("招商"), last
four digits, or number in the list shown to them ("第二张", "#2"). Cards are never taken from
memory: with none listed yet the Plan is `list_cards` plus the step on the card, completed
from the Bank's list in the second stage. A card the message doesn't pin down to one, with
several cards, is a `Clarification` offering the listed cards; a missing amount, scope or
direction is asked too. A card ID the Customer typed goes to the Bank as written (the Bank
refuses a card that isn't theirs); one is never made up.
"""

import re
from decimal import Decimal
from typing import Any

from agent import untrusted
from agent.events import ClarificationOption, PlanStep
from agent.language import Language
from agent.planner import Clarification, Declined, Plan
from agent.rules import slots
from agent.rules.core import Intent, Reply, Request, pattern

CARD_WORD = r"(?=.*(?:卡|\bcards?\b|第.{1,3}[张个]|#\d))"
CARD_ID = re.compile(r"\bCARD-[A-Z]+-\d+\b", re.IGNORECASE)
LAST4 = re.compile(r"(?:尾号|末四位|末4位|后四位|后4位)\s*(\d{4})|(?:ending(?: in| with)?|last 4(?: digits)?(?: of)?)\s*(\d{4})")
NUMBER = re.compile(r"第\s*([一二两三四五六七八九十\d]+)\s*[张个]|#\s*(\d+)")
ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4}
ORDINAL = re.compile(r"\b(first|second|third|fourth|1st|2nd|3rd)\s+(?:one|card)\b")
CHOSEN = re.compile(r"^客户对上面的问题选择了第 \d+ 个选项（(.*?)）", re.DOTALL)

LOWER = re.compile(r"降|调低|减少|减到|下调|\b(lower|reduce|decrease|cut)\b")
RAISE = re.compile(r"调高|提高|提升|增加|上调|升到|\b(raise|increase)\b")
SCOPES = {
    "online": re.compile(r"网上|线上|网购|在线|\bonline\b"),
    "overseas": re.compile(r"境外|海外|国外|\b(overseas|abroad|foreign)\b"),
    "all": re.compile(r"全部|所有|整张|\b(all|completely)\b"),
}
SCOPE_LABELS: dict[str, dict[Language, str]] = {
    "all": {"zh": "全部交易", "en": "all transactions"},
    "overseas": {"zh": "仅境外交易", "en": "overseas transactions only"},
    "online": {"zh": "仅网上支付", "en": "online payments only"},
}
KINDS = {
    "credit": re.compile(r"信用|\bcredit\b"),
    "debit": re.compile(r"借记|储蓄|\bdebit\b"),
    "virtual": re.compile(r"虚拟|\bvirtual\b"),
}

LIFT = r"解锁|解除.*(?:锁|冻结)|(?:锁定?|冻结).{0,4}(?:解除|取消)|取消.*锁|解冻|\bunlock\b|\b(?:lift|remove)\b.*\block\b"
WITHDRAW = (
    r"撤销挂失|取消挂失|解除挂失|撤回挂失|找到了|找回|\bwithdraw\b|"
    r"cancel.*(?:loss|lost)|\bfound\b"
)


def _chosen(request: Request) -> list[str]:
    """The values of the options the Customer tapped since their last message."""
    values: list[str] = []
    for message in reversed(request.history):
        if untrusted.from_customer(message):
            break
        if message["role"] == "system" and (found := CHOSEN.match(message["content"])):
            values.append(found[1])
    return values


def _cards(request: Request) -> list[dict[str, Any]]:
    """The cards the Bank has listed: this turn's look-up, else those listed before."""
    for result in request.looked_up or []:
        if result["operation"] == "list_cards" and result["output"]:
            return list(result["output"]["cards"])
    return [
        {"card_id": c["card_id"], "name": c["name"], "last4": c["last4"]} for c in request.cards
    ]


def _last4(card: dict[str, Any]) -> str:
    return str(card["last4"]) if "last4" in card else str(card["masked_number"])[-4:]


def _kind(card: dict[str, Any]) -> set[str]:
    name = str(card["name"]).lower()
    return {kind for kind, expression in KINDS.items() if expression.search(name)}


def _bank(card: dict[str, Any]) -> str:
    head = str(card["name"]).split()[0]
    return re.sub(r"借记卡|信用卡|虚拟卡|卡", "", head)


def _number(word: str) -> int | None:
    return int(word) if word.isdigit() else slots.chinese_number(word)


def _narrowed(
    text: str, cards: list[dict[str, Any]], listed_before: bool
) -> list[dict[str, Any]] | None:
    """The cards the message could mean; None if it says nothing about which card."""
    found = None
    if (m := NUMBER.search(text)) or (m := ORDINAL.search(text)):
        if listed_before:  # a number means the list the Customer was shown
            word = m.group(1) or m.group(2) if m.re is NUMBER else str(ORDINALS[m.group(1)])
            index = _number(word)
            found = [cards[index - 1]] if index and 1 <= index <= len(cards) else []
        else:
            found = []
    if m := LAST4.search(text):
        digits = m.group(1) or m.group(2)
        found = [c for c in (cards if found is None else found) if _last4(c) == digits]
    kinds = [kind for kind, expression in KINDS.items() if expression.search(text)]
    banks = [c for c in cards if (bank := _bank(c)) and bank in text]
    if kinds or banks:
        pool = cards if found is None else found
        pool = [c for c in pool if not kinds or _kind(c) & set(kinds)]
        if banks:
            pool = [c for c in pool if c in banks]
        found = pool
    return found


def _resolve(request: Request, cards: list[dict[str, Any]], look_back: bool) -> Reply | str:
    """The card_id, or the question that asks which card."""
    listed_before = bool(request.cards)
    for value in _chosen(request):
        if any(c["card_id"] == value for c in cards):
            return value
    texts = [request.matching]
    if look_back:  # "found it, withdraw the report": the card talked about before
        texts += [
            slots.normalise(str(m["content"]))
            for m in reversed(request.history[:-1])
            if untrusted.from_customer(m)
        ]
    candidates = None
    for text in texts:
        candidates = _narrowed(text, cards, listed_before)
        if candidates is not None and (len(candidates) == 1 or text is texts[0]):
            break
    if candidates is None and len(cards) == 1:
        candidates = cards
    if candidates is not None and len(candidates) == 1:
        return str(candidates[0]["card_id"])
    if not cards:
        return Declined(reason="out_of_scope")
    options = candidates or cards
    return Clarification(
        question=request.say("您指的是哪一张卡？", "Which card do you mean?"),
        options=[
            ClarificationOption(label=_label(c, request.language), value=str(c["card_id"]))
            for c in options
        ],
    )


def _label(card: dict[str, Any], language: Language) -> str:
    if language == "en":
        return f"{card['name']}, ending {_last4(card)}"
    return f"{card['name']}（尾号 {_last4(card)}）"


def _step(
    request: Request, intent: str, operation: str, fixed: dict[str, Any], look_back: bool = False
) -> Reply:
    """The Plan for `operation` on the card the message means (`fixed` is the rest of its
    input), or the question that asks which."""
    if (literal := CARD_ID.search(request.text)) is not None:
        step = PlanStep(
            id="s1", operation=operation, input={"card_id": literal[0].upper(), **fixed},
            depends_on=[],
        )
        return Plan(intent=intent, steps=[step])
    listing = PlanStep(id="s1", operation="list_cards", input={}, depends_on=[])
    if request.looked_up is None and not request.cards:
        waiting = PlanStep(id="s2", operation=operation, input=fixed, depends_on=["s1"])
        return Plan(intent=intent, steps=[listing, waiting])
    resolved = _resolve(request, _cards(request), look_back)
    if not isinstance(resolved, str):
        return resolved
    step = PlanStep(
        id="s2", operation=operation, input={"card_id": resolved, **fixed}, depends_on=["s1"]
    )
    return Plan(intent=intent, steps=[step])


def _ask(request: Request, zh: str, en: str, *options: tuple[str, str, str]) -> Clarification:
    return Clarification(
        question=request.say(zh, en),
        options=[
            ClarificationOption(label=request.say(z, e), value=value) for z, e, value in options
        ],
    )


def _scope(request: Request, whole_card_by_default: bool = False) -> str | Clarification:
    text = request.matching
    chosen = [v for v in _chosen(request) if v in SCOPES]
    if chosen:
        return chosen[0]
    named = [scope for scope, expression in SCOPES.items() if expression.search(text)]
    if len(named) == 1:
        return named[0]
    if not named and whole_card_by_default:
        return "all"  # "lock my card" plainly means the whole card
    return _ask(
        request, "要锁定哪类交易？", "Which transactions should the lock cover?",
        *((SCOPE_LABELS[s]["zh"], SCOPE_LABELS[s]["en"], s) for s in SCOPES),
    )


def _lock(request: Request) -> Reply:
    scope = _scope(request, whole_card_by_default=True)
    if isinstance(scope, Clarification):
        return scope
    return _step(request, "add_card_lock", "add_card_lock", {"scope": scope})


def _lift(request: Request) -> Reply:
    scope = _scope(request)
    if isinstance(scope, Clarification):
        return scope
    return _step(request, "lift_card_lock", "lift_card_lock", {"scope": scope})


def _limit(request: Request) -> Reply:
    text = re.sub(
        r"CARD-[A-Z]+-\d+|(?:尾号|末四位|后四位|ending(?: in| with)?)\s*\d{4}|第\s*\d+\s*[张个]|#\s*\d+",
        " ", request.matching, flags=re.IGNORECASE,
    )
    chosen = [v for v in _chosen(request) if v in ("lower", "raise")]
    if chosen:
        direction = chosen[0]
    elif bool(LOWER.search(text)) != bool(RAISE.search(text)):
        direction = "lower" if LOWER.search(text) else "raise"
    else:
        return _ask(
            request, "要调低还是调高额度？", "Do you want to lower or raise the limit?",
            ("调低", "lower it", "lower"), ("调高", "raise it", "raise"),
        )
    found: set[Decimal] = set(slots.amounts(text))
    if len(found) != 1:
        return _ask(
            request,
            "新的额度是多少？例如“把信用卡额度降到5000元”。",
            "What should the new limit be? For example: lower my credit card limit to 5000.",
        )
    operation = f"{direction}_card_limit"
    return _step(request, operation, operation, {"new_limit": slots.money(found.pop())})


def _report(request: Request) -> Reply:
    return _step(request, "file_loss_report", "file_loss_report", {})


def _withdraw(request: Request) -> Reply:
    return _step(request, "withdraw_loss_report", "withdraw_loss_report", {}, look_back=True)


def _apply(request: Request) -> Reply:
    step = PlanStep(id="s1", operation="apply_virtual_card", input={}, depends_on=[])
    return Plan(intent="apply_virtual_card", steps=[step])


def _list(request: Request) -> Reply:
    return Plan(
        intent="list_cards",
        steps=[PlanStep(id="s1", operation="list_cards", input={}, depends_on=[])],
    )


INTENTS = [
    Intent(
        name="apply_virtual_card",
        label={"zh": "申请虚拟卡", "en": "apply for a virtual card"},
        triggers=(pattern(r"申请.*虚拟卡|\bapply\b.*\bvirtual\b|new virtual card"),),
        plan=_apply,
    ),
    Intent(
        name="list_cards",
        label={"zh": "查看银行卡", "en": "list your cards"},
        triggers=(
            pattern(r"(哪些|几张|列出|列一下|有什么|所有).{0,6}卡|^\W*(我的)?(银行卡|卡片)\W*$"),
            pattern(
                r"\b(list|show|view|see)\b.{0,20}\bcards?\b|\b(which|what) cards?\b"
                r"|cards do i have|^\W*my cards\W*$"
            ),
        ),
        excludes=(
            pattern(r"锁|冻结|解|限额|额度|挂失|丢|申请|余额|多少钱|转|虚拟"),
            pattern(
                r"\b(lock|unlock|freeze|limit|lost|stolen|loss|apply|virtual|balance"
                r"|transfer|send)\b"
            ),
        ),
        plan=_list,
    ),
    Intent(
        name="add_card_lock",
        label={"zh": "锁定银行卡", "en": "lock a card"},
        triggers=(pattern(CARD_WORD + r".*(?:锁|冻结|\block\b|\bfreeze\b)"),),
        excludes=(pattern(LIFT), pattern(r"挂失|\b(?:lost|stolen)\b")),
        plan=_lock,
        complete=_lock,
    ),
    Intent(
        name="lift_card_lock",
        label={"zh": "解除卡片锁定", "en": "lift a card lock"},
        triggers=(pattern(CARD_WORD + r".*(?:" + LIFT + ")"),),
        plan=_lift,
        complete=_lift,
    ),
    Intent(
        name="card_limit",
        label={"zh": "调整卡片额度", "en": "change a card limit"},
        triggers=(pattern(CARD_WORD + r".*(?:限额|额度|\blimit\b)"),),
        excludes=(pattern(r"转账限额|transfer limit|余额|\bbalance\b"),),
        plan=_limit,
        complete=_limit,
    ),
    Intent(
        name="file_loss_report",
        label={"zh": "挂失银行卡", "en": "report a card lost"},
        triggers=(
            pattern(r"挂失|\bloss report\b"),
            pattern(CARD_WORD + r".*(?:丢了|丢失|遗失|被盗|弄丢|\b(?:lost|stolen)\b)"),
        ),
        excludes=(pattern(WITHDRAW),),
        plan=_report,
        complete=_report,
    ),
    Intent(
        name="withdraw_loss_report",
        label={"zh": "撤销挂失", "en": "withdraw a loss report"},
        triggers=(pattern(r"撤销挂失|取消挂失|解除挂失|撤回挂失|找回|\bwithdraw\b.*\b(?:loss|lost)\b"
                          r"|cancel.*(?:loss|lost)|卡.*找到了|\bfound\b.*\bcard\b|\bcard\b.*\bfound\b"),),
        plan=_withdraw,
        complete=_withdraw,
    ),
]
