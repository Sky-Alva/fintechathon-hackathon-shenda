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
    language: it takes the Daily Transfer Total above ¥1,000, it is a large first transfer to
    a Payee the Customer has never paid, or it is Red in itself."""

    step_id: str
    reason: Literal["daily_transfer_total", "strong_verification", "first_time_payee"]
    text: str


class CardPayee(BaseModel):
    """Who a step on the Plan card pays, as the Bank's Payee lookup found them:
    `is_new` when the Customer has never paid them. `phone` is masked by the Bank."""

    step_id: str
    payee_id: str
    name: str
    nickname: str | None
    phone: str | None
    is_new: bool


class StepCard(BaseModel):
    """Which of the Customer's cards a step on the Plan card acts on, as the Bank listed
    it: its name and the last four digits of its number, never the full number."""

    step_id: str
    card_id: str
    name: str
    last4: str


class PlanProposed(BaseModel):
    """A Plan card: the Plan has a Yellow or Red step. `steps` are in the order they will
    run, each with the Bank's tier; `refusals` are steps the tier preview says can't run;
    `warnings` say why each Red step needs Strong Verification."""

    type: Literal["plan_proposed"] = "plan_proposed"
    plan_id: str
    steps: list[PlanStep]
    refusals: list[StepRefusal]
    warnings: list[PlanWarning] = []
    payees: list[CardPayee] = []
    cards: list[StepCard] = []


class ConfirmationRequested(BaseModel):
    """Ask the Customer to confirm every Yellow step of the Plan card with one tap."""

    type: Literal["confirmation_requested"] = "confirmation_requested"
    plan_id: str
    step_ids: list[str]


class VerificationRequested(BaseModel):
    """A Red step needs Strong Verification: the Bank has texted one code to the
    Customer's simulated phone, for it and the Plan's other Red steps it could cover. The
    Customer answers with `verified` and the verification tokens the Channel's SMS
    verification box got back from the Bank; the code itself never reaches the Agent."""

    type: Literal["verification_requested"] = "verification_requested"
    plan_id: str
    step_id: str
    """The Red step the Plan is paused at."""
    step_ids: list[str]
    """Every step the one code approves, in order, `step_id` first. The Bank returns one
    verification token per step, in this order."""
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


class Reversal(BaseModel):
    """The call that would undo a completed step: the Operation that reverses it, with its
    input, as the Bank said when the step ran."""

    operation: str
    input: dict[str, Any]


class ReversalStep(Reversal):
    """How one completed step would be undone."""

    step_id: str
    """The completed step it would undo."""


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


class SubscriptionReason(BaseModel):
    """One of the Bank's reasons a Subscription may not be needed."""

    reason: Literal["same_category", "price_increase", "trial_converted"]
    detail: str


class Subscription(BaseModel):
    """A Subscription as the Bank's `list_subscriptions` returns it, unchanged."""

    subscription_id: str
    merchant: str
    category: str
    original_amount: str
    current_amount: str
    signed_up_on: str
    status: Literal["active", "cancelled"]
    next_renewal_date: str | None
    reasons_it_may_not_be_needed: list[SubscriptionReason]


class CancellationOffered(BaseModel):
    """The Subscriptions the Bank flags as possibly unneeded, for the Customer to pick
    which to cancel. Each candidate is the Bank's Subscription, unchanged, with the Bank's
    `reasons_it_may_not_be_needed`; `text` lists them in the Customer's language. Nothing
    is cancelled unless the Customer answers with `cancel_subscriptions`, and then only
    through a Plan card. Ends the turn."""

    type: Literal["cancellation_offered"] = "cancellation_offered"
    offer_id: str
    candidates: list[Subscription]
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


class ClarificationOption(BaseModel):
    """One answer to a clarifying question: `label` is shown, `value` is sent back with
    `choose`. Never a full account or phone number."""

    label: str
    value: str


class ClarificationQuestion(BaseModel):
    """One short question when the request is genuinely ambiguous (which 张伟?) or misses
    something the Agent needs (how much?). The Customer taps an option (`choose`) or
    answers in words. Ends the turn."""

    type: Literal["clarification_question"] = "clarification_question"
    text: str
    options: list[ClarificationOption] = []


ChartOperation = Literal["spending_by_category", "monthly_report", "annual_report"]
"""The Bank Operations whose output the Channel draws as a chart."""

ChartForm = Literal["bars", "pie"]
"""How the Channel draws a chart's categories: bars, or a pie when the Customer asked for one."""


class Chart(BaseModel):
    """A chart of what the Bank returned for one step: a category summary, or a monthly or
    annual report. `data` is the Bank's output for the step, unchanged: every number the
    Channel draws is the Bank's. `title` names it in the Customer's language, from the
    period the Bank's output gives. `form` is "pie" when the Customer asked for a pie chart
of the categories, else "bars". Follows the step's `step_succeeded`."""

    type: Literal["chart"] = "chart"
    step_id: str
    operation: ChartOperation
    title: str
    data: dict[str, Any]
    form: ChartForm = "bars"


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


class InjectionWarning(BaseModel):
    """What the Bank returned for a step holds instruction-like text (a Payee's note that
    tries to give orders): the Agent doesn't follow it and carries on with what the
    Customer asked. `reported` says whether the Bank accepted the report of the suspected
    injection; `text` warns the Customer, in their language."""

    type: Literal["injection_warning"] = "injection_warning"
    step_id: str
    operation: str
    reported: bool
    text: str


LockTrigger = Literal["wrong_codes", "reported_attempts", "red_operations", "staff"]
"""What made the Bank place a Safety Lock; `staff`: a Staff Member set it by hand."""


class SessionLocked(BaseModel):
    """The Bank has put the session's Customer under a Safety Lock: only Green Operations
    run, in any of their sessions, until `locked_until` or until a Staff Member lifts it.
    `text` tells the Customer so, in their language. Ends the turn."""

    type: Literal["session_locked"] = "session_locked"
    trigger: LockTrigger
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
    | Chart
    | Answer
    | Refusal
    | InjectionWarning
    | PlanProposed
    | ConfirmationRequested
    | PlanPaused
    | ReversalOffered
    | ClarificationQuestion
    | CancellationOffered
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
