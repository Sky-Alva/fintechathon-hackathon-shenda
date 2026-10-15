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
    client: TestClient, headers: dict[str, str], operation: str, input: dict[str, Any] | None = None
) -> httpx.Response:
    response: httpx.Response = client.post(
        f"/operations/{operation}", headers=headers, json={"input": input or {}}
    )
    return response
