"""The rules Planner: the Planner for when there is no model (offline mode, or the model
can't be reached). Same contract as `planner.make_plan`, same two stages (look-ups, then
completion from the Bank's results), same result union. It never guesses: a required slot
that is missing or ambiguous is a `Clarification`, two intents matching is a
`Clarification`, and a request no intent recognises is `Declined` (out of scope, listing
what the Agent can do). Everything it plans is still tiered, confirmed and verified by the
Bank.

How to add an intent (each Scenario has its own module here):

1. Create `agent/rules/<scenario>.py` with `INTENTS: list[Intent]` (see `core.Intent`):
   trigger patterns (Chinese and English, matched on the lower-cased, half-width message),
   optional `excludes`, `plan(request)` for the first stage and, if later steps need the
   Bank's results, `complete(request)` for the second. Build steps with `PlanStep`.
2. Extract slots with `rules.slots` (`amounts`, `chinese_number`, ...). Resolve Payees,
   cards and Subscriptions through the Bank's look-ups (a first-stage look-up step, then
   read `request.looked_up`), never in the rules. Ask with `Clarification` when unsure.
3. Add the module to `MODULES` below. That is the only shared line you touch.
4. Test it through the conversation interface with the `offline_agent` fixture.
"""

import logging
from collections.abc import Sequence
from typing import Any

from agent import guard, planner, untrusted
from agent.events import ClarificationOption
from agent.language import Language, customer_language
from agent.rules import balance, cards, orchestration, safety, spending, transfers
from agent.rules.core import Intent, Reply, Request, matching

log = logging.getLogger(__name__)

MODULES: list[Sequence[Intent]] = [
    balance.INTENTS,
    cards.INTENTS,
    spending.INTENTS,
    safety.INTENTS,
    orchestration.INTENTS,
    transfers.INTENTS,
]
"""Every module of intents, one per Scenario."""


def intents() -> list[Intent]:
    return [intent for module in MODULES for intent in module]


def make_plan(
    history: list[dict[str, Any]],
    catalogue: list[dict[str, Any]],
    looked_up: list[dict[str, Any]] | None = None,
    interrupted: dict[str, Any] | None = None,
    failed: dict[str, Any] | None = None,
    now: str = "",
    payees: list[dict[str, Any]] | None = None,
    cards: list[dict[str, Any]] | None = None,
    subscriptions: list[dict[str, Any]] | None = None,
    language: Language | None = None,
) -> Reply:
    """`planner.make_plan` without a model."""
    language = language or customer_language(history)
    text = next(
        (str(m["content"]) for m in reversed(history) if untrusted.from_customer(m)),
        "",
    )
    request = Request(
        text=text,
        matching=matching(text),
        language=language,
        history=history,
        catalogue=catalogue,
        looked_up=looked_up,
        interrupted=interrupted,
        failed=failed,
        now=now,
        payees=payees or [],
        cards=cards or [],
        subscriptions=subscriptions or [],
    )
    if failed is not None:
        return planner.Plan(steps=[])  # a step failed: the rules never re-plan around it
    if guard.injected(request.text) is not None:
        return planner.Declined(reason="manipulation", kind="injection")
    if guard.scam(request.matching) is not None:
        return planner.Declined(reason="manipulation", kind="impersonation")
    candidates = [intent for intent in intents() if intent.matches(request)]
    if len(candidates) > 1:
        return planner.Clarification(
            question=request.say(
                "您是想做下面哪一件事？", "Which of these would you like me to do?"
            ),
            options=[
                ClarificationOption(label=i.label[language], value=i.label[language])
                for i in candidates
            ],
        )
    if not candidates:
        return planner.Declined(reason="out_of_scope")
    intent = candidates[0]
    if looked_up is None:
        return intent.plan(request)
    if intent.complete is None:
        log.info("Rules intent %s has no second stage", intent.name)
        return planner.Declined(reason="out_of_scope")
    return intent.complete(request)
