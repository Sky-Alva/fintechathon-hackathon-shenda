"""The Plan reviewer: asks the Bank's tier preview what each step of a Plan will need.

The Bank is the authority on tiers (ADR-0001); the reviewer only records what it says, in
the order the steps will run, so that crossing the ¥1,000 line shows up front.
"""

from dataclasses import dataclass, field
from typing import Any

from agent.bank import BankSession
from agent.events import PlanStep
from agent.planner import Plan


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


def review(plan: Plan, bank: BankSession) -> ReviewedPlan:
    ordered = plan.in_dependency_order()
    preview = bank.tier_preview([{"operation": s.operation, "input": s.input} for s in ordered])
    return ReviewedPlan(
        steps=[
            step.model_copy(update={"tier": tiered.get("tier")})
            for step, tiered in zip(ordered, preview, strict=True)
        ],
        refusals={
            step.id: tiered["refusal"]
            for step, tiered in zip(ordered, preview, strict=True)
            if tiered.get("refusal")
        },
    )
