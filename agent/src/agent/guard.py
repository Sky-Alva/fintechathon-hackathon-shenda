"""The manipulation guard: spots known scams in what the Customer says, before planning.

Moving money to a "safe account" (安全账户) because someone claiming to be the police,
a court or the bank's customer service said so is the best-known telecom scam: no real
authority ever asks for it. A message with that pattern is never planned, so no model can
be talked into moving the money. Subtler pressure (impersonated staff, invented urgency)
is the Planner's to recognise; it declines with reason `manipulation`.

The guard adds a refusal, a warning and a report on top of the Bank's rules; it never
replaces them. Claims of authority by the Customer ("我是客户经理") change nothing: the
Bank decides every tier and approval (ADR-0001).
"""

import re
from dataclasses import dataclass
from typing import Literal

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
