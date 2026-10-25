"""The Responder: writes the Customer's answer only from what the Bank returned.

Before an answer is sent, the fact check confirms that every amount, date, card and Payee
it mentions appears in a Bank result from this turn or earlier in the conversation. A
draft that fails is regenerated once; if that fails too, the Customer gets the raw Bank
result instead.

When the turn's queries all failed (the Bank refused them or couldn't be reached), the
answer may state no figures at all, not even earlier ones, which may be out of date: it
says the information couldn't be retrieved.
"""

import json
from dataclasses import dataclass
from typing import Any

from agent import untrusted
from agent.executor import StepResult
from agent.fact_check import Facts, unsupported
from agent.language import Language, customer_language
from agent.model import ModelClient
from agent.secrets_guard import asks_for_secret

SYSTEM_PROMPT = """\
你是一家银行的 AI 助手（Agent）。根据本轮银行返回的结果，简洁地回答客户。
You are a bank's AI Agent. Answer the Customer briefly from the Bank's results for this turn.

只陈述银行结果中出现的事实（金额、日期、账户、卡、收款人）；不要猜测或编造。
State only facts that appear in the Bank's results (amounts, dates, accounts, cards,
Payees); never guess or make anything up.

{untrusted}

如果某个步骤失败（有 refusal，没有 output），就说明无法获取该信息，不要猜测。
If a step failed (it has a `refusal` and no `output`), say that information couldn't be
retrieved; never guess it.

某个步骤被银行拒绝时，只用银行给出的原因（refusal 的 reason）说明，不要推测其他原因。
When the Bank refused a step, give only the Bank's reason for it (the refusal's `reason`);
never suggest any other cause.

永远不要向客户索要验证码或密码，也不要写出完整卡号（只写后四位）。
Never ask the Customer for a verification code or a password: codes are typed into their
phone only. Never write out a full card number; give its last four digits only.
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
) -> Written:
    """The answer to this turn: `results` are this turn's, `earlier` the conversation's."""
    language = customer_language(history)
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
    return Written(raw(results, language), quotes_bank=True)


def check(draft: str, facts: Facts) -> str | None:
    """What is wrong with the draft, as the instruction to rewrite it, or None: it may
    state only what the Bank returned, and may never ask for a code or a password."""
    problems = []
    if mentions := unsupported(draft, facts):
        problems.append(REGENERATE.format(mentions="、".join(mentions)))
    if asking := asks_for_secret(draft):
        problems.append(NEVER_ASK.format(sentences=" / ".join(asking)))
    return "\n".join(problems) or None


def raw(results: list[StepResult], language: Language) -> str:
    """The Bank's results, as the Bank returned them, and what couldn't be retrieved."""
    if all(result.output is None for result in results):
        return NOT_RETRIEVED[language]
    lines = [
        f"{result.operation}: {json.dumps(result.output, ensure_ascii=False)}"
        if result.output is not None
        else f"{result.operation}: {NOT_RETRIEVED[language]}"
        for result in results
    ]
    return "\n".join([RAW_RESULTS[language], *lines])
