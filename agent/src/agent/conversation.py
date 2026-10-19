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

from agent import planner, refusals, responder, reviewer
from agent.bank import BankRefused, BankSession, BankUnavailable, HttpClient
from agent.events import (
    Answer,
    ConfirmationRequested,
    CustomerActionEntry,
    CustomerMessageEntry,
    Event,
    HistoryEntry,
    PlanPaused,
    PlanProposed,
    Refusal,
    StepFailed,
    Trace,
)
from agent.executor import StepResult, execute
from agent.language import Language, customer_language
from agent.model import ModelClient
from agent.reviewer import ReviewedPlan
from agent.trace import trace, with_bank_traces


@dataclass
class PendingPlan:
    """A Plan card waiting for the Customer's answer."""

    plan_id: str
    plan: ReviewedPlan
    looked_up: list[StepResult]
    """What the Plan's Green look-ups returned before the card was shown."""


@dataclass
class RunningPlan:
    """A confirmed Plan whose steps are running: the Customer can stop it."""

    plan_id: str
    stop: threading.Event = field(default_factory=threading.Event)


@dataclass
class PausedPlan:
    """A confirmed Plan the Customer stopped: waiting to be resumed, changed or cancelled."""

    plan_id: str
    card: list[str]
    """The Plan card's step IDs, in order: the Customer knows each step by its number."""
    remaining: ReviewedPlan
    """The steps still to run, as the Bank tiered them for the card."""
    tokens: dict[str, str]
    """The Customer's Confirmations of the steps still to run. Never used for another Plan."""
    results: list[StepResult]
    """What the Bank returned for the Plan (its look-ups included) before the stop."""


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
    pending: PendingPlan | None = None
    running: RunningPlan | None = None
    paused: PausedPlan | None = None
    plans_proposed: int = 0
    bank_results: list[StepResult] = field(default_factory=list)
    """Every Bank result so far: the facts an answer may state."""
    record: list[HistoryEntry] = field(default_factory=list)
    """Everything Staff Members read: each Customer message or action and every event
    sent (the decision trace included, so the Plan, its tiers and every Bank call), in
    order."""
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


class StopPlan(BaseModel):
    """The stop button: no further step of the running Plan starts."""

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
    ConfirmPlan | RejectPlan | StopPlan | ResumePlan | CancelPlan,
    Field(discriminator="action"),
]

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


class PlanNotRunning(Exception):
    """The Customer tapped stop, but that Plan isn't running."""


def paused_text(paused: PausedPlan, ran: list[str], language: Language) -> str:
    """Which of the card's steps ran and which didn't, by their number on the card."""

    def numbers(ids: list[str]) -> list[str]:
        return [str(paused.card.index(id) + 1) for id in ids]

    done = numbers(ran)
    not_run = numbers([step.id for step in paused.remaining.steps])
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


def interruption(paused: PausedPlan | None) -> dict[str, Any] | None:
    """Where a stopped Plan stopped, for the Planner: what the Bank returned for the steps
    that ran, and the steps that didn't."""
    if paused is None:
        return None
    return {
        "ran": [asdict(result) for result in paused.results],
        "not_run": [
            step.model_dump(mode="json", exclude={"tier"}) for step in paused.remaining.steps
        ],
    }


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

    def _turn(self, conversation: Conversation, text: str) -> Iterator[Event]:
        with conversation.lock:
            conversation.record.append(CustomerMessageEntry(text=text))
            conversation.pending = None  # a new request replaces an unanswered Plan card
            # ...and changes a stopped Plan: the Planner re-plans knowing where it stopped,
            # and whatever it plans needs a new Plan card. The old Confirmations are dropped.
            interrupted = interruption(conversation.paused)
            conversation.paused = None
            conversation.history.append({"role": "user", "content": text})
            try:
                bank, catalogue = conversation.bank, conversation.bank.catalogue()
            except (BankRefused, BankUnavailable):
                yield self._not_retrieved(conversation)
                return
            plan = planner.make_plan(
                self.model, conversation.history, catalogue, interrupted=interrupted
            )
            if isinstance(plan, planner.Declined):
                yield from self._decline(conversation, plan, catalogue)
                return
            yield trace("intent", {"intent": plan.intent})
            # Exact details first: run the Green look-ups a later step's input waits on,
            # then have the Planner complete the Plan from what the Bank returned.
            looked_up: list[StepResult] = []
            look_ups = plan.look_ups(catalogue)
            if look_ups is not None:
                try:
                    reviewed_look_ups = reviewer.review(look_ups, bank)
                except (BankRefused, BankUnavailable):
                    yield self._not_retrieved(conversation)
                    return
                yield trace("plan", plan_data(reviewed_look_ups, look_ups=True))
                if reviewed_look_ups.green_only:  # the Bank agrees they need no approval
                    yield from execute(reviewed_look_ups, bank, looked_up)
                    plan = planner.make_plan(
                        self.model,
                        conversation.history,
                        catalogue,
                        [asdict(result) for result in looked_up],
                        interrupted=interrupted,
                    )
                    if isinstance(plan, planner.Declined):  # not on offer, but just in case
                        yield from self._decline(conversation, plan, catalogue)
                        return
            try:
                reviewed = reviewer.review(plan, bank)
            except (BankRefused, BankUnavailable):
                yield self._not_retrieved(conversation)
                return
            yield trace("plan", plan_data(reviewed))
            if reviewed.green_only:
                yield from self._run(conversation, reviewed, looked_up)
                return
            if any(step.tier == "red" for step in reviewed.steps):
                # Red steps need Strong Verification (#37).
                raise NotImplementedError("Plans with Red steps need Strong Verification")
            pending = PendingPlan(conversation.next_plan_id(), reviewed, looked_up)
            conversation.pending = pending
            yield PlanProposed(
                plan_id=pending.plan_id, steps=reviewed.steps, refusals=reviewed.card_refusals
            )
            yield ConfirmationRequested(
                plan_id=pending.plan_id, step_ids=[step.id for step in reviewed.yellow_steps]
            )

    def act(self, conversation: Conversation, action: CustomerAction) -> Iterator[Event]:
        """One turn: the Customer's answer to a prompt (a Plan card) in, the events out.

        Raises PlanNotPending straight away, before any event, if the Plan card isn't
        waiting for an answer.
        """
        if isinstance(action, StopPlan):
            # Not under the conversation's lock: the running Plan's turn holds it.
            self.stop(conversation, action.plan_id)
            return iter(())
        if isinstance(action, ResumePlan | CancelPlan):
            return self._answer_stopped(conversation, action)
        with conversation.lock:
            pending = conversation.pending
            if pending is None or pending.plan_id != action.plan_id:
                raise PlanNotPending(action.plan_id)
            removed = set(action.removed_step_ids) if isinstance(action, ConfirmPlan) else set()
            card = [step.id for step in pending.plan.steps]
            if not removed <= set(card):
                raise UnknownStep(sorted(removed - set(card)))
            conversation.pending = None  # a Plan card is answered once
            conversation.record.append(
                CustomerActionEntry(action=action.action, plan_id=action.plan_id)
            )
            if isinstance(action, RejectPlan) or removed == set(card):
                return self._recorded(conversation, self._rejected(conversation))
            # Running from now: a stop tapped before the first step starts still counts.
            conversation.running = RunningPlan(pending.plan_id)
        return self._recorded(
            conversation,
            with_bank_traces(conversation.bank, self._confirmed(conversation, pending, removed)),
        )

    def _answer_stopped(
        self, conversation: Conversation, action: "ResumePlan | CancelPlan"
    ) -> Iterator[Event]:
        """Resume a stopped Plan, on the Confirmations the Customer gave it (the Plan
        hasn't changed), or cancel the rest of it. A change is a new message instead."""
        with conversation.lock:
            paused = conversation.paused
            if paused is None or paused.plan_id != action.plan_id:
                raise PlanNotPending(action.plan_id)
            conversation.paused = None  # a stopped Plan is answered once
            conversation.record.append(
                CustomerActionEntry(action=action.action, plan_id=action.plan_id)
            )
            if isinstance(action, ResumePlan):
                conversation.running = RunningPlan(paused.plan_id)

        def cancelled() -> Iterator[Event]:
            with conversation.lock:
                text = CANCELLED[customer_language(conversation.history)]
                conversation.history.append({"role": "assistant", "content": text})
                yield Answer(text=text)

        def resumed() -> Iterator[Event]:
            with conversation.lock:
                yield from self._run_confirmed(
                    conversation,
                    paused.plan_id,
                    paused.card,
                    paused.remaining,
                    paused.results,
                    paused.tokens,
                )

        if isinstance(action, CancelPlan):
            return self._recorded(conversation, cancelled())
        return self._recorded(conversation, with_bank_traces(conversation.bank, resumed()))

    def stop(self, conversation: Conversation, plan_id: str) -> None:
        """The stop button: no further step of the running Plan starts; its turn ends with
        `plan_paused`. Raises PlanNotRunning if that Plan isn't running."""
        running = conversation.running
        if running is None or running.plan_id != plan_id:
            raise PlanNotRunning(plan_id)
        running.stop.set()
        conversation.record.append(CustomerActionEntry(action="stop", plan_id=plan_id))

    def _decline(
        self,
        conversation: Conversation,
        declined: planner.Declined,
        catalogue: list[dict[str, Any]],
    ) -> Iterator[Event]:
        yield trace("intent", {"intent": "", "declined": declined.model_dump(mode="json")})
        # Nothing runs: the Customer hears what the Agent can do instead.
        text = refusals.out_of_scope(
            declined.can_do, catalogue, customer_language(conversation.history)
        )
        conversation.history.append({"role": "assistant", "content": text})
        yield Refusal(reason=declined.reason, text=text)

    def _rejected(self, conversation: Conversation) -> Iterator[Event]:
        with conversation.lock:
            text = REJECTED[customer_language(conversation.history)]
            conversation.history.append({"role": "assistant", "content": text})
            yield Answer(text=text)

    def _confirmed(
        self, conversation: Conversation, pending: PendingPlan, removed: set[str]
    ) -> Iterator[Event]:
        """The Customer tapped confirm, with `removed` steps taken off the card first."""
        with conversation.lock:
            plan = pending.plan
            if removed:
                # Tiers depend on order (an earlier transfer counts toward the Daily Transfer
                # Total), so the Bank tiers the rest afresh, as they will now run.
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
                step = refused.step
                yield StepFailed(step_id=step.id, operation=step.operation, **refused.refusal)
                failed = StepResult(step.id, step.operation, step.input, None, refused.refusal)
                yield self._answer(conversation, [*pending.looked_up, failed])
                return
            except BankUnavailable:
                conversation.running = None
                yield self._not_retrieved(conversation)
                return
            card = [step.id for step in pending.plan.steps]
            yield from self._run_confirmed(
                conversation, pending.plan_id, card, plan, pending.looked_up, tokens
            )

    def _run_confirmed(
        self,
        conversation: Conversation,
        plan_id: str,
        card: list[str],
        plan: ReviewedPlan,
        results: list[StepResult],
        tokens: dict[str, str],
    ) -> Iterator[Event]:
        """Run a confirmed Plan's steps until they're done or the Customer taps stop, then
        answer from everything the Bank returned for the Plan, or say where it paused."""
        running = conversation.running
        stop = running.stop if running is not None and running.plan_id == plan_id else None
        results = list(results)
        ran_before = len(results)
        remaining = yield from execute(plan, conversation.bank, results, tokens, stop)
        conversation.running = None
        if not remaining:
            yield self._answer(conversation, results)
            return
        still_to_run = {step.id for step in remaining}
        paused = PausedPlan(
            plan_id=plan_id,
            card=card,
            remaining=ReviewedPlan(
                steps=remaining,
                refusals={id: r for id, r in plan.refusals.items() if id in still_to_run},
            ),
            tokens={id: token for id, token in tokens.items() if id in still_to_run},
            results=results,
        )
        conversation.paused = paused
        ran = [result.step_id for result in results[ran_before:] if result.output is not None]
        text = paused_text(paused, ran, customer_language(conversation.history))
        self._remember(conversation, results)
        conversation.history.append({"role": "assistant", "content": text})
        yield PlanPaused(
            plan_id=plan_id, ran=ran, not_run=[step.id for step in remaining], text=text
        )

    def _run(
        self,
        conversation: Conversation,
        plan: ReviewedPlan,
        looked_up: list[StepResult],
    ) -> Iterator[Event]:
        """Run a Green-only Plan, then answer from everything the Bank returned for it."""
        results = list(looked_up)
        yield from execute(plan, conversation.bank, results)
        yield self._answer(conversation, results)

    def _answer(self, conversation: Conversation, results: list[StepResult]) -> Answer:
        """The answer from this Plan's Bank results (and earlier ones in the conversation)."""
        text = responder.answer(
            self.model, conversation.history, results, conversation.bank_results
        )
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
