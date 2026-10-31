"""The rules Planner: the Planner for when there is no model (offline mode, or the model
can't be reached). Same contract as `planner.make_plan`, same two stages (look-ups, then
completion from the Bank's results), same result union. It never guesses: a required slot
that is missing or ambiguous is a `Clarification`, two intents matching is a
`Clarification`, and a request no intent recognises is `Declined` (out of scope, listing
what the Agent can do). Everything it plans is still tiered, confirmed and verified by the
Bank.

How to add an intent (each Scenario has its own module here):

1. Create `agent/rules/<scenario>.py` with `INTENTS: list[Intent]` (see `core.Intent`):
   trigger patterns (Chinese and English, matched on `guard.normalise` of the message: half-width, disguises undone, lower-cased),
   optional `excludes`, `plan(request)` for the first stage and, if later steps need the
   Bank's results, `complete(request)` for the second. Build steps with `PlanStep`.
2. Extract slots with `rules.slots` (`amounts`, `chinese_number`, ...). Resolve Payees,
   cards and Subscriptions through the Bank's look-ups (a first-stage look-up step, then
   read `request.looked_up`), never in the rules. Ask with `Clarification` when unsure.
3. Add the module to `MODULES` below. That is the only shared line you touch.
4. Test it through the conversation interface with the `offline_agent` fixture.
"""

import logging
from dataclasses import replace
from collections.abc import Sequence
from typing import Any

from agent import guard, planner, untrusted
from agent.events import ClarificationOption
from agent.language import Language, customer_language
from agent.rules import balance, cards, orchestration, safety, spending, transfers
from agent.rules.core import Intent, Reply, Request

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


def latest_message(history: list[dict[str, Any]]) -> str:
    return next((str(m["content"]) for m in reversed(history) if untrusted.from_customer(m)), "")


def the_request_asked_about(history: list[dict[str, Any]]) -> str | None:
    """The Customer's earlier message, if the one before their latest was a question the
    Agent asked them (shown, not a Customer message), else None."""
    customer = [i for i, m in enumerate(history) if untrusted.from_customer(m)]
    if len(customer) < 2 or customer[-1] - customer[-2] < 2:
        return None
    between = history[customer[-2] + 1 : customer[-1]]
    if not any(m["content"].startswith(f"<bank_data>\n{untrusted.SHOWN}\n") for m in between):
        return None
    return str(history[customer[-2]]["content"])


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
    text = latest_message(history)
    request = Request(
        text=text,
        matching=guard.normalise(text),
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
    if not candidates and orchestration.accepts_the_reminder(history, request.matching):
        candidates = [intent for intent in intents() if intent.name == "birthday"]
    if not candidates and (asked := the_request_asked_about(history)) is not None:
        return answer_to_question(request, asked)
    return respond(request, candidates)


def birthday_plan_instead(
    reply: Reply,
    history: list[dict[str, Any]],
    *args: Any,
    **kwargs: Any,
) -> Reply | None:
    """The birthday Plan, when the Customer clearly asked for it and the model's Plan has no
    orders in it: a model often stops at the look-ups, and a Plan of Green steps only ends
    the turn. None keeps the model's reply. The Bank still tiers and confirms the Plan."""
    if not isinstance(reply, planner.Plan) or kwargs.get("failed") or kwargs.get("interrupted"):
        return None
    if any(step.operation == orchestration.ORDER for step in reply.steps):
        return None
    matching = guard.normalise(latest_message(history))
    if not orchestration.asks_for_the_birthday_plan(
        matching
    ) and not orchestration.accepts_the_reminder(history, matching):
        return None
    instead = make_plan(history, *args, **kwargs)
    return instead if isinstance(instead, planner.Plan) and instead.steps else None


JOINERS = (" ", "给", " to ")
"""How the Customer's answer is attached to the request it answers: the words of a
transfer fit together differently in each ("转200块" + "给老妈", "send 200" + " to Mum")."""


def answer_to_question(request: Request, asked: str) -> Reply:
    """Nothing in the message is a request of its own: it answers the question just asked
    ("200", "老妈"), so it is read together with the request that was asked about, joined
    whichever way gives a Plan or a Decline rather than asking the same thing again."""
    first: Reply | None = None
    for joiner in JOINERS:
        text = f"{asked}{joiner}{request.text}"
        combined = replace(request, text=text, matching=guard.normalise(text))
        candidates = [intent for intent in intents() if intent.matches(combined)]
        reply = respond(combined, candidates)
        if not isinstance(reply, planner.Clarification):
            return reply
        first = first or reply
    return first or planner.Declined(reason="out_of_scope")


def respond(request: Request, candidates: list[Intent]) -> Reply:
    language = request.language
    looked_up = request.looked_up
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
