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


class StepFailed(BaseModel):
    """The step didn't run. `reason`, `message` and `detail` are the Bank's refusal,
    unchanged, or `bank_unavailable` when the Agent couldn't reach the Bank."""

    type: Literal["step_failed"] = "step_failed"
    step_id: str
    operation: str
    reason: str
    message: str
    detail: dict[str, Any]


class Answer(BaseModel):
    type: Literal["answer"] = "answer"
    text: str


Event = StepStarted | StepSucceeded | StepFailed | Answer
