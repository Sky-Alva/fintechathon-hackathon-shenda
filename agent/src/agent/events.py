"""Conversation events: what a turn streams back to the Channel and the staff console.

The full contract, including the event types later slices add, is
docs/conversation-events.md. Each event is one JSON object with a `type`.
"""

from typing import Any, Literal

from pydantic import BaseModel


class PlanStep(BaseModel):
    """One Operation of a Plan, with the Bank's tier for it once the tier preview ran."""

    id: str
    operation: str
    input: dict[str, Any]
    depends_on: list[str]
    tier: Literal["green", "yellow", "red"] | None = None


class StepStarted(BaseModel):
    type: Literal["step_started"] = "step_started"
    step: PlanStep


class StepSucceeded(BaseModel):
    type: Literal["step_succeeded"] = "step_succeeded"
    step_id: str
    operation: str
    tier: Literal["green", "yellow", "red"]
    output: dict[str, Any]


class BankRefusal(BaseModel):
    """A refusal from the Bank, unchanged: one of its refusal reasons, with its detail."""

    reason: str
    message: str
    detail: dict[str, Any]


class StepRefusal(BankRefusal):
    step_id: str


class PlanProposed(BaseModel):
    """A Plan card: the Plan has a Yellow or Red step. `steps` are in the order they will
    run, each with the Bank's tier; `refusals` are steps the tier preview says can't run."""

    type: Literal["plan_proposed"] = "plan_proposed"
    plan_id: str
    steps: list[PlanStep]
    refusals: list[StepRefusal]


class ConfirmationRequested(BaseModel):
    """Ask the Customer to confirm every Yellow step of the Plan card with one tap."""

    type: Literal["confirmation_requested"] = "confirmation_requested"
    plan_id: str
    step_ids: list[str]


class StepFailed(BankRefusal):
    """The Bank refused the step: `reason`, `message` and `detail` are its refusal."""

    type: Literal["step_failed"] = "step_failed"
    step_id: str
    operation: str


class Answer(BaseModel):
    type: Literal["answer"] = "answer"
    text: str


Event = (
    StepStarted | StepSucceeded | StepFailed | Answer | PlanProposed | ConfirmationRequested
)
