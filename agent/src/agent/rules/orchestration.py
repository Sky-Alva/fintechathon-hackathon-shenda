"""The birthday Plan in the rules Planner: reserve money for the spouse's birthday and order
flowers and a cake for two days before it, as one Plan the Customer confirms once.

First stage: the profile (the spouse's birthday) and the Merchants, both Green. Second
stage: the whole Plan from what the Bank returned.

Choices the Customer didn't make are fixed, plain and shown on the Plan card for them to
confirm or reject: the amount is the one they said, else ¥1,000; the flowers are the first
bouquet the flower shop lists and the cake is the dearest one of the bakery that still fits
the amount together with the flowers; delivery is two days before the birthday (or
tomorrow, if that is already past). If nothing fits, the Customer is asked for a bigger
amount.
"""

import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from agent import untrusted
from agent.birthday import OFFER
from agent.events import PlanStep
from agent.planner import Clarification, Declined, Plan
from agent.rules import slots
from agent.rules.core import Intent, Reply, Request, pattern

DEFAULT_AMOUNT = Decimal(1000)
DELIVER_BEFORE = timedelta(days=2)
NOTICE_WITHIN = timedelta(days=60)
"""A birthday further away than this is not planned for."""

GIFT = ["flowers", "cake"]


def plan(request: Request) -> Plan:
    return Plan(
        intent="为配偶的生日做准备",
        steps=[
            PlanStep(id="s1", operation="query_profile", input={}, depends_on=[]),
            PlanStep(id="s2", operation="list_merchants", input={}, depends_on=[]),
            PlanStep(id="s3", operation="reserve_funds", input={}, depends_on=["s1"]),
            PlanStep(id="s4", operation="order_from_merchant", input={}, depends_on=["s2", "s3"]),
            PlanStep(id="s5", operation="order_from_merchant", input={}, depends_on=["s2", "s3"]),
        ],
    )


def output_of(request: Request, operation: str) -> dict[str, Any] | None:
    return next(
        (r["output"] for r in request.looked_up or [] if r["operation"] == operation and r["output"]),
        None,
    )


def complete(request: Request) -> Reply:
    profile = output_of(request, "query_profile")
    merchants = (output_of(request, "list_merchants") or {}).get("merchants", [])
    spouse = (profile or {}).get("spouse")
    if not spouse or not spouse.get("birthday"):
        return Declined(reason="out_of_scope")
    today = datetime.fromisoformat(request.now).date()
    born = date.fromisoformat(spouse["birthday"])
    coming = next(
        (d for d in (_on(born, today.year), _on(born, today.year + 1)) if d >= today), None
    )
    if coming is None or coming - today > NOTICE_WITHIN:
        return Plan(
            intent="为配偶的生日做准备",
            steps=[],
            note=None,
        )
    amount = _amount(request)
    delivery = coming - DELIVER_BEFORE
    if delivery <= today:
        delivery = today + timedelta(days=1)
    if delivery > coming:
        return Declined(reason="out_of_scope")
    flowers = next((m for m in merchants if m["category"] == "flowers" and m["products"]), None)
    cakes = next((m for m in merchants if m["category"] == "cake" and m["products"]), None)
    if flowers is None or cakes is None:
        return Declined(reason="out_of_scope")
    bouquet = flowers["products"][0]
    left = amount - Decimal(bouquet["price"])
    fitting = [p for p in cakes["products"] if Decimal(p["price"]) <= left]
    if not fitting:
        return Clarification(
            question=request.say(
                f"¥{amount:,.2f} 不够同时订花和蛋糕，您想预留多少？",
                f"¥{amount:,.2f} is not enough for both flowers and a cake. How much should I"
                " set aside?",
            )
        )
    cake = max(fitting, key=lambda p: Decimal(p["price"]))
    name = spouse["name"]
    note = request.say(
        f"{name}的生日是 {coming.month}月{coming.day}日，还有 {(coming - today).days} 天。",
        f"{name}'s birthday is on {coming:%B} {coming.day} ({coming.isoformat()}),"
        f" in {(coming - today).days} days.",
    )
    return Plan(
        intent="为配偶的生日做准备",
        note=note,
        steps=[
            PlanStep(
                id="s3",
                operation="reserve_funds",
                input={
                    "amount": slots.money(amount),
                    "purpose": request.say(f"{name}生日礼物", f"{name}'s birthday gift"),
                    "merchant_categories": GIFT,
                },
                depends_on=["s1"],
            ),
            _order(flowers, bouquet, delivery, "s4"),
            _order(cakes, cake, delivery, "s5"),
        ],
    )


def _on(born: date, year: int) -> date:
    try:
        return born.replace(year=year)
    except ValueError:  # 29 February
        return date(year, 3, 1)


def _amount(request: Request) -> Decimal:
    given = slots.amounts(request.matching)
    return given[0] if given else DEFAULT_AMOUNT


def _order(merchant: dict[str, Any], product: dict[str, Any], delivery: date, id: str) -> PlanStep:
    return PlanStep(
        id=id,
        operation="order_from_merchant",
        input={
            "merchant_id": merchant["merchant_id"],
            "product_id": product["product_id"],
            "quantity": 1,
            "amount": slots.money(Decimal(product["price"])),
            "delivery_date": delivery.isoformat(),
        },
        depends_on=["s2", "s3"],
    )


CATEGORY_WORDS = {"flowers": r"花|\bflowers?\b", "cake": r"蛋糕|\bcakes?\b"}
DELIVERY = r"送到|送达|送去|订|\border\b|deliver"
"""The Customer asked for the orders too, not only for money to be set aside."""
RESERVE_ONLY = rf"^(?!.*(?:{DELIVERY})).*(?:预留|\breserve)"

ORDER = "order_from_merchant"
ASKS_FOR_THE_PLAN = (
    pattern(r"需要我安排|要我安排|有什么(要|需要)?安排"),
    pattern(r"生日.*(帮我|替我|安排|准备|订)"),
    pattern(r"birthday.*(arrange|prepare|order|organi[sz]e)"),
    pattern(r"anything.*(arrange|need me to)"),
)
"""Wording that clearly asks for the birthday to be arranged, narrower than the `birthday`
intent's triggers: a question about a birthday gift is left to the model."""
ONLY_ASKS_ABOUT = pattern(r"查|多少|哪天|几号|什么时候|转|汇|\b(transfer|send|how much|when)\b")
PLAIN_YES = pattern(
    r"(?:好的?|好啊|好呀|行|可以|嗯+|要|需要|是的|对|ok|okay|yes|yep|sure)?[\s,，.。!！~]*"
    r"(?:(?:帮我|请)?(?:安排|准备|订)(?:一下|好)?|谢谢|please|do it|go ahead|thanks)?[吧呀啊]?[\s,，.。!！~]*"
)


def asks_for_the_birthday_plan(matching: str) -> bool:
    return (
        any(p.search(matching) for p in ASKS_FOR_THE_PLAN)
        and not ONLY_ASKS_ABOUT.search(matching)
        and not re.search(RESERVE_ONLY, matching, re.IGNORECASE)
    )


def accepts_the_reminder(history: list[dict[str, Any]], matching: str) -> bool:
    """The Customer's latest message is a plain yes, and nothing but the birthday reminder's
    offer was shown to them since their message before it. The offer comes from the Agent,
    never the model or the Customer, and a yes still only gets a Plan card to confirm."""
    if not re.search(r"\w", matching) or PLAIN_YES.fullmatch(matching.strip()) is None:
        return False
    customer = [i for i, m in enumerate(history) if untrusted.from_customer(m)]
    since = history[customer[-2] + 1 if len(customer) > 1 else 0 : customer[-1]] if customer else []
    return any(offer in str(m["content"]) for m in since for offer in OFFER.values())


def plan_reserve(request: Request) -> Reply:
    """Reserve Funds the Customer asked for, and nothing else: no orders."""
    given = slots.amounts(request.matching)
    categories = [
        c for c, words in CATEGORY_WORDS.items() if re.search(words, request.matching)
    ]
    if len(given) != 1:
        return Clarification(
            question=request.say(
                "您想预留多少钱？" if not given else "您提到了几个金额，要预留多少钱？",
                "How much would you like to set aside?"
                if not given
                else "You mentioned several amounts. How much should I set aside?",
            )
        )
    if not categories:
        return Clarification(
            question=request.say(
                "这笔钱用来买什么？例如鲜花或蛋糕。",
                "What is the money for? For example flowers or a cake.",
            )
        )
    gift = "生日" in request.matching or "birthday" in request.matching
    return Plan(
        intent="预留资金",
        steps=[
            PlanStep(
                id="s1",
                operation="reserve_funds",
                input={
                    "amount": slots.money(given[0]),
                    "purpose": request.say(
                        "生日礼物" if gift else "预留资金", "birthday gift" if gift else "reserved"
                    ),
                    "merchant_categories": categories,
                },
                depends_on=[],
            )
        ],
    )


INTENTS = [
    Intent(
        name="reserve",
        label={"zh": "预留资金", "en": "set money aside"},
        triggers=(pattern(r"预留|\breserve\b"),),
        excludes=(pattern(DELIVERY), pattern(r"转|汇|\b(transfer|send)\b|释放|release")),
        plan=plan_reserve,
    ),
    Intent(
        name="birthday",
        label={"zh": "为配偶的生日做准备", "en": "prepare for your spouse's birthday"},
        triggers=(
            pattern(r"需要我安排|要我安排|有什么(要|需要)?安排"),
            pattern(r"生日.*(准备|礼物|预留|鲜花|蛋糕|安排)|(准备|礼物|预留|鲜花|蛋糕|安排).*生日"),
            pattern(r"birthday.*(gift|flowers?|cake|prepare|arrange|plan|present)"),
            pattern(r"(anything|what).*(arrange|plan|prepare|need me to)"),
        ),
        excludes=(pattern(r"转|汇|\b(transfer|send)\b"), pattern(RESERVE_ONLY)),
        plan=plan,
        complete=complete,
    ),
]
