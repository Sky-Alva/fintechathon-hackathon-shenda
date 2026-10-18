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


class Answer(BaseModel):
    type: Literal["answer"] = "answer"
    text: str


class Refusal(BaseModel):
    """The Agent won't do this; `text` explains and lists what it can do. Ends the turn."""

    type: Literal["refusal"] = "refusal"
    reason: Literal["out_of_scope", "manipulation", "secret"]
    text: str


Event = StepStarted | StepSucceeded | Answer | Refusal


# A conversation's history, as Staff Members read it: the Customer's messages, each Plan
# with the Bank's tiers, and every event the Customer was sent, in order.


class CustomerMessageEntry(BaseModel):
    type: Literal["customer_message"] = "customer_message"
    text: str


class PlanEntry(BaseModel):
    """A Plan the Agent made, after the Bank's tier preview: every Plan, Green-only too."""

    type: Literal["plan"] = "plan"
    steps: list[PlanStep]


HistoryEntry = CustomerMessageEntry | PlanEntry | Event
