"""Secrets stay secret: what looks like a verification code or a full card number.

A code the Customer types into the chat while a verification is pending is masked before
it goes anywhere (history, model, staff history), and every answer is masked before it
is sent, even when a Bank result contained the number.
"""

import re

from agent.language import Language

NOT_IN_A_WORD = r"(?<![A-Za-z0-9_.,¥￥-])"
"""Not part of an amount or of an ID such as the Bank's TRF-000001."""
CODE = re.compile(
    rf"{NOT_IN_A_WORD}\d{{6}}(?![A-Za-z0-9_-])(?!\s*(?:[.,]\d|元|块|万|yuan|RMB|CNY))",
    re.IGNORECASE,
)
"""Six digits on their own, as the Bank's SMS codes are, and not an amount."""
CODE_MASK = "******"

CARD_NUMBER = re.compile(rf"{NOT_IN_A_WORD}\d(?:[ -]?\d){{11,18}}(?![A-Za-z0-9_])")
"""13 to 19 digits, maybe grouped by spaces or dashes: what a full card number looks like."""

CODE_IN_CHAT: dict[Language, str] = {
    "zh": "为了您的账户安全，请不要把验证码作为聊天消息发送，我不会查看、保存或转发它。"
    "请在下方的“短信验证”框中输入短信里的验证码以继续，它会直接提交给银行。",
    "en": "To keep your account safe, please don't send verification codes as a chat"
    " message; I don't read, keep or pass them on. Please type the code from the SMS into"
    " the SMS verification box below to continue: it goes straight to the Bank.",
}


SECRET_WORD_ZH = r"(?:验证码|密码|动态码|校验码)"
SECRET_WORD_EN = r"\b(?:verification\s+code|code|password|passcode|PIN)s?\b"
ASKING = [
    re.compile(rf"(?:告诉|发给|发送给|发我|提供|报给|回复|给我|说出)[^。！？!?\n]{{0,10}}{SECRET_WORD_ZH}"),
    re.compile(rf"{SECRET_WORD_ZH}[^。！？!?\n]{{0,10}}(?:告诉我|发给我|发我|给我|报给我|是多少|提供给我)"),
    re.compile(
        rf"\b(?:tell|send|give|share|provide|reply\s+with)\b[^.!?\n]{{0,30}}{SECRET_WORD_EN}",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bwhat(?:'s|\s+is)\s+(?:your|the)\b[^.!?\n]{{0,20}}{SECRET_WORD_EN}", re.IGNORECASE
    ),
]
NEGATION = re.compile(r"不要|别|切勿|请勿|不会|绝不|\b(?:never|don't|do\s+not|not)\b", re.IGNORECASE)
SENTENCE_END = re.compile(r"(?<=[。！？!?\n])|(?<=[.;])\s")


def asks_for_secret(text: str) -> list[str]:
    """Each sentence of `text` that asks the Customer for a code or a password, which the
    Agent never does ("never tell anyone your code" is fine)."""
    return [
        sentence.strip()
        for sentence in SENTENCE_END.split(text)
        if not NEGATION.search(sentence) and any(p.search(sentence) for p in ASKING)
    ]


def has_code(text: str) -> bool:
    return CODE.search(text) is not None


def mask_codes(text: str) -> str:
    return CODE.sub(CODE_MASK, text)


def _mask_card(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group())
    return f"**** **** **** {digits[-4:]}"


def mask(text: str) -> str:
    """The text with every full card number cut to its last four digits, as the Bank
    shows cards, and every verification code masked."""
    return mask_codes(CARD_NUMBER.sub(_mask_card, text))
