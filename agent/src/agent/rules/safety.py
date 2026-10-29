"""Safety and takeover in the rules Planner: asking for a human, and a request about an
account by its number. Scams and injections are declined before any Planner is asked
(`guard.scam`, `guard.injected` in the conversation), so they are not repeated here.
"""

import re

from agent.events import PlanStep
from agent.planner import HumanAsked, Plan
from agent.rules.core import Intent, Request, pattern

ACCOUNT_ID = re.compile(r"\bacc-[a-z]+-\d+\b", re.IGNORECASE)
"""An account number as the Bank writes it (ACC-LM-001). The message is lower-cased before
matching, the Bank's IDs are upper case."""


def human(request: Request) -> HumanAsked:
    return HumanAsked()


def account_balance(request: Request) -> Plan:
    """A balance by account number, whoever's it is: the Bank refuses another Customer's
    account (`forbidden_other_customer`), the rules never judge whose it is."""
    ids = list(dict.fromkeys(m.upper() for m in ACCOUNT_ID.findall(request.matching)))
    return Plan(
        intent="query_balance",
        steps=[
            PlanStep(
                id=f"s{n}", operation="query_balance", input={"account_id": id}, depends_on=[]
            )
            for n, id in enumerate(ids, start=1)
        ],
    )


BALANCE_WORDS = r"余额|多少|查|看一下|balance|how much|check"

INTENTS = [
    Intent(
        name="human",
        label={"zh": "转人工客服", "en": "talk to a person"},
        triggers=(
            pattern(r"人工|真人|(转|找|联系|要|叫).{0,4}(客服|工作人员)"),
            pattern(
                r"\b(human|real person|live agent|a person|representative|customer service"
                r"|customer support)\b"
            ),
        ),
        plan=human,
    ),
    Intent(
        name="account_balance",
        label={"zh": "查询指定账户的余额", "en": "check the balance of an account"},
        triggers=(
            pattern(rf"\bacc-[a-z]+-\d+\b.*({BALANCE_WORDS})|({BALANCE_WORDS}).*\bacc-[a-z]+-\d+\b"),
        ),
        plan=account_balance,
    ),
]
