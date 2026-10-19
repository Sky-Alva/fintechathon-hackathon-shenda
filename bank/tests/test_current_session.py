"""A session token tells its holder which session and Customer it is, and nothing more:
the Agent uses this to show Staff Members whose conversation is whose."""

from fastapi.testclient import TestClient

from conftest import admin, login_session


def test_a_session_token_reads_its_own_session_and_customer(client: TestClient) -> None:
    session_id, headers = login_session(client, "hanmei")

    response = client.get("/sessions/current", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "session_id": session_id,
        "customer": {"customer_id": "CUST-HM", "name": "韩梅梅"},
    }


def test_reading_the_current_session_is_not_an_operation_and_is_not_audited(
    client: TestClient,
) -> None:
    session_id, headers = login_session(client, "liming")

    client.get("/sessions/current", headers=headers)

    assert admin(client, "get", f"/admin/sessions/{session_id}/audit")["entries"] == []


def test_an_ended_or_unknown_session_token_is_not_authenticated(client: TestClient) -> None:
    _, headers = login_session(client, "liming")
    admin(client, "post", "/admin/reset")

    for sent in [headers, {"Authorization": "Bearer no-such-token"}, {}]:
        response = client.get("/sessions/current", headers=sent)
        assert response.status_code == 401, sent
        assert response.json()["refusal"]["reason"] == "not_authenticated"
