"""What the Customer is told, in their language, that the Agent writes itself: why a Red
step on the Plan card needs extra proof, a wrong code, what still works while a Safety Lock
holds the session, a Plan card changed, rejected, stopped or cancelled, the offer to undo
a failed Plan, a Staff Member asked for, and a request the model couldn't plan; and the
notes the model is given about a takeover or a chosen option.

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
FIRST_TIME_PAYEE_LINE: dict[Language, str] = {
    "zh": "这是你首次向该收款人转账，金额较大，执行前需要短信验证。",
    "en": "This is your first transfer to this Payee and the amount is large, so it needs an SMS"
    " code before it runs.",
}
STRONG_VERIFICATION: dict[Language, str] = {
    "zh": "这是高风险操作，执行前需要短信验证。",
    "en": "This is a high-risk Operation, so it needs an SMS code before it runs.",
}


WRONG_CODE: dict[Language, str] = {
    "zh": "验证码不正确（本次会话已输错 {count} 次）。请在下方的短信验证框中重新输入短信里的验证码；"
    "累计输错 {limit} 次，会话将被安全锁定。",
    "en": "That code isn't right ({count} wrong so far this session). Please type the code"
    " from the SMS into the SMS verification box below again; after {limit} wrong codes the"
    " session is put under a Safety Lock.",
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


# A lock a Staff Member set by hand: never who set it or why.
STAFF_SAFETY_LOCK: dict[Language, str] = {
    "zh": "为保护您的账户安全，银行工作人员已为您的账户设置安全锁定。在 {until} 前，"
    "或银行工作人员解除锁定前，只能办理查询类业务（例如查询余额）；"
    "转账等需要确认或验证的操作暂时无法办理。",
    "en": "To keep your account safe, the Bank's staff have put your account under a Safety"
    " Lock. Until {until}, or until they lift it, only queries work (such as checking your"
    " balance); transfers and anything else that needs confirming or verifying can't be"
    " done.",
}


def safety_lock(trigger: str, locked_until: str, language: Language) -> str:
    """What still works while the session is locked, and until when (Bank time)."""
    until = datetime.fromisoformat(locked_until).strftime("%H:%M")
    if trigger == "staff":
        return STAFF_SAFETY_LOCK[language].format(until=until)
    because = LOCKED_BECAUSE.get(trigger, {"zh": "安全原因", "en": "for safety"})[language]
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
    steps: list[PlanStep],
    catalogue: list[dict[str, Any]],
    language: Language,
    reasons: dict[str, list[str]] | None = None,
) -> list[PlanWarning]:
    """One warning per Red step: the Bank's reason if it gave one (a first transfer to a new
    Payee); else a step that counts toward the Daily Transfer Total is Red only because it
    takes the total above ¥1,000."""
    reasons = reasons or {}
    counts = {
        op["name"] for op in catalogue if op.get("counts_toward_daily_transfer_total")
    }
    return [
        PlanWarning(
            step_id=step.id, reason="first_time_payee", text=FIRST_TIME_PAYEE_LINE[language]
        )
        if "first_time_payee" in reasons.get(step.id, [])
        else PlanWarning(
            step_id=step.id, reason="daily_transfer_total", text=CROSSES_DAILY_LINE[language]
        )
        if step.operation in counts
        else PlanWarning(
            step_id=step.id, reason="strong_verification", text=STRONG_VERIFICATION[language]
        )
        for step in steps
        if step.tier == "red"
    ]


HANDED_BACK_NOTE = (
    "人工客服 {staff} 接管过这段对话，现在交还给你（Agent）。标有 [Staff Member {staff}] 的消息是"
    "人工客服写的，不是你写的。\n"
    "The Staff Member {staff} took this conversation over and has handed it back to you (the"
    " Agent). Messages marked [Staff Member {staff}] were written by them, not by you."
)

CHOSEN_NOTE = (
    "客户对上面的问题选择了第 {number} 个选项（{value}）。\n"
    "The Customer answered the question above by choosing option {number} ({value})."
)
"""Not the option's label, which may quote the Bank's data (a Payee's masked phone): the
question above, with its options, is in the history as data."""


HUMAN_REQUESTED: dict[Language, str] = {
    "zh": "已为您呼叫人工客服，请稍候。",
    "en": "I've asked for a Staff Member to join you. Please wait a moment.",
}

REJECTED: dict[Language, str] = {
    "zh": "好的，已取消这个计划，没有执行任何操作。",
    "en": "OK, I've cancelled that Plan. Nothing was done.",
}
"""The Agent's acknowledgement of a rejected Plan card: nothing ran, so nothing to report."""

CANCELLED: dict[Language, str] = {
    "zh": "好的，已取消剩下的步骤，不会再执行。已执行的步骤不受影响。",
    "en": "OK, I've cancelled the rest of the Plan; nothing more will run. The steps that"
    " already ran stay done.",
}
"""The Agent's acknowledgement of a cancelled stopped Plan: the pause already said what ran."""

NOT_REVERSED: dict[Language, str] = {
    "zh": "好的，不撤销，已完成的步骤保持不变。",
    "en": "OK, I won't undo anything: the steps that ran stay as they are.",
}
"""The Agent's acknowledgement of a declined reversal offer."""

NOT_PLANNED: dict[Language, str] = {
    "zh": "抱歉，我暂时无法处理这个请求，没有执行任何操作。请稍后再试。",
    "en": "Sorry, I can't work on that request right now, and nothing was done. Please try"
    " again in a moment.",
}
"""When the model can't be asked, or gives no reply the Agent can act on."""


def reversal_text(numbers: list[str], language: Language) -> str:
    """The offer to undo the card's steps with these numbers, in the order they'd be
    undone."""
    if language == "en":
        steps = f"step{'s' if len(numbers) > 1 else ''} {', '.join(numbers)}"
        return (
            f"I can undo what already ran: {steps}, in that order. Shall I? Undoing is a new"
            " Plan: you'll see its Plan card and confirm it first. Otherwise they stay done."
        )
    return (
        f"已完成的步骤可以撤销：按第 {'、'.join(numbers)} 步的顺序。需要撤销吗？"
        "撤销是一个新的计划，会先给您看计划卡片并请您确认；不撤销则保持不变。"
    )


def paused_text(card: list[str], ran: list[str], left: list[str], language: Language) -> str:
    """Which of the card's steps ran and which didn't (`left`), by their number on the
    card."""

    def numbers(ids: list[str]) -> list[str]:
        return [str(card.index(id) + 1) for id in ids if id in card]

    done = numbers(ran)
    not_run = numbers(left)
    if language == "en":
        parts = ["Paused: no further step will run."]
        if done:
            parts.append(f"Done: step{'s' if len(done) > 1 else ''} {', '.join(done)}.")
        parts.append(f"Not done: step{'s' if len(not_run) > 1 else ''} {', '.join(not_run)}.")
        parts.append("You can resume, change or cancel the rest.")
        return " ".join(parts)
    parts = ["已暂停，后面的步骤不会执行。"]
    if done:
        parts.append(f"已执行：第 {'、'.join(done)} 步。")
    parts.append(f"未执行：第 {'、'.join(not_run)} 步。")
    parts.append("您可以继续、修改或取消剩下的步骤。")
    return "".join(parts)
