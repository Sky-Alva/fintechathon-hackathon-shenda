import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from bank.app import create_app

ADMIN_KEY = "test-admin-key"
SEED_NOW = "2026-10-20T09:00:00+08:00"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "bank.sqlite3"


@pytest.fixture
def client(db_path: Path) -> Iterator[TestClient]:
    app = create_app(db_path=db_path, admin_key=ADMIN_KEY)
    with TestClient(app) as client:
        admin(client, "post", "/admin/reset")
        admin(client, "put", "/admin/clock", json={"now": SEED_NOW})
        yield client


def admin(client: TestClient, method: str, url: str, **kwargs: Any) -> Any:
    response = client.request(method, url, headers={"X-Admin-Key": ADMIN_KEY}, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def login(client: TestClient, username: str, password: str = "demo1234") -> dict[str, str]:
    return login_session(client, username, password)[1]


def login_with_phone(
    client: TestClient, username: str, password: str = "demo1234"
) -> tuple[dict[str, str], dict[str, str]]:
    """Log in and return the session's request headers and its simulated phone's headers,
    as the Channel holds them (the Agent only ever gets the first)."""
    _, headers, phone = login_session_with_phone(client, username, password)
    return headers, phone


def login_session(
    client: TestClient, username: str, password: str = "demo1234"
) -> tuple[str, dict[str, str]]:
    """Log in and return the session ID (for audit reads) and the request headers."""
    session_id, headers, _ = login_session_with_phone(client, username, password)
    return session_id, headers


def login_session_with_phone(
    client: TestClient, username: str, password: str = "demo1234"
) -> tuple[str, dict[str, str], dict[str, str]]:
    response = client.post("/sessions", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    body = response.json()
    return (
        body["session_id"],
        {"Authorization": f"Bearer {body['session_token']}"},
        {"Authorization": f"Bearer {body['phone_token']}"},
    )


def call(
    client: TestClient,
    headers: dict[str, str],
    operation: str,
    input: dict[str, Any] | None = None,
    confirmation_token: str | None = None,
    verification_token: str | None = None,
) -> httpx.Response:
    body: dict[str, Any] = {"input": input or {}}
    if confirmation_token is not None:
        body["confirmation_token"] = confirmation_token
    if verification_token is not None:
        body["verification_token"] = verification_token
    response: httpx.Response = client.post(f"/operations/{operation}", headers=headers, json=body)
    return response


def request_confirmations(
    client: TestClient, headers: dict[str, str], *operations: tuple[str, dict[str, Any]]
) -> httpx.Response:
    """Ask the Bank for one Confirmation token per (operation, input), as a Plan card would."""
    response: httpx.Response = client.post(
        "/confirmations",
        headers=headers,
        json={"operations": [{"operation": name, "input": input} for name, input in operations]},
    )
    return response


def confirm(
    client: TestClient, headers: dict[str, str], operation: str, input: dict[str, Any]
) -> str:
    """Get a Confirmation token for one Operation, as if the Customer confirmed it."""
    response = request_confirmations(client, headers, (operation, input))
    assert response.status_code == 200, response.text
    token: str = response.json()["confirmations"][0]["confirmation_token"]
    return token


def balance(client: TestClient, headers: dict[str, str], account_id: str) -> str:
    accounts = call(client, headers, "query_balance").json()["output"]["accounts"]
    found: str = next(a["balance"] for a in accounts if a["account_id"] == account_id)
    return found


def request_verification(
    client: TestClient, headers: dict[str, str], operation: str, input: dict[str, Any]
) -> httpx.Response:
    """Ask the Bank for a Strong Verification challenge for one Red Operation."""
    return request_verifications(client, headers, (operation, input))


def request_verifications(
    client: TestClient, headers: dict[str, str], *operations: tuple[str, dict[str, Any]]
) -> httpx.Response:
    """Ask the Bank for one Strong Verification challenge covering every (operation, input)."""
    response: httpx.Response = client.post(
        "/verifications",
        headers=headers,
        json={"operations": [{"operation": name, "input": input} for name, input in operations]},
    )
    return response


def submit_code(
    client: TestClient, phone: dict[str, str], challenge_id: str, code: str
) -> httpx.Response:
    """Type the SMS code into the simulated phone, which sends it straight to the Bank."""
    response: httpx.Response = client.post(
        f"/verifications/{challenge_id}", headers=phone, json={"code": code}
    )
    return response


def phone_messages(client: TestClient, phone: dict[str, str]) -> list[dict[str, Any]]:
    response = client.get("/phone/messages", headers=phone)
    assert response.status_code == 200, response.text
    messages: list[dict[str, Any]] = response.json()["messages"]
    return messages


def latest_code(client: TestClient, phone: dict[str, str]) -> str:
    """The code in the newest SMS on the Customer's simulated phone."""
    match = re.search(r"\b(\d{6})\b", phone_messages(client, phone)[-1]["text"])
    assert match, "no code in the latest SMS"
    return match.group(1)


def verify(
    client: TestClient,
    headers: dict[str, str],
    phone: dict[str, str],
    operation: str,
    input: dict[str, Any],
) -> str:
    """Pass Strong Verification for one Operation, as if the Customer typed the SMS code."""
    challenge = request_verification(client, headers, operation, input)
    assert challenge.status_code == 200, challenge.text
    challenge_id = challenge.json()["challenge_id"]
    response = submit_code(client, phone, challenge_id, latest_code(client, phone))
    assert response.status_code == 200, response.text
    token: str = response.json()["verifications"][0]["verification_token"]
    return token


def preview_tiers(
    client: TestClient, headers: dict[str, str], *operations: tuple[str, Any]
) -> httpx.Response:
    """Ask the Bank for the tier of each step of a proposed Plan, in order."""
    response: httpx.Response = client.post(
        "/tier-preview",
        headers=headers,
        json={"operations": [{"operation": name, "input": input} for name, input in operations]},
    )
    return response
