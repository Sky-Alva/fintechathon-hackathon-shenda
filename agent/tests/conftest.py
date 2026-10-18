"""Tests drive the Agent's conversation interface only: messages and Customer actions in,
events out (docs/conversation-events.md).

Every test gets a real, freshly reset Bank with the Bank clock fixed at the seed time,
and an Agent whose model client is a fake that returns scripted replies. The Agent never
gets the Bank's admin key; only the test (standing in for the Bank's staff) uses it to
reset the Bank and read audit trails.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent.app import create_app as create_agent
from bank.app import create_app as create_bank
from fake_model import FakeModel

ADMIN_KEY = "test-admin-key"
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


@pytest.fixture
def agent(bank: TestClient, model: FakeModel) -> Iterator[TestClient]:
    # The Bank test client has no admin key on it: the Agent is an untrusted caller.
    with TestClient(create_agent(bank=bank, model=model)) as client:
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
    return [event["type"] for event in events]


def bank_audit(bank: TestClient, login: Login) -> list[dict[str, Any]]:
    """The session's audit trail as a Staff Member sees it."""
    entries: list[dict[str, Any]] = admin(bank, "get", f"/admin/sessions/{login.session_id}/audit")[
        "entries"
    ]
    return entries
