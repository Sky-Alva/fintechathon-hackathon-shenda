"""Tests drive the Agent's conversation interface only: messages and Customer actions in,
events out (docs/conversation-events.md).

Every test gets a real, freshly reset Bank with the Bank clock fixed at the seed time,
and an Agent whose model client is a fake that returns scripted replies. The Agent never
gets the Bank's admin key; only the test (standing in for the Bank's staff) uses it to
reset the Bank and read audit trails.
"""

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agent.app import create_app as create_agent
from bank.app import create_app as create_bank
from fake_model import FakeModel

ADMIN_KEY = "test-admin-key"
STAFF_KEY = "test-staff-key"
SEED_NOW = "2026-10-20T09:00:00+08:00"


@pytest.fixture
def bank(tmp_path: Path) -> Iterator[TestClient]:
    app = create_bank(db_path=tmp_path / "bank.sqlite3", admin_key=ADMIN_KEY)
    with TestClient(app) as client:
        admin(client, "post", "/admin/reset")
        admin(client, "put", "/admin/clock", json={"now": SEED_NOW})
        yield client


@pytest.fixture
def model() -> FakeModel:
    return FakeModel()


class BankLink:
    """The Agent's line to the Bank, which a test can cut to make the Bank unreachable."""

    def __init__(self, bank: TestClient):
        self.bank = bank
        self.cut_at: str | None = None
        self.hooks: list[tuple[str, Callable[[], object]]] = []

    def cut(self, url_prefix: str = "/") -> None:
        """From now on, every request to a URL starting with `url_prefix` can't connect."""
        self.cut_at = url_prefix

    def after_first(self, url_prefix: str, then: Callable[[], object]) -> None:
        """Once the Bank has answered the next request to a URL starting with `url_prefix`,
        call `then` (e.g. the Customer taps stop while the Plan runs), before the Agent
        sees the answer."""
        self.hooks.append((url_prefix, then))

    def request(
        self, method: str, url: str, *, headers: dict[str, str], json: Any = None
    ) -> Any:
        if self.cut_at is not None and url.startswith(self.cut_at):
            raise httpx.ConnectError(f"cannot reach the Bank: {method} {url}")
        response = self.bank.request(method, url, headers=headers, json=json)
        for hook in list(self.hooks):
            if url.startswith(hook[0]):
                self.hooks.remove(hook)
                hook[1]()
        return response


@pytest.fixture
def bank_link(bank: TestClient) -> BankLink:
    return BankLink(bank)


@pytest.fixture
def agent(bank_link: BankLink, model: FakeModel) -> Iterator[TestClient]:
    # The Bank test client has no admin key on it: the Agent is an untrusted caller.
    with TestClient(create_agent(bank=bank_link, model=model, staff_key=STAFF_KEY)) as client:
        yield client


def admin(bank: TestClient, method: str, url: str, **kwargs: Any) -> Any:
    response = bank.request(method, url, headers={"X-Admin-Key": ADMIN_KEY}, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


@dataclass(frozen=True)
class Login:
    """What the Channel holds after logging the Customer in with the Bank."""

    session_id: str
    session_token: str
    phone_token: str


def log_in(bank: TestClient, username: str, password: str = "demo1234") -> Login:
    """Log the Customer in with the Bank, as the Channel does."""
    response = bank.post("/sessions", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    body = response.json()
    return Login(body["session_id"], body["session_token"], body["phone_token"])


def start_conversation(agent: TestClient, login: Login) -> str:
    """Hand the Agent the session token (only) and get a conversation."""
    response = agent.post("/conversations", json={"session_token": login.session_token})
    assert response.status_code == 200, response.text
    conversation_id: str = response.json()["conversation_id"]
    return conversation_id


def send(agent: TestClient, conversation_id: str, text: str) -> list[dict[str, Any]]:
    """Send a Customer message and collect the turn's stream of events."""
    response = agent.post(f"/conversations/{conversation_id}/messages", json={"text": text})
    assert response.status_code == 200, response.text
    return [json.loads(line) for line in response.text.splitlines() if line]


def act(
    agent: TestClient, conversation_id: str, action: str, **fields: Any
) -> list[dict[str, Any]]:
    """Answer a prompt (e.g. tap confirm on a Plan card) and collect the turn's events."""
    response = agent.post(
        f"/conversations/{conversation_id}/actions", json={"action": action, **fields}
    )
    assert response.status_code == 200, response.text
    return [json.loads(line) for line in response.text.splitlines() if line]


def event_types(events: list[dict[str, Any]]) -> list[str]:
    """The turn's event types, leaving out the decision trace (see test_decision_trace.py)."""
    return [event["type"] for event in events if event["type"] != "trace"]


def traces(events: list[dict[str, Any]], stage: str | None = None) -> list[dict[str, Any]]:
    """The `data` of the turn's `trace` events, of one stage if given."""
    return [
        event["data"]
        for event in events
        if event["type"] == "trace" and (stage is None or event["stage"] == stage)
    ]


def audit_trail(agent: TestClient, conversation_id: str) -> dict[str, Any]:
    """The `trace` event with the session's audit trail, as the decision trace panel
    fetches it from the Agent."""
    response = agent.get(f"/conversations/{conversation_id}/audit-trail")
    assert response.status_code == 200, response.text
    event: dict[str, Any] = response.json()
    return event


def staff(agent: TestClient, url: str) -> Any:
    """What a Staff Member reads from the Agent with the Agent's staff key (never the
    Bank's admin key, which the Agent doesn't have)."""
    response = agent.get(url, headers={"X-Staff-Key": STAFF_KEY})
    assert response.status_code == 200, response.text
    return response.json()


def phone_messages(bank: TestClient, login: Login) -> list[dict[str, Any]]:
    """The SMS on the Customer's simulated phone, read as the Channel's phone does: with
    the phone token, which the Agent never has."""
    response = bank.get("/phone/messages", headers={"Authorization": f"Bearer {login.phone_token}"})
    assert response.status_code == 200, response.text
    messages: list[dict[str, Any]] = response.json()["messages"]
    return messages


def latest_code(bank: TestClient, login: Login) -> str:
    """The code in the newest SMS on the simulated phone."""
    match = re.search(r"\b(\d{6})\b", phone_messages(bank, login)[-1]["text"])
    assert match, "no code in the latest SMS"
    return match.group(1)


def type_code(bank: TestClient, login: Login, challenge_id: str, code: str) -> dict[str, Any]:
    """Type a code into the simulated phone, which sends it straight to the Bank with the
    phone token, as the Channel does. Returns the Bank's body: a verification token, or
    its refusal."""
    response = bank.post(
        f"/verifications/{challenge_id}",
        headers={"Authorization": f"Bearer {login.phone_token}"},
        json={"code": code},
    )
    body: dict[str, Any] = response.json()
    return body


def bank_audit(bank: TestClient, login: Login) -> list[dict[str, Any]]:
    """The session's audit trail as a Staff Member sees it."""
    entries: list[dict[str, Any]] = admin(bank, "get", f"/admin/sessions/{login.session_id}/audit")[
        "entries"
    ]
    return entries


def updates(agent: TestClient, conversation_id: str, after: int = 0) -> dict[str, Any]:
    """The events the Customer was sent outside their own turns (a Staff Member taking
    over, replying, handing back), from the `after`-th on, as the Channel polls them."""
    response = agent.get(f"/conversations/{conversation_id}/updates", params={"after": after})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def staff_post(agent: TestClient, url: str, json: Any = None) -> Any:
    """What a Staff Member does through the Agent with the Agent's staff key."""
    response = agent.post(url, headers={"X-Staff-Key": STAFF_KEY}, json=json)
    assert response.status_code == 200, response.text
    return response.json()


def take_over(agent: TestClient, conversation_id: str, staff_member: str = "staff-1") -> Any:
    """A Staff Member takes the conversation over from the Agent."""
    return staff_post(
        agent, f"/staff/conversations/{conversation_id}/takeover", {"staff": staff_member}
    )


def staff_reply(agent: TestClient, conversation_id: str, text: str) -> Any:
    """The Staff Member who took the conversation over replies to the Customer."""
    return staff_post(agent, f"/staff/conversations/{conversation_id}/messages", {"text": text})


def hand_back(agent: TestClient, conversation_id: str) -> Any:
    """The Staff Member hands the conversation back to the Agent."""
    return staff_post(agent, f"/staff/conversations/{conversation_id}/hand-back")
