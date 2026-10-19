"""The Executor: runs a reviewed Plan's steps against the Bank, in order, as events."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from agent.bank import BankRefused, BankSession, BankUnavailable
from agent.events import Event, PlanStep, StepFailed, StepStarted, StepSucceeded
from agent.reviewer import ReviewedPlan


# A step's failure when the Bank couldn't be reached: not a Bank refusal reason.
BANK_UNAVAILABLE: dict[str, Any] = {
    "reason": "bank_unavailable",
    "message": "The Bank could not be reached",
    "detail": {},
}


@dataclass(frozen=True)
class StepResult:
    """What the Bank returned for one step: the only facts the Responder may state.

    A step that didn't run has no `output`, and `refusal` says why (the Bank's
    `reason`, `message` and `detail`)."""

    step_id: str
    operation: str
    input: dict[str, Any]
    output: dict[str, Any] | None
    refusal: dict[str, Any] | None = None


def execute(plan: ReviewedPlan, bank: BankSession, results: list[StepResult]) -> Iterator[Event]:
    """Run every step, appending each Bank result to `results`. A step the tier preview
    refused is never sent to the Bank."""
    for step in plan.steps:
        if (refusal := plan.refusals.get(step.id)) is not None:
            yield _failed(step, refusal, results)
            continue
        yield StepStarted(step=step)
        try:
            result = bank.call(step.operation, step.input)
        except BankRefused as refused:
            yield _failed(step, refused.body(), results)
            continue
        except BankUnavailable:
            yield _failed(step, BANK_UNAVAILABLE, results)
            continue
        results.append(StepResult(step.id, step.operation, step.input, result["output"]))
        yield StepSucceeded(
            step_id=step.id, operation=step.operation, tier=result["tier"], output=result["output"]
        )


def _failed(step: PlanStep, refusal: dict[str, Any], results: list[StepResult]) -> StepFailed:
    results.append(StepResult(step.id, step.operation, step.input, None, refusal))
    return StepFailed(step_id=step.id, operation=step.operation, **refusal)
