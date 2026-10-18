"""A fake model client: returns scripted replies in order and records every request.

Tests script what the model would say (a Plan, then an answer) with `plan(...)` and
`say(...)`, so no test ever calls a real model.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from agent.model import ModelReply, ToolCall


@dataclass
class ModelRequest:
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None

    def text(self) -> str:
        """Everything the model was shown, as one string, for `in` checks."""
        return json.dumps({"messages": self.messages, "tools": self.tools}, ensure_ascii=False)


@dataclass
class FakeModel:
    replies: list[ModelReply] = field(default_factory=list)
    requests: list[ModelRequest] = field(default_factory=list)

    def script(self, *replies: ModelReply) -> None:
        self.replies.extend(replies)

    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> ModelReply:
        self.requests.append(ModelRequest(messages=messages, tools=tools))
        assert self.replies, "the model was asked for more replies than the test scripted"
        return self.replies.pop(0)


def step(
    id: str, operation: str, input: dict[str, Any] | None = None, depends_on: list[str] | None = None
) -> dict[str, Any]:
    return {"id": id, "operation": operation, "input": input or {}, "depends_on": depends_on or []}


def plan(*steps: dict[str, Any], intent: str | None = None) -> ModelReply:
    """The model proposes a Plan with these steps, saying what it understood the Customer
    wants (`intent`) when given."""
    arguments: dict[str, Any] = {"steps": list(steps)}
    if intent is not None:
        arguments["intent"] = intent
    return ModelReply(
        tool_calls=[ToolCall(id="call-plan", name="propose_plan", arguments=arguments)]
    )


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
