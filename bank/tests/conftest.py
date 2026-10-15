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
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(db_path=tmp_path / "bank.sqlite3", admin_key=ADMIN_KEY)
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


def login_session(
    client: TestClient, username: str, password: str = "demo1234"
) -> tuple[str, dict[str, str]]:
    """Log in and return the session ID (for audit reads) and the request headers."""
    response = client.post("/sessions", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    body = response.json()
    return body["session_id"], {"Authorization": f"Bearer {body['session_token']}"}


def call(
    client: TestClient,
    headers: dict[str, str],
    operation: str,
    input: dict[str, Any] | None = None,
    confirmation_token: str | None = None,
) -> httpx.Response:
    body: dict[str, Any] = {"input": input or {}}
    if confirmation_token is not None:
        body["confirmation_token"] = confirmation_token
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
