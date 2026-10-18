"""The Conversation service: owns conversations and their history, and runs each turn.

A turn takes one Customer message (later slices add Customer actions) and yields the
turn's events: plan → review → execute → answer.
"""

import secrets
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from agent import planner, responder, reviewer
from agent.bank import BankSession, HttpClient
from agent.events import Answer, Event, Trace
from agent.executor import StepResult, execute
from agent.model import ModelClient
from agent.trace import trace, with_bank_traces


@dataclass
class Conversation:
    conversation_id: str
    bank: BankSession
    history: list[dict[str, Any]] = field(default_factory=list)
    """The Customer's messages and the Agent's answers, in the model's chat format."""
    lock: threading.Lock = field(default_factory=threading.Lock)


class UnknownConversation(Exception):
    pass


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
            conversation.history.append({"role": "user", "content": text})
            plan = planner.make_plan(
                self.model, conversation.history, conversation.bank.catalogue()
            )
            yield trace("intent", {"intent": plan.intent})
            reviewed = reviewer.review(plan, conversation.bank)
            yield trace("plan", {"steps": [s.model_dump(mode="json") for s in reviewed.steps]})
            if not reviewed.green_only:
                # Yellow and Red steps need a Plan card first (#31).
                raise NotImplementedError("Plans with Yellow or Red steps need a Plan card")
            results: list[StepResult] = []
            yield from execute(reviewed, conversation.bank, results)
            text = responder.answer(self.model, conversation.history, results)
            conversation.history.append({"role": "assistant", "content": text})
            yield Answer(text=text)
