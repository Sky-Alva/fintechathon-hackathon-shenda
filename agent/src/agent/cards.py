"""The Customer's cards as the Bank's card list (`list_cards`) showed them in this
conversation.

The Customer names a card the way it was shown to them: by its name, its last four digits
or its number in a list the Agent gave ("#2", "第二张"). The cards listed so far are what
such a follow-up refers to, so the Planner takes their Bank IDs from here instead of making
one up, and the Plan card names the card a step acts on instead of showing its ID.

A card_id the Planner made up anyway ("3" for "card #3") never runs: the Agent lists the
cards first and has the Planner complete the step from the Bank's list, and if it still
can't tell which card is meant, the Customer picks one of the listed cards.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from itertools import count
from typing import Any

from agent.events import BankRefusal, ClarificationOption, ClarificationQuestion, PlanStep, StepCard
from agent.executor import StepResult
from agent.language import Language
from agent.planner import Plan

LOOKUP = "list_cards"
APPLY = "apply_virtual_card"
"""The Operation that adds a card: its output is the new card, as the list would show it."""


@dataclass(frozen=True)
class Card:
    """What the Agent keeps of a card the Bank listed: how the Customer tells it apart,
    never its status, limit or locks, which a later turn must read from the Bank again."""

    card_id: str
    name: str
    last4: str
    """The Bank shows only the last four digits of a card number."""

    def as_data(self) -> dict[str, Any]:
        return {"card_id": self.card_id, "name": self.name, "last4": self.last4}

    def label(self, language: Language) -> str:
        """How the Customer tells this card apart from their others."""
        if language == "en":
            return f"{self.name}, ending {self.last4}"
        return f"{self.name}（尾号 {self.last4}）"


def _card(view: dict[str, Any]) -> Card:
    return Card(card_id=view["card_id"], name=view["name"], last4=view["masked_number"][-4:])


def known(results: list[StepResult]) -> dict[str, Card]:
    """The cards the Bank listed (or issued) in `results`, by ID, in the order it showed
    them first."""
    found: dict[str, Card] = {}
    for result in results:
        if result.output is None:
            continue
        if result.operation == LOOKUP:
            for view in result.output["cards"]:
                card = _card(view)
                found[card.card_id] = card
        elif result.operation == APPLY:
            card = _card(result.output["card"])
            found[card.card_id] = card
    return found


def on_card(steps: list[PlanStep], found: dict[str, Card]) -> list[StepCard]:
    """Which card each step acting on a card the Bank listed acts on, in card order."""
    return [
        StepCard(step_id=step.id, **found[card_id].as_data())
        for step in steps
        if isinstance(card_id := step.input.get("card_id"), str) and card_id in found
    ]


def made_up(
    steps: list[PlanStep],
    found: dict[str, Card],
    catalogue: list[dict[str, Any]],
    refusals: Mapping[str, BankRefusal],
) -> list[PlanStep]:
    """The steps of an Operation on a card (by the catalogue) that name no card, or one
    the Bank never listed (`found`) and can't find (`not_found` in `refusals`, its tier
    preview): a card_id the Planner made up. A step waiting on a `list_cards` step of the
    Plan isn't one, the Planner completes it from that list; and a card the Bank finds,
    or refuses as another Customer's, goes ahead for the Bank to decide."""
    on_a_card = {
        op["name"] for op in catalogue if "card_id" in op["input_schema"].get("required", [])
    }
    listing = {step.id for step in steps if step.operation == LOOKUP}
    return [
        step
        for step in steps
        if step.operation in on_a_card
        and not listing & set(step.depends_on)
        and (
            not isinstance(card_id := step.input.get("card_id"), str)
            or (
                card_id not in found
                and (refusal := refusals.get(step.id)) is not None
                and refusal.reason == "not_found"
            )
        )
    ]


def listed_first(plan: Plan, steps: list[PlanStep]) -> Plan:
    """The Plan with the cards listed first: `steps` wait on a `list_cards` look-up, without
    the card_id they made up, and the Planner completes them from the Bank's list."""
    ids = {step.id for step in plan.steps}
    look_up = next(id for n in count(1) if (id := f"cards-{n}") not in ids)
    waiting = {step.id for step in steps}
    return plan.model_copy(
        update={
            "steps": [
                PlanStep(id=look_up, operation=LOOKUP, input={}, depends_on=[]),
                *(
                    step.model_copy(
                        update={
                            "input": {k: v for k, v in step.input.items() if k != "card_id"},
                            "depends_on": [*step.depends_on, look_up],
                        }
                    )
                    if step.id in waiting
                    else step
                    for step in plan.steps
                ),
            ]
        }
    )


def which_one(found: dict[str, Card], language: Language) -> ClarificationQuestion:
    """The question to ask when the Planner still names no card the Bank listed, even
    with the list: which of the listed cards the Customer means. Never a guess."""
    return ClarificationQuestion(
        text="Which card do you mean?" if language == "en" else "您指的是哪一张卡？",
        options=[
            ClarificationOption(label=card.label(language), value=card.card_id)
            for card in found.values()
        ],
    )
