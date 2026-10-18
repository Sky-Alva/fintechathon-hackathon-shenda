"""The Executor: runs a reviewed Plan's steps against the Bank, in order, as events."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from agent.bank import BankSession
from agent.events import Event, StepStarted, StepSucceeded
from agent.reviewer import ReviewedPlan


@dataclass(frozen=True)
class StepResult:
    """What the Bank returned for one step: the only facts the Responder may state."""

    step_id: str
    operation: str
    input: dict[str, Any]
    output: dict[str, Any]


def execute(plan: ReviewedPlan, bank: BankSession, results: list[StepResult]) -> Iterator[Event]:
    """Run every step, appending each Bank result to `results`."""
    for step in plan.steps:
        yield StepStarted(step=step)
        result = bank.call(step.operation, step.input)
        results.append(StepResult(step.id, step.operation, step.input, result["output"]))
        yield StepSucceeded(
            step_id=step.id,
            operation=step.operation,
            tier=result["tier"],
            output=result["output"],
        )
