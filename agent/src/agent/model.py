"""The model client: one OpenAI-compatible chat interface for every model the Agent uses.

DeepSeek is the default and `qwen-plus` on DashScope the configured alternative, chosen with
`AGENT_MODEL_PROVIDER`. Both speak the OpenAI chat-completions protocol, so one client
covers them. Tests use a fake with the same `chat` method (tests/fake_model.py).
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from openai import OpenAI, OpenAIError


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelReply:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


class ModelClient(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        require_tool: bool = False,
    ) -> ModelReply:
        """One chat completion. `messages` and `tools` are in the OpenAI format. With
        `require_tool`, the reply should call one of `tools` rather than answer in text:
        a model that can be made to (`tool_choice: "required"`) is."""
        ...


@dataclass(frozen=True)
class Provider:
    base_url: str
    model: str
    api_key_env: str
    requires_tool_choice: bool = False
    """Honours `tool_choice: "required"`. Only set once checked against the real API:
    left off, the model may still answer in text, and the caller has to cope."""


PROVIDERS = {
    "qwen": Provider(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen-plus",
        api_key_env="DASHSCOPE_API_KEY",
    ),
    "deepseek": Provider(
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        api_key_env="DEEPSEEK_API_KEY",
        requires_tool_choice=True,
    ),
}
DEFAULT_PROVIDER = "deepseek"


class ModelUnavailable(Exception):
    """The model couldn't be asked, or its reply couldn't be read: no API key, the API
    refused or timed out, or a tool call's arguments aren't JSON."""


class ModelNotConfigured(ModelUnavailable):
    pass


OFFLINE = "offline"


class OfflineModel:
    """`AGENT_MODEL_PROVIDER=offline`: no model at all. The conversation plans with the
    built-in rules (`agent.rules`) and the Responder quotes the Bank; `chat` is never
    meant to be called, and says so the way an unconfigured model does."""

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        require_tool: bool = False,
    ) -> ModelReply:
        raise ModelNotConfigured("AGENT_MODEL_PROVIDER=offline: no model is used")


class OpenAICompatibleModel:
    def __init__(self, provider: Provider, api_key: str | None):
        self.provider = provider
        self.api_key = api_key
        self.client = OpenAI(base_url=provider.base_url, api_key=api_key or "not-set")

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        require_tool: bool = False,
    ) -> ModelReply:
        if not self.api_key:
            raise ModelNotConfigured(
                f"Set {self.provider.api_key_env} in .env to use {self.provider.model}"
            )
        kwargs: dict[str, Any] = {"model": self.provider.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            if require_tool and self.provider.requires_tool_choice:
                kwargs["tool_choice"] = "required"
        try:
            message = self.client.chat.completions.create(**kwargs).choices[0].message
            return ModelReply(
                content=message.content,
                tool_calls=[
                    ToolCall(
                        id=call.id,
                        name=call.function.name,
                        arguments=json.loads(call.function.arguments or "{}"),
                    )
                    for call in message.tool_calls or []
                    if call.type == "function"
                ],
            )
        except (OpenAIError, ValueError) as error:  # json.JSONDecodeError is a ValueError
            raise ModelUnavailable(f"{self.provider.model}: {error}") from error


def model_from_env(env: Mapping[str, str]) -> OpenAICompatibleModel | OfflineModel:
    """The model the configuration selects: `AGENT_MODEL_PROVIDER` is `deepseek` (default),
    `qwen` or `offline` (no model: the built-in rules plan), and a provider's API key
    comes from its env variable."""
    name = env.get("AGENT_MODEL_PROVIDER") or DEFAULT_PROVIDER
    if name == OFFLINE:
        return OfflineModel()
    if name not in PROVIDERS:
        raise ModelNotConfigured(
            f"AGENT_MODEL_PROVIDER must be one of {', '.join([*PROVIDERS, OFFLINE])}, not {name!r}"
        )
    provider = PROVIDERS[name]
    return OpenAICompatibleModel(provider, env.get(provider.api_key_env))
