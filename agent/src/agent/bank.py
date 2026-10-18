"""The Agent's view of the Bank: one Customer's session, and nothing more.

The Agent holds only the session token the Channel handed it. It never holds the
Customer's password, the simulated phone's token or the Bank's admin key (ADR-0001: the
Agent is an untrusted caller, and the Bank enforces every Permission Tier).
"""

from dataclasses import dataclass
from typing import Any, Protocol


class HttpResponse(Protocol):
    def json(self) -> Any: ...

    def raise_for_status(self) -> Any: ...


class HttpClient(Protocol):
    """An HTTP client whose base URL is the Bank's: an `httpx.Client` in the service, the
    Bank's in-process test client in tests."""

    def request(
        self, method: str, url: str, *, headers: dict[str, str], json: Any = None
    ) -> HttpResponse: ...


class BankRefused(Exception):
    """The Bank refused a call: `reason` is one of its refusal reasons."""

    def __init__(self, reason: str, message: str, detail: dict[str, Any]):
        super().__init__(f"{reason}: {message}")
        self.reason = reason
        self.message = message
        self.detail = detail


@dataclass(frozen=True)
class BankSession:
    http: HttpClient
    session_token: str

    def catalogue(self) -> list[dict[str, Any]]:
        """The Bank's Operation catalogue, which the Planner plans from."""
        operations: list[dict[str, Any]] = self._send("get", "/operations")["operations"]
        return operations

    def tier_preview(self, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Each `{operation, input}`'s tier (or refusal), in order. Changes nothing."""
        steps: list[dict[str, Any]] = self._send(
            "post", "/tier-preview", json={"operations": operations}
        )["steps"]
        return steps

    def call(self, operation: str, input: dict[str, Any]) -> dict[str, Any]:
        """Run one Operation through the Bank's policy gate: `{operation, tier, output}`."""
        result: dict[str, Any] = self._send(
            "post", f"/operations/{operation}", json={"input": input}
        )
        return result

    def _send(self, method: str, url: str, json: Any = None) -> Any:
        response = self.http.request(
            method, url, headers={"Authorization": f"Bearer {self.session_token}"}, json=json
        )
        body = response.json()
        if "refusal" in body:
            refusal = body["refusal"]
            raise BankRefused(refusal["reason"], refusal["message"], refusal["detail"])
        response.raise_for_status()
        return body
