"""The Conversation service: owns conversations and their history, and runs each turn.

A turn takes one Customer message or action (the answer to a Plan card) and yields the
turn's events: plan → review → execute → answer. A Plan with a Yellow or Red step stops
at its Plan card instead, and runs only when the Customer confirms it.
"""

import secrets
import threading
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent import guard, notices, payees, planner, refusals, responder, reviewer, secrets_guard
from agent.bank import BankRefused, BankSession, BankUnavailable, HttpClient
from agent.events import (
    Answer,
    ClarificationQuestion,
    ConfirmationRequested,
    CustomerActionEntry,
    CustomerMessageEntry,
    Event,
    HandedBack,
    HandedOver,
    HistoryEntry,
    HumanRequested,
    PlanPaused,
    PlanProposed,
    PlanStep,
    Refusal,
    ReversalOffered,
    ReversalStep,
    SessionLocked,
    StaffMessage,
    Trace,
    VerificationRequested,
)
from agent.executor import BANK_UNAVAILABLE, StepResult, execute, failed
from agent.language import Language, customer_language
from agent.model import ModelClient
from agent.reviewer import ReviewedPlan
from agent.trace import trace, with_bank_traces

HANDED_BACK_NOTE = (
    "人工客服 {staff} 接管过这段对话，现在交还给你（Agent）。标有 [Staff Member {staff}] 的消息是"
    "人工客服写的，不是你写的。\n"
    "The Staff Member {staff} took this conversation over and has handed it back to you (the"
    " Agent). Messages marked [Staff Member {staff}] were written by them, not by you."
)

CHOSEN_NOTE = (
    "客户对上面的问题选择了：{label}（{value}）。\n"
    "The Customer answered the question above by choosing: {label} ({value})."
)


@dataclass
class PendingPlan:
    """A Plan card waiting for the Customer's answer."""

    plan_id: str
    plan: ReviewedPlan
    looked_up: list[StepResult]
    """What the Plan's Green look-ups returned before the card was shown."""


@dataclass
class PendingVerification:
    """A confirmed Plan paused at a Red step, waiting for its Strong Verification."""

    plan_id: str
    plan: ReviewedPlan
    """The steps still to run, the Red step first."""
    results: list[StepResult]
    """What the Bank returned for the Plan so far, look-ups included."""
    confirmation_tokens: dict[str, str]
    """The Customer's Confirmations of the Yellow steps still to run."""
    requested: VerificationRequested
    card: list[str]
    """The Plan card's step IDs, in order: the Customer knows each step by its number."""


@dataclass
class PendingClarification:
    """A clarifying question waiting for the Customer's answer: a tap on one of its
    options (`choose`), or a new message."""

    question: ClarificationQuestion


@dataclass
class RunningPlan:
    """A confirmed Plan whose steps are running: the Customer can stop it."""

    plan_id: str
    stop: threading.Event = field(default_factory=threading.Event)


@dataclass
class PausedPlan:
    """A confirmed Plan the Customer stopped: waiting to be resumed, changed (by a new
    message) or cancelled."""

    plan_id: str
    card: list[str]
    """The Plan card's step IDs, in order: the Customer knows each step by its number."""
    plan: ReviewedPlan
    """The steps still to run, as the Bank tiered them."""
    confirmation_tokens: dict[str, str]
    """The Customer's Confirmations of the Yellow steps still to run. Never used for
    another Plan."""
    results: list[StepResult]
    """What the Bank returned for the Plan so far, look-ups included."""


@dataclass
class ReversalOffer:
    """A failed Plan's completed reversible steps, offered for undoing, waiting for the
    Customer's answer."""

    plan_id: str
    """The Plan that failed."""
    steps: list[ReversalStep]
    """In the order they would be undone: the reverse of the order they ran."""


@dataclass
class Conversation:
    conversation_id: str
    bank: BankSession
    session_id: str
    """The Bank's ID for the session: Staff Members read its audit trail by it."""
    customer: dict[str, str]
    """`{customer_id, name}`, as the Bank told it."""
    history: list[dict[str, Any]] = field(default_factory=list)
    """The Customer's messages and the Agent's answers, in the model's chat format."""
    pending: PendingPlan | PendingVerification | PausedPlan | PendingClarification | None = None
    """The prompt waiting for the Customer's answer: a Plan card, a Red step's
    verification, a stopped Plan, or a clarifying question."""
    reversal: ReversalOffer | None = None
    """The offer to undo a failed Plan's completed steps, until the Customer answers it or
    sends a new message. Apart from `pending`: a new Plan card for what's left of the goal
    can wait at the same time."""
    running: RunningPlan | None = None
    """The confirmed Plan whose steps are running, from the Customer's tap until its turn
    ends."""
    plans_proposed: int = 0
    bank_results: list[StepResult] = field(default_factory=list)
    """Every Bank result so far: the facts an answer may state."""
    record: list[HistoryEntry] = field(default_factory=list)
    """Everything Staff Members read: each Customer message or action and every event
    sent (the decision trace included, so the Plan, its tiers and every Bank call), in
    order."""
    handed_over_to: str | None = None
    """The Staff Member who took the conversation over, while they have it: the Agent is
    paused."""
    updates: list[Event] = field(default_factory=list)
    """The events sent outside the Customer's own turns (a Staff Member taking over,
    replying, handing back), which the Channel polls for."""
    human_requested: bool = False
    """The Customer asked for a human and no Staff Member has taken over since."""
    lock: threading.Lock = field(default_factory=threading.Lock)

    def next_plan_id(self) -> str:
        self.plans_proposed += 1
        return f"p{self.plans_proposed}"


class ConfirmPlan(BaseModel):
    """One tap on the Plan card: confirm every Yellow step on it."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["confirm"]
    plan_id: str
    removed_step_ids: list[str] = []
    """Steps the Customer took off the card first: the rest are re-tiered by the Bank."""


class RejectPlan(BaseModel):
    """Reject the Plan card: nothing on it runs."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["reject"]
    plan_id: str


class AskForHuman(BaseModel):
    """The Customer wants a Staff Member: at any time, whatever else is going on."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["ask_for_human"]


class VerifiedStep(BaseModel):
    """The Customer typed the right code into the simulated phone, which sent it straight
    to the Bank and hands the Agent only the verification token it got back."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["verified"]
    plan_id: str
    step_id: str
    verification_token: str = Field(min_length=1)


class WrongCode(BaseModel):
    """The Bank refused the code the Customer typed into the simulated phone
    (`token_invalid`); `failed_attempts` is the Bank's count of wrong codes this session.
    The Agent asks the Bank itself whether that locked the session."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["wrong_code"]
    plan_id: str
    step_id: str
    failed_attempts: int = Field(ge=1)


class ReverseSteps(BaseModel):
    """Accept the reversal offer, for these of its steps: the undo becomes a new Plan
    card."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["reverse"]
    plan_id: str
    step_ids: list[str] = Field(min_length=1)


class ChooseOption(BaseModel):
    """The Customer tapped one of the clarifying question's options."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["choose"]
    value: str


class StopPlan(BaseModel):
    """The stop button: no further step of the Plan starts."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["stop"]
    plan_id: str


class ResumePlan(BaseModel):
    """Run the rest of a stopped Plan, as confirmed."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["resume"]
    plan_id: str


class CancelPlan(BaseModel):
    """Drop the rest of a stopped Plan: nothing more of it runs."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["cancel"]
    plan_id: str


CustomerAction = Annotated[
    ConfirmPlan
    | RejectPlan
    | AskForHuman
    | VerifiedStep
    | WrongCode
    | ReverseSteps
    | StopPlan
    | ResumePlan
    | CancelPlan
    | ChooseOption,
    Field(discriminator="action"),
]

HUMAN_REQUESTED: dict[Language, str] = {
    "zh": "已为您呼叫人工客服，请稍候。",
    "en": "I've asked for a Staff Member to join you. Please wait a moment.",
}

REJECTED: dict[Language, str] = {
    "zh": "好的，已取消这个计划，没有执行任何操作。",
    "en": "OK, I've cancelled that Plan. Nothing was done.",
}
"""The Agent's acknowledgement of a rejected Plan card: nothing ran, so nothing to report."""

CANCELLED: dict[Language, str] = {
    "zh": "好的，已取消剩下的步骤，不会再执行。已执行的步骤不受影响。",
    "en": "OK, I've cancelled the rest of the Plan; nothing more will run. The steps that"
    " already ran stay done.",
}
"""The Agent's acknowledgement of a cancelled stopped Plan: the pause already said what ran."""

NOT_REVERSED: dict[Language, str] = {
    "zh": "好的，不撤销，已完成的步骤保持不变。",
    "en": "OK, I won't undo anything: the steps that ran stay as they are.",
}
"""The Agent's acknowledgement of a declined reversal offer."""

NO_CLEAN_UP = {"session_locked", "not_authenticated", "forbidden_other_customer"}
"""Refusals after which nothing more is offered: under a Safety Lock only Green Operations
run (the lock notice says so), a session the Bank no longer knows runs nothing, and another
Customer's information is never looked for another way (#44)."""


def reversal_text(numbers: list[str], language: Language) -> str:
    """The offer to undo the card's steps with these numbers, in the order they'd be
    undone."""
    if language == "en":
        steps = f"step{'s' if len(numbers) > 1 else ''} {', '.join(numbers)}"
        return (
            f"I can undo what already ran: {steps}, in that order. Shall I? Undoing is a new"
            " Plan: you'll see its Plan card and approve it first. Otherwise they stay done."
        )
    return (
        f"已完成的步骤可以撤销：按第 {'、'.join(numbers)} 步的顺序。需要撤销吗？"
        "撤销是一个新的计划，会先给您看计划卡片并请您确认；不撤销则保持不变。"
    )


def asked(clarification: planner.Clarification) -> ClarificationQuestion:
    """The Planner's question as the Customer sees it: model-written, so nothing that
    looks like a full card number or a code goes out."""
    return ClarificationQuestion(
        text=secrets_guard.mask(clarification.question),
        options=[
            option.model_copy(
                update={
                    "label": secrets_guard.mask(option.label),
                    "value": secrets_guard.mask(option.value),
                }
            )
            for option in clarification.options
        ],
    )


def plan_data(plan: ReviewedPlan, look_ups: bool = False) -> dict[str, Any]:
    """A reviewed Plan for the decision trace: its look-ups, which run first, are marked
    `look_ups`, and the steps the tier preview refused are listed under `refusals`."""
    data: dict[str, Any] = {"steps": [step.model_dump(mode="json") for step in plan.steps]}
    if look_ups:
        data["look_ups"] = True
    if plan.refusals:
        data["refusals"] = [refusal.model_dump(mode="json") for refusal in plan.card_refusals]
    return data


def answers(
    action: ConfirmPlan | RejectPlan | VerifiedStep | WrongCode | ResumePlan | CancelPlan,
    pending: PendingPlan | PendingVerification | PausedPlan | PendingClarification | None,
) -> bool:
    """Whether the action answers the prompt waiting for an answer: confirm and reject
    answer a Plan card, a verification answers its own Red step, resume and cancel answer
    a stopped Plan."""
    if pending is None or isinstance(pending, PendingClarification):
        return False
    if pending.plan_id != action.plan_id:
        return False
    if isinstance(action, VerifiedStep | WrongCode):
        return isinstance(pending, PendingVerification) and (
            pending.requested.step_id == action.step_id
        )
    if isinstance(action, ResumePlan | CancelPlan):
        return isinstance(pending, PausedPlan)
    return isinstance(pending, PendingPlan)


def paused_text(paused: PausedPlan, language: Language) -> str:
    """Which of the card's steps ran and which didn't, by their number on the card."""

    def numbers(ids: list[str]) -> list[str]:
        return [str(paused.card.index(id) + 1) for id in ids if id in paused.card]

    done = numbers(ran_steps(paused))
    not_run = numbers([step.id for step in paused.plan.steps])
    if language == "en":
        parts = ["Paused: no further step will run."]
        if done:
            parts.append(f"Done: step{'s' if len(done) > 1 else ''} {', '.join(done)}.")
        parts.append(f"Not done: step{'s' if len(not_run) > 1 else ''} {', '.join(not_run)}.")
        parts.append("You can resume, change or cancel the rest.")
        return " ".join(parts)
    parts = ["已暂停，后面的步骤不会执行。"]
    if done:
        parts.append(f"已执行：第 {'、'.join(done)} 步。")
    parts.append(f"未执行：第 {'、'.join(not_run)} 步。")
    parts.append("您可以继续、修改或取消剩下的步骤。")
    return "".join(parts)


def ran_steps(paused: PausedPlan) -> list[str]:
    """The card's steps the Bank has run, in card order."""
    ran = {result.step_id for result in paused.results if result.output is not None}
    return [id for id in paused.card if id in ran]


def interruption(
    pending: PendingPlan | PendingVerification | PausedPlan | PendingClarification | None,
) -> dict[str, Any] | None:
    """Where a confirmed Plan stopped (by the Customer, or at a Red step waiting for its
    verification), for the Planner: what the Bank returned for the steps that ran, and the
    steps that didn't."""
    if not isinstance(pending, PausedPlan | PendingVerification):
        return None
    return {
        "ran": [asdict(result) for result in pending.results],
        "not_run": [step.model_dump(mode="json", exclude={"tier"}) for step in pending.plan.steps],
    }


class UnknownConversation(Exception):
    pass


class PlanNotPending(Exception):
    """The action answers a Plan card that isn't waiting for an answer: answered already,
    replaced by a newer request, or never shown."""


class UnknownStep(Exception):
    """The Customer removed a step that isn't on the Plan card."""


class UnknownOption(Exception):
    """The Customer chose a value that isn't one of the waiting question's options."""


class PlanNotRunning(Exception):
    """The Customer tapped stop, but that Plan isn't running or waiting for a
    verification."""


class NotHandedOver(Exception):
    """Only a conversation a Staff Member took over gets staff replies or is handed back."""


class AlreadyHandedOver(Exception):
    """A Staff Member has the conversation: no one else takes it over, and the Agent
    acts on nothing the Customer sends."""

    def __init__(self, staff: str):
        super().__init__(staff)
        self.staff = staff


class Conversations:
    def __init__(self, bank: HttpClient, model: ModelClient):
        self.bank = bank
        self.model = model
        self.conversations: dict[str, Conversation] = {}

    def start(self, session_token: str) -> Conversation:
        """A conversation acting through one Customer's own Bank session. Refused
        (`BankRefused`, `not_authenticated`) if the Bank doesn't know the session."""
        bank = BankSession(self.bank, session_token)
        current = bank.current()
        conversation = Conversation(
            conversation_id=secrets.token_urlsafe(16),
            bank=bank,
            session_id=current["session_id"],
            customer=current["customer"],
        )
        self.conversations[conversation.conversation_id] = conversation
        return conversation

    def get(self, conversation_id: str) -> Conversation:
        try:
            return self.conversations[conversation_id]
        except KeyError:
            raise UnknownConversation(conversation_id) from None

    def active(self) -> list[Conversation]:
        """The conversations whose Bank session is still active, in the order they
        started."""
        active = []
        for conversation in list(self.conversations.values()):
            try:
                conversation.bank.current()
            except BankRefused as refused:
                if refused.reason != "not_authenticated":
                    raise
            else:
                active.append(conversation)
        return active

    def audit_trail(self, conversation: Conversation) -> Trace:
        """The session's audit trail as the Bank recorded it, for the decision trace."""
        return trace("audit_trail", conversation.bank.audit_trail())

    def send(self, conversation: Conversation, text: str) -> Iterator[Event]:
        """One turn: the Customer's message in, the turn's events out, with every Bank
        call of the turn in the decision trace."""
        return self._recorded(
            conversation, with_bank_traces(conversation.bank, self._turn(conversation, text))
        )

    def _recorded(self, conversation: Conversation, events: Iterator[Event]) -> Iterator[Event]:
        """The turn's events, each recorded in the history Staff Members read as sent."""
        for event in events:
            conversation.record.append(event)
            yield event

    def take_over(self, conversation: Conversation, staff: str) -> HandedOver:
        """A Staff Member takes the conversation over. A turn under way finishes first."""
        with conversation.lock:
            if conversation.handed_over_to is not None:
                raise AlreadyHandedOver(conversation.handed_over_to)
            conversation.handed_over_to = staff
            conversation.human_requested = False
            return self._push(conversation, HandedOver(staff=staff))

    def staff_reply(self, conversation: Conversation, text: str) -> StaffMessage:
        """The Staff Member who has the conversation replies to the Customer."""
        with conversation.lock:
            staff = conversation.handed_over_to
            if staff is None:
                raise NotHandedOver()
            # In the Agent's context too, marked as the Staff Member's, not the Agent's.
            conversation.history.append(
                {"role": "assistant", "content": f"[Staff Member {staff}] {text}"}
            )
            return self._push(conversation, StaffMessage(staff=staff, text=text))

    def hand_back(self, conversation: Conversation) -> HandedBack:
        """The Staff Member hands the conversation back: the Agent resumes, with what was
        said during the takeover in its context."""
        with conversation.lock:
            staff = conversation.handed_over_to
            if staff is None:
                raise NotHandedOver()
            conversation.handed_over_to = None
            note = HANDED_BACK_NOTE.format(staff=staff)
            conversation.history.append({"role": "system", "content": note})
            return self._push(conversation, HandedBack())

    def updates(self, conversation: Conversation, after: int) -> list[Event]:
        return conversation.updates[after:]

    def _push[E: Event](self, conversation: Conversation, event: E) -> E:
        """Send the Customer an event outside their own turn."""
        conversation.updates.append(event)
        conversation.record.append(event)
        return event

    def _turn(self, conversation: Conversation, text: str) -> Iterator[Event]:
        with conversation.lock:
            pending = conversation.pending
            # A code typed into the chat while a verification is pending is masked before
            # it goes anywhere: not into the history, the model or the staff history.
            code_typed = isinstance(pending, PendingVerification) and secrets_guard.has_code(
                text
            )
            if code_typed:
                text = secrets_guard.mask_codes(text)
            conversation.record.append(CustomerMessageEntry(text=text))
            conversation.history.append({"role": "user", "content": text})
            if code_typed and conversation.handed_over_to is None:
                assert isinstance(pending, PendingVerification)
                yield self._code_in_chat(conversation)
                yield pending.requested  # the challenge stays open for the phone
                return
            # A new request replaces an unanswered Plan card, and changes a stopped Plan (or
            # one waiting at a Red step): the Planner re-plans knowing where it stopped,
            # whatever it plans needs a new Plan card, and the old Confirmations are dropped.
            interrupted = interruption(conversation.pending)
            conversation.pending = None
            conversation.reversal = None  # declined by moving on: the steps stay done
            if conversation.handed_over_to is not None:
                # The message is the Staff Member's to answer: the Agent stays silent.
                return
            try:
                catalogue = conversation.bank.catalogue()
            except (BankRefused, BankUnavailable):
                yield self._not_retrieved(conversation)
                return
            if (suspicion := guard.scam(text)) is not None:
                # A known scam is never planned: no model can be talked into it.
                yield from self._manipulation(conversation, suspicion)
                return
            yield from self._plan(conversation, catalogue, interrupted=interrupted)

    def _plan(
        self,
        conversation: Conversation,
        catalogue: list[dict[str, Any]],
        interrupted: dict[str, Any] | None = None,
        failed: StepResult | None = None,
        failed_context: dict[str, Any] | None = None,
    ) -> Iterator[Event]:
        """Ask the Planner for the Plan, then run its look-ups and propose it. With `failed`
        (a step of the previous Plan that the Bank refused) and its `failed_context`, the
        Planner re-plans what's left of the goal: the failed call itself is never planned
        again as it was, and an empty Plan means there is nothing more to propose."""
        bank = conversation.bank
        language = customer_language(conversation.history)

        def ask(
            looked_up: list[StepResult] | None = None,
        ) -> planner.Plan | planner.Declined | planner.HumanAsked | planner.Clarification:
            plan = planner.make_plan(
                self.model,
                conversation.history,
                catalogue,
                [asdict(result) for result in looked_up] if looked_up else None,
                interrupted=interrupted,
                failed=failed_context,
                payees=[
                    payee.as_data() for payee in payees.known(conversation.bank_results).values()
                ],
            )
            if failed is None or not isinstance(plan, planner.Plan):
                return plan
            retried = (failed.operation, failed.input)
            return plan.model_copy(
                update={"steps": [s for s in plan.steps if (s.operation, s.input) != retried]}
            )

        plan = ask()
        if isinstance(plan, planner.Declined):
            yield from self._decline(conversation, plan, catalogue)
            return
        if isinstance(plan, planner.HumanAsked):
            yield trace("intent", {"intent": "", "ask_for_human": True})
            yield self._human_requested(conversation)
            return
        if isinstance(plan, planner.Clarification):
            question = asked(plan)
            yield trace("intent", {"intent": "", "clarification": question.text})
            yield self._clarify(conversation, question)
            return
        if failed is not None and not plan.steps:
            return  # nothing else reaches the goal: the answer already said what happened
        yield trace("intent", {"intent": plan.intent})
        # Exact details first: run the Green look-ups a later step's input waits on,
        # then have the Planner complete the Plan from what the Bank returned.
        looked_up: list[StepResult] = []
        look_ups = plan.look_ups(catalogue)
        if look_ups is not None:
            try:
                reviewed_look_ups = reviewer.review(look_ups, bank, catalogue)
            except (BankRefused, BankUnavailable):
                yield self._not_retrieved(conversation)
                return
            yield trace("plan", plan_data(reviewed_look_ups, look_ups=True))
            if reviewed_look_ups.other_customers:
                yield from self._other_customer(conversation, reviewed_look_ups)
                return
            if reviewed_look_ups.green_only:  # the Bank agrees they need no approval
                yield from execute(reviewed_look_ups, bank, looked_up, language=language)
                if (which := payees.which_one(looked_up, language)) is not None:
                    # Several Payees match: ask which one, never guess with the money.
                    self._remember(conversation, looked_up)
                    yield self._clarify(conversation, which)
                    return
                plan = ask(looked_up)
                if isinstance(plan, planner.Declined):  # not on offer, but just in case
                    yield from self._decline(conversation, plan, catalogue)
                    return
                if isinstance(plan, planner.HumanAsked):  # nor is this
                    yield self._human_requested(conversation)
                    return
                if isinstance(plan, planner.Clarification):
                    # Something the Bank doesn't show (no past rent, say): ask for it.
                    self._remember(conversation, looked_up)
                    yield self._clarify(conversation, asked(plan))
                    return
                if failed is not None and not plan.steps:
                    return
        try:
            reviewed = reviewer.review(plan, bank, catalogue)
        except (BankRefused, BankUnavailable):
            yield self._not_retrieved(conversation)
            return
        yield from self._propose(conversation, reviewed, looked_up, catalogue)

    def _propose(
        self,
        conversation: Conversation,
        reviewed: ReviewedPlan,
        looked_up: list[StepResult],
        catalogue: list[dict[str, Any]],
    ) -> Iterator[Event]:
        """The reviewed Plan's Plan card, or, if it needs none, its run."""
        yield trace("plan", plan_data(reviewed))
        if reviewed.other_customers:
            yield from self._other_customer(conversation, reviewed)
            return
        if (lock := reviewed.locked) is not None:
            # Under a Safety Lock only Green Operations run: the notice, no Plan card.
            yield self._locked(conversation, lock)
            return
        if reviewed.green_only:
            yield from self._run(conversation, reviewed, looked_up)
            return
        pending = PendingPlan(conversation.next_plan_id(), reviewed, looked_up)
        conversation.pending = pending
        yield PlanProposed(
            plan_id=pending.plan_id,
            steps=reviewed.steps,
            refusals=reviewed.card_refusals,
            warnings=notices.red_warnings(
                reviewed.steps, catalogue, customer_language(conversation.history)
            ),
            payees=payees.on_card(
                reviewed.steps, payees.known([*conversation.bank_results, *looked_up])
            ),
        )
        yield ConfirmationRequested(
            plan_id=pending.plan_id, step_ids=[step.id for step in reviewed.yellow_steps]
        )

    def act(self, conversation: Conversation, action: CustomerAction) -> Iterator[Event]:
        """One turn: the Customer's answer to a prompt (a Plan card) in, the events out.

        Raises PlanNotPending straight away, before any event, if the Plan card isn't
        waiting for an answer, and AlreadyHandedOver while a Staff Member has the
        conversation: the Agent starts no step then.
        """
        if isinstance(action, AskForHuman):
            return self._recorded(conversation, self._ask_for_human(conversation))
        if isinstance(action, StopPlan):
            return self.stop(conversation, action.plan_id)
        with conversation.lock:
            if conversation.handed_over_to is not None:
                raise AlreadyHandedOver(conversation.handed_over_to)
            if isinstance(action, ChooseOption):
                return self._choose(conversation, action.value)
            offer = conversation.reversal
            if isinstance(action, ReverseSteps) or (
                isinstance(action, RejectPlan)
                and offer is not None
                and offer.plan_id == action.plan_id
            ):
                return self._answer_reversal(conversation, action)
            pending = conversation.pending
            if not answers(action, pending):
                raise PlanNotPending(action.plan_id)
            removed: set[str] = set()
            if isinstance(action, ConfirmPlan):
                assert isinstance(pending, PendingPlan)
                removed = set(action.removed_step_ids)
                card = {step.id for step in pending.plan.steps}
                if not removed <= card:
                    raise UnknownStep(sorted(removed - card))
                if removed == card:  # nothing left to confirm: as good as a reject
                    action = RejectPlan(action="reject", plan_id=action.plan_id)
            conversation.pending = None  # a prompt is answered once
            conversation.record.append(
                CustomerActionEntry(action=action.action, plan_id=action.plan_id)
            )
            if isinstance(action, ConfirmPlan | VerifiedStep | ResumePlan):
                # Running from now: a stop tapped before the first step starts still counts.
                conversation.running = RunningPlan(action.plan_id)
        if isinstance(action, RejectPlan):
            return self._recorded(conversation, self._rejected(conversation))
        if isinstance(action, CancelPlan):
            return self._recorded(conversation, self._cancelled(conversation))
        if isinstance(action, VerifiedStep):
            assert isinstance(pending, PendingVerification)
            turn = self._verified(conversation, pending, action.verification_token)
        elif isinstance(action, WrongCode):
            assert isinstance(pending, PendingVerification)
            turn = self._wrong_code(conversation, pending, action.failed_attempts)
        elif isinstance(action, ResumePlan):
            assert isinstance(pending, PausedPlan)
            turn = self._resumed(conversation, pending)
        else:
            assert isinstance(pending, PendingPlan)
            turn = self._confirmed(conversation, pending, removed)
        return self._recorded(conversation, with_bank_traces(conversation.bank, turn))

    def _choose(self, conversation: Conversation, value: str) -> Iterator[Event]:
        """The Customer's tap on an option of the clarifying question: the Planner plans
        the request again, told which option it was. Called under the conversation's
        lock."""
        pending = conversation.pending
        if not isinstance(pending, PendingClarification):
            raise PlanNotPending("")
        chosen = next((o for o in pending.question.options if o.value == value), None)
        if chosen is None:
            raise UnknownOption(value)
        conversation.pending = None  # a question is answered once
        conversation.reversal = None
        conversation.record.append(CustomerActionEntry(action="choose", plan_id=None))
        # A note, not a Customer message: the option's label mustn't change the language.
        conversation.history.append(
            {"role": "system", "content": CHOSEN_NOTE.format(label=chosen.label, value=value)}
        )
        turn = self._replan(conversation)
        return self._recorded(conversation, with_bank_traces(conversation.bank, turn))

    def _replan(self, conversation: Conversation) -> Iterator[Event]:
        with conversation.lock:
            if conversation.handed_over_to is not None:
                return  # taken over since the Customer chose: the Agent starts nothing
            try:
                catalogue = conversation.bank.catalogue()
            except (BankRefused, BankUnavailable):
                yield self._not_retrieved(conversation)
                return
            yield from self._plan(conversation, catalogue)

    def _answer_reversal(
        self, conversation: Conversation, action: ReverseSteps | RejectPlan
    ) -> Iterator[Event]:
        """The Customer's answer to the reversal offer: undo these of its steps, as a new
        Plan card, or (`reject`) nothing. Called under the conversation's lock."""
        offer = conversation.reversal
        if offer is None or offer.plan_id != action.plan_id:
            raise PlanNotPending(action.plan_id)
        chosen: list[ReversalStep] = []
        if isinstance(action, ReverseSteps):
            offered = {step.step_id for step in offer.steps}
            if not set(action.step_ids) <= offered:
                raise UnknownStep(sorted(set(action.step_ids) - offered))
            chosen = [step for step in offer.steps if step.step_id in action.step_ids]
        conversation.reversal = None  # an offer is answered once
        conversation.record.append(
            CustomerActionEntry(action=action.action, plan_id=action.plan_id)
        )
        if not chosen:
            return self._recorded(conversation, self._not_reversed(conversation))
        turn = self._reverse(conversation, chosen)
        return self._recorded(conversation, with_bank_traces(conversation.bank, turn))

    def _not_reversed(self, conversation: Conversation) -> Iterator[Event]:
        with conversation.lock:
            text = NOT_REVERSED[customer_language(conversation.history)]
            conversation.history.append({"role": "assistant", "content": text})
            yield Answer(text=text)

    def _reverse(self, conversation: Conversation, chosen: list[ReversalStep]) -> Iterator[Event]:
        """The accepted undo, as a new Plan: the Bank tiers it and it gets its own Plan card,
        like any other. Nothing runs before the Customer approves it there."""
        with conversation.lock:
            if conversation.handed_over_to is not None:
                return  # taken over since the Customer accepted: the Agent starts nothing
            undo = planner.Plan(
                intent="undo",
                steps=[
                    PlanStep(
                        id=f"undo_{step.step_id}",
                        operation=step.operation,
                        input=step.input,
                        depends_on=[],
                    )
                    for step in chosen
                ],
            )
            try:
                catalogue = conversation.bank.catalogue()
                reviewed = reviewer.review(undo, conversation.bank, catalogue)
            except (BankRefused, BankUnavailable):
                yield self._not_retrieved(conversation)
                return
            conversation.pending = None  # the undo replaces an unanswered Plan card
            yield trace("intent", {"intent": undo.intent})
            yield from self._propose(conversation, reviewed, [], catalogue)

    def stop(self, conversation: Conversation, plan_id: str) -> Iterator[Event]:
        """The stop button. A running Plan starts no further step, and its own turn ends
        with `plan_paused`: the stop has no events of its own. A Plan waiting at a Red step
        for its verification is stopped there, and the stop's events are its
        `plan_paused`.

        Raises PlanNotRunning if the Plan is neither, and AlreadyHandedOver while a Staff
        Member has the conversation (a takeover waits for a running turn to end).
        """
        if conversation.handed_over_to is not None:
            raise AlreadyHandedOver(conversation.handed_over_to)
        running = conversation.running
        if running is not None and running.plan_id == plan_id:
            # Not under the conversation's lock: the running Plan's turn holds it.
            running.stop.set()
            conversation.record.append(CustomerActionEntry(action="stop", plan_id=plan_id))
            return iter(())
        with conversation.lock:
            if conversation.handed_over_to is not None:
                raise AlreadyHandedOver(conversation.handed_over_to)
            pending = conversation.pending
            if not isinstance(pending, PendingVerification) or pending.plan_id != plan_id:
                raise PlanNotRunning(plan_id)
            conversation.pending = None  # the verification no longer waits
            conversation.record.append(CustomerActionEntry(action="stop", plan_id=plan_id))
            paused = self._pause(
                conversation,
                PausedPlan(
                    plan_id, pending.card, pending.plan, pending.confirmation_tokens,
                    pending.results,
                ),
            )
        return self._recorded(conversation, iter([paused]))

    def _decline(
        self,
        conversation: Conversation,
        declined: planner.Declined,
        catalogue: list[dict[str, Any]],
    ) -> Iterator[Event]:
        if declined.reason == "manipulation":
            # Quote the Customer's own words, never the model's account of them.
            said = next(m["content"] for m in reversed(conversation.history) if m["role"] == "user")
            yield from self._manipulation(
                conversation,
                guard.Suspicion(declined.kind or "impersonation", guard.quoted(said)),
            )
            return
        declined_data = declined.model_dump(mode="json", exclude_none=True)
        yield trace("intent", {"intent": "", "declined": declined_data})
        # Nothing runs: the Customer hears what the Agent can do instead.
        text = refusals.declined(
            declined.reason, declined.can_do, catalogue, customer_language(conversation.history)
        )
        conversation.history.append({"role": "assistant", "content": text})
        yield Refusal(reason=declined.reason, text=text)

    def _other_customer(self, conversation: Conversation, plan: ReviewedPlan) -> Iterator[Event]:
        """The Bank refused a step naming another Customer's account, card or the like
        (the spouse's balance): the Bank's reason, then a polite refusal. Nothing in the
        Plan runs and the Agent doesn't look for another way to the same information."""
        language = customer_language(conversation.history)
        for step in plan.other_customers:
            yield failed(step, plan.refusals[step.id], [], language)
        text = refusals.other_customer(customer_language(conversation.history))
        conversation.history.append({"role": "assistant", "content": text})
        yield Refusal(reason="secret", text=text)

    def _clarify(
        self, conversation: Conversation, question: ClarificationQuestion
    ) -> ClarificationQuestion:
        """Ask the Customer one short question and wait for the answer."""
        conversation.pending = PendingClarification(question)
        listed = "".join(
            f"\n- {option.label} [{option.value}]" for option in question.options
        )
        conversation.history.append({"role": "assistant", "content": question.text + listed})
        return question

    def _code_in_chat(self, conversation: Conversation) -> Answer:
        """The Customer typed a code into the chat: point them to the simulated phone."""
        text = secrets_guard.CODE_IN_CHAT[customer_language(conversation.history)]
        conversation.history.append({"role": "assistant", "content": text})
        return Answer(text=text)

    def _manipulation(
        self, conversation: Conversation, suspicion: guard.Suspicion
    ) -> Iterator[Event]:
        """Refuse a suspected scam with a warning, and report it to the Bank, quoting the
        text that raised it; nothing else runs."""
        declined = {"reason": "manipulation", **asdict(suspicion)}
        yield trace("intent", {"intent": "", "declined": declined})
        try:
            report = conversation.bank.call(
                "report_suspected_attempt",
                {"kind": suspicion.kind, "evidence": suspicion.evidence},
            )
        except (BankRefused, BankUnavailable):
            report = None  # still refused: the Customer just isn't told it was reported
        lock = None
        if report is not None and report["output"]["session_locked"]:
            # The second report locks the session: the Bank's trail says until when.
            try:
                lock = conversation.bank.latest_lock()
            except (BankRefused, BankUnavailable):
                lock = None
        text = guard.warning(report is not None, customer_language(conversation.history))
        conversation.history.append({"role": "assistant", "content": text})
        yield Refusal(reason="manipulation", text=text)
        if lock is not None:
            yield self._locked(conversation, lock)

    def _ask_for_human(self, conversation: Conversation) -> Iterator[Event]:
        with conversation.lock:
            conversation.record.append(CustomerActionEntry(action="ask_for_human", plan_id=None))
            if conversation.handed_over_to is not None:
                return  # a Staff Member is already here
            yield self._human_requested(conversation)

    def _human_requested(self, conversation: Conversation) -> HumanRequested:
        conversation.human_requested = True
        text = HUMAN_REQUESTED[customer_language(conversation.history)]
        conversation.history.append({"role": "assistant", "content": text})
        return HumanRequested(text=text)

    def _rejected(self, conversation: Conversation) -> Iterator[Event]:
        with conversation.lock:
            text = REJECTED[customer_language(conversation.history)]
            conversation.history.append({"role": "assistant", "content": text})
            yield Answer(text=text)

    def _cancelled(self, conversation: Conversation) -> Iterator[Event]:
        with conversation.lock:
            text = CANCELLED[customer_language(conversation.history)]
            conversation.history.append({"role": "assistant", "content": text})
            yield Answer(text=text)

    def _confirmed(
        self, conversation: Conversation, pending: PendingPlan, removed: set[str]
    ) -> Iterator[Event]:
        """The Customer tapped confirm, with `removed` steps taken off the card first."""
        with conversation.lock:
            if conversation.handed_over_to is not None:
                # Taken over since the Customer confirmed: the Agent starts nothing.
                conversation.running = None
                return
            card = [step.id for step in pending.plan.steps]
            plan = pending.plan
            if removed:
                # Tiers depend on order (an earlier transfer counts toward the Daily Transfer
                # Total), so the Bank tiers the rest afresh, as they will now run: removing
                # an earlier transfer can turn a Red step Yellow.
                rest = [step for step in plan.steps if step.id not in removed]
                try:
                    plan = reviewer.review(planner.Plan(steps=rest), conversation.bank)
                except (BankRefused, BankUnavailable):
                    conversation.running = None
                    yield self._not_retrieved(conversation)
                    return
                yield trace("plan", plan_data(plan))
            try:
                tokens = reviewer.collect_confirmations(plan, conversation.bank)
            except reviewer.ConfirmationRefused as refused:
                conversation.running = None  # nothing runs
                results = list(pending.looked_up)
                language = customer_language(conversation.history)
                yield failed(refused.step, refused.refusal, results, language)
                yield from self._finish(conversation, results)
                return
            except BankUnavailable:
                conversation.running = None
                yield self._not_retrieved(conversation)
                return
            yield from self._run(
                conversation, plan, pending.looked_up, tokens, plan_id=pending.plan_id, card=card
            )

    def _verified(
        self, conversation: Conversation, pending: PendingVerification, verification_token: str
    ) -> Iterator[Event]:
        """Resume the Plan at its Red step, with the step's verification token."""
        with conversation.lock:
            if conversation.handed_over_to is not None:
                # Taken over since the Customer verified: the Agent starts nothing.
                conversation.running = None
                return
            yield from self._run(
                conversation,
                pending.plan,
                pending.results,
                pending.confirmation_tokens,
                {pending.requested.step_id: verification_token},
                plan_id=pending.plan_id,
                card=pending.card,
            )

    def _resumed(self, conversation: Conversation, paused: PausedPlan) -> Iterator[Event]:
        """Run the rest of a stopped Plan on the Confirmations the Customer gave it: the
        Plan hasn't changed. A Red step still to run asks for its verification as usual."""
        with conversation.lock:
            if conversation.handed_over_to is not None:
                # Taken over since the Customer tapped resume: the Agent starts nothing.
                conversation.running = None
                return
            yield from self._run(
                conversation,
                paused.plan,
                paused.results,
                paused.confirmation_tokens,
                plan_id=paused.plan_id,
                card=paused.card,
            )

    def _wrong_code(
        self, conversation: Conversation, pending: PendingVerification, failed_attempts: int
    ) -> Iterator[Event]:
        """The Bank refused a code from the phone: tell the Customer, who can try again on
        the same challenge."""
        with conversation.lock:
            if conversation.handed_over_to is not None:
                return  # taken over meanwhile: the Staff Member answers, not the Agent
            step = pending.plan.steps[0]
            # The phone's word isn't taken for it: ask the Bank whether the session is
            # locked now, with a tier preview of the step, which changes nothing.
            try:
                [preview] = conversation.bank.tier_preview(
                    [{"operation": step.operation, "input": step.input}]
                )
            except (BankRefused, BankUnavailable):
                preview = {}
            if (refusal := preview.get("refusal")) is not None:
                language = customer_language(conversation.history)
                yield failed(step, refusal, pending.results, language)
                yield from self._finish(conversation, pending.results)
                return
            text = notices.wrong_code(failed_attempts, customer_language(conversation.history))
            conversation.history.append({"role": "assistant", "content": text})
            conversation.pending = pending  # the same challenge still takes the right code
            yield Answer(text=text)
            yield pending.requested

    def _run(
        self,
        conversation: Conversation,
        plan: ReviewedPlan,
        looked_up: list[StepResult],
        confirmation_tokens: dict[str, str] | None = None,
        verification_tokens: dict[str, str] | None = None,
        plan_id: str | None = None,
        card: list[str] | None = None,
    ) -> Iterator[Event]:
        """Run the Plan, then answer from everything the Bank returned for it. A Plan that
        reaches a Red step pauses there for its Strong Verification instead, and one the
        Customer stops pauses where it is. `card` is a confirmed Plan's card, in order."""
        results = list(looked_up)
        tokens = confirmation_tokens or {}
        running = conversation.running
        stop = running.stop if running is not None and running.plan_id == plan_id else None
        language = customer_language(conversation.history)
        halt = yield from execute(
            plan, conversation.bank, results, tokens, verification_tokens, stop, language
        )
        conversation.running = None
        if halt is None or (halt.why == "failed" and (plan_id is None or card is None)):
            yield from self._finish(conversation, results)
            return
        assert plan_id is not None and card is not None, "only a confirmed Plan card halts"
        paused = halt.step
        if halt.why == "failed":
            not_run = plan.from_step(paused.id).steps[1:]
            yield from self._failed(conversation, results, not_run, plan_id, card)
            return
        if halt.why == "stop":
            remaining = plan.from_step(paused.id)
            unused = {step.id for step in remaining.steps}
            kept = {id: token for id, token in tokens.items() if id in unused}
            yield self._pause(conversation, PausedPlan(plan_id, card, remaining, kept, results))
            return
        # The challenge is asked for only now: a step may be Red only because of the
        # steps that ran before it.
        try:
            challenge = conversation.bank.request_verification(paused.operation, paused.input)
        except BankRefused as refused:
            yield failed(paused, refused.body(), results, language)
            not_run = plan.from_step(paused.id).steps[1:]
            yield from self._failed(conversation, results, not_run, plan_id, card)
            return
        except BankUnavailable:
            yield failed(paused, BANK_UNAVAILABLE, results, language)
            not_run = plan.from_step(paused.id).steps[1:]
            yield from self._failed(conversation, results, not_run, plan_id, card)
            return
        requested = VerificationRequested(
            plan_id=plan_id,
            step_id=paused.id,
            challenge_id=challenge["challenge_id"],
            sent_to=challenge["sent_to"],
            expires_at=challenge["expires_at"],
        )
        conversation.pending = PendingVerification(
            plan_id, plan.from_step(paused.id), results, tokens, requested, card
        )
        yield requested

    def _failed(
        self,
        conversation: Conversation,
        results: list[StepResult],
        not_run: list[PlanStep],
        plan_id: str,
        card: list[str],
    ) -> Iterator[Event]:
        """A step of a confirmed Plan failed (its `step_failed` is the last result): the
        answer, the offer to undo the Plan's completed reversible steps, then a new Plan for
        what's left of the goal (`not_run`: the steps after the failed one)."""
        yield from self._finish(conversation, results)
        failed_step = results[-1]
        refusal = failed_step.refusal
        assert refusal is not None, "the failed step is the last result"
        if refusal["reason"] in NO_CLEAN_UP:
            return
        # The card's completed steps that can be undone, the last to run first.
        done = [
            (result.step_id, result.reversal)
            for result in reversed(results)
            if result.output is not None and result.reversal is not None and result.step_id in card
        ]
        if done:
            offer = ReversalOffer(
                plan_id, [ReversalStep(step_id=id, **reversal) for id, reversal in done]
            )
            conversation.reversal = offer
            numbers = [str(card.index(id) + 1) for id, _ in done]
            text = reversal_text(numbers, customer_language(conversation.history))
            conversation.history.append({"role": "assistant", "content": text})
            yield ReversalOffered(plan_id=plan_id, steps=offer.steps, text=text)
        if refusal["reason"] == "bank_unavailable":
            return  # nothing to re-plan around: the Bank can't be reached
        try:
            catalogue = conversation.bank.catalogue()
        except (BankRefused, BankUnavailable):
            return
        context = {
            "ran": [asdict(result) for result in results[:-1] if result.output is not None],
            "failed": asdict(failed_step),
            "not_run": [step.model_dump(mode="json", exclude={"tier"}) for step in not_run],
        }
        yield from self._plan(conversation, catalogue, failed=failed_step, failed_context=context)

    def _pause(self, conversation: Conversation, paused: PausedPlan) -> PlanPaused:
        """The Plan is stopped: tell the Customer which steps ran and which didn't, and
        wait for resume, cancel or a change."""
        conversation.pending = paused
        text = paused_text(paused, customer_language(conversation.history))
        self._remember(conversation, paused.results)
        conversation.history.append({"role": "assistant", "content": text})
        return PlanPaused(
            plan_id=paused.plan_id,
            ran=ran_steps(paused),
            not_run=[step.id for step in paused.plan.steps],
            text=text,
        )

    def _finish(self, conversation: Conversation, results: list[StepResult]) -> Iterator[Event]:
        """End the Plan's turn: the answer from its results, then, if the Bank refused a
        step because the session is under a Safety Lock, the notice of what still works."""
        yield self._answer(conversation, results)
        for result in results:
            if result.refusal is not None and result.refusal["reason"] == "session_locked":
                yield self._locked(conversation, result.refusal["detail"])
                return

    def _locked(self, conversation: Conversation, lock: dict[str, Any]) -> SessionLocked:
        """The Safety Lock notice, from the detail of the Bank's `session_locked` refusal."""
        text = notices.safety_lock(
            lock["trigger"], lock["locked_until"], customer_language(conversation.history)
        )
        conversation.history.append({"role": "assistant", "content": text})
        return SessionLocked(trigger=lock["trigger"], locked_until=lock["locked_until"], text=text)

    def _answer(self, conversation: Conversation, results: list[StepResult]) -> Answer:
        """The answer from this Plan's Bank results (and earlier ones in the conversation)."""
        text = responder.answer(
            self.model, conversation.history, results, conversation.bank_results
        )
        # The outgoing check: nothing that looks like a full card number or a code goes
        # out, even when a Bank result contained it.
        text = secrets_guard.mask(text)
        self._remember(conversation, results)
        conversation.history.append({"role": "assistant", "content": text})
        return Answer(text=text)

    def _remember(self, conversation: Conversation, results: list[StepResult]) -> None:
        """Keep the Bank's results as facts later answers may state."""
        conversation.bank_results.extend(
            result for result in results if result not in conversation.bank_results
        )

    def _not_retrieved(self, conversation: Conversation) -> Answer:
        """The answer when the Bank can't be reached, or refuses the session, before
        anything could run: nothing to plan from and nothing to state."""
        text = responder.NOT_RETRIEVED[customer_language(conversation.history)]
        conversation.history.append({"role": "assistant", "content": text})
        return Answer(text=text)
