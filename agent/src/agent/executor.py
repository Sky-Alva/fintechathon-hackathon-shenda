"""The Executor: runs a reviewed Plan's steps against the Bank, in order, as events."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from agent.bank import BankRefused, BankSession
from agent.events import Event, StepFailed, StepStarted, StepSucceeded
from agent.reviewer import ReviewedPlan


@dataclass(frozen=True)
class StepResult:
    """What the Bank returned for one step: the only facts the Responder may state.
    A step the Bank refused has its refusal (`reason`, `message`, `detail`) and no output."""

    step_id: str
    operation: str
    input: dict[str, Any]
    output: dict[str, Any]
    refusal: dict[str, Any] | None = None


def execute(
    plan: ReviewedPlan,
    bank: BankSession,
    results: list[StepResult],
    confirmation_tokens: Mapping[str, str] | None = None,
) -> Iterator[Event]:
    """Run the steps in order, appending each Bank result to `results`, until one is
    refused: the steps after it don't run. `confirmation_tokens` holds the Customer's
    Confirmation for each Yellow step, by step ID."""
    tokens = confirmation_tokens or {}
    for step in plan.steps:
        yield StepStarted(step=step)
        try:
            result = bank.call(step.operation, step.input, tokens.get(step.id))
        except BankRefused as refused:
            refusal = refused.body()
            results.append(StepResult(step.id, step.operation, step.input, {}, refusal))
            yield StepFailed(step_id=step.id, operation=step.operation, **refusal)
            return
        results.append(StepResult(step.id, step.operation, step.input, result["output"]))
        yield StepSucceeded(
            step_id=step.id,
            operation=step.operation,
            tier=result["tier"],
            output=result["output"],
        )
