"""Balance look-ups: the tracer intent of the rules Planner."""

from agent.events import PlanStep
from agent.planner import Plan
from agent.rules.core import Intent, Request, pattern


def plan(request: Request) -> Plan:
    return Plan(
        intent="query_balance",
        steps=[PlanStep(id="s1", operation="query_balance", input={}, depends_on=[])],
    )


INTENTS = [
    Intent(
        name="balance",
        label={"zh": "查询余额", "en": "check your balance"},
        triggers=(
            pattern(r"余额|还有多少钱|有多少钱|剩多少|剩下多少|卡里.*(多少|几)"),
            pattern(r"\bbalance\b|how much (money )?(do i have|is (there|left|in))"),
        ),
        excludes=(pattern(r"转|汇|付|\b(transfer|send|pay|split)\b"),),
        plan=plan,
    ),
]
