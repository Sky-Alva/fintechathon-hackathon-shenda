"""The Plan reviewer: asks the Bank's tier preview what each step of a Plan will need.

The Bank is the authority on tiers (ADR-0001); the reviewer only records what it says, in
the order the steps will run, so that crossing the ¥1,000 line shows up front.
"""

from dataclasses import dataclass, field
from typing import Any

from agent.bank import BankRefused, BankSession
from agent.events import PlanStep, StepRefusal
from agent.planner import Plan, irreversible


@dataclass(frozen=True)
class ReviewedPlan:
    steps: list[PlanStep]
    """In the order they will run, each with the Bank's tier."""
    refusals: dict[str, dict[str, Any]] = field(default_factory=dict)
    """The Bank's refusal (`reason`, `message`, `detail`) of each step that can't run, by
    step ID. Those steps have no tier."""

    @property
    def green_only(self) -> bool:
        """Runs straight away, with no Plan card: every step that can run is Green."""
        return all(step.tier == "green" for step in self.steps if step.id not in self.refusals)

    @property
    def locked(self) -> dict[str, Any] | None:
        """The Safety Lock (`trigger`, `locked_until`) if the Bank refused a step because
        the session is under one: then nothing but Green Operations would run."""
        for refusal in self.refusals.values():
            if refusal["reason"] == "session_locked":
                lock: dict[str, Any] = refusal["detail"]
                return lock
        return None

    @property
    def other_customers(self) -> list[PlanStep]:
        """The steps the Bank refused because their input names another Customer's
        account, card or the like (`forbidden_other_customer`): the spouse's included."""
        return [
            step
            for step in self.steps
            if self.refusals.get(step.id, {}).get("reason") == "forbidden_other_customer"
        ]

    @property
    def yellow_steps(self) -> list[PlanStep]:
        """The steps one tap on the Plan card confirms."""
        return [step for step in self.steps if step.tier == "yellow"]

    def from_step(self, step_id: str) -> "ReviewedPlan":
        """The steps still to run from this one on, as when the Plan resumes after it
        paused for this step's Strong Verification."""
        ids = [step.id for step in self.steps]
        return ReviewedPlan(steps=self.steps[ids.index(step_id) :], refusals=self.refusals)

    @property
    def card_refusals(self) -> list[StepRefusal]:
        """The refused steps, as the Plan card lists them."""
        return [StepRefusal(step_id=id, **refusal) for id, refusal in self.refusals.items()]


class ConfirmationRefused(Exception):
    """The Bank refused to issue the Plan card's Confirmations because of this step, so no
    token was issued and nothing may run."""

    def __init__(self, step: PlanStep, refused: BankRefused):
        super().__init__(f"{step.id}: {refused}")
        self.step = step
        self.refusal = refused.body()


def collect_confirmations(plan: ReviewedPlan, bank: BankSession) -> dict[str, str]:
    """The Customer tapped confirm on this Plan card: get one Confirmation token per Yellow
    step on it, in one request, in the order they will run. Returns tokens by step ID.

    The Agent's session could ask the Bank for tokens on its own, so the Bank can't tell
    whether the Customer really confirmed. This is the only place that asks, and the
    Conversation service calls it only for the Customer's confirm action.
    """
    steps = plan.yellow_steps
    if not steps:
        return {}
    try:
        confirmations = bank.request_confirmations(
            [{"operation": step.operation, "input": step.input} for step in steps]
        )
    except BankRefused as refused:
        # Things changed since the tier preview (e.g. the Daily Transfer Total grew), so
        # the Bank won't confirm one of the steps; it says which by its index.
        index = refused.detail.get("index", 0)
        step = steps[index] if isinstance(index, int) and 0 <= index < len(steps) else steps[0]
        raise ConfirmationRefused(step, refused) from None
    return {
        step.id: confirmation["confirmation_token"]
        for step, confirmation in zip(steps, confirmations, strict=True)
    }


def review(
    plan: Plan, bank: BankSession, catalogue: list[dict[str, Any]] | None = None
) -> ReviewedPlan:
    """The Plan in the order it will run, each step with the Bank's tier or refusal. With
    the `catalogue`, irreversible Operations go as late as the dependencies allow; without
    it the Plan keeps its order (a Plan reviewed before, with some steps removed)."""
    ordered = plan.in_dependency_order(irreversible(catalogue or []))
    if not ordered:
        return ReviewedPlan(steps=[])
    preview = bank.tier_preview([{"operation": s.operation, "input": s.input} for s in ordered])
    pairs = list(zip(ordered, preview, strict=True))
    return ReviewedPlan(
        steps=[step.model_copy(update={"tier": tiered.get("tier")}) for step, tiered in pairs],
        refusals={step.id: tiered["refusal"] for step, tiered in pairs if tiered.get("refusal")},
    )
