"""The Plan reviewer: asks the Bank's tier preview what each step of a Plan will need.

The Bank is the authority on tiers (ADR-0001); the reviewer only records what it says, in
the order the steps will run, so that crossing the ¥1,000 line shows up front.
"""

from dataclasses import dataclass

from agent.bank import BankRefused, BankSession
from agent.events import PlanStep, StepRefusal
from agent.planner import Plan


@dataclass(frozen=True)
class ReviewedPlan:
    steps: list[PlanStep]
    """In the order they will run, each with the Bank's tier (none if it was refused)."""
    refusals: list[StepRefusal]
    """Steps the tier preview says can never run, with the Bank's reason."""

    @property
    def green_only(self) -> bool:
        """Runs straight away, with no Plan card."""
        return all(step.tier == "green" for step in self.steps)

    @property
    def yellow_steps(self) -> list[PlanStep]:
        """The steps one tap on the Plan card confirms."""
        return [step for step in self.steps if step.tier == "yellow"]


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


def review(plan: Plan, bank: BankSession) -> ReviewedPlan:
    ordered = plan.in_dependency_order()
    if not ordered:
        return ReviewedPlan(steps=[], refusals=[])
    preview = bank.tier_preview([{"operation": s.operation, "input": s.input} for s in ordered])
    pairs = list(zip(ordered, preview, strict=True))
    return ReviewedPlan(
        steps=[step.model_copy(update={"tier": tiered.get("tier")}) for step, tiered in pairs],
        refusals=[
            StepRefusal(step_id=step.id, **tiered["refusal"])
            for step, tiered in pairs
            if "refusal" in tiered
        ],
    )
