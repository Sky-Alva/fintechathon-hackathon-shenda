"""The Responder: writes the Customer's answer only from what the Bank returned.

Before an answer is sent, the fact check confirms that every amount, date, card and Payee
it mentions appears in a Bank result from this turn or earlier in the conversation. A
draft that fails is regenerated once; if that fails too, the Customer gets the raw Bank
result instead. So do they when the model can't be asked: what ran is never left unsaid.
The answer never writes out the Agent's internal data either (a `<bank_data>` block, a Bank
result as JSON, a step ID, a card's Bank ID or a Safety Lock ID: `INTERNALS`), and
whatever way it was written, what reaches the Channel has no such block and names cards by
their last four digits (`for_channel`).

When the turn's queries all failed (the Bank refused them or couldn't be reached), the
answer may state no figures at all, not even earlier ones, which may be out of date: it
says the information couldn't be retrieved. A step that would have changed something says
it couldn't be done, and why: the Bank's reason.
"""

import json
import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from agent import failures, untrusted
from agent.cards import Card
from agent.executor import StepResult
from agent.fact_check import Facts, unsupported
from agent.language import Language, customer_language
from agent.model import ModelClient, ModelUnavailable, OfflineModel
from agent.secrets_guard import asks_for_secret

SYSTEM_PROMPT = """\
你是一家银行的 AI 助手（Agent）。根据本轮银行返回的结果，简洁地回答客户。
You are a bank's AI Agent. Answer the Customer briefly from the Bank's results for this turn.

只陈述银行结果中出现的事实（金额、日期、账户、卡、收款人）；不要猜测或编造。
State only facts that appear in the Bank's results (amounts, dates, accounts, cards,
Payees); never guess or make anything up.

{untrusted}

如果查询步骤失败（有 refusal，没有 output），就说明无法获取该信息，不要猜测；如果办理步骤失败，就说明没能办成。
If a query step failed (it has a `refusal` and no `output`), say that information couldn't
be retrieved; never guess it. If a step that changes something failed, say it couldn't be done.

某个步骤被银行拒绝时，只用银行给出的原因（refusal 的 reason，以及 detail.errors 说明的哪个值有什么问题）说明，不要推测其他原因。
When the Bank refused a step, give only the Bank's reason for it (the refusal's `reason`,
and what its `detail.errors` say is wrong with which value); never suggest any other cause.

<bank_data> 块、步骤编号（s1）和银行内部编号（如 CARD-LM-001）只给你看：回答里永远不要写出它们，也不要写 JSON。
The <bank_data> blocks, step IDs (s1) and the Bank's internal IDs (such as CARD-LM-001)
are for you only: never write them, or any JSON, in your answer.

永远不要向客户索要验证码或密码，也不要写出完整卡号（只写后四位）。
Never ask the Customer for a verification code or a password: codes are typed into their
phone only. Never write out a full card number; give its last four digits only.

回答显示在手机聊天气泡里，只能显示纯文本：用简短的句子回答，不要用表格、标题或 ** 加粗等 Markdown 格式。
本轮有 spending_by_category、monthly_report 或 annual_report 的结果时，聊天中已在你的回答上方画出
图表（各类别或各月的金额，饼图或柱状图），所以不要逐项重复这些金额；用一两句话说总数和最值得注意的一两点。
Your answer is shown in a phone's chat bubble as plain text: write short sentences, never
a table, a heading or Markdown such as ** for bold. When this turn has a
spending_by_category, monthly_report or annual_report result, the chat already shows it as
a chart above your answer (each category's or month's amount, as a pie or bars), so don't
list those amounts again: give the totals and the one or two things that stand out.
"""

REPLY_IN: dict[Language, str] = {
    "zh": "请用中文回答。Reply in Chinese.",
    "en": "The Customer writes in English. Reply in English.",
}

REGENERATE = """\
你的回答提到了银行结果中没有的内容：{mentions}。请重写，只陈述银行结果中出现的事实。
Your answer mentioned what no Bank result says: {mentions}. Rewrite it, stating only
facts that appear in the Bank's results.
"""

NEVER_ASK = """\
你的回答向客户索要了验证码或密码：{sentences}。请重写，不要索要验证码或密码。
Your answer asked the Customer for a code or a password: {sentences}. Rewrite it: Never ask
the Customer for a verification code or a password.
"""

INTERNAL = """\
你的回答写出了只给你看的内部数据：{leaks}。客户看不到也不该看到这些。请重写，只用简短的句子，
不要写出 <bank_data> 块、JSON、步骤编号或银行内部编号；卡只用后四位称呼。
Your answer wrote out internal data that is for you only: {leaks}. Rewrite it in short
plain sentences, without a <bank_data> block, JSON, step IDs or the Bank's internal IDs;
name a card by its last four digits only.
"""


@dataclass(frozen=True)
class Internal:
    """Something of the Agent's internal data format that never reaches the Customer."""

    pattern: re.Pattern[str]
    what: dict[Language, str]
    """What it is, for the instruction to rewrite the answer."""


NOT_IN_AN_ID = r"(?<![A-Za-z0-9_-])"
CARD_ID = re.compile(rf"{NOT_IN_AN_ID}CARD-[A-Z0-9]+(?:-[A-Z0-9]+)*")
BANK_DATA_BLOCK = re.compile(r"<bank_data>.*?</bank_data>\s*", re.DOTALL | re.IGNORECASE)
BANK_DATA_TAG = re.compile(r"</?bank_data>\s*", re.IGNORECASE)
LOCK_ID = re.compile(rf"{NOT_IN_AN_ID}LCK-[A-Za-z0-9]*")
STEP_ID = re.compile(
    r"""["'`]s\d+["'`]|(?:(?<![A-Za-z])steps?\s+|步骤\s*)s\d+(?![A-Za-z0-9_-])""", re.IGNORECASE
)
"""A step ID as the model would write it out: quoted, or after "step" / "步骤". A bare s2 in
prose is not one."""
STEP_ID_PAIR = re.compile(r'"step_id"\s*:\s*"[^"]*"\s*,?\s*')
JSON_KEY = re.compile(r'"(step_id|operation|refusal|reversal)"\s*:')
INTERNALS = [
    Internal(
        re.compile(r"</?bank_data", re.IGNORECASE),
        {"zh": "<bank_data> 块", "en": "a <bank_data> block"},
    ),
    Internal(
        re.compile(r'"(?:step_id|operation|refusal|reversal)"\s*:'),
        {"zh": "银行结果的 JSON", "en": "a Bank result as JSON"},
    ),
    Internal(CARD_ID, {"zh": "卡的银行内部编号", "en": "a card's Bank ID"}),
    Internal(STEP_ID, {"zh": "步骤编号", "en": "a step ID"}),
    Internal(LOCK_ID, {"zh": "安全锁编号", "en": "a Safety Lock ID"}),
]
"""What a draft must never contain, checked in order; one more is one more entry."""

A_CARD: dict[Language, str] = {"zh": "该卡", "en": "the card"}
CARD_ENDING: dict[Language, str] = {"zh": "尾号 {last4} 的卡", "en": "card ending {last4}"}

RAW_RESULTS: dict[Language, str] = {
    "zh": "以下是银行返回的结果：",
    "en": "Here is what the Bank returned:",
}
NOT_RETRIEVED: dict[Language, str] = {
    "zh": "抱歉，暂时无法从银行获取该信息，请稍后再试。",
    "en": "Sorry, I couldn't get that information from the Bank. Please try again later.",
}


@dataclass(frozen=True)
class Written:
    """An answer, and whether it quotes the Bank's data as it was returned (the raw Bank
    result), which the model then reads back only as data."""

    text: str
    quotes_bank: bool = False


def answer(
    model: ModelClient,
    history: list[dict[str, Any]],
    results: list[StepResult],
    earlier: list[StepResult],
    queries: Collection[str] | None = None,
) -> Written:
    """The answer to this turn: `results` are this turn's, `earlier` the conversation's.
    `queries` are the Operations that only look something up (all of them if None)."""
    language = customer_language(history)
    if isinstance(model, OfflineModel):  # no model to ask: quote the Bank directly
        return Written(raw(results, language, queries), quotes_bank=True)
    retrieved = [result.output for result in results if result.output is not None]
    if results and not retrieved:
        facts = Facts()
    else:
        facts = Facts.of(r.output for r in [*earlier, *results] if r.output is not None)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(untrusted=untrusted.RULE)},
        *history,
        untrusted.bank_data("Bank results for this turn:", [result.as_data() for result in results]),
        {"role": "system", "content": REPLY_IN[language]},
    ]
    try:
        draft = model.chat(messages).content or ""
        if (problems := check(draft, facts)) is None:
            return Written(draft)
        messages += [
            {"role": "assistant", "content": draft},
            {"role": "system", "content": problems},
        ]
        draft = model.chat(messages).content or ""
        if check(draft, facts) is None:
            return Written(draft)
    except ModelUnavailable:
        pass
    return Written(raw(results, language, queries), quotes_bank=True)


def check(draft: str, facts: Facts) -> str | None:
    """What is wrong with the draft, as the instruction to rewrite it, or None: it may
    state only what the Bank returned, may never ask for a code or a password, and never
    writes out the Agent's internal data (`INTERNALS`)."""
    problems = []
    if found := [i.what for i in INTERNALS if i.pattern.search(draft)]:
        leaks = "；".join(what["zh"] for what in found) + " / " + ", ".join(
            what["en"] for what in found
        )
        problems.append(INTERNAL.format(leaks=leaks))
    if mentions := unsupported(draft, facts):
        problems.append(REGENERATE.format(mentions="、".join(mentions)))
    if asking := asks_for_secret(draft):
        problems.append(NEVER_ASK.format(sentences=" / ".join(asking)))
    return "\n".join(problems) or None


def raw(
    results: list[StepResult], language: Language, queries: Collection[str] | None = None
) -> str:
    """The Bank's results, as the Bank returned them; what couldn't be retrieved; and what
    couldn't be done, with the Bank's reason. `queries` are the Operations that only look
    something up (all of them if None)."""

    def missing(result: StepResult) -> str:
        if result.refusal is None or queries is None or result.operation in queries:
            return NOT_RETRIEVED[language]
        return failures.not_done(result.refusal, language)

    if not results:
        return NOT_RETRIEVED[language]
    if all(result.output is None for result in results):
        return "\n".join(dict.fromkeys(missing(result) for result in results))
    lines = [
        line
        for result in results
        for line in (readable(result.output) if result.output is not None else [missing(result)])
    ]
    return "\n".join([RAW_RESULTS[language], *lines])


def readable(value: Any, indent: str = "") -> list[str]:
    """A Bank result as plain lines: the Bank's own field names and values, with nothing
    added or computed."""
    if isinstance(value, dict) and value:
        lines = []
        for key, item in value.items():
            if isinstance(item, dict | list) and item and not is_flat(item):
                lines += [f"{indent}{key}:", *readable(item, indent + "  ")]
            else:
                lines.append(f"{indent}{key}: {scalar(item)}")
        return lines
    if isinstance(value, list) and value and not is_flat(value):
        lines = []
        for item in value:
            first, *rest = readable(item, indent + "  ")
            lines += [f"{indent}- {first.lstrip()}", *rest]
        return lines
    return [f"{indent}{scalar(value)}"]


def is_flat(value: Any) -> bool:
    """A list of plain values, which fits on one line."""
    return isinstance(value, list) and not any(isinstance(item, dict | list) for item in value)


def scalar(value: Any) -> str:
    if value is None or value == [] or value == {}:
        return "-"
    if isinstance(value, list):
        return ", ".join(scalar(item) for item in value)
    return json.dumps(value) if isinstance(value, bool) else str(value)


def for_channel(text: str, cards: dict[str, Card], language: Language) -> str:
    """The answer as it may reach the Channel, whichever way it was written: no
    `<bank_data>` block, no Safety Lock ID, no Bank JSON key (`"refusal":` becomes
    `refusal:`; a `step_id` pair goes), and each card named by its last four digits
    (`cards`, those the Bank listed) instead of its Bank ID."""
    text = BANK_DATA_TAG.sub("", BANK_DATA_BLOCK.sub("", text)).strip()

    def name(match: re.Match[str]) -> str:
        card = cards.get(match.group())
        if card is None:
            return A_CARD[language]
        return CARD_ENDING[language].format(last4=card.last4)

    text = LOCK_ID.sub("", STEP_ID_PAIR.sub("", CARD_ID.sub(name, text)))
    return JSON_KEY.sub(r"\1:", text)
