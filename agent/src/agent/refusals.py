"""Refusals: what the Customer is told when the Agent won't do something.

An out-of-scope refusal lists what the Agent can do, in the Bank's own words from the
Operation catalogue, so the list grows as the Bank gains Operations.
"""

from typing import Any

from agent.language import Language
from agent.planner import MAX_OFFERED


def offered(can_do: list[str], catalogue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The catalogue entries the model chose to offer, ignoring any the Bank doesn't have;
    the catalogue's first few if it chose none."""
    by_name = {op["name"]: op for op in catalogue}
    chosen = [by_name[name] for name in dict.fromkeys(can_do) if name in by_name]
    return (chosen or catalogue)[:MAX_OFFERED]


OUT_OF_SCOPE: dict[Language, str] = {
    "zh": "抱歉，这个我暂时帮不了您。我可以帮您：",
    "en": "Sorry, I can't help with that. Here's what I can do for you:",
}


SECRET: dict[Language, str] = {
    "zh": (
        "抱歉，为保护您的账户安全，我不能透露完整卡号、密码或验证码。卡号我只能显示后四位；"
        "密码和验证码只有您本人知道，请不要告诉任何人，包括我和自称银行工作人员的人。我可以帮您："
    ),
    "en": (
        "Sorry, to keep your account safe I can't reveal full card numbers, passwords or"
        " verification codes. I can only show a card's last four digits, and only you should"
        " ever know your password and codes: never tell anyone, including me or anyone"
        " claiming to work for the Bank. Here's what I can do for you:"
    ),
}

DECLINED: dict[str, dict[Language, str]] = {"out_of_scope": OUT_OF_SCOPE, "secret": SECRET}


def declined(
    reason: str, can_do: list[str], catalogue: list[dict[str, Any]], language: Language
) -> str:
    """Why the Agent won't, in the Customer's language, then what it can do instead."""
    lines = [f"- {op[f'description_{language}']}" for op in offered(can_do, catalogue)]
    return "\n".join([DECLINED[reason][language], *lines])


OTHER_CUSTOMER: dict[Language, str] = {
    "zh": (
        "抱歉，为保护每位客户的隐私和资金安全，我不能查看或透露其他客户（包括您的家人和配偶）"
        "的账户、卡片或交易信息。如需了解，请对方本人登录查询。我可以帮您查询您自己名下的账户和卡片。"
    ),
    "en": (
        "Sorry, to protect every customer's privacy and money, I can't look at or share another"
        " customer's accounts, cards or transactions, not even a family member's or your"
        " spouse's. They can log in and check for themselves. I can help you with your own"
        " accounts and cards."
    ),
}
"""The Bank refused a step naming another Customer's account, card or the like."""


def other_customer(language: Language) -> str:
    return OTHER_CUSTOMER[language]
