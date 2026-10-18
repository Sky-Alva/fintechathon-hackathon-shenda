"""The Plan reviewer: asks the Bank's tier preview what each step of a Plan will need.

The Bank is the authority on tiers (ADR-0001); the reviewer only records what it says, in
the order the steps will run, so that crossing the ¥1,000 line shows up front.
"""

from dataclasses import dataclass

from agent.bank import BankSession
from agent.events import PlanStep
from agent.planner import Plan


@dataclass(frozen=True)
class ReviewedPlan:
    steps: list[PlanStep]
    """In the order they will run, each with the Bank's tier."""

    @property
    def green_only(self) -> bool:
        """Runs straight away, with no Plan card."""
        return all(step.tier == "green" for step in self.steps)


def review(plan: Plan, bank: BankSession) -> ReviewedPlan:
    ordered = plan.in_dependency_order()
    preview = bank.tier_preview([{"operation": s.operation, "input": s.input} for s in ordered])
    return ReviewedPlan(
        steps=[
            step.model_copy(update={"tier": tiered.get("tier")})
            for step, tiered in zip(ordered, preview, strict=True)
        ]
    )
