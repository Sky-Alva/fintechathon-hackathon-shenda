"""What the Customer names by the Bank's own list of it: a card (`list_cards`) or a
Subscription (`list_subscriptions`). A step on one takes its Bank ID from that list.

An ID the Planner made up anyway ("3" for "card #3", "SUB-TUNEBOX" for TuneBox) never
runs: the Agent lists them first and has the Planner complete the step from the Bank's
list, and if it still can't tell which one is meant, the Customer picks one of those listed.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from itertools import count
from typing import Any, Protocol

from agent.events import BankRefusal, ClarificationOption, ClarificationQuestion, PlanStep
from agent.executor import StepResult
from agent.language import Language
from agent.planner import Plan


class Listed(Protocol):
    def label(self, language: Language) -> str:
        """How the Customer tells this one apart from their others."""
        ...


@dataclass(frozen=True)
class Reference:
    """One kind of thing the Customer names by the Bank's list of it."""

    lookup: str
    """The Green Operation that lists them."""
    key: str
    """The input that names one, by its Bank ID."""
    prefix: str
    """For the ID of the look-up step the Agent adds."""
    known: Callable[[list[StepResult]], Mapping[str, Listed]]
    """Those the Bank listed in some results, by ID, in the order it showed them first."""
    question: Mapping[Language, str]
    """Which one the Customer means, asked with those listed as options."""


def _needing(kind: Reference, catalogue: list[dict[str, Any]]) -> set[str]:
    """The Operations that require `kind.key`, by the catalogue."""
    return {op["name"] for op in catalogue if kind.key in op["input_schema"].get("required", [])}


def unnamed(
    kind: Reference, steps: list[PlanStep], catalogue: list[dict[str, Any]]
) -> list[PlanStep]:
    """The steps of an Operation that needs `kind.key` (by the catalogue) that name none
    yet, waiting on a `kind.lookup` step or not."""
    needs_one = _needing(kind, catalogue)
    return [
        step
        for step in steps
        if step.operation in needs_one and not isinstance(step.input.get(kind.key), str)
    ]


def made_up(
    kind: Reference,
    steps: list[PlanStep],
    found: Mapping[str, Listed],
    catalogue: list[dict[str, Any]],
    refusals: Mapping[str, BankRefusal],
) -> list[PlanStep]:
    """The steps of an Operation that needs `kind.key` (by the catalogue) that name none,
    or one the Bank never listed (`found`) and can't find (`not_found` in `refusals`, its
    tier preview): an ID the Planner made up. A step waiting on a `kind.lookup` step of the
    Plan isn't one, the Planner completes it from that list; and one the Bank finds, or
    refuses as another Customer's, goes ahead for the Bank to decide."""
    needs_one = _needing(kind, catalogue)
    listing = {step.id for step in steps if step.operation == kind.lookup}
    return [
        step
        for step in steps
        if step.operation in needs_one
        and not listing & set(step.depends_on)
        and (
            not isinstance(id := step.input.get(kind.key), str)
            or (
                id not in found
                and (refusal := refusals.get(step.id)) is not None
                and refusal.reason == "not_found"
            )
        )
    ]


def listed_first(kind: Reference, plan: Plan, steps: list[PlanStep]) -> Plan:
    """The Plan with them listed first: `steps` wait on a `kind.lookup` look-up, without
    the ID they made up, and the Planner completes them from the Bank's list."""
    ids = {step.id for step in plan.steps}
    look_up = next(id for n in count(1) if (id := f"{kind.prefix}-{n}") not in ids)
    waiting = {step.id for step in steps}
    return plan.model_copy(
        update={
            "steps": [
                PlanStep(id=look_up, operation=kind.lookup, input={}, depends_on=[]),
                *(
                    step.model_copy(
                        update={
                            "input": {k: v for k, v in step.input.items() if k != kind.key},
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


def which_one(
    kind: Reference, found: Mapping[str, Listed], language: Language
) -> ClarificationQuestion:
    """The question to ask when the Planner still names none the Bank listed, even with
    the list: which of those listed the Customer means. Never a guess."""
    return ClarificationQuestion(
        text=kind.question[language],
        options=[
            ClarificationOption(label=listed.label(language), value=id)
            for id, listed in found.items()
        ],
    )
