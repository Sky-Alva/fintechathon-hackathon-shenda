"""The Conversation service: owns conversations and their history, and runs each turn.

A turn takes one Customer message (later slices add Customer actions) and yields the
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
from agent.bank import BankSession, HttpClient
from agent.events import (
    Answer,
    ConfirmationRequested,
    Event,
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
class Conversation:
    conversation_id: str
    bank: BankSession
    history: list[dict[str, Any]] = field(default_factory=list)
    """The Customer's messages and the Agent's answers, in the model's chat format."""
    pending: PendingPlan | None = None
    plans_proposed: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def next_plan_id(self) -> str:
        self.plans_proposed += 1
        return f"p{self.plans_proposed}"


class ConfirmPlan(BaseModel):
    """One tap on the Plan card: confirm every Yellow step on it."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["confirm"]
    plan_id: str


class RejectPlan(BaseModel):
    """Reject the Plan card: nothing on it runs."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["reject"]
    plan_id: str


CustomerAction = Annotated[ConfirmPlan | RejectPlan, Field(discriminator="action")]

REJECTED: dict[Language, str] = {
    "zh": "好的，已取消这个计划，没有执行任何操作。",
    "en": "OK, I've cancelled that Plan. Nothing was done.",
}
"""The Agent's acknowledgement of a rejected Plan card: nothing ran, so nothing to report."""


def plan_data(plan: ReviewedPlan, look_ups: bool = False) -> dict[str, Any]:
    """A reviewed Plan for the decision trace: its look-ups, which run first, are marked
    `look_ups`, and the steps the tier preview refused are listed under `refusals`."""
    data: dict[str, Any] = {"steps": [step.model_dump(mode="json") for step in plan.steps]}
    if look_ups:
        data["look_ups"] = True
    if plan.refusals:
        data["refusals"] = [refusal.model_dump(mode="json") for refusal in plan.refusals]
    return data


class UnknownConversation(Exception):
    pass


class PlanNotPending(Exception):
    """The action answers a Plan card that isn't waiting for an answer: answered already,
    replaced by a newer request, or never shown."""


class Conversations:
    def __init__(self, bank: HttpClient, model: ModelClient):
        self.bank = bank
        self.model = model
        self.conversations: dict[str, Conversation] = {}

    def start(self, session_token: str) -> Conversation:
        """A conversation acting through one Customer's own Bank session."""
        conversation = Conversation(
            conversation_id=secrets.token_urlsafe(16),
            bank=BankSession(self.bank, session_token),
        )
        self.conversations[conversation.conversation_id] = conversation
        return conversation

    def get(self, conversation_id: str) -> Conversation:
        try:
            return self.conversations[conversation_id]
        except KeyError:
            raise UnknownConversation(conversation_id) from None

    def audit_trail(self, conversation: Conversation) -> Trace:
        """The session's audit trail as the Bank recorded it, for the decision trace."""
        return trace("audit_trail", conversation.bank.audit_trail())

    def send(self, conversation: Conversation, text: str) -> Iterator[Event]:
        """One turn: the Customer's message in, the turn's events out, with every Bank
        call of the turn in the decision trace."""
        yield from with_bank_traces(conversation.bank, self._turn(conversation, text))

    def _turn(self, conversation: Conversation, text: str) -> Iterator[Event]:
        with conversation.lock:
            conversation.pending = None  # a new request replaces an unanswered Plan card
            conversation.history.append({"role": "user", "content": text})
            bank, catalogue = conversation.bank, conversation.bank.catalogue()
            plan = planner.make_plan(self.model, conversation.history, catalogue)
            if isinstance(plan, planner.Declined):
                yield from self._decline(conversation, plan, catalogue)
                return
            yield trace("intent", {"intent": plan.intent})
            # Exact details first: run the Green look-ups a later step's input waits on,
            # then have the Planner complete the Plan from what the Bank returned.
            looked_up: list[StepResult] = []
            look_ups = plan.look_ups(catalogue)
            if look_ups is not None:
                reviewed_look_ups = reviewer.review(look_ups, bank)
                yield trace("plan", plan_data(reviewed_look_ups, look_ups=True))
                if reviewed_look_ups.green_only:  # the Bank agrees they need no approval
                    yield from execute(reviewed_look_ups, bank, looked_up)
                    plan = planner.make_plan(
                        self.model,
                        conversation.history,
                        catalogue,
                        [asdict(result) for result in looked_up],
                    )
                    if isinstance(plan, planner.Declined):  # not on offer, but just in case
                        yield from self._decline(conversation, plan, catalogue)
                        return
            reviewed = reviewer.review(plan, bank)
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
                plan_id=pending.plan_id, steps=reviewed.steps, refusals=reviewed.refusals
            )
            yield ConfirmationRequested(
                plan_id=pending.plan_id, step_ids=[step.id for step in reviewed.yellow_steps]
            )

    def act(self, conversation: Conversation, action: CustomerAction) -> Iterator[Event]:
        """One turn: the Customer's answer to a prompt (a Plan card) in, the events out.

        Raises PlanNotPending straight away, before any event, if the Plan card isn't
        waiting for an answer.
        """
        with conversation.lock:
            pending = conversation.pending
            if pending is None or pending.plan_id != action.plan_id:
                raise PlanNotPending(action.plan_id)
            conversation.pending = None  # a Plan card is answered once
        if isinstance(action, RejectPlan):
            return self._rejected(conversation)
        return with_bank_traces(conversation.bank, self._confirmed(conversation, pending))

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

    def _confirmed(self, conversation: Conversation, pending: PendingPlan) -> Iterator[Event]:
        with conversation.lock:
            try:
                tokens = reviewer.collect_confirmations(pending.plan, conversation.bank)
            except reviewer.ConfirmationRefused as refused:
                step = refused.step
                yield StepFailed(step_id=step.id, operation=step.operation, **refused.refusal)
                failed = StepResult(step.id, step.operation, step.input, {}, refused.refusal)
                yield self._answer(conversation, [*pending.looked_up, failed])
                return
            yield from self._run(conversation, pending.plan, pending.looked_up, tokens)

    def _run(
        self,
        conversation: Conversation,
        plan: ReviewedPlan,
        looked_up: list[StepResult],
        confirmation_tokens: dict[str, str] | None = None,
    ) -> Iterator[Event]:
        """Run the Plan, then answer from everything the Bank returned for it."""
        results = list(looked_up)
        yield from execute(plan, conversation.bank, results, confirmation_tokens)
        yield self._answer(conversation, results)

    def _answer(self, conversation: Conversation, results: list[StepResult]) -> Answer:
        text = responder.answer(self.model, conversation.history, results)
        conversation.history.append({"role": "assistant", "content": text})
        return Answer(text=text)
