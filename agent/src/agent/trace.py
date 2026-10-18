"""The decision trace: what the Agent understood, planned and was told by the Bank, as
`trace` events for the Channel's toggleable panel (docs/conversation-events.md).

A trace never carries a token (session, phone, Confirmation, verification), a password or
a verification code: every field named like one is redacted, wherever it is.
"""

from collections.abc import Iterator
from typing import Any, Literal

from agent.bank import BankExchange, BankSession
from agent.events import Event, Trace

REDACTED = "[redacted]"


def _secret(key: str) -> bool:
    key = key.lower()
    return "token" in key or "password" in key or key == "code"


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: REDACTED if _secret(str(key)) else redact(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def trace(
    stage: Literal["intent", "plan", "bank_request", "bank_response", "audit_trail"],
    data: dict[str, Any],
) -> Trace:
    redacted: dict[str, Any] = redact(data)
    return Trace(stage=stage, data=redacted)


def bank_traces(exchange: BankExchange) -> Iterator[Trace]:
    yield trace(
        "bank_request", {"method": exchange.method, "path": exchange.path, "body": exchange.body}
    )
    yield trace(
        "bank_response",
        {
            "method": exchange.method,
            "path": exchange.path,
            "status": exchange.status,
            "body": exchange.response,
        },
    )


def with_bank_traces(bank: BankSession, events: Iterator[Event]) -> Iterator[Event]:
    """`events`, each preceded by the Bank calls made since the one before, so that every
    Bank exchange shows up in the trace where it happened, even when a call fails."""

    def since_last() -> Iterator[Trace]:
        for exchange in bank.take_exchanges():
            yield from bank_traces(exchange)

    try:
        for event in events:
            yield from since_last()
            yield event
    except Exception:
        yield from since_last()
        raise
    yield from since_last()
