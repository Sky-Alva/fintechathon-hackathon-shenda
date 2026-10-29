"""Smart Transfers in the rules Planner: a transfer, a Scheduled Transfer (and cancelling
one) and a Split-bill Request.

The Payee is always found by the Bank's `find_payee`; the rules only pull the Payee's name
out of the Customer's words. Anything missing or ambiguous (no Payee, no amount, "half my
balance", a date that has passed, shares that don't add up) is a `Clarification`.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from agent import planner
from agent.events import ClarificationOption, PlanStep
from agent.rules import slots
from agent.rules.core import Intent, Reply, Request, pattern

MAX_NOTE = 100
LOOKUP = "find_payee"

KINSHIP = {
    "mum": "老妈", "mom": "老妈", "mother": "老妈", "mama": "老妈",
    "dad": "老爸", "father": "老爸", "papa": "老爸",
    "wife": "老婆", "husband": "老公", "landlord": "房东",
}  # fmt: skip
"""English words for the nicknames the Bank knows; the Bank's lookup still decides."""

CHINESE_WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6,
                    "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6}  # fmt: skip
ENGLISH_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
                    "saturday": 5, "sunday": 6}  # fmt: skip
MONTHS = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
          "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
          "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9,
          "sept": 9, "oct": 10, "nov": 11, "dec": 12}  # fmt: skip

NUMERAL = slots.NUMERAL
PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
BANK_ID = re.compile(r"\b[A-Z]{2,}-[A-Z0-9-]+\b", re.IGNORECASE)
VAGUE_AMOUNT = re.compile(
    r"全部|所有|全额|一半|半数|三分之一|剩下|剩余|余额|\ball\b|everything|\bhalf\b|\bremaining\b|balance"
)
USUAL = re.compile(r"老样子|照旧|跟上次一样|和上次一样|同上次|上次那样|\bthe usual\b|\bas usual\b|same as (last|before)")
RECURRING = re.compile(r"每(?!次)|定期|\bevery\b|\bweekly\b|\bmonthly\b|\bdaily\b|\brecurring\b")
SCHEDULING = re.compile(r"预约|预定|定时|\bschedul")
PRONOUN_WORDS = {"她", "他", "它", "对方", "ta", "her", "him", "them", "这位", "同一个人", "同一位"}
PRONOUN = re.compile(r"(?:再|也|继续)?给(她|他|它|对方|ta)|(?:to|send|pay) (her|him|them)\b|给(这位|同一个人|同一位)")


# --- Dates, against the Bank clock ------------------------------------------------------


@dataclass(frozen=True)
class Dates:
    found: list[date]
    """Every distinct date the message names; empty if it names none."""
    rest: str
    """The message with the date words taken out."""


def _weekday(today: date, weekday: int, week: str) -> date:
    monday = today - timedelta(days=today.weekday())
    if week == "next":
        return monday + timedelta(days=7 + weekday)
    if week == "next_next":
        return monday + timedelta(days=14 + weekday)
    if week == "this":
        return monday + timedelta(days=weekday)
    ahead = (weekday - today.weekday()) % 7  # the next one coming, never today
    return today + timedelta(days=ahead or 7)


def _chinese_count(text: str) -> int | None:
    return int(text) if text.isdigit() else slots.chinese_number(text)


def find_dates(text: str, today: date) -> Dates:
    found: list[date] = []
    rest = text

    def take(regex: str, make: Callable[[re.Match[str]], date | None]) -> None:
        nonlocal rest
        for match in list(re.finditer(regex, rest, re.IGNORECASE)):
            try:
                value = make(match)
            except ValueError:  # 2月30日
                value = date.max
            if value is not None and value not in found:
                found.append(value)
        rest = re.sub(regex, " ", rest, flags=re.IGNORECASE)

    take(r"(\d{4})\s*[年/-]\s*(\d{1,2})\s*[月/-]\s*(\d{1,2})\s*[日号]?",
         lambda m: date(int(m[1]), int(m[2]), int(m[3])))  # fmt: skip
    take(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?",
         lambda m: date(today.year, int(m[1]), int(m[2])))  # fmt: skip
    take(r"下个?月\s*(\d{1,2})\s*[日号]",
         lambda m: date(today.year + (today.month == 12), today.month % 12 + 1, int(m[1])))  # fmt: skip
    take(r"大后天", lambda m: today + timedelta(days=3))
    take(r"(?<![大])后天", lambda m: today + timedelta(days=2))
    take(r"明天|明日", lambda m: today + timedelta(days=1))
    take(r"今天|今日", lambda m: today)
    take(r"(下下|下|这|本|)\s*(?:个)?(?:周|星期|礼拜)\s*([一二三四五六日天])",
         lambda m: _weekday(today, CHINESE_WEEKDAYS[m[2]],
                            {"下下": "next_next", "下": "next", "这": "this", "本": "this"}.get(m[1], "")))  # fmt: skip
    take(r"(\d+|[一二两三四五六七八九十]+)\s*天后",
         lambda m: today + timedelta(days=_chinese_count(m[1]) or 0) if _chinese_count(m[1]) else None)  # fmt: skip
    take(r"\bin (\d+) days?\b", lambda m: today + timedelta(days=int(m[1])))
    take(r"\bday after tomorrow\b", lambda m: today + timedelta(days=2))
    take(r"\btomorrow\b", lambda m: today + timedelta(days=1))
    take(r"\btoday\b", lambda m: today)
    names = "|".join(ENGLISH_WEEKDAYS)
    take(rf"\b(next|this|on)?\s*({names})\b",
         lambda m: _weekday(today, ENGLISH_WEEKDAYS[m[2].lower()],
                            "next" if (m[1] or "").lower() == "next" else "this" if (m[1] or "").lower() == "this" else ""))  # fmt: skip
    months = "|".join(sorted(MONTHS, key=len, reverse=True))
    take(rf"\b({months})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b",
         lambda m: date(today.year, MONTHS[m[1].lower()], int(m[2])))  # fmt: skip
    take(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({months})\b",
         lambda m: date(today.year, MONTHS[m[2].lower()], int(m[1])))  # fmt: skip
    return Dates(found, rest)


def today_of(request: Request) -> date | None:
    try:
        return datetime.fromisoformat(request.now).date()
    except ValueError:
        return None


# --- The words of a transfer ------------------------------------------------------------

PAYEE_CHARS = r"[^\s，,。！!？?；;：:\d¥￥$块元的转汇付打给向跟让和与及、]"
ZH_PAYEE = (
    re.compile(rf"(?:转|汇|付|打)(?:账|款|钱)?\s*(?:给|到)\s*(?P<p>{PAYEE_CHARS}{{1,12}})"),
    re.compile(rf"(?:给|向|跟)\s*(?P<p>{PAYEE_CHARS}{{1,12}}?)\s*(?:再|也|又|一共)?\s*(?:转|汇|付|打)"),
    re.compile(rf"(?:给|向)\s*(?P<p>{PAYEE_CHARS}{{1,12}})\s*(?:$|[，,。！!])"),
)
EN_PAYEE_STOP = (
    r"(?=\s+(?:for|with|note|memo|using|from|on|tomorrow|today|next|this|in|at|by|yuan|rmb|cny)\b"
    r"|\s+[¥￥$\d]|[,.;:!?，。]|$)"
)
EN_PAYEE = (
    re.compile(rf"\bto\s+(?P<p>[^,.;:!?\d¥￥$]+?){EN_PAYEE_STOP}", re.IGNORECASE),
    re.compile(
        rf"\b(?:pay|send|transfer|wire)\s+(?P<p>[^,.;:!?\d¥￥$]+?)\s+(?:[¥￥$\d])", re.IGNORECASE
    ),
)
BY_NOTE = re.compile(
    rf"(?:上次|之前|以前|上回)[^，,。]*?(?:付|转|交|给)(?P<n>[^，,。的]{{2,12}}?)的(?:那个|那位|那)?{PAYEE_CHARS}{{1,6}}\s*(?:再|又)?(?:转|汇|付|打)"
)
ZH_NOTE = re.compile(r"(?:备注|附言|留言|注明|用途)\s*(?:为|是|[:：])?\s*[“\"']?(?P<n>.+?)[”\"']?\s*$")
EN_NOTE = re.compile(
    r"\b(?:note|memo|remark|reference|message)\s*[:：]?\s*(?:is\s+)?[“\"']?(?P<n>.+?)[”\"']?\s*$",
    re.IGNORECASE,
)
EN_FOR_NOTE = re.compile(r"\bfor\s+(?P<n>[^,.;]+?)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Transfer:
    """What a transfer request says: each field is None / empty if the Customer didn't say."""

    payee: str | None
    """The Payee as the Customer named them (or a phone number); None if not named."""
    pronoun: bool
    """Meant 'her / him': whoever they paid just now."""
    amount: Decimal | None
    amount_problem: str | None
    """'many' (several amounts), 'vague' (all / half / the balance), or None."""
    note: str
    usual: bool
    """Asked for 'the usual': the amount of the last transfer to the Payee."""
    dates: list[date]
    scheduling: bool
    recurring: bool


def _clean_payee(term: str) -> str:
    term = re.sub(r"^(?:my|the|our)\s+", "", term.strip(), flags=re.IGNORECASE)
    term = re.sub(r"^(?:我的|我们的|我)", "", term).strip(" ，,。")
    return str(KINSHIP.get(term.lower(), term))


def amount_of(text: str) -> tuple[Decimal | None, str | None]:
    """The one amount in `text` (dates, phone numbers and notes already taken out)."""
    text = BANK_ID.sub(" ", PHONE.sub(" ", text))
    found = slots.amounts(text)
    if not found:
        bare = re.search(
            rf"(?:转|汇|付|打)(?:账|款)?\s*(?:了|共)?\s*(?P<n>{NUMERAL}+)(?=$|[\s，,。！!给到的])", text
        )
        number = slots.chinese_number(bare["n"]) if bare else None
        if number:
            found = [Decimal(number)]
    if len(found) > 1 and len(set(found)) > 1:
        return None, "many"
    if not found:
        return None, "vague" if VAGUE_AMOUNT.search(text) else None
    return found[0], None


def parse(request: Request) -> Transfer:
    text = request.matching
    today = today_of(request)
    dates = find_dates(text, today) if today else Dates([], text)
    rest = dates.rest
    note = ""
    explicit = ZH_NOTE.search(rest) or EN_NOTE.search(rest)
    if explicit:
        note = explicit["n"].strip()
        rest = rest[: explicit.start()]
    # The note keeps the Customer's own words, not the lower-cased copy used for matching.
    if note:
        original = _original_note(request.text, note)
        note = original or note
    phone = PHONE.search(rest)
    payee: str | None = None
    by_note = BY_NOTE.search(rest)
    if by_note:  # "the 张伟 I paid the court fee": the past note tells the two apart
        payee = by_note["n"].strip()
    for regex in () if payee else (*ZH_PAYEE, *EN_PAYEE):
        match = regex.search(rest)
        if match and (term := _clean_payee(match["p"])):
            payee = term
            break
    if payee is not None and not note and (match_for := EN_FOR_NOTE.search(rest)):
        if match_for.start() > rest.find(payee):
            note = _original_note(request.text, match_for["n"].strip()) or match_for["n"].strip()
    if phone and (payee is None or payee == phone[0] or not payee):
        payee = phone[0]
    pronoun = payee is not None and payee.lower() in PRONOUN_WORDS
    if pronoun:
        payee = None
    pronoun = pronoun or (payee is None and PRONOUN.search(rest) is not None)
    amount, problem = amount_of(rest if explicit is None else rest)
    return Transfer(
        payee=payee,
        pronoun=pronoun,
        amount=amount,
        amount_problem=problem,
        note=note,
        usual=USUAL.search(text) is not None,
        dates=dates.found,
        scheduling=SCHEDULING.search(text) is not None,
        recurring=RECURRING.search(text) is not None,
    )


def _original_note(original: str, lowered: str) -> str | None:
    """The note as the Customer wrote it (the match was made on a lower-cased copy)."""
    import unicodedata

    plain = unicodedata.normalize("NFKC", original)
    start = plain.lower().rfind(lowered.lower())
    return plain[start : start + len(lowered)] if start >= 0 else None


# --- Which Payee: the Customer's pick, or one the Bank already found ------------------------


def chosen(request: Request) -> str | None:
    """The option the Customer tapped to answer the last question, if they did so after
    their latest message."""
    for message in reversed(request.history):
        if message["role"] == "user" and "<bank_data>" not in message["content"]:
            return None
        if message["role"] == "system":
            found = re.search(r"choosing option \d+ \(([^)]+)\)", str(message["content"]))
            if found:
                return found[1]
    return None


def known_payee(request: Request, term: str | None, pronoun: bool = False) -> str | None:
    """The ID of the Payee the Bank's lookup has already found for `term`, if exactly one
    fits; the Customer's pick settles a name that matched several."""
    pick = chosen(request)
    known = {p["payee_id"]: p for p in request.payees}
    if pronoun and pick is None and len(known) == 1:
        return str(next(iter(known)))
    if pick is not None and pick in known:
        payee = known[pick]
        if pronoun:
            return pick
        names = {str(payee.get("name", "")).lower(), str(payee.get("nickname") or "").lower()}
        if term is not None and term.lower() in names:
            return pick
    if term is None:
        return None
    fits = [
        p["payee_id"]
        for p in request.payees
        if term.lower() in {str(p.get("name", "")).lower(), str(p.get("nickname") or "").lower()}
    ]
    return fits[0] if len(fits) == 1 else None


def looked_up_payee(request: Request, term: str) -> dict[str, Any] | None:
    for result in request.looked_up or []:
        if result["operation"] == LOOKUP and result["input"].get("query") == term:
            output = result.get("output")
            return output["payee"] if output else None
    return None


# --- Asking ---------------------------------------------------------------------------------


def ask(request: Request, zh: str, en: str, options: list[ClarificationOption] | None = None) -> Reply:
    return planner.Clarification(question=request.say(zh, en), options=options or [])


def _who_and_how_much(request: Request, transfer: Transfer) -> Reply | None:
    """The question to ask when the Payee or the amount is missing or unclear, or None."""
    no_payee = transfer.payee is None and not transfer.pronoun
    no_amount = transfer.amount is None and not transfer.usual
    if no_payee and no_amount:
        return ask(request, "您要转给谁，转多少钱？", "Who should I send money to, and how much?")
    if no_payee:
        return ask(request, "您要转给哪一位收款人？", "Which Payee should I send it to?")
    if transfer.amount_problem == "many":
        return ask(request, "您提到了几个金额，这笔要转多少钱？", "Which amount should I send?")
    if no_amount:
        if transfer.amount_problem == "vague":
            return ask(
                request,
                "请告诉我具体的转账金额，我不会按“全部”或“一半”估算。",
                "Please tell me the exact amount; I won't work it out from “all” or “half”.",
            )
        return ask(request, "要转多少钱？", "How much should I send?")
    if len(transfer.note) > MAX_NOTE:
        return ask(request, "备注太长了（最多100个字），请缩短一些。", "That note is too long (100 characters at most). Please shorten it.")
    return None


def _when(request: Request, transfer: Transfer) -> tuple[date | None, Reply | None]:
    """The run date of a Scheduled Transfer, or the question to ask about it."""
    today = today_of(request)
    if transfer.recurring:
        return None, ask(
            request,
            "我只能预约一次性转账。请告诉我具体哪一天转。",
            "I can only schedule a one-off transfer. Which date should it go on?",
        )
    if today is None or not transfer.dates:
        return None, ask(request, "要在哪一天转账？", "Which date should the transfer go on?")
    if len(transfer.dates) > 1:
        return None, ask(request, "您提到了几个日期，要在哪一天转账？", "You mentioned several dates. Which one should it go on?")
    when = transfer.dates[0]
    if when <= today:
        return None, ask(
            request,
            f"预约日期必须在今天（{today.isoformat()}）之后，请告诉我另一个日期。",
            f"A Scheduled Transfer must be after today ({today.isoformat()}). Which other date?",
        )
    return when, None


# --- Intent: transfer (now, or on a date) -------------------------------------------------------


def _step_input(transfer: Transfer, when: date | None, payee_id: str | None) -> dict[str, Any]:
    step: dict[str, Any] = {}
    if payee_id is not None:
        step["payee_id"] = payee_id
    if transfer.amount is not None:
        step["amount"] = slots.money(transfer.amount)
    if when is not None:
        step["run_date"] = when.isoformat()
    step["note"] = transfer.note
    return step


def _operation(transfer: Transfer) -> str:
    wants_a_date = transfer.scheduling or bool(transfer.dates) or transfer.recurring
    return "schedule_transfer" if wants_a_date else "transfer"


def plan_transfer(request: Request) -> Reply:
    transfer = parse(request)
    if (question := _who_and_how_much(request, transfer)) is not None:
        return question
    operation = _operation(transfer)
    when: date | None = None
    if operation == "schedule_transfer":
        when, question = _when(request, transfer)
        if question is not None:
            return question
    payee_id = None if transfer.usual and transfer.amount is None else known_payee(
        request, transfer.payee, transfer.pronoun
    )
    if transfer.pronoun and payee_id is None:
        return _which_of_the_known(request)
    if payee_id is not None:
        step = PlanStep(id="s1", operation=operation, input=_step_input(transfer, when, payee_id), depends_on=[])
        return planner.Plan(intent=operation, steps=[step])
    assert transfer.payee is not None
    return planner.Plan(
        intent=operation,
        steps=[
            PlanStep(id="s1", operation=LOOKUP, input={"query": transfer.payee}, depends_on=[]),
            PlanStep(id="s2", operation=operation, input=_step_input(transfer, when, None), depends_on=["s1"]),
        ],
    )


def _which_of_the_known(request: Request) -> Reply:
    """'Her' with no one, or no single one, just found: ask who."""
    options = [
        ClarificationOption(label=str(p.get("name", p["payee_id"])), value=p["payee_id"])
        for p in request.payees
    ]
    return ask(
        request,
        "您指的是哪一位收款人？请说出姓名或昵称。",
        "Which Payee do you mean? Please give me a name or nickname.",
        options if len(options) > 1 else None,
    )


def complete_transfer(request: Request) -> Reply:
    transfer = parse(request)
    operation = _operation(transfer)
    when: date | None = None
    if operation == "schedule_transfer":
        when, question = _when(request, transfer)
        if question is not None:
            return question
    if transfer.payee is None:  # the message, read alone, names nobody: ask as the first stage does
        return plan_transfer(request)
    payee = looked_up_payee(request, transfer.payee)
    if payee is None:
        return ask(
            request,
            f"银行里没有找到“{transfer.payee}”这位收款人，请告诉我姓名、昵称或手机号。",
            f"The Bank has no Payee matching “{transfer.payee}”. Please give me a name, nickname or phone number.",
        )
    if transfer.amount is None:
        last = payee.get("last_transfer_amount")
        if not transfer.usual or last is None:
            return ask(request, "要转多少钱？", "How much should I send?")
        transfer = Transfer(**{**transfer.__dict__, "amount": Decimal(str(last))})
    step = PlanStep(
        id="s1", operation=operation, input=_step_input(transfer, when, payee["payee_id"]), depends_on=[]
    )
    return planner.Plan(intent=operation, steps=[step])


# --- Intent: cancel a Scheduled Transfer -----------------------------------------------------------


def plan_cancel(request: Request) -> Reply:
    return planner.Plan(
        intent="cancel_scheduled_transfer",
        steps=[
            PlanStep(
                id="s1", operation="list_scheduled_transfers", input={"status": "scheduled"}, depends_on=[]
            ),
            PlanStep(id="s2", operation="cancel_scheduled_transfer", input={}, depends_on=["s1"]),
        ],
    )


def complete_cancel(request: Request) -> Reply:
    listed: list[dict[str, Any]] = []
    for result in request.looked_up or []:
        if result["operation"] == "list_scheduled_transfers" and result.get("output"):
            listed = [
                s for s in result["output"]["scheduled_transfers"] if s["status"] == "scheduled"
            ]
    if not listed:
        return ask(
            request,
            "您目前没有可以取消的预约转账。",
            "You have no Scheduled Transfers that can be cancelled.",
        )
    pick = chosen(request)
    picked = [s for s in listed if s["scheduled_transfer_id"] == pick]
    if len(listed) == 1 or picked:
        target = (picked or listed)[0]
        return planner.Plan(
            intent="cancel_scheduled_transfer",
            steps=[
                PlanStep(
                    id="s1",
                    operation="cancel_scheduled_transfer",
                    input={"scheduled_transfer_id": target["scheduled_transfer_id"]},
                    depends_on=[],
                )
            ],
        )
    return ask(
        request,
        "您要取消哪一笔预约转账？",
        "Which Scheduled Transfer do you want to cancel?",
        [
            ClarificationOption(
                label=f"{s['payee_name']} ¥{s['amount']} {s['run_date']}",
                value=s["scheduled_transfer_id"],
            )
            for s in listed
        ],
    )


# --- Intent: Split-bill Request -----------------------------------------------------------------------

AMOUNT = rf"(?:[¥￥]\s*)?(?P<amt>\d+(?:\.\d+)?|{NUMERAL}+)\s*(?:块钱|块|元|圆|yuan|rmb)?"
NAME = r"[^\s，,。！!？?；;：:\d¥￥$块元的转汇付打给向跟让叫请和与及、各]{1,10}"
TOTAL = (
    re.compile(rf"(?:一共|总共|共计|合计|总计|共|total(?: of)?|totalling)\s*[:：]?\s*{AMOUNT}", re.IGNORECASE),
    re.compile(rf"{AMOUNT}\s*(?:in total|total|altogether|in all)\b", re.IGNORECASE),
)
EACH = re.compile(
    rf"(?:让|叫|请|由|with|between|among)?\s*(?P<names>{NAME}(?:\s*(?:和|与|及|、|，|,|and)\s*{NAME})*)"
    rf"\s*(?:各自|各|each)\s*(?:付|出|承担|pays?|owes?)?\s*{AMOUNT}",
    re.IGNORECASE,
)
ONE = re.compile(
    rf"(?P<name>{NAME}(?: {NAME}){{0,2}}?)\s*(?:付|出|承担|pays?|owes?)\s*{AMOUNT}", re.IGNORECASE
)


def _number(text: str) -> Decimal | None:
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return Decimal(text)
    number = slots.chinese_number(text)
    return Decimal(number) if number else None


def _names(text: str) -> list[str]:
    parts = re.split(r"\s*(?:和|与|及|、|，|,|\band\b)\s*", text)
    return [_clean_payee(re.sub(r"^(?:让|叫|请|由|with|between|among)\s*", "", p, flags=re.I)) for p in parts if p.strip()]


def split_shares(text: str) -> list[tuple[str, Decimal]]:
    """Who pays what, as named: 'A and B each pay 150' or 'A pays 100, B pays 80'."""
    shares: list[tuple[str, Decimal]] = []
    for match in EACH.finditer(text):
        amount = _number(match["amt"])
        if amount:
            shares += [(name, amount) for name in _names(match["names"])]
    if shares:
        return shares
    for match in ONE.finditer(text):
        amount = _number(match["amt"])
        name = re.sub(r"^(?:and|让|叫|请|由)\s*", "", match["name"].strip(), flags=re.I)
        if amount and name:
            shares.append((_clean_payee(name), amount))
    return shares


def split_total(text: str) -> Decimal | None:
    for regex in TOTAL:
        if (match := regex.search(text)) and (total := _number(match["amt"])):
            return total
    return None


def plan_split(request: Request) -> Reply:
    shares = split_shares(request.matching)
    total = split_total(request.matching)
    if not shares:
        return ask(
            request,
            "请告诉我每位收款人各付多少钱，例如“让老婆和陈静各付150”。",
            "Please tell me what each person should pay, for example “Han Meimei and Chen Jing pay 150 each”.",
        )
    if total is None:
        return ask(request, "这笔账一共多少钱？", "What is the total of the bill?")
    names = [name for name, _ in shares]
    if len({n.lower() for n in names}) != len(names):
        return ask(request, "同一位收款人出现了多次，请重新说明各自要付多少。", "A Payee appears more than once. Please say again who pays what.")
    if len(shares) < 2:
        return ask(request, "分摊账单至少需要两位收款人。", "A split needs at least two Payees.")
    if sum(amount for _, amount in shares) != total:
        added = sum(amount for _, amount in shares)
        return ask(
            request,
            f"各人要付的金额加起来是 {added:.2f} 元，和总额 {total:.2f} 元不一致。请告诉我正确的分摊。",
            f"The shares add up to {added:.2f}, not the total {total:.2f}. Please tell me the right split.",
        )
    lookups = [
        PlanStep(id=f"s{i}", operation=LOOKUP, input={"query": name}, depends_on=[])
        for i, name in enumerate(names, 1)
        if known_payee(request, name) is None
    ]
    step = PlanStep(
        id=f"s{len(names) + 1}",
        operation="request_split_bill",
        input={"total": slots.money(total)},
        depends_on=[s.id for s in lookups],
    )
    if not lookups:
        return complete_split(request)
    return planner.Plan(intent="request_split_bill", steps=[*lookups, step])


def complete_split(request: Request) -> Reply:
    shares = split_shares(request.matching)
    total = split_total(request.matching)
    if not shares or total is None:
        return plan_split(request)
    resolved: list[dict[str, str]] = []
    for name, amount in shares:
        payee_id = known_payee(request, name)
        if payee_id is None:
            payee = looked_up_payee(request, name)
            payee_id = payee["payee_id"] if payee else None
        if payee_id is None:
            return ask(
                request,
                f"银行里没有找到“{name}”这位收款人，请告诉我姓名、昵称或手机号。",
                f"The Bank has no Payee matching “{name}”. Please give me a name, nickname or phone number.",
            )
        resolved.append({"payee_id": payee_id, "amount": slots.money(amount)})
    if len({s["payee_id"] for s in resolved}) != len(resolved):
        return ask(request, "其中有两个名字指向同一位收款人，请重新说明。", "Two of those names are the same Payee. Please say again who pays what.")
    return planner.Plan(
        intent="request_split_bill",
        steps=[
            PlanStep(
                id="s1",
                operation="request_split_bill",
                input={"total": slots.money(total), "shares": resolved},
                depends_on=[],
            )
        ],
    )


# --- The intents --------------------------------------------------------------------------------------

SPLIT_WORDS = r"分摊|分账|平摊|均摊|aa制|各付|\bsplit"
CANCEL_WORDS = r"取消|撤销|撤回|\bcancel"

INTENTS = [
    Intent(
        name="transfer",
        label={"zh": "转账", "en": "send a transfer"},
        triggers=(
            pattern(r"转(?!人工|接)|汇款|汇给|打钱|打款|付给|付款给"),
            pattern(r"\b(?:transfer|send|wire)\b|\bschedule\b.*\bto\b|\bpay\s+(?!for\b|off\b|my\b|the\b|bills?\b|back\b)"),
        ),
        excludes=(
            pattern(SPLIT_WORDS),
            pattern(CANCEL_WORDS),
            pattern(r"预留|\breserve"),
            pattern(r"限额|\blimit\b|记录|明细|历史|\bhistory\b|转了|转过|转出去|\bsent\b|多少钱|how much (?:did|have)"),
            pattern(r"人工|客服|\bhuman\b"),
        ),
        plan=plan_transfer,
        complete=complete_transfer,
    ),
    Intent(
        name="cancel_scheduled_transfer",
        label={"zh": "取消预约转账", "en": "cancel a Scheduled Transfer"},
        triggers=(
            pattern(rf"(?:{CANCEL_WORDS}).*(?:预约|定时|scheduled)|(?:预约|定时|scheduled).*(?:{CANCEL_WORDS})"),
        ),
        excludes=(pattern(r"卡|\bcard|订阅|会员|subscription"),),
        plan=plan_cancel,
        complete=complete_cancel,
    ),
    Intent(
        name="split_bill",
        label={"zh": "发起分摊账单", "en": "send a split-bill request"},
        triggers=(pattern(SPLIT_WORDS),),
        excludes=(pattern(CANCEL_WORDS),),
        plan=plan_split,
        complete=complete_split,
    ),
]
