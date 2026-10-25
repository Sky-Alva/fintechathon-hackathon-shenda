"""The prompts waiting for the Customer's answer, and the state of a Plan while it runs.

A conversation has at most one prompt waiting (`Conversation.pending`): a Plan card, a Red
step's verification, a stopped Plan or a clarifying question. An offer to undo a failed
Plan's steps, or to cancel Subscriptions, can wait beside it. Each prompt says which
Customer actions answer it.
"""

import threading
from dataclasses import dataclass, field
from typing import Any

from agent.actions import (
    CancelPlan,
    ConfirmPlan,
    RejectPlan,
    RemoveSteps,
    ResumePlan,
    VerifiedStep,
    WrongCode,
)
from agent.events import (
    ClarificationQuestion,
    ReversalStep,
    Subscription,
    VerificationRequested,
)
from agent.executor import StepResult
from agent.reviewer import ReviewedPlan

PlanAnswer = (
    ConfirmPlan | RemoveSteps | RejectPlan | VerifiedStep | WrongCode | ResumePlan | CancelPlan
)
"""The actions that answer a prompt about a Plan."""


@dataclass
class PendingPlan:
    """A Plan card waiting for the Customer's answer."""

    plan_id: str
    plan: ReviewedPlan
    looked_up: list[StepResult]
    """What the Plan's Green look-ups returned before the card was shown."""

    def answered_by(self, action: PlanAnswer) -> bool:
        """Confirm, remove_steps and reject answer the card."""
        return isinstance(action, ConfirmPlan | RemoveSteps | RejectPlan) and (
            action.plan_id == self.plan_id
        )


@dataclass
class PendingVerification:
    """A confirmed Plan paused at a Red step, waiting for its Strong Verification."""

    plan_id: str
    plan: ReviewedPlan
    """The steps still to run, the Red step first."""
    results: list[StepResult]
    """What the Bank returned for the Plan so far, look-ups included."""
    confirmation_tokens: dict[str, str]
    """The Customer's Confirmations of the Yellow steps still to run."""
    requested: VerificationRequested
    card: list[str]
    """The Plan card's step IDs, in order: the Customer knows each step by its number."""

    def answered_by(self, action: PlanAnswer) -> bool:
        """A verification answers its own Red step."""
        return isinstance(action, VerifiedStep | WrongCode) and (
            (action.plan_id, action.step_id) == (self.plan_id, self.requested.step_id)
        )


@dataclass
class PendingClarification:
    """A clarifying question waiting for the Customer's answer: a tap on one of its
    options (`choose`), or a new message."""

    question: ClarificationQuestion

    def answered_by(self, action: PlanAnswer) -> bool:
        """Only `choose` (or a new message) answers a question."""
        return False


@dataclass
class RunningPlan:
    """A confirmed Plan whose steps are running: the Customer can stop it."""

    plan_id: str
    stop: threading.Event = field(default_factory=threading.Event)


@dataclass
class PausedPlan:
    """A confirmed Plan the Customer stopped: waiting to be resumed, changed (by a new
    message) or cancelled."""

    plan_id: str
    card: list[str]
    """The Plan card's step IDs, in order: the Customer knows each step by its number."""
    plan: ReviewedPlan
    """The steps still to run, as the Bank tiered them."""
    confirmation_tokens: dict[str, str]
    """The Customer's Confirmations of the Yellow steps still to run. Never used for
    another Plan."""
    results: list[StepResult]
    """What the Bank returned for the Plan so far, look-ups included."""

    def answered_by(self, action: PlanAnswer) -> bool:
        """Resume and cancel answer a stopped Plan."""
        return isinstance(action, ResumePlan | CancelPlan) and action.plan_id == self.plan_id

    @property
    def ran(self) -> list[str]:
        """The card's steps the Bank has run, in card order."""
        ran = {result.step_id for result in self.results if result.output is not None}
        return [id for id in self.card if id in ran]


@dataclass
class ReversalOffer:
    """A failed Plan's completed reversible steps, offered for undoing, waiting for the
    Customer's answer."""

    plan_id: str
    """The Plan that failed."""
    steps: list[ReversalStep]
    """In the order they would be undone: the reverse of the order they ran."""


@dataclass
class CancellationOffer:
    """The Subscriptions the Bank flagged as possibly unneeded, offered for the Customer to
    pick which to cancel, until they answer or send a new message."""

    offer_id: str
    candidates: list[Subscription]
    """The Bank's Subscriptions, as `list_subscriptions` returned them."""


Prompt = PendingPlan | PendingVerification | PausedPlan | PendingClarification
"""A prompt waiting for the Customer's answer."""


def without(plan: ReviewedPlan, removed: set[str]) -> set[str]:
    """The card's steps that go when the Customer removes `removed`: those, and every step
    left with nothing on the card to wait on, since all the card's steps it waits on went."""
    on_card = {step.id for step in plan.steps}
    gone = set(removed)
    changed = True
    while changed:
        changed = False
        for step in plan.steps:
            waits_on = [id for id in step.depends_on if id in on_card]
            if step.id not in gone and waits_on and all(id in gone for id in waits_on):
                gone.add(step.id)
                changed = True
    return gone


def interruption(pending: Prompt | None) -> dict[str, Any] | None:
    """Where a confirmed Plan stopped (by the Customer, or at a Red step waiting for its
    verification), for the Planner: what the Bank returned for the steps that ran, and the
    steps that didn't."""
    if not isinstance(pending, PausedPlan | PendingVerification):
        return None
    return {
        "ran": [result.as_data() for result in pending.results],
        "not_run": [step.model_dump(mode="json", exclude={"tier"}) for step in pending.plan.steps],
    }
