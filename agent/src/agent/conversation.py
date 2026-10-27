"""The Conversation service: owns conversations and their history, and runs each turn.

A turn takes one Customer message or action (the answer to a Plan card) and yields the
turn's events: plan → review → execute → answer. A Plan with a Yellow or Red step stops
at its Plan card instead, and runs only when the Customer confirms it.
"""

import logging
import secrets
import threading
from collections.abc import Generator, Iterator
from dataclasses import asdict, dataclass, field
from typing import Any

from agent import (
    birthday,
    cards,
    guard,
    notices,
    orders,
    payees,
    planner,
    refusals,
    responder,
    reviewer,
    secrets_guard,
    subscriptions,
    untrusted,
)
from agent.actions import (
    AskForHuman,
    CancelPlan,
    CancelSubscriptions,
    ChooseOption,
    ConfirmPlan,
    CustomerAction,
    RejectPlan,
    RemoveSteps,
    ResumePlan,
    ReverseSteps,
    StopPlan,
    VerifiedStep,
    WrongCode,
)
from agent.bank import BankRefused, BankSession, BankUnavailable, HttpClient
from agent.events import (
    Answer,
    BankRefusal,
    CancellationOffered,
    ClarificationQuestion,
    ConfirmationRequested,
    CustomerActionEntry,
    CustomerMessageEntry,
    Event,
    HandedBack,
    HandedOver,
    HistoryEntry,
    HumanRequested,
    InjectionWarning,
    PlanPaused,
    PlanProposed,
    PlanStep,
    Refusal,
    ReversalOffered,
    ReversalStep,
    SessionLocked,
    StaffMessage,
    Subscription,
    Trace,
    VerificationRequested,
)
from agent.executor import BANK_UNAVAILABLE, StepResult, execute, failed
from agent.fact_check import Facts
from agent.language import customer_language
from agent.model import ModelClient, ModelUnavailable
from agent.prompts import (
    CancellationOffer,
    PausedPlan,
    PendingClarification,
    PendingPlan,
    PendingVerification,
    Prompt,
    ReversalOffer,
    RunningPlan,
    interruption,
    without,
)
from agent.reviewer import ReviewedPlan
from agent.trace import trace, with_bank_traces

log = logging.getLogger(__name__)

NOT_PLANNED = (ModelUnavailable, planner.PlannerFailed)
"""The model couldn't be asked, or gave no reply the Agent can act on: nothing is planned."""


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
    pending: Prompt | None = None
    """The prompt waiting for the Customer's answer: a Plan card, a Red step's
    verification, a stopped Plan, or a clarifying question."""
    reversal: ReversalOffer | None = None
    """The offer to undo a failed Plan's completed steps, until the Customer answers it or
    sends a new message. Apart from `pending`: a new Plan card for what's left of the goal
    can wait at the same time."""
    running: RunningPlan | None = None
    """The confirmed Plan whose steps are running, from the Customer's tap until its turn
    ends."""
    offer: CancellationOffer | None = None
    """The Subscriptions offered for cancelling, until the Customer picks or moves on.
    Apart from `pending`, like `reversal`."""
    plans_proposed: int = 0
    offers_made: int = 0
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
    awaiting_orders: dict[str, str] = field(default_factory=dict)
    """The Merchant orders placed in this conversation that the Bank will pay on their
    delivery date, by order ID: the Customer is told when the Bank has paid them (or
    couldn't)."""
    birthday_checked_on: str | None = None
    """The Bank date the spouse's birthday was last checked on, between turns."""
    birthday_reminded: bool = False
    """The Agent has brought the spouse's coming birthday up: once per conversation."""
    injections_seen: set[str] = field(default_factory=set)
    """The instruction-like texts found in Bank data so far, quoted: each is warned about
    and reported once, however often the Bank returns it."""
    lock: threading.Lock = field(default_factory=threading.Lock)

    def next_plan_id(self) -> str:
        self.plans_proposed += 1
        return f"p{self.plans_proposed}"

    def next_offer_id(self) -> str:
        self.offers_made += 1
        return f"o{self.offers_made}"


NO_CLEAN_UP = {"session_locked", "not_authenticated", "forbidden_other_customer"}
"""Refusals after which nothing more is offered: under a Safety Lock only Green Operations
run (the lock notice says so), a session the Bank no longer knows runs nothing, and another
Customer's information is never looked for another way (#44)."""


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
            note = notices.HANDED_BACK_NOTE.format(staff=staff)
            conversation.history.append({"role": "system", "content": note})
            return self._push(conversation, HandedBack())

    def updates(self, conversation: Conversation, after: int) -> list[Event]:
        """What the Customer was sent outside their own turns, from the `after`-th on:
        a Staff Member's takeover, replies and hand-back, the spouse's coming birthday, and
        the Merchant orders the Bank has paid on the Bank clock."""
        self.check_birthday(conversation)
        self.check_orders(conversation)
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
            conversation.offer = None  # a pick in words is planned like any request
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
        try:
            now = bank.clock()
        except (BankRefused, BankUnavailable):
            yield self._not_retrieved(conversation)
            return

        def ask(
            looked_up: list[StepResult] | None = None,
        ) -> (
            planner.Plan
            | planner.Declined
            | planner.HumanAsked
            | planner.Clarification
            | planner.CancellationsAsked
        ):
            plan = planner.make_plan(
                self.model,
                conversation.history,
                catalogue,
                [result.as_data() for result in looked_up] if looked_up else None,
                interrupted=interrupted,
                failed=failed_context,
                now=now,
                payees=[
                    payee.as_data() for payee in payees.known(conversation.bank_results).values()
                ],
                cards=[card.as_data() for card in cards.known(conversation.bank_results).values()],
            )
            if failed is None or not isinstance(plan, planner.Plan):
                return plan
            retried = (failed.operation, failed.input)
            return plan.model_copy(
                update={"steps": [s for s in plan.steps if (s.operation, s.input) != retried]}
            )

        try:
            plan = ask()
        except NOT_PLANNED:
            log.warning("No Plan for conversation %s", conversation.conversation_id, exc_info=True)
            if failed is None:  # after a failed step, the answer already said what happened
                yield self._not_planned(conversation)
            return
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
        if isinstance(plan, planner.CancellationsAsked):
            yield from self._offer_cancellations(conversation, catalogue)
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
            if reviewed_look_ups.green_only:  # the Bank agrees they need no Plan card
                yield from execute(reviewed_look_ups, bank, looked_up, language=language)
                if (lock := (yield from self._screen(conversation, looked_up))) is not None:
                    yield self._locked(conversation, lock)
                    return
                if (which := payees.which_one(looked_up, language)) is not None:
                    # Several Payees match: ask which one, never guess with the money.
                    self._remember(conversation, looked_up)
                    yield self._clarify(conversation, which)
                    return
                try:
                    plan = ask(looked_up)
                except NOT_PLANNED:
                    log.warning(
                        "No Plan for conversation %s", conversation.conversation_id, exc_info=True
                    )
                    self._remember(conversation, looked_up)
                    yield self._not_planned(conversation)
                    return
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
                if isinstance(plan, planner.CancellationsAsked):  # nor this
                    yield from self._offer_cancellations(conversation, catalogue)
                    return
                if failed is not None and not plan.steps:
                    return
        try:
            reviewed = reviewer.review(plan, bank, catalogue)
        except (BankRefused, BankUnavailable):
            yield self._not_retrieved(conversation)
            return
        yield from self._propose(conversation, reviewed, looked_up, catalogue, plan.note)

    def _propose(
        self,
        conversation: Conversation,
        reviewed: ReviewedPlan,
        looked_up: list[StepResult],
        catalogue: list[dict[str, Any]],
        note: str | None = None,
    ) -> Iterator[Event]:
        """The reviewed Plan's Plan card, or, if it needs none, its run. A Plan card comes
        after the Planner's `note` of what it noticed, if the Bank's results back it."""
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
        if note and (noticed := self._noticed(conversation, note, looked_up)) is not None:
            yield noticed
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
            cards=cards.on_card(
                reviewed.steps, cards.known([*conversation.bank_results, *looked_up])
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
        match action:
            case AskForHuman():
                return self._recorded(conversation, self._ask_for_human(conversation))
            case StopPlan():
                return self.stop(conversation, action.plan_id)
        with conversation.lock:
            if conversation.handed_over_to is not None:
                raise AlreadyHandedOver(conversation.handed_over_to)
            offer = conversation.reversal
            match action:
                case ChooseOption():
                    return self._choose(conversation, action.value)
                case CancelSubscriptions():
                    return self._answer_offer(conversation, action)
                case ReverseSteps():
                    return self._answer_reversal(conversation, action)
                case RejectPlan() if offer is not None and offer.plan_id == action.plan_id:
                    return self._answer_reversal(conversation, action)
            pending = conversation.pending
            if pending is None or not pending.answered_by(action):
                raise PlanNotPending(action.plan_id)
            removed: set[str] = set()
            if isinstance(action, RemoveSteps) and isinstance(pending, PendingPlan):
                card = {step.id for step in pending.plan.steps}
                if not set(action.step_ids) <= card:
                    raise UnknownStep(sorted(set(action.step_ids) - card))
                removed = without(pending.plan, set(action.step_ids))
                if removed == card:  # nothing left to confirm: as good as a reject
                    action = RejectPlan(action="reject", plan_id=action.plan_id)
            conversation.pending = None  # a prompt is answered once
            conversation.record.append(
                CustomerActionEntry(action=action.action, plan_id=action.plan_id)
            )
            if isinstance(action, ConfirmPlan | VerifiedStep | ResumePlan):
                # Running from now: a stop tapped before the first step starts still counts.
                conversation.running = RunningPlan(action.plan_id)
        match action, pending:
            case RejectPlan(), _:
                return self._recorded(conversation, self._rejected(conversation))
            case CancelPlan(), _:
                return self._recorded(conversation, self._cancelled(conversation))
            case VerifiedStep(), PendingVerification():
                turn = self._verified(conversation, pending, action.verification_token)
            case WrongCode(), PendingVerification():
                turn = self._wrong_code(conversation, pending, action.failed_attempts)
            case ResumePlan(), PausedPlan():
                turn = self._resumed(conversation, pending)
            case RemoveSteps(), PendingPlan():
                turn = self._edited(conversation, pending, set(action.step_ids), removed)
            case ConfirmPlan(), PendingPlan():
                turn = self._confirmed(conversation, pending)
            case _:
                raise AssertionError(f"{action.action} answered {type(pending).__name__}")
        return self._recorded(conversation, with_bank_traces(conversation.bank, turn))

    def _choose(self, conversation: Conversation, value: str) -> Iterator[Event]:
        """The Customer's tap on an option of the clarifying question: the Planner plans
        the request again, told which option it was. Called under the conversation's
        lock."""
        pending = conversation.pending
        if not isinstance(pending, PendingClarification):
            raise PlanNotPending("")
        values = [option.value for option in pending.question.options]
        if value not in values:
            raise UnknownOption(value)
        conversation.pending = None  # a question is answered once
        conversation.reversal = None  # moving on drops the other offers, as a message does
        conversation.offer = None
        conversation.record.append(CustomerActionEntry(action="choose", plan_id=None))
        # A note, not a Customer message: the option's label mustn't change the language.
        note = notices.CHOSEN_NOTE.format(number=values.index(value) + 1, value=value)
        conversation.history.append({"role": "system", "content": note})
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

    def _answer_offer(
        self, conversation: Conversation, action: CancelSubscriptions
    ) -> Iterator[Event]:
        """The Customer's pick from the Subscriptions offered for cancelling. Called under
        the conversation's lock."""
        offer = conversation.offer
        if offer is None or offer.offer_id != action.offer_id:
            raise PlanNotPending(action.offer_id)
        offered = [candidate.subscription_id for candidate in offer.candidates]
        if not set(action.subscription_ids) <= set(offered):
            raise UnknownStep(sorted(set(action.subscription_ids) - set(offered)))
        conversation.offer = None  # an offer is answered once
        conversation.record.append(CustomerActionEntry(action=action.action, plan_id=None))
        picked = [id for id in offered if id in action.subscription_ids]  # the offer's order
        turn = self._cancel_picked(conversation, picked)
        return self._recorded(conversation, with_bank_traces(conversation.bank, turn))

    def _cancel_picked(self, conversation: Conversation, picked: list[str]) -> Iterator[Event]:
        """One Plan card with one cancellation per picked Subscription, then a read-back
        of what the Bank shows. Nothing runs before the Customer confirms the card."""
        with conversation.lock:
            if conversation.handed_over_to is not None:
                return  # taken over since the Customer picked: the Agent starts nothing
            language = customer_language(conversation.history)
            if not picked:
                text = subscriptions.NONE_PICKED[language]
                conversation.history.append({"role": "assistant", "content": text})
                yield Answer(text=text)
                return
            cancels = [
                PlanStep(
                    id=f"s{number}",
                    operation=subscriptions.CANCEL,
                    input={"subscription_id": id},
                    depends_on=[],
                )
                for number, id in enumerate(picked, start=1)
            ]
            read_back = PlanStep(
                id=f"s{len(cancels) + 1}",
                operation=subscriptions.LIST,
                input={},
                depends_on=[step.id for step in cancels],
            )
            plan = planner.Plan(intent="cancel_subscriptions", steps=[*cancels, read_back])
            try:
                catalogue = conversation.bank.catalogue()
                reviewed = reviewer.review(plan, conversation.bank, catalogue)
            except (BankRefused, BankUnavailable):
                yield self._not_retrieved(conversation)
                return
            conversation.pending = None  # the pick replaces an unanswered Plan card
            yield trace("intent", {"intent": plan.intent})
            yield from self._propose(conversation, reviewed, [], catalogue)

    def _not_reversed(self, conversation: Conversation) -> Iterator[Event]:
        with conversation.lock:
            text = notices.NOT_REVERSED[customer_language(conversation.history)]
            conversation.history.append({"role": "assistant", "content": text})
            yield Answer(text=text)

    def _reverse(self, conversation: Conversation, chosen: list[ReversalStep]) -> Iterator[Event]:
        """The accepted undo, as a new Plan: the Bank tiers it and it gets its own Plan card,
        like any other. Nothing runs before the Customer confirms it there."""
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
        conversation.history.append(untrusted.shown(question.text + listed))
        return question

    def _code_in_chat(self, conversation: Conversation) -> Answer:
        """The Customer typed a code into the chat: point them to the verification box."""
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
        # Still refused if the report fails: the Customer just isn't told it was reported.
        reported, lock = self._report(conversation, suspicion)
        text = guard.warning(reported, customer_language(conversation.history))
        conversation.history.append({"role": "assistant", "content": text})
        yield Refusal(reason="manipulation", text=text)
        if lock is not None:
            yield self._locked(conversation, lock)

    def _report(
        self, conversation: Conversation, suspicion: guard.Suspicion
    ) -> tuple[bool, dict[str, Any] | None]:
        """Report a suspected attempt to the Bank, quoting the text that raised it: whether
        the Bank accepted the report, and the Safety Lock it placed if this report locked
        the session (the second in a session does)."""
        try:
            report = conversation.bank.call(
                "report_suspected_attempt",
                {"kind": suspicion.kind, "evidence": suspicion.evidence},
            )
        except (BankRefused, BankUnavailable):
            return False, None
        if not report["output"]["session_locked"]:
            return True, None
        try:  # the Bank's trail says until when
            return True, conversation.bank.latest_lock()
        except (BankRefused, BankUnavailable):
            return True, None

    def _screen(
        self, conversation: Conversation, results: list[StepResult]
    ) -> Generator[Event, None, dict[str, Any] | None]:
        """Look through what the Bank returned for instruction-like text (an injection).
        It is never followed: the Customer is warned about it and it is reported to the
        Bank, once per text. Returns the Safety Lock a report placed, if one did."""
        language = customer_language(conversation.history)
        for result in results:
            found = guard.injection(result.output)
            if found is None or found.suspicion.evidence in conversation.injections_seen:
                continue
            conversation.injections_seen.add(found.suspicion.evidence)
            reported, lock = self._report(conversation, found.suspicion)
            text = guard.injection_warning(found.field, reported, language)
            conversation.history.append({"role": "assistant", "content": text})
            yield InjectionWarning(
                step_id=result.step_id, operation=result.operation, reported=reported, text=text
            )
            if lock is not None:
                return lock
        return None

    def _ask_for_human(self, conversation: Conversation) -> Iterator[Event]:
        with conversation.lock:
            conversation.record.append(CustomerActionEntry(action="ask_for_human", plan_id=None))
            if conversation.handed_over_to is not None:
                return  # a Staff Member is already here
            yield self._human_requested(conversation)

    def _human_requested(self, conversation: Conversation) -> HumanRequested:
        conversation.human_requested = True
        text = notices.HUMAN_REQUESTED[customer_language(conversation.history)]
        conversation.history.append({"role": "assistant", "content": text})
        return HumanRequested(text=text)

    def _offer_cancellations(
        self, conversation: Conversation, catalogue: list[dict[str, Any]]
    ) -> Iterator[Event]:
        """List the Subscriptions the Bank flags as possibly unneeded, with its reasons,
        for the Customer to pick which to cancel. Planned like any request: one Green
        look-up, which the Bank tiers and runs; the candidates are its output, unchanged."""
        language = customer_language(conversation.history)
        look_up = planner.Plan(
            intent="offer_cancellations",
            steps=[PlanStep(id="s1", operation=subscriptions.LIST, input={}, depends_on=[])],
        )
        yield trace("intent", {"intent": look_up.intent})
        try:
            reviewed = reviewer.review(look_up, conversation.bank, catalogue)
        except (BankRefused, BankUnavailable):
            yield self._not_retrieved(conversation)
            return
        yield trace("plan", plan_data(reviewed))
        results: list[StepResult] = []
        yield from execute(reviewed, conversation.bank, results, language=language)
        if (lock := (yield from self._screen(conversation, results))) is not None:
            yield self._locked(conversation, lock)
            return
        listed = results[-1].output if results else None
        if listed is None:  # the Bank refused it or couldn't be reached: say so
            yield from self._finish(conversation, results)
            return
        self._remember(conversation, results)
        offered = subscriptions.candidates(listed)
        if not offered:
            text = subscriptions.NONE_FLAGGED[language]
            conversation.history.append({"role": "assistant", "content": text})
            yield Answer(text=text)
            return
        offer = CancellationOffer(
            conversation.next_offer_id(), [Subscription.model_validate(c) for c in offered]
        )
        conversation.offer = offer
        text = subscriptions.offer_text(offered, listed["subscriptions"], language)
        conversation.history.append(untrusted.shown(text))
        yield CancellationOffered(offer_id=offer.offer_id, candidates=offer.candidates, text=text)

    def _rejected(self, conversation: Conversation) -> Iterator[Event]:
        with conversation.lock:
            text = notices.REJECTED[customer_language(conversation.history)]
            conversation.history.append({"role": "assistant", "content": text})
            yield Answer(text=text)

    def _cancelled(self, conversation: Conversation) -> Iterator[Event]:
        with conversation.lock:
            text = notices.CANCELLED[customer_language(conversation.history)]
            conversation.history.append({"role": "assistant", "content": text})
            yield Answer(text=text)

    def _edited(
        self,
        conversation: Conversation,
        pending: PendingPlan,
        asked: set[str],
        removed: set[str],
    ) -> Iterator[Event]:
        """The Customer took the `asked` steps off the Plan card (`removed` adds the steps
        left waiting on nothing): the rest are a new Plan card, to confirm again."""
        with conversation.lock:
            if conversation.handed_over_to is not None:
                return  # taken over since the Customer tapped: the Agent starts nothing
            card = [step.id for step in pending.plan.steps]
            # Tiers depend on order (an earlier transfer counts toward the Daily Transfer
            # Total), so the Bank tiers the rest afresh, as they will now run: removing an
            # earlier transfer can turn a Red step Yellow.
            rest = [step for step in pending.plan.steps if step.id not in removed]
            try:
                catalogue = conversation.bank.catalogue()
                reviewed = reviewer.review(planner.Plan(steps=rest), conversation.bank)
            except (BankRefused, BankUnavailable):
                yield self._not_retrieved(conversation)
                return
            if waiting := [str(card.index(id) + 1) for id in card if id in removed - asked]:
                text = notices.removed_with(waiting, customer_language(conversation.history))
                conversation.history.append({"role": "assistant", "content": text})
                yield Answer(text=text)
            yield from self._propose(conversation, reviewed, pending.looked_up, catalogue)

    def _confirmed(self, conversation: Conversation, pending: PendingPlan) -> Iterator[Event]:
        """The Customer tapped confirm: every Yellow step on the card is confirmed."""
        with conversation.lock:
            if conversation.handed_over_to is not None:
                # Taken over since the Customer confirmed: the Agent starts nothing.
                conversation.running = None
                return
            card = [step.id for step in pending.plan.steps]
            plan = pending.plan
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
                yield failed(step, BankRefusal.model_validate(refusal), pending.results, language)
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
        if (lock := (yield from self._screen(conversation, results))) is not None:
            # The report locked the session: answer for what ran; nothing more starts.
            yield self._answer(conversation, results)
            yield self._locked(conversation, lock)
            return
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
        if refusal.reason in NO_CLEAN_UP:
            return
        # The card's completed steps that can be undone, the last to run first.
        done = [
            (result.step_id, result.reversal)
            for result in reversed(results)
            if result.output is not None and result.reversal is not None and result.step_id in card
        ]
        if done:
            offer = ReversalOffer(
                plan_id, [ReversalStep(step_id=id, **reversal.model_dump()) for id, reversal in done]
            )
            conversation.reversal = offer
            numbers = [str(card.index(id) + 1) for id, _ in done]
            text = notices.reversal_text(numbers, customer_language(conversation.history))
            conversation.history.append({"role": "assistant", "content": text})
            yield ReversalOffered(plan_id=plan_id, steps=offer.steps, text=text)
        if refusal.reason == "bank_unavailable":
            return  # nothing to re-plan around: the Bank can't be reached
        try:
            catalogue = conversation.bank.catalogue()
        except (BankRefused, BankUnavailable):
            return
        context = {
            "ran": [result.as_data() for result in results[:-1] if result.output is not None],
            "failed": failed_step.as_data(),
            "not_run": [step.model_dump(mode="json", exclude={"tier"}) for step in not_run],
        }
        yield from self._plan(conversation, catalogue, failed=failed_step, failed_context=context)

    def _pause(self, conversation: Conversation, paused: PausedPlan) -> PlanPaused:
        """The Plan is stopped: tell the Customer which steps ran and which didn't, and
        wait for resume, cancel or a change."""
        conversation.pending = paused
        text = notices.paused_text(
            paused.card, paused.ran, [step.id for step in paused.plan.steps],
            customer_language(conversation.history),
        )
        self._remember(conversation, paused.results)
        conversation.history.append({"role": "assistant", "content": text})
        return PlanPaused(
            plan_id=paused.plan_id,
            ran=paused.ran,
            not_run=[step.id for step in paused.plan.steps],
            text=text,
        )

    def _finish(self, conversation: Conversation, results: list[StepResult]) -> Iterator[Event]:
        """End the Plan's turn: the answer from its results, then, if the Bank refused a
        step because the session is under a Safety Lock, the notice of what still works."""
        yield self._answer(conversation, results)
        for result in results:
            if result.refusal is not None and result.refusal.reason == "session_locked":
                yield self._locked(conversation, result.refusal.detail)
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
        reported = subscriptions.report(results, customer_language(conversation.history))
        if reported is not None:
            written = responder.Written(reported, quotes_bank=True)
        else:
            written = responder.answer(
                self.model, conversation.history, results, conversation.bank_results
            )
        # The outgoing check: nothing that looks like a full card number or a code goes
        # out, even when a Bank result contained it.
        text = secrets_guard.mask(written.text)
        self._remember(conversation, results)
        conversation.history.append(
            untrusted.shown(text)
            if written.quotes_bank
            else {"role": "assistant", "content": text}
        )
        return Answer(text=text)

    def _noticed(
        self, conversation: Conversation, note: str, looked_up: list[StepResult]
    ) -> Answer | None:
        """The Planner's note of what it noticed in the look-ups, as an answer before the
        Plan card; None, so nothing is said, unless every fact in it is in a Bank result.
        Not regenerated: the Plan card says what will happen either way."""
        earlier = [*conversation.bank_results, *looked_up]
        facts = Facts.of(result.output for result in earlier if result.output is not None)
        if responder.check(note, facts) is not None:
            return None
        text = secrets_guard.mask(note)
        self._remember(conversation, looked_up)
        conversation.history.append({"role": "assistant", "content": text})
        return Answer(text=text)

    def _remember(self, conversation: Conversation, results: list[StepResult]) -> None:
        """Keep the Bank's results as facts later answers may state, and note the Merchant
        orders the Bank will pay later."""
        conversation.bank_results.extend(
            result for result in results if result not in conversation.bank_results
        )
        conversation.awaiting_orders.update(orders.placed(results))

    def check_birthday(self, conversation: Conversation) -> None:
        """Bring the spouse's birthday up, unprompted, once it is a week away or less on the
        Bank clock: from the profile and last year's gift purchase, both Green look-ups.
        Checked once per Bank date and told once, between turns; skipped while a turn is
        under way or a Staff Member has the conversation."""
        if conversation.birthday_reminded or not conversation.lock.acquire(blocking=False):
            return
        try:
            if conversation.handed_over_to is not None:
                return
            bank = conversation.bank
            try:
                now = bank.clock()
                if now[:10] == conversation.birthday_checked_on:
                    return
                conversation.birthday_checked_on = now[:10]
                profile = bank.call(birthday.PROFILE, {})["output"]
                coming = birthday.coming(profile, now)
                if coming is None:
                    bank.take_exchanges()  # nothing to tell: nothing for the trace either
                    return
                query = birthday.gifts_query(coming)
                gifts = bank.call(birthday.TRANSACTIONS, query)["output"]
            except (BankRefused, BankUnavailable):
                bank.take_exchanges()
                return  # try again on the next Bank date
            conversation.birthday_reminded = True
            language = customer_language(conversation.history)
            text = birthday.reminder(profile, coming, birthday.last_gift(gifts), now, language)
            self._remember(
                conversation,
                [
                    StepResult("birthday_profile", birthday.PROFILE, {}, profile),
                    StepResult("birthday_gifts", birthday.TRANSACTIONS, query, gifts),
                ],
            )
            conversation.history.append(untrusted.shown(text))
            for event in with_bank_traces(bank, iter([Answer(text=text)])):
                self._push(conversation, event)
        finally:
            conversation.lock.release()

    def check_orders(self, conversation: Conversation) -> None:
        """Tell the Customer, between their turns, about the orders placed in this
        conversation that the Bank has paid since (or couldn't pay) on their delivery
        date. Checked only once the Bank clock has reached one, and skipped while a turn
        is under way: the next poll checks again."""
        if not conversation.awaiting_orders or not conversation.lock.acquire(blocking=False):
            return
        try:
            if conversation.handed_over_to is not None:
                return  # the Staff Member has the conversation: told once it is handed back
            bank = conversation.bank
            try:
                if not orders.any_due(conversation.awaiting_orders, bank.clock()):
                    return
                listed = bank.call("list_merchant_orders", {})
            except (BankRefused, BankUnavailable):
                return  # try again on the next poll
            result = StepResult(
                "orders", "list_merchant_orders", {}, listed["output"], None, None
            )
            settled = orders.settled(conversation.awaiting_orders, listed["output"])
            if not settled:
                bank.take_exchanges()  # nothing to tell: nothing for the trace either
                return
            for order in settled:
                del conversation.awaiting_orders[order["order_id"]]
            text = orders.settled_text(settled, customer_language(conversation.history))
            self._remember(conversation, [result])
            conversation.history.append(untrusted.shown(text))
            for event in with_bank_traces(bank, iter([Answer(text=text)])):
                self._push(conversation, event)
        finally:
            conversation.lock.release()

    def _not_planned(self, conversation: Conversation) -> Answer:
        """The answer when the model couldn't plan the request: nothing but look-ups ran."""
        text = notices.NOT_PLANNED[customer_language(conversation.history)]
        conversation.history.append({"role": "assistant", "content": text})
        return Answer(text=text)

    def _not_retrieved(self, conversation: Conversation) -> Answer:
        """The answer when the Bank can't be reached, or refuses the session, before
        anything could run: nothing to plan from and nothing to state."""
        text = responder.NOT_RETRIEVED[customer_language(conversation.history)]
        conversation.history.append({"role": "assistant", "content": text})
        return Answer(text=text)
