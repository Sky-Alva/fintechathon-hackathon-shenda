"""Why a step failed, in plain words: from the Bank's refusal reason and nothing else.

The Agent writes this itself, never the model, so the Customer is never told a cause the
Bank didn't give. A reason without plain words here is named as the Bank gave it. For
`invalid_input`, the Bank's `detail.errors` say which value is wrong and how (more than two
decimal places, say): those the Agent has words for are said instead of the reason.
"""

import re
from collections.abc import Collection
from typing import Any

from agent.events import BankRefusal
from agent.language import Language

PLAIN_WORDS: dict[str, dict[Language, str]] = {
    "insufficient_funds": {
        "zh": "账户余额不足",
        "en": "there isn't enough money in the account",
    },
    "needs_confirmation": {
        "zh": "这一步需要您的确认，但没有有效的确认",
        "en": "it needs your Confirmation, and it had none that was valid",
    },
    "needs_verification": {
        "zh": "这一步现在需要短信验证",
        "en": "it now needs Strong Verification (an SMS code)",
    },
    "token_invalid": {
        "zh": "这一步的确认或验证已失效",
        "en": "its Confirmation or verification is no longer valid",
    },
    "session_locked": {
        "zh": "会话处于安全锁定中，只能进行查询类操作",
        "en": "the session is under a Safety Lock, so only Green Operations can run",
    },
    "forbidden_other_customer": {
        "zh": "这一步涉及其他客户的信息，银行不允许",
        "en": "it concerns another Customer's information, which the Bank doesn't allow",
    },
    "ambiguous_payee": {
        "zh": "有不止一位收款人符合，银行无法确定是哪一位",
        "en": "more than one Payee matches, so the Bank can't tell which one",
    },
    "not_found": {
        "zh": "银行找不到这一步涉及的对象",
        "en": "the Bank couldn't find what it refers to",
    },
    "invalid_input": {
        "zh": "银行认为这一步的内容无效",
        "en": "the Bank found its details invalid",
    },
    "not_authenticated": {
        "zh": "会话已失效，需要重新登录",
        "en": "the session is no longer signed in",
    },
    "bank_unavailable": {
        "zh": "暂时无法连接银行",
        "en": "the Bank couldn't be reached",
    },
}


FIELDS: dict[str, dict[Language, str]] = {
    "new_limit": {"zh": "新额度", "en": "the new limit"},
    "amount": {"zh": "金额", "en": "the amount"},
    "total": {"zh": "总金额", "en": "the total"},
    "quantity": {"zh": "数量", "en": "the quantity"},
}
"""The inputs whose value the Bank checks, as the Customer would name them."""

WRONG_VALUE: dict[str, dict[Language, str]] = {
    "greater_than": {"zh": "{field}必须大于 {n}", "en": "{field} must be greater than {n}"},
    "greater_than_equal": {"zh": "{field}不能小于 {n}", "en": "{field} must be at least {n}"},
    "less_than": {"zh": "{field}必须小于 {n}", "en": "{field} must be less than {n}"},
    "less_than_equal": {
        "zh": "{field}过大，最多为 {n}",
        "en": "{field} is too large; it can be at most {n}",
    },
    "decimal_max_places": {
        "zh": "{field}最多只能有 {n} 位小数",
        "en": "{field} can have at most {n} decimal places",
    },
}
"""What is wrong with a value, by the type of the Bank's error; `n` is the bound in the
Bank's own message ("Input should be greater than 0")."""


def _wrong(error: Any, language: Language, ignore: Collection[str]) -> str | None:
    """One of the Bank's `detail.errors` in plain words; None if it is about an input in
    `ignore`, or the Agent has no words for it."""
    if not isinstance(error, dict):
        return None
    words = WRONG_VALUE.get(str(error.get("type")))
    names = [part for part in error.get("loc") or [] if isinstance(part, str)]
    bound = re.findall(r"-?\d+(?:\.\d+)?", str(error.get("msg", "")))
    if words is None or not names or names[-1] in ignore or not bound:
        return None
    name = names[-1]
    field = FIELDS.get(name, {"zh": name, "en": f"the {name.replace('_', ' ')}"})[language]
    return words[language].format(field=field, n=bound[0])


def _wrong_values(
    refusal: BankRefusal, language: Language, ignore: Collection[str] = ()
) -> list[str]:
    """What the Bank said is wrong with the step's values, if it refused them."""
    errors = refusal.detail.get("errors")
    if refusal.reason != "invalid_input" or not isinstance(errors, list):
        return []
    wrong = (_wrong(error, language, ignore) for error in errors)
    return list(dict.fromkeys(text for text in wrong if text is not None))


def _why(refusal: BankRefusal, language: Language) -> str | None:
    """Why the Bank refused, in plain words; None if the Agent has none for it."""
    if wrong := _wrong_values(refusal, language):
        return ("；" if language == "zh" else "; ").join(wrong)
    words = PLAIN_WORDS.get(refusal.reason)
    return words[language] if words is not None else None


def explain(refusal: BankRefusal, language: Language) -> str:
    """The Customer's explanation of a step that didn't run, from the Bank's refusal."""
    why = _why(refusal, language)
    if language == "en":
        if why is None:
            return f"The Bank didn't run this step (its reason: {refusal.reason})."
        return f"The Bank didn't run this step: {why}."
    if why is None:
        return f"银行未执行这一步（银行给出的原因：{refusal.reason}）。"
    return f"银行未执行这一步：{why}。"


def not_done(refusal: BankRefusal, language: Language) -> str:
    """The answer when a step that changes something didn't run: it couldn't be done, and
    the Bank's reason for it."""
    why = _why(refusal, language)
    if language == "en":
        return f"Sorry, that couldn't be done: {why or f'the Bank gave {refusal.reason}'}."
    return f"抱歉，未能办理：{why or f'银行给出的原因：{refusal.reason}'}。"


def wrong_values(
    refusal: BankRefusal, language: Language, ignore: Collection[str]
) -> str | None:
    """The answer when the Bank refuses a value the Customer gave (a limit of 0, an amount
    with three decimal places): what is wrong with it, before anything else is asked. None
    if the refusal names no such value, leaving out the inputs in `ignore` (the card the
    Customer hasn't named yet)."""
    wrong = _wrong_values(refusal, language, ignore)
    if not wrong:
        return None
    if language == "en":
        return f"Sorry, that can't be done: {'; '.join(wrong)}."
    return f"抱歉，无法办理：{'；'.join(wrong)}。"
