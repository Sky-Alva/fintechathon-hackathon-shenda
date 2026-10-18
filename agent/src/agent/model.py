"""The model client: one OpenAI-compatible chat interface for every model the Agent uses.

`qwen-plus` on DashScope is the default and DeepSeek the configured backup, chosen with
`AGENT_MODEL_PROVIDER`. Both speak the OpenAI chat-completions protocol, so one client
covers them. Tests use a fake with the same `chat` method (tests/fake_model.py).
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from openai import OpenAI


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
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> ModelReply:
        """One chat completion. `messages` and `tools` are in the OpenAI format."""
        ...


@dataclass(frozen=True)
class Provider:
    base_url: str
    model: str
    api_key_env: str


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
    ),
}
DEFAULT_PROVIDER = "qwen"


class ModelNotConfigured(Exception):
    pass


class OpenAICompatibleModel:
    def __init__(self, provider: Provider, api_key: str | None):
        self.provider = provider
        self.api_key = api_key
        self.client = OpenAI(base_url=provider.base_url, api_key=api_key or "not-set")

    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> ModelReply:
        if not self.api_key:
            raise ModelNotConfigured(
                f"Set {self.provider.api_key_env} in .env to use {self.provider.model}"
            )
        kwargs: dict[str, Any] = {"model": self.provider.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
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


def model_from_env(env: Mapping[str, str]) -> OpenAICompatibleModel:
    """The model the configuration selects: `AGENT_MODEL_PROVIDER` is `qwen` (default) or
    `deepseek`, and its API key comes from that provider's env variable."""
    name = env.get("AGENT_MODEL_PROVIDER") or DEFAULT_PROVIDER
    if name not in PROVIDERS:
        raise ModelNotConfigured(
            f"AGENT_MODEL_PROVIDER must be one of {', '.join(PROVIDERS)}, not {name!r}"
        )
    provider = PROVIDERS[name]
    return OpenAICompatibleModel(provider, env.get(provider.api_key_env))
