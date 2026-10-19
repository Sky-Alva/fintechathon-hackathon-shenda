"""The Conversation service: owns conversations and their history, and runs each turn.

A turn takes one Customer message (later slices add Customer actions) and yields the
turn's events: plan → review → execute → answer.
"""

import secrets
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from agent import planner, refusals, responder, reviewer
from agent.bank import BankRefused, BankSession, BankUnavailable, HttpClient
from agent.events import Answer, CustomerMessageEntry, Event, HistoryEntry, Refusal, Trace
from agent.executor import StepResult, execute
from agent.language import customer_language
from agent.model import ModelClient
from agent.trace import trace, with_bank_traces


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
    bank_results: list[StepResult] = field(default_factory=list)
    """Every Bank result so far: the facts an answer may state."""
    record: list[HistoryEntry] = field(default_factory=list)
    """Everything Staff Members read: each Customer message and every event sent (the
    decision trace included, so the Plan, its tiers and every Bank call), in order."""
    lock: threading.Lock = field(default_factory=threading.Lock)


class UnknownConversation(Exception):
    pass


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
        for event in with_bank_traces(conversation.bank, self._turn(conversation, text)):
            conversation.record.append(event)
            yield event

    def _turn(self, conversation: Conversation, text: str) -> Iterator[Event]:
        with conversation.lock:
            conversation.record.append(CustomerMessageEntry(text=text))
            conversation.history.append({"role": "user", "content": text})
            try:
                catalogue = conversation.bank.catalogue()
            except (BankRefused, BankUnavailable):
                yield self._not_retrieved(conversation)
                return
            plan = planner.make_plan(self.model, conversation.history, catalogue)
            if isinstance(plan, planner.Declined):
                yield trace("intent", {"intent": "", "declined": plan.model_dump(mode="json")})
                # Nothing runs: the Customer hears what the Agent can do instead.
                refusal = refusals.out_of_scope(
                    plan.can_do, catalogue, customer_language(conversation.history)
                )
                conversation.history.append({"role": "assistant", "content": refusal})
                yield Refusal(reason=plan.reason, text=refusal)
                return
            yield trace("intent", {"intent": plan.intent})
            try:
                reviewed = reviewer.review(plan, conversation.bank)
            except (BankRefused, BankUnavailable):
                yield self._not_retrieved(conversation)
                return
            yield trace("plan", {"steps": [s.model_dump(mode="json") for s in reviewed.steps]})
            if not reviewed.green_only:
                # Yellow and Red steps need a Plan card first (#31).
                raise NotImplementedError("Plans with Yellow or Red steps need a Plan card")
            results: list[StepResult] = []
            yield from execute(reviewed, conversation.bank, results)
            text = responder.answer(
                self.model, conversation.history, results, conversation.bank_results
            )
            conversation.bank_results.extend(results)
            conversation.history.append({"role": "assistant", "content": text})
            yield Answer(text=text)

    def _not_retrieved(self, conversation: Conversation) -> Answer:
        """The answer when the Bank can't be reached, or refuses the session, before
        anything could run: nothing to plan from and nothing to state."""
        text = responder.NOT_RETRIEVED[customer_language(conversation.history)]
        conversation.history.append({"role": "assistant", "content": text})
        return Answer(text=text)
