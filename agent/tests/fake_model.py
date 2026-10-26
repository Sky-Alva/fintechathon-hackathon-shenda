"""A fake model client: returns scripted replies in order and records every request.

Tests script what the model would say (a Plan, then an answer) with `plan(...)` and
`say(...)`, so no test ever calls a real model.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from agent.model import ModelReply, ModelUnavailable, ToolCall

__all__ = ["FakeModel", "ModelReply", "ModelRequest", "ToolCall"]
"""`ModelReply` and `ToolCall` are what a scripted reply is made of, for type hints and
hand-made replies: tests import them from here."""


@dataclass
class ModelRequest:
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None

    def text(self) -> str:
        """Everything the model was shown, as one string, for `in` checks."""
        return json.dumps({"messages": self.messages, "tools": self.tools}, ensure_ascii=False)


@dataclass
class FakeModel:
    replies: list[ModelReply | ModelUnavailable] = field(default_factory=list)
    requests: list[ModelRequest] = field(default_factory=list)

    def script(self, *replies: ModelReply | ModelUnavailable) -> None:
        self.replies.extend(replies)

    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> ModelReply:
        self.requests.append(ModelRequest(messages=messages, tools=tools))
        assert self.replies, "the model was asked for more replies than the test scripted"
        reply = self.replies.pop(0)
        if isinstance(reply, ModelUnavailable):
            raise reply
        return reply


def step(
    id: str, operation: str, input: dict[str, Any] | None = None, depends_on: list[str] | None = None
) -> dict[str, Any]:
    return {"id": id, "operation": operation, "input": input or {}, "depends_on": depends_on or []}


def plan(
    *steps: dict[str, Any], intent: str | None = None, note: str | None = None
) -> ModelReply:
    """The model proposes a Plan with these steps, saying what it understood the Customer
    wants (`intent`) and what it noticed in the look-ups (`note`) when given."""
    arguments: dict[str, Any] = {"steps": list(steps)}
    if intent is not None:
        arguments["intent"] = intent
    if note is not None:
        arguments["note"] = note
    return ModelReply(
        tool_calls=[ToolCall(id="call-plan", name="propose_plan", arguments=arguments)]
    )


def unavailable() -> ModelUnavailable:
    """The model can't be asked this time: the API is down, refuses or times out."""
    return ModelUnavailable("the model API timed out")


def say(text: str) -> ModelReply:
    """The model replies with plain text."""
    return ModelReply(content=text)


def decline(reason: str, *can_do: str) -> ModelReply:
    """The model declines the request, offering these catalogue Operations instead."""
    return ModelReply(
        tool_calls=[
            ToolCall(
                id="call-decline",
                name="decline_request",
                arguments={"reason": reason, "can_do": list(can_do)},
            )
        ]
    )


def suspect(kind: str) -> ModelReply:
    """The model declines the request as a suspected manipulation attempt of this kind
    (`impersonation` or `injection`)."""
    return ModelReply(
        tool_calls=[
            ToolCall(
                id="call-decline",
                name="decline_request",
                arguments={"reason": "manipulation", "kind": kind, "can_do": []},
            )
        ]
    )


def clarify(question: str, *options: tuple[str, str]) -> ModelReply:
    """The model asks the Customer one clarifying question, with (label, value) options."""
    arguments = {
        "question": question,
        "options": [{"label": label, "value": value} for label, value in options],
    }
    return ModelReply(
        tool_calls=[ToolCall(id="call-clarify", name="ask_clarification", arguments=arguments)]
    )


def ask_for_human() -> ModelReply:
    """The model reads the Customer's message as asking for a human."""
    return ModelReply(tool_calls=[ToolCall(id="call-human", name="ask_for_human", arguments={})])


def offer_cancellations() -> ModelReply:
    """The model reads the Customer's message as asking to cancel the Subscriptions they
    may not need, without saying which: the Agent lists the Bank's candidates to pick from."""
    return ModelReply(
        tool_calls=[ToolCall(id="call-offer", name="offer_cancellations", arguments={})]
    )
