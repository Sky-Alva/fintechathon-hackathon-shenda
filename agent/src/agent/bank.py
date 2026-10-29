"""The Agent's view of the Bank: one Customer's session, and nothing more.

The Agent holds only the session token the Channel handed it. It never holds the
Customer's password, the simulated phone's token or the Bank's admin key (ADR-0001: the
Agent is an untrusted caller, and the Bank enforces every Permission Tier).
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from agent.events import BankRefusal


class HttpResponse(Protocol):
    status_code: int

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

    def body(self) -> BankRefusal:
        """The refusal as the Bank sent it."""
        return BankRefusal(reason=self.reason, message=self.message, detail=self.detail)


class BankUnavailable(Exception):
    """The Bank couldn't be reached, or failed without a refusal."""


@dataclass(frozen=True)
class BankExchange:
    """One call to the Bank and its raw response, refusals included, for the decision
    trace. The session token is in a header, so it is never part of one."""

    method: str
    path: str
    body: Any
    status: int
    response: Any


@dataclass(frozen=True)
class BankSession:
    http: HttpClient
    session_token: str
    exchanges: list[BankExchange] = field(default_factory=list)
    """Every traced call since the last `take_exchanges()`, in order."""

    def current(self) -> dict[str, Any]:
        """Which Bank session and Customer this is: `{session_id, customer}`. Refused with
        `not_authenticated` once the session has ended."""
        # Not traced: it is asked outside turns (at start, by staff views) and decides nothing.
        current: dict[str, Any] = self._send("get", "/sessions/current", traced=False)
        return current

    def catalogue(self) -> list[dict[str, Any]]:
        """The Bank's Operation catalogue, which the Planner plans from."""
        # Not traced: the catalogue is the same every turn and decides nothing.
        operations: list[dict[str, Any]] = self._send("get", "/operations", traced=False)[
            "operations"
        ]
        return operations

    def clock(self) -> str:
        """The Bank clock's "now" (ISO 8601, Bank time): the only date the Agent plans by."""
        # Not traced, like the catalogue: context for planning, read every turn.
        now: str = self._send("get", "/clock", traced=False)["now"]
        return now

    def tier_preview(self, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Each `{operation, input}`'s tier (or refusal), in order. Changes nothing."""
        steps: list[dict[str, Any]] = self._send(
            "post", "/tier-preview", json={"operations": operations}
        )["steps"]
        return steps

    def request_confirmations(self, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """One Confirmation token per Yellow `{operation, input}`, in order, all or none.

        Only the Plan reviewer calls this, and only when the Customer has tapped confirm
        on the Plan card listing exactly these Operations.
        """
        confirmations: list[dict[str, Any]] = self._send(
            "post", "/confirmations", json={"operations": operations}
        )["confirmations"]
        return confirmations

    def request_verification(self, operation: str, input: dict[str, Any]) -> dict[str, Any]:
        """Open a Strong Verification challenge for one Red call: the Bank texts a code to
        the Customer's simulated phone. Returns `{challenge_id, operation, tier, sent_to,
        expires_at}`; the code itself never comes back to the Agent's session."""
        challenge: dict[str, Any] = self._send(
            "post", "/verifications", json={"operation": operation, "input": input}
        )
        return challenge

    def call(
        self,
        operation: str,
        input: dict[str, Any],
        confirmation_token: str | None = None,
        verification_token: str | None = None,
    ) -> dict[str, Any]:
        """Run one Operation through the Bank's policy gate: `{operation, tier, output}`."""
        body: dict[str, Any] = {"input": input}
        if confirmation_token is not None:
            body["confirmation_token"] = confirmation_token
        if verification_token is not None:
            body["verification_token"] = verification_token
        result: dict[str, Any] = self._send("post", f"/operations/{operation}", json=body)
        return result

    def audit_trail(self) -> dict[str, Any]:
        """The session's own audit trail, from the Green `read_audit_trail` Operation:
        `{session_id, entries}`. Not traced: it is what the trace panel shows beside a turn."""
        output: dict[str, Any] = self._send(
            "post", "/operations/read_audit_trail", json={"input": {}}, traced=False
        )["output"]
        return output

    def take_exchanges(self) -> list[BankExchange]:
        """The traced calls since the last time, oldest first."""
        taken = list(self.exchanges)
        self.exchanges.clear()
        return taken

    def _send(self, method: str, url: str, json: Any = None, *, traced: bool = True) -> Any:
        try:
            response = self.http.request(
                method, url, headers={"Authorization": f"Bearer {self.session_token}"}, json=json
            )
        except httpx.HTTPError as error:
            raise BankUnavailable(f"{method} {url}: {error}") from error
        try:
            body = response.json()
        except ValueError:
            body = None
        if traced:
            self.exchanges.append(
                BankExchange(method.upper(), url, json, response.status_code, body)
            )
        if response.status_code >= 500 or not isinstance(body, dict):
            raise BankUnavailable(f"{method} {url}: HTTP {response.status_code}")
        if "refusal" in body:
            refusal = body["refusal"]
            raise BankRefused(refusal["reason"], refusal["message"], refusal["detail"])
        response.raise_for_status()
        return body
