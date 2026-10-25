"""What the Customer is told about Strong Verification and the Safety Lock, in their
language: why a Red step on the Plan card needs extra proof, a wrong code, and what
still works while a Safety Lock holds the session.

The Agent writes these itself, never the model: the Bank decides what is Red and when a
session is locked (ADR-0001), so nothing the Customer or the model says changes them.
"""

from datetime import datetime
from typing import Any

from agent.events import PlanStep, PlanWarning
from agent.language import Language

CROSSES_DAILY_LINE: dict[Language, str] = {
    "zh": "这笔转账会使今日转账累计超过 ¥1,000，执行前需要短信验证。",
    "en": "This transfer takes today's transfers above ¥1,000, so it needs an SMS code"
    " before it runs.",
}
STRONG_VERIFICATION: dict[Language, str] = {
    "zh": "这是高风险操作，执行前需要短信验证。",
    "en": "This is a high-risk Operation, so it needs an SMS code before it runs.",
}


WRONG_CODE: dict[Language, str] = {
    "zh": "验证码不正确（本次会话已输错 {count} 次）。请在模拟手机上重新输入短信中的验证码；"
    "累计输错 {limit} 次，会话将被安全锁定。",
    "en": "That code isn't right ({count} wrong so far this session). Please type the code"
    " from the SMS into the simulated phone again; after {limit} wrong codes the session"
    " is put under a Safety Lock.",
}
WRONG_CODES_BEFORE_LOCK = 3


def wrong_code(failed_attempts: int, language: Language) -> str:
    return WRONG_CODE[language].format(count=failed_attempts, limit=WRONG_CODES_BEFORE_LOCK)


LOCKED_BECAUSE: dict[str, dict[Language, str]] = {
    "wrong_codes": {"zh": "连续输错验证码", "en": "too many wrong verification codes"},
    "reported_attempts": {"zh": "检测到可疑请求", "en": "suspicious requests"},
    "red_operations": {
        "zh": "短时间内高风险操作过多",
        "en": "too many high-risk Operations in a short time",
    },
}
SAFETY_LOCK: dict[Language, str] = {
    "zh": "为保护您的账户安全，本次会话已被安全锁定（原因：{because}）。在 {until} 前，"
    "或银行工作人员解除锁定前，只能办理查询类业务（例如查询余额）；"
    "转账等需要确认或验证的操作暂时无法办理。",
    "en": "To keep your account safe, this session is under a Safety Lock ({because})."
    " Until {until}, or until a bank Staff Member lifts it, only queries work (such as"
    " checking your balance); transfers and anything else that needs"
    " confirming or verifying can't be done.",
}


def safety_lock(trigger: str, locked_until: str, language: Language) -> str:
    """What still works while the session is locked, and until when (Bank time)."""
    because = LOCKED_BECAUSE.get(trigger, {"zh": "安全原因", "en": "for safety"})[language]
    until = datetime.fromisoformat(locked_until).strftime("%H:%M")
    return SAFETY_LOCK[language].format(because=because, until=until)


REMOVED_WITH: dict[Language, str] = {
    "zh": "第 {numbers} 步要等您移除的步骤完成后才能执行，所以也一并移除了。",
    "en": "Step{s} {numbers} waited on what you removed, so I've removed {them} too.",
}


def removed_with(numbers: list[str], language: Language) -> str:
    """The card's steps, by number, that went with the ones the Customer removed."""
    if language == "en":
        many = len(numbers) > 1
        return REMOVED_WITH["en"].format(
            s="s" if many else "", numbers=", ".join(numbers), them="them" if many else "it"
        )
    return REMOVED_WITH["zh"].format(numbers="、".join(numbers))


def red_warnings(
    steps: list[PlanStep], catalogue: list[dict[str, Any]], language: Language
) -> list[PlanWarning]:
    """One warning per Red step: a step that counts toward the Daily Transfer Total is Red
    only because it takes the total above ¥1,000."""
    counts = {
        op["name"] for op in catalogue if op.get("counts_toward_daily_transfer_total")
    }
    return [
        PlanWarning(step_id=step.id, reason="daily_transfer_total", text=CROSSES_DAILY_LINE[language])
        if step.operation in counts
        else PlanWarning(
            step_id=step.id, reason="strong_verification", text=STRONG_VERIFICATION[language]
        )
        for step in steps
        if step.tier == "red"
    ]
