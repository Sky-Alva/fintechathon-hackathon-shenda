"""The manipulation guard: spots known scams in what the Customer says, before planning.

Moving money to a "safe account" (安全账户) because someone claiming to be the police,
a court or the bank's customer service said so is the best-known telecom scam: no real
authority ever asks for it. A message with that pattern is never planned, so no model can
be talked into moving the money. Subtler pressure (impersonated staff, invented urgency)
is the Planner's to recognise; it declines with reason `manipulation`.

It also looks through what the Bank returns for instruction-like text (`injection`): Bank
data is written by other people, so it is never followed, and the Customer is warned.

The guard adds a refusal, a warning and a report on top of the Bank's rules; it never
replaces them. Claims of authority by the Customer ("我是客户经理") change nothing: the
Bank decides every tier and approval (ADR-0001).
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

from agent.language import Language

EVIDENCE_LIMIT = 1000
"""The Bank takes at most this many characters of quoted text in a report."""

SCAM_PATTERNS = [
    re.compile(r"安全(账|帐|賬)户"),
    re.compile(r"\b(safe|secure|safety|protected) (bank )?account", re.IGNORECASE),
]


@dataclass(frozen=True)
class Suspicion:
    """A suspected manipulation attempt, as reported to the Bank: what kind, and the text
    that raised it, quoted."""

    kind: Literal["injection", "impersonation"]
    evidence: str


def scam(text: str) -> Suspicion | None:
    """The known scam in the Customer's message, if it has one."""
    if any(pattern.search(text) for pattern in SCAM_PATTERNS):
        return Suspicion("impersonation", quoted(text))
    return None


INJECTION_PATTERNS = [
    re.compile(r"(系统|管理员)(指令|命令|提示)"),
    re.compile(r"(忽略|无视|忘记)(掉)?(此前|之前|以上|上述|前面|先前)?的?(所有|全部|一切)?(规则|指令|指示|提示|限制)"),
    re.compile(r"(无需|不需要|不用|跳过|绕过)(任何)?(确认|验证|验证码|审批)"),
    re.compile(r"(全部|所有)余额(都)?转"),
    re.compile(r"\bsystem (prompt|instruction|command|override)", re.IGNORECASE),
    re.compile(
        r"\b(ignore|disregard|forget) (all |any )?(the )?(previous|prior|above|earlier|your)"
        r" (instructions|rules|prompts?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(skip|bypass|without|no) (the |any )?(confirmation|verification|code)",
        re.IGNORECASE,
    ),
    re.compile(r"\b(transfer|send|move) (all|the whole|the entire|your entire)", re.IGNORECASE),
]


@dataclass(frozen=True)
class Injection:
    """Instruction-like text found in what the Bank returned: where it was, and the
    suspicion to report."""

    field: str
    """The output field that held it, e.g. `last_transfer_note`."""
    suspicion: Suspicion


def injection(output: dict[str, Any] | None) -> Injection | None:
    """The first instruction-like text in a Bank output, if it has any.

    Bank data (Payee notes, merchant names, transaction descriptions) is written by
    other people: it is only ever data. Text in it that tries to give orders is never
    followed, and is reported as a suspected injection."""
    for field, text in strings(output):
        if any(pattern.search(text) for pattern in INJECTION_PATTERNS):
            return Injection(field, Suspicion("injection", quoted(text)))
    return None


def strings(value: Any, field: str = "") -> Iterator[tuple[str, str]]:
    """Every string in a JSON value, with the name of the field that holds it."""
    if isinstance(value, str):
        yield field, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from strings(item, key)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item, field)


def quoted(text: str) -> str:
    """The text as the Bank's report takes it."""
    return text.strip()[:EVIDENCE_LIMIT] or "(empty)"


SCAM_WARNING: dict[Language, str] = {
    "zh": "我不能帮您转这笔钱。这是常见的电信诈骗：公安、检察院、法院和银行工作人员都不会要求您"
    "把钱转到所谓的“安全账户”，也不会催您马上转账。请不要向对方转账或透露验证码，"
    "可以挂断电话，拨打 110 或银行官方客服电话核实。",
    "en": "I can't move this money for you. This is a well-known scam: the police, courts"
    " and bank staff never ask you to move money to a so-called \"safe account\", or rush"
    " you into a transfer. Please don't send them money or tell them any code; hang up"
    " and check with the police (110) or the bank's official hotline.",
}
REPORTED: dict[Language, str] = {
    "zh": "我已将这次情况报告给银行。",
    "en": "I've reported this to the Bank.",
}
STILL_HERE: dict[Language, str] = {
    "zh": "查询余额等其他业务，我可以继续为您办理。",
    "en": "I can still help with anything else, such as checking your balance.",
}


def warning(reported: bool, language: Language) -> str:
    """The refusal's text: why the Agent won't, and whether the Bank was told."""
    parts = [SCAM_WARNING[language]]
    if reported:
        parts.append(REPORTED[language])
    parts.append(STILL_HERE[language])
    return ("" if language == "zh" else " ").join(parts)


FIELD: dict[str, dict[Language, str]] = {
    "note": {"zh": "转账备注", "en": "a transfer note"},
    "merchant": {"zh": "商户名称", "en": "a merchant name"},
    "description": {"zh": "交易描述", "en": "a transaction description"},
}
BANK_DATA: dict[Language, str] = {"zh": "银行返回的数据", "en": "the Bank's data"}


def where(field: str, language: Language) -> str:
    """The kind of Bank data a field holds, in the Customer's words."""
    for key, names in FIELD.items():
        if key in field.casefold():
            return names[language]
    return BANK_DATA[language]


def injection_warning(field: str, reported: bool, language: Language) -> str:
    """The warning that Bank data holds suspicious instructions, which the Agent ignores."""
    if language == "en":
        parts = [
            f"Heads-up: {where(field, language)} the Bank returned contains suspicious"
            " instructions (for example to move your whole balance or skip confirmation)."
            " It was written by someone else: I'm ignoring it and doing only what you asked."
        ]
    else:
        parts = [
            f"提醒您：银行返回的{where(field, language)}里有一段可疑的指令性文字（例如要求转出全部余额、"
            "跳过确认或验证）。这段文字是别人写的，我不会照做，只按您本人的要求办理。"
        ]
    if reported:
        parts.append(REPORTED[language])
    return ("" if language == "zh" else " ").join(parts)
