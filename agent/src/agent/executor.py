"""The Executor: runs a reviewed Plan's steps against the Bank, in order, as events."""

from collections.abc import Generator, Mapping
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


def execute(
    plan: ReviewedPlan,
    bank: BankSession,
    results: list[StepResult],
    confirmation_tokens: Mapping[str, str] | None = None,
    verification_tokens: Mapping[str, str] | None = None,
) -> Generator[Event, None, PlanStep | None]:
    """Run the steps in order, appending each Bank result to `results`.

    A step the tier preview refused is never sent to the Bank; the steps after it still
    run, as the Plan card showed. A step the Bank refuses (or can't be reached for) when
    it runs stops the Plan: the steps after it don't run. `confirmation_tokens` holds the
    Customer's Confirmation for each Yellow step, by step ID, and `verification_tokens`
    the Strong Verification of each Red step.

    Nothing Red runs without its verification token: the Plan pauses at the first Red
    step that has none, and that step is returned, not yet run. Otherwise returns None.
    """
    confirmations = confirmation_tokens or {}
    verifications = verification_tokens or {}
    for step in plan.steps:
        if (refusal := plan.refusals.get(step.id)) is not None:
            yield failed(step, refusal, results)
            continue
        if step.tier == "red" and step.id not in verifications:
            return step
        yield StepStarted(step=step)
        try:
            result = bank.call(
                step.operation,
                step.input,
                confirmations.get(step.id),
                verifications.get(step.id),
            )
        except BankRefused as refused:
            yield failed(step, refused.body(), results)
            return None
        except BankUnavailable:
            yield failed(step, BANK_UNAVAILABLE, results)
            return None
        results.append(StepResult(step.id, step.operation, step.input, result["output"]))
        yield StepSucceeded(
            step_id=step.id, operation=step.operation, tier=result["tier"], output=result["output"]
        )
    return None


def failed(step: PlanStep, refusal: dict[str, Any], results: list[StepResult]) -> StepFailed:
    results.append(StepResult(step.id, step.operation, step.input, None, refusal))
    return StepFailed(step_id=step.id, operation=step.operation, **refusal)
