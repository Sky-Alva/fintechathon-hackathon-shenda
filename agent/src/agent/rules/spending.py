"""Bill Analysis and Subscriptions for the rules Planner: the spending report, monthly and
annual reports (the Channel draws them), Unusual Transactions, recurring charges, listing
Subscriptions, renewals, the cancel-unneeded offer and cancelling by name or by reference
to the Bank's list ("取消第一个").

A Subscription's ID always comes from the Bank's own list (`request.subscriptions`, or the
look-up the first stage plans), never from the Customer's words. A period, a month or a
Subscription that is missing or unclear is a `Clarification`.
"""

import calendar
import re
from datetime import date, datetime
from typing import Any

from agent.events import ClarificationOption, PlanStep
from agent.planner import CancellationsAsked, Clarification, Plan
from agent.rules.core import Intent, Reply, Request, pattern
from agent.rules.slots import chinese_number
from agent.subscriptions import CANCEL, LIST, Listed

MONTH_NAMES = {
    name: number
    for number, names in enumerate(
        [
            ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
            ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
            ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for name in names
}
ORDINALS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4,
    "4th": 4, "fifth": 5, "5th": 5,
}
CATEGORY_WORDS = {"音乐": "music", "music": "music", "视频": "video", "video": "video"}


# --- Slots -----------------------------------------------------------------------------


def chosen(request: Request) -> str | None:
    """The value of the option the Customer just tapped on a question this module asked."""
    if not request.history or request.history[-1]["role"] != "system":
        return None
    found = re.search(r"choosing option \d+ \((.*)\)\.?\s*$", str(request.history[-1]["content"]))
    return found[1] if found else None


def today(request: Request) -> date:
    return datetime.fromisoformat(request.now).date()


def previous_month(day: date) -> tuple[int, int]:
    return (day.year, day.month - 1) if day.month > 1 else (day.year - 1, 12)


def year_of(request: Request) -> int | None:
    """The year the Customer wrote, or "this year" / "last year"."""
    text = request.matching
    if found := re.search(r"(20\d\d)", text):
        return int(found[1])
    if re.search(r"去年|last year", text):
        return today(request).year - 1
    if re.search(r"今年|this year", text):
        return today(request).year
    return None


def month_of(request: Request) -> tuple[int, int] | None | str:
    """The (year, month) the Customer names: "这个月", "上个月", "九月份", "september", with an
    optional year. None if they name none; "year?" if the month is still ahead this year
    and they gave no year (this year's or last year's: never guessed)."""
    text = request.matching
    now = today(request)
    if re.search(r"上个?月|last month|previous month", text):
        return previous_month(now)
    if re.search(r"这个月|本月|当月|this month", text):
        return now.year, now.month
    number: int | None = None
    if found := re.search(r"(\d{1,2})\s*月", text):
        number = int(found[1])
    elif found := re.search(r"([一二三四五六七八九十]{1,3})\s*月", text):
        number = chinese_number(found[1])
    elif found := re.search(
        r"\b(" + "|".join(sorted(MONTH_NAMES, key=len, reverse=True)) + r")\b", text
    ):
        number = MONTH_NAMES[found[1]]
    if number is None or not 1 <= number <= 12:
        return None
    year = year_of(request)
    if year is None:
        value = chosen(request) or ""
        if value.isdigit():
            return int(value), number
        if number > now.month:
            return "year?"
        year = now.year
    return year, number


def ask_year(request: Request) -> Clarification:
    year = today(request).year
    return Clarification(
        question=request.say("您指的是哪一年？", "Which year do you mean?"),
        options=[
            ClarificationOption(label=request.say(f"{y}年", str(y)), value=str(y))
            for y in (year, year - 1)
        ],
    )


def ask_month(request: Request) -> Clarification:
    return Clarification(
        question=request.say("您想看哪个月的？", "Which month would you like?"),
        options=[
            ClarificationOption(label=request.say("这个月", "This month"), value="this_month"),
            ClarificationOption(label=request.say("上个月", "Last month"), value="last_month"),
        ],
    )


def period(request: Request) -> tuple[int, int] | Clarification:
    """The month asked for (a month named, or the option chosen), or the question to ask."""
    found = month_of(request)
    if found == "year?":
        return ask_year(request)
    if isinstance(found, tuple):
        return found
    now = today(request)
    return {"this_month": (now.year, now.month), "last_month": previous_month(now)}.get(
        chosen(request) or "", ask_month(request)
    )


def one(operation: str, input: dict[str, Any], intent: str | None = None) -> Plan:
    return Plan(
        intent=intent or operation,
        steps=[PlanStep(id="s1", operation=operation, input=input, depends_on=[])],
    )


# --- Bill Analysis ---------------------------------------------------------------------


def spending_report(request: Request) -> Reply:
    """A month named (up to today for this month) or a year; else ask for the month."""
    now = today(request)
    found = period(request) if month_of(request) is not None else None
    if found is None and (year := year_of(request)) is not None and year <= now.year:
        end = now if year == now.year else date(year, 12, 31)
        return one(
            "spending_by_category",
            {"start_date": date(year, 1, 1).isoformat(), "end_date": end.isoformat()},
        )
    if found is None:
        found = period(request)
    if isinstance(found, Clarification):
        return found
    year, month = found
    to_today = re.search(r"这个月|本月|当月|this month", request.matching) or (
        chosen(request) == "this_month"
    )  # "this month" runs to today; a month named (even this one) is the whole month
    end = now if to_today else date(year, month, calendar.monthrange(year, month)[1])
    return one(
        "spending_by_category",
        {"start_date": date(year, month, 1).isoformat(), "end_date": end.isoformat()},
    )


def monthly_report(request: Request) -> Reply:
    found = period(request)
    if isinstance(found, Clarification):
        return found
    year, month = found
    return one("monthly_report", {"year": year, "month": month})


def annual_report(request: Request) -> Reply:
    """A year named, or this year: "每个月的收入和支出" is a chart of the Bank's year."""
    return one("annual_report", {"year": year_of(request) or today(request).year})


def unusual(request: Request) -> Reply:
    return one("detect_unusual_transactions", {})


def recurring(request: Request) -> Reply:
    return one("detect_recurring_charges", {})


# --- Subscriptions ---------------------------------------------------------------------


def list_subscriptions(request: Request) -> Reply:
    return one(LIST, {})


def ask_days(request: Request) -> Clarification:
    return Clarification(
        question=request.say("您想看未来多少天内的续费？", "How far ahead should I look?"),
        options=[
            ClarificationOption(label=request.say(f"{n}天内", f"Next {n} days"), value=str(n))
            for n in (7, 14, 30)
        ],
    )


def renewals(request: Request) -> Reply:
    text = request.matching
    if re.search(r"这周|本周|这星期|本星期|this week", text):
        days = 7
    elif found := re.search(r"(\d+)\s*天|next (\d+) days", text):
        days = int(found[1] or found[2])
    elif (value := chosen(request)) in ("7", "14", "30"):
        days = int(str(value))
    else:
        return ask_days(request)
    if not 0 <= days <= 31:  # the Bank looks at most 31 days ahead
        return ask_days(request)
    return one("renewal_reminders", {"days_ahead": days})


def offer_cancellations(request: Request) -> Reply:
    return CancellationsAsked()


def ordinals(text: str) -> list[int]:
    """Positions in the Bank's list the Customer refers to: 第一个, 第2个, the second one.
    -1 is the last."""
    found = [
        int(m[1]) if m[1].isdigit() else ORDINALS[m[1]]
        for m in re.finditer(r"第\s*(\d+|[一二两三四五六七八九])\s*(?:个|项|条|款|份)?", text)
    ]
    found += [
        ORDINALS[m[1]]
        for m in re.finditer(r"\b(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th)\b", text)
    ]
    if re.search(r"最后一?个|\blast one\b|\bthe last\b", text):
        found.append(-1)
    return found


def resolve(request: Request, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The Subscriptions among `items` (the Bank's list, in its order, each with at least
    its ID and merchant) that the option just chosen or the Customer's words point to, by
    the chosen option, by name, by position in the list, or by kind (音乐). Empty if none."""
    by_id = {item["subscription_id"]: item for item in items}
    if (value := chosen(request)) in by_id:
        return [by_id[str(value)]]
    text = request.matching
    squashed = re.sub(r"\s+", "", text)
    named = [i for i in items if re.sub(r"\s+", "", str(i["merchant"]).lower()) in squashed]
    if named:
        return named
    numbered = [
        items[position - 1 if position > 0 else position]
        for position in ordinals(text)
        if position != 0 and -len(items) <= (position - 1 if position > 0 else position) < len(items)
    ]
    if numbered:
        return list({i["subscription_id"]: i for i in numbered}.values())
    of_kind = of_kind_named(text, items)
    return of_kind if len(of_kind) == 1 else []  # two of a kind is a question, not a pick


def of_kind_named(text: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The Subscriptions of a kind the Customer names (音乐), if they name one."""
    wanted = {kind for word, kind in CATEGORY_WORDS.items() if word in text}
    return [i for i in items if i.get("category") in wanted] if wanted else []


def cancel_plan(items: list[dict[str, Any]], first: int = 1) -> Plan:
    """One cancellation per Subscription and a read-back of what the Bank shows."""
    cancels = [
        PlanStep(
            id=f"s{n}",
            operation=CANCEL,
            input={"subscription_id": item["subscription_id"]},
            depends_on=[],
        )
        for n, item in enumerate(items, start=first)
    ]
    read_back = PlanStep(
        id=f"s{first + len(cancels)}", operation=LIST, input={},
        depends_on=[c.id for c in cancels],
    )
    return Plan(intent="cancel_subscriptions", steps=[*cancels, read_back])


def which_subscription(request: Request, items: list[dict[str, Any]]) -> Clarification:
    """Ask which of the active Subscriptions the Bank listed."""
    options = [
        ClarificationOption(
            label=Listed(
                subscription_id=item["subscription_id"],
                merchant=item["merchant"],
                current_amount=str(item["current_amount"]),
                next_renewal_date=item.get("next_renewal_date"),
            ).label(request.language),
            value=item["subscription_id"],
        )
        for item in items
        if item["status"] == "active"
    ]
    return Clarification(
        question=request.say("您指的是哪一个订阅？", "Which Subscription do you mean?"),
        options=options,
    )


def cancel(request: Request) -> Reply:
    """First stage. If the Subscriptions the Bank listed in this conversation point to the
    one meant, plan its cancellation; otherwise list them first (the Bank's list is the
    only source of an ID) and finish in `cancel_listed`."""
    if picked := resolve(request, request.subscriptions):
        return cancel_plan(picked)
    return Plan(
        intent="cancel_subscriptions",
        steps=[
            PlanStep(id="s1", operation=LIST, input={}, depends_on=[]),
            PlanStep(id="s2", operation=CANCEL, input={}, depends_on=["s1"]),
        ],
    )


def cancel_listed(request: Request) -> Reply:
    """Second stage, with the Bank's list: the Subscription the Customer's words point to,
    or the question which one."""
    listing = next(
        (
            r["output"]["subscriptions"]
            for r in reversed(request.looked_up or [])
            if r["operation"] == LIST and r["output"] is not None
        ),
        None,
    )
    if listing is None:
        return Plan(intent="cancel_subscriptions", steps=[])
    picked = [s for s in resolve(request, listing) if s["status"] == "active"]
    if not picked:
        if not any(s["status"] == "active" for s in listing):
            return Plan(intent="cancel_subscriptions", steps=[])  # nothing active to cancel
        return which_subscription(
            request, of_kind_named(request.matching, listing) or listing
        )
    return cancel_plan(picked, first=2)


CANCEL_VERB = r"取消|退订|退掉|退了|退出|终止|关掉|\bcancel|unsubscribe"
SUBSCRIPTION_WORD = r"订阅|会员|subscription|membership"
UNNEEDED = (
    r"用不上|用不到|不用的|不使用|不需要|不再需要|没用|没在用|不要的|多余|没必要|"
    r"unneeded|unused|don'?t (use|need)|not using|no longer (use|need)"
)
ANNUAL = (
    r"(?=.*(每个月|每月|按月|逐月|月度|全年|年度|month[- ]by[- ]month|monthly|each month"
    r"|every month|per month))"
    r"(?=.*(收入|支出|收支|图表|income|spending|expense|chart|graph))"
)

INTENTS = [
    Intent(
        name="spending_report",
        label={"zh": "查看消费分类", "en": "see where your money went"},
        triggers=(
            pattern(r"花哪|花到哪|花在哪|花去哪|钱.{0,4}去哪|各类|分类|类别|消费构成|支出构成"),
            pattern(
                r"where did (all )?my money go|spend(ing)? (by|per) category|"
                r"spend(ing)? breakdown|categories|what did i spend"
            ),
        ),
        excludes=(pattern(ANNUAL), pattern(r"转账|转给|\btransfer\b")),
        plan=spending_report,
    ),
    Intent(
        name="monthly_report",
        label={"zh": "查看月度收支报告", "en": "see a monthly income and spending report"},
        triggers=(
            pattern(r"月报|收支|收入和支出|收入与支出"),
            pattern(r"monthly report|income and (spending|expenses?)"),
        ),
        excludes=(pattern(ANNUAL),),
        plan=monthly_report,
    ),
    Intent(
        name="annual_report",
        label={"zh": "查看全年每月收支图表", "en": "see a month-by-month chart for the year"},
        triggers=(pattern(ANNUAL), pattern(r"年报|annual report|yearly report")),
        plan=annual_report,
    ),
    Intent(
        name="unusual_transactions",
        label={"zh": "查看异常交易", "en": "check for unusual transactions"},
        triggers=(
            pattern(
                r"(异常|可疑|不寻常|盗刷|陌生).{0,8}(交易|消费|扣款|支出|账单|记录)|"
                r"(交易|消费|扣款).{0,8}(异常|可疑)"
            ),
            pattern(r"unusual|suspicious|anomal|fraud(ulent)? (charge|transaction)"),
        ),
        plan=unusual,
    ),
    Intent(
        name="recurring_charges",
        label={"zh": "查看固定扣款", "en": "see your recurring charges"},
        triggers=(
            pattern(r"固定扣款|定期扣款|固定支出|周期性扣款|自动扣款"),
            pattern(r"recurring (charges?|payments?)|regular (charges?|payments?)"),
        ),
        excludes=(pattern(CANCEL_VERB),),
        plan=recurring,
    ),
    Intent(
        name="list_subscriptions",
        label={"zh": "列出您的订阅", "en": "list your Subscriptions"},
        triggers=(
            pattern(
                rf"({SUBSCRIPTION_WORD}).{{0,12}}(哪些|什么|列|查|看|多少|有没有)|"
                rf"(哪些|什么|列出|查看|查一下|看看|看一下).{{0,12}}({SUBSCRIPTION_WORD})"
            ),
            pattern(
                r"(list|show|what|which|see|check|view).{0,20}(subscriptions?|memberships?)|"
                r"(my|any) (subscriptions?|memberships?)"
            ),
        ),
        excludes=(
            pattern(CANCEL_VERB),
            pattern(r"续费|到期|renew"),
            pattern(r"固定扣款|定期扣款|recurring"),
        ),
        plan=list_subscriptions,
    ),
    Intent(
        name="renewals",
        label={"zh": "查看即将续费的订阅", "en": "see Subscriptions that renew soon"},
        triggers=(pattern(r"续费|快到期|即将到期|扣费提醒"), pattern(r"\brenew(al|s|ing)?\b")),
        excludes=(pattern(CANCEL_VERB),),
        plan=renewals,
    ),
    Intent(
        name="cancel_unneeded",
        label={"zh": "取消用不上的订阅", "en": "cancel the Subscriptions you don't need"},
        triggers=(
            pattern(
                rf"({CANCEL_VERB}).{{0,30}}({UNNEEDED}).{{0,30}}({SUBSCRIPTION_WORD})|"
                rf"({UNNEEDED}).{{0,30}}({SUBSCRIPTION_WORD}).{{0,30}}({CANCEL_VERB})"
            ),
        ),
        plan=offer_cancellations,
    ),
    Intent(
        name="cancel_subscription",
        label={"zh": "取消订阅", "en": "cancel a Subscription"},
        triggers=(
            pattern(
                rf"({CANCEL_VERB}).{{0,40}}({SUBSCRIPTION_WORD})|"
                rf"({SUBSCRIPTION_WORD}).{{0,40}}({CANCEL_VERB})"
            ),
            pattern(
                r"(取消|退订|退掉|退了|cancel|unsubscribe).{0,6}"
                r"(第\s*(\d+|[一二两三四五六七八九])|最后一?个|(the )?(first|second|third|last)\b)"
            ),
        ),
        excludes=(
            pattern(UNNEEDED),
            pattern(r"张|\bcards?\b|卡片|银行卡|挂失|冻结"),
            pattern(r"转账|转给|\btransfer\b|\bplan\b|计划"),
        ),
        plan=cancel,
        complete=cancel_listed,
    ),
]
