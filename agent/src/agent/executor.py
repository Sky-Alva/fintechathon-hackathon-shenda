"""The Executor: runs a reviewed Plan's steps against the Bank, in order, as events."""

import threading
from collections.abc import Generator, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from agent import charts, failures
from agent.bank import BankRefused, BankSession, BankUnavailable
from agent.events import (
    BankRefusal,
    ChartForm,
    Event,
    PlanStep,
    Reversal,
    StepFailed,
    StepStarted,
    StepSucceeded,
)
from agent.language import Language
from agent.reviewer import ReviewedPlan


# A step's failure when the Bank couldn't be reached: not a Bank refusal reason.
BANK_UNAVAILABLE = BankRefusal(
    reason="bank_unavailable", message="The Bank could not be reached", detail={}
)


@dataclass(frozen=True)
class StepResult:
    """What the Bank returned for one step: the only facts the Responder may state.

    A step that didn't run has no `output`, and `refusal` says why (the Bank's
    `reason`, `message` and `detail`)."""

    step_id: str
    operation: str
    input: dict[str, Any]
    output: dict[str, Any] | None
    refusal: BankRefusal | None = None
    reversal: Reversal | None = None
    """The call that would undo the step, as the Bank said when it ran it; None if it
    can't be undone or changed nothing."""

    def as_data(self) -> dict[str, Any]:
        """The result as plain data, for the model (inside a `<bank_data>` block)."""
        return {
            "step_id": self.step_id,
            "operation": self.operation,
            "input": self.input,
            "output": self.output,
            "refusal": self.refusal.model_dump() if self.refusal else None,
            "reversal": self.reversal.model_dump() if self.reversal else None,
        }


@dataclass(frozen=True)
class Halt:
    """Where a Plan paused before running all its steps: `step` is the first not yet run."""

    step: PlanStep
    why: Literal["verification", "stop", "failed"]
    """`verification`: the step is Red and needs its Strong Verification first. `stop`: the
    Customer tapped stop. `failed`: the Bank refused the step (or couldn't be reached) when
    it ran, so the steps after it won't run."""


def execute(
    plan: ReviewedPlan,
    bank: BankSession,
    results: list[StepResult],
    confirmation_tokens: Mapping[str, str] | None = None,
    verification_tokens: Mapping[str, str] | None = None,
    stop: threading.Event | None = None,
    language: Language = "zh",
    chart_form: ChartForm = "bars",
) -> Generator[Event, None, Halt | None]:
    """Run the steps in order, appending each Bank result to `results`.

    A step the tier preview refused is never sent to the Bank; the steps after it still
    run, as the Plan card showed. A step the Bank refuses (or can't be reached for) when
    it runs stops the Plan: the steps after it don't run, and its Halt is returned.
    `confirmation_tokens` holds the Customer's Confirmation for each Yellow step, by step
    ID, and `verification_tokens` the Strong Verification of each Red step. A chart of
    categories is drawn as `chart_form`.

    Nothing Red runs without its verification token: the Plan pauses at the first Red
    step that has none. Once `stop` is set (the Customer tapped stop), no further step
    starts; a step already with the Bank finishes. Either way the Plan's Halt is
    returned. Otherwise (every step ran or was refused by the tier preview) returns None.
    """
    confirmations = confirmation_tokens or {}
    verifications = verification_tokens or {}
    for step in plan.steps:
        if stop is not None and stop.is_set():
            return Halt(step, "stop")
        if (refusal := plan.refusals.get(step.id)) is not None:
            yield failed(step, refusal, results, language)
            continue
        if step.tier == "red" and step.id not in verifications:
            return Halt(step, "verification")
        yield StepStarted(step=step)
        try:
            result = bank.call(
                step.operation,
                step.input,
                confirmations.get(step.id),
                verifications.get(step.id),
            )
        except BankRefused as refused:
            yield failed(step, refused.body(), results, language)
            return Halt(step, "failed")
        except BankUnavailable:
            yield failed(step, BANK_UNAVAILABLE, results, language)
            return Halt(step, "failed")
        reversal = result.get("reversal")
        results.append(
            StepResult(
                step.id,
                step.operation,
                step.input,
                result["output"],
                None,
                Reversal.model_validate(reversal) if reversal is not None else None,
            )
        )
        yield StepSucceeded(
            step_id=step.id, operation=step.operation, tier=result["tier"], output=result["output"]
        )
        if (drawn := charts.chart(
            step.id, step.operation, result["output"], language, chart_form
        )) is not None:
            yield drawn
    return None


def failed(
    step: PlanStep, refusal: BankRefusal, results: list[StepResult], language: Language
) -> StepFailed:
    """The step didn't run: record the Bank's refusal and explain it by its reason."""
    results.append(StepResult(step.id, step.operation, step.input, None, refusal))
    text = failures.explain(refusal, language)
    return StepFailed(
        step_id=step.id, operation=step.operation, text=text, **refusal.model_dump()
    )
