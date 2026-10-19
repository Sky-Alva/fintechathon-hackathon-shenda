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


class PlanWarning(BaseModel):
    """Why a Red step on the Plan card needs Strong Verification, in the Customer's
    language: it takes the Daily Transfer Total above ¥1,000, or it is Red in itself."""

    step_id: str
    reason: Literal["daily_transfer_total", "strong_verification"]
    text: str


class PlanProposed(BaseModel):
    """A Plan card: the Plan has a Yellow or Red step. `steps` are in the order they will
    run, each with the Bank's tier; `refusals` are steps the tier preview says can't run;
    `warnings` say why each Red step needs Strong Verification."""

    type: Literal["plan_proposed"] = "plan_proposed"
    plan_id: str
    steps: list[PlanStep]
    refusals: list[StepRefusal]
    warnings: list[PlanWarning] = []


class ConfirmationRequested(BaseModel):
    """Ask the Customer to confirm every Yellow step of the Plan card with one tap."""

    type: Literal["confirmation_requested"] = "confirmation_requested"
    plan_id: str
    step_ids: list[str]


class VerificationRequested(BaseModel):
    """A Red step needs Strong Verification: the Bank has texted a code to the Customer's
    simulated phone. The Customer answers with `verified` and the verification token the
    phone got back from the Bank; the code itself never reaches the Agent."""

    type: Literal["verification_requested"] = "verification_requested"
    plan_id: str
    step_id: str
    challenge_id: str
    sent_to: str
    expires_at: str


class StepFailed(BankRefusal):
    """The step didn't run. `reason`, `message` and `detail` are the Bank's refusal,
    unchanged, or `bank_unavailable` when the Agent couldn't reach the Bank. `text` says
    why in plain words, in the Customer's language, from `reason` alone."""

    type: Literal["step_failed"] = "step_failed"
    step_id: str
    operation: str
    text: str


class ReversalStep(BaseModel):
    """How one completed step would be undone: the Operation that reverses it, with its
    input, as the Bank said when the step ran."""

    step_id: str
    """The completed step it would undo."""
    operation: str
    input: dict[str, Any]


class ReversalOffered(BaseModel):
    """A step of the Plan failed: the Agent offers to undo its completed reversible steps,
    in reverse order. Nothing is undone unless the Customer answers with `reverse`; an
    accepted undo is a new Plan, with its own Plan card and Permission Tiers. `reject`
    declines it: the steps stay done."""

    type: Literal["reversal_offered"] = "reversal_offered"
    plan_id: str
    """The Plan that failed."""
    steps: list[ReversalStep]
    text: str


class PlanPaused(BaseModel):
    """The Customer tapped stop: no further step of the Plan starts until they resume,
    change or cancel it. `ran` are the card's steps the Bank ran, `not_run` those still to
    run; `text` tells the Customer which, by their number on the Plan card. Ends the turn."""

    type: Literal["plan_paused"] = "plan_paused"
    plan_id: str
    ran: list[str]
    not_run: list[str]
    text: str


class Answer(BaseModel):
    type: Literal["answer"] = "answer"
    text: str


class Trace(BaseModel):
    """Decision-trace data for the Channel's toggleable panel. Never carries a token,
    password or verification code."""

    type: Literal["trace"] = "trace"
    stage: Literal["intent", "plan", "bank_request", "bank_response", "audit_trail"]
    data: dict[str, Any]


class Refusal(BaseModel):
    """The Agent won't do this; `text` explains and lists what it can do. Ends the turn.

    `secret`: a full card number, a password or a code, or another Customer's
    information (after the Bank's `forbidden_other_customer` step_failed)."""

    type: Literal["refusal"] = "refusal"
    reason: Literal["out_of_scope", "manipulation", "secret"]
    text: str


class SessionLocked(BaseModel):
    """The Bank has put the session under a Safety Lock: only Green Operations run until
    `locked_until` or until a Staff Member lifts it. `text` tells the Customer so, in
    their language. Ends the turn."""

    type: Literal["session_locked"] = "session_locked"
    trigger: str
    locked_until: str
    text: str


class HumanRequested(BaseModel):
    """The Customer asked for a human: the staff console flags the session until a Staff
    Member takes it over. `text` tells the Customer so."""

    type: Literal["human_requested"] = "human_requested"
    text: str


class HandedOver(BaseModel):
    """A Staff Member took the conversation over: the Agent sends nothing and starts no
    step until it is handed back."""

    type: Literal["handed_over"] = "handed_over"
    staff: str


class HandedBack(BaseModel):
    """The Staff Member handed the conversation back to the Agent."""

    type: Literal["handed_back"] = "handed_back"


class StaffMessage(BaseModel):
    """A reply from the Staff Member who took the conversation over, not from the Agent."""

    type: Literal["staff_message"] = "staff_message"
    staff: str
    text: str


Event = (
    StepStarted
    | StepSucceeded
    | StepFailed
    | Answer
    | Refusal
    | PlanProposed
    | ConfirmationRequested
    | PlanPaused
    | ReversalOffered
    | VerificationRequested
    | SessionLocked
    | HumanRequested
    | HandedOver
    | StaffMessage
    | HandedBack
    | Trace
)


# A conversation's history, as Staff Members read it: each Customer message or action,
# then every event the Customer was sent (the decision trace included), in order.


class CustomerMessageEntry(BaseModel):
    type: Literal["customer_message"] = "customer_message"
    text: str


class CustomerActionEntry(BaseModel):
    """The Customer's answer to a prompt: which action, on which Plan card. Never the
    action's other fields, so no token reaches the history."""

    type: Literal["customer_action"] = "customer_action"
    action: str
    plan_id: str | None
    """The Plan card it answers; None for an action that answers none (`ask_for_human`)."""


HistoryEntry = CustomerMessageEntry | CustomerActionEntry | Event
