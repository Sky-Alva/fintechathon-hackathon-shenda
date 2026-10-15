from typing import Any

import httpx
from fastapi.testclient import TestClient

from conftest import ADMIN_KEY, SEED_NOW, admin, call, login, login_session


def refusal(response: httpx.Response) -> Any:
    return response.json()["refusal"]


# --- Sessions and the balance query ---------------------------------------------------


def test_customer_logs_in_and_queries_their_balance_without_a_token(client: TestClient) -> None:
    headers = login(client, "liming")

    response = call(client, headers, "query_balance")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["operation"] == "query_balance"
    assert body["tier"] == "green"
    accounts = body["output"]["accounts"]
    assert [(a["account_id"], a["balance"]) for a in accounts] == [("ACC-LM-001", "1180.00")]


def test_each_session_sees_only_its_own_customers_accounts(client: TestClient) -> None:
    liming = login(client, "liming")
    hanmei = login(client, "hanmei")

    liming_accounts = call(client, liming, "query_balance").json()["output"]["accounts"]
    hanmei_accounts = call(client, hanmei, "query_balance").json()["output"]["accounts"]

    assert {a["account_id"] for a in liming_accounts} == {"ACC-LM-001"}
    assert {a["account_id"] for a in hanmei_accounts} == {"ACC-HM-001", "ACC-HM-002"}


def test_login_binds_the_session_to_one_customer(client: TestClient) -> None:
    response = client.post("/sessions", json={"username": "hanmei", "password": "demo1234"})

    assert response.status_code == 200, response.text
    assert response.json()["customer"]["customer_id"] == "CUST-HM"


def test_login_with_a_wrong_password_is_refused(client: TestClient) -> None:
    response = client.post("/sessions", json={"username": "liming", "password": "wrong"})

    assert response.status_code == 401
    assert refusal(response)["reason"] == "not_authenticated"


def test_operation_without_a_valid_session_is_refused(client: TestClient) -> None:
    response = call(client, {"Authorization": "Bearer not-a-session"}, "query_balance")

    assert response.status_code == 401
    assert refusal(response)["reason"] == "not_authenticated"


# --- Refusal reasons ----------------------------------------------------------------


def test_unknown_operation_is_refused_with_not_found(client: TestClient) -> None:
    response = call(client, login(client, "liming"), "transfer_everything_to_me")

    assert response.status_code == 404
    assert refusal(response)["reason"] == "not_found"
    assert refusal(response)["detail"] == {"operation": "transfer_everything_to_me"}


def test_malformed_input_is_refused_with_invalid_input(client: TestClient) -> None:
    response = call(client, login(client, "liming"), "query_balance", {"customer_id": "CUST-HM"})

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"
    assert refusal(response)["message"]


# --- Operation catalogue ------------------------------------------------------------


def test_catalogue_describes_the_balance_query(client: TestClient) -> None:
    response = client.get("/operations")

    assert response.status_code == 200
    entry = next(op for op in response.json()["operations"] if op["name"] == "query_balance")
    assert entry["description_zh"] and entry["description_en"]
    assert entry["tier"] == {"kind": "fixed", "tier": "green"}
    assert entry["reversibility"] == {"kind": "read_only"}
    assert entry["input_schema"]["type"] == "object"
    assert "accounts" in entry["output_schema"]["properties"]


# --- Audit log ----------------------------------------------------------------------


def test_every_operation_call_is_audited_against_its_session(client: TestClient) -> None:
    session_id, headers = login_session(client, "liming")
    call(client, headers, "query_balance")
    call(client, headers, "query_balance", {"bogus": 1})
    call(client, headers, "no_such_operation")

    entries = admin(client, "get", f"/admin/sessions/{session_id}/audit")["entries"]

    assert [(e["operation"], e["tier"], e["outcome"], e["reason"]) for e in entries] == [
        ("query_balance", "green", "ok", None),
        ("query_balance", "green", "refused", "invalid_input"),
        ("no_such_operation", None, "refused", "not_found"),
    ]
    assert all(e["session_id"] == session_id for e in entries)
    assert all(e["at"] == SEED_NOW for e in entries)


def test_audit_log_is_kept_per_session(client: TestClient) -> None:
    first, first_headers = login_session(client, "liming")
    second, _ = login_session(client, "liming")
    call(client, first_headers, "query_balance")

    assert len(admin(client, "get", f"/admin/sessions/{first}/audit")["entries"]) == 1
    assert admin(client, "get", f"/admin/sessions/{second}/audit")["entries"] == []


# --- Bank clock ---------------------------------------------------------------------


def test_bank_clock_can_be_read_set_and_advanced(client: TestClient) -> None:
    assert client.get("/clock").json() == {"now": SEED_NOW}

    admin(client, "put", "/admin/clock", json={"now": "2026-10-25T18:30:00+08:00"})
    assert client.get("/clock").json() == {"now": "2026-10-25T18:30:00+08:00"}

    advanced = admin(client, "post", "/admin/clock/advance", json={"minutes": 90})
    assert advanced == {"now": "2026-10-25T20:00:00+08:00"}
    assert client.get("/clock").json() == advanced


def test_audit_timestamps_come_from_the_bank_clock(client: TestClient) -> None:
    admin(client, "put", "/admin/clock", json={"now": "2027-01-01T00:00:00+08:00"})
    session_id, headers = login_session(client, "liming")

    call(client, headers, "query_balance")

    entries = admin(client, "get", f"/admin/sessions/{session_id}/audit")["entries"]
    assert entries[0]["at"] == "2027-01-01T00:00:00+08:00"


def test_admin_endpoints_require_the_admin_key(client: TestClient) -> None:
    for method, url in [("post", "/admin/reset"), ("put", "/admin/clock")]:
        response = client.request(method, url, headers={"X-Admin-Key": "guess"}, json={})
        assert response.status_code == 401
        assert refusal(response)["reason"] == "not_authenticated"


# --- Reset --------------------------------------------------------------------------


def test_reset_restores_the_seed_state(client: TestClient) -> None:
    session_id, headers = login_session(client, "liming")
    call(client, headers, "query_balance")
    admin(client, "post", "/admin/clock/advance", json={"minutes": 600})

    admin(client, "post", "/admin/reset")

    assert client.get("/clock").json() == {"now": SEED_NOW}
    assert call(client, headers, "query_balance").status_code == 401
    response = client.get(
        f"/admin/sessions/{session_id}/audit", headers={"X-Admin-Key": ADMIN_KEY}
    )
    assert response.status_code == 404
    accounts = call(client, login(client, "liming"), "query_balance").json()["output"]["accounts"]
    assert accounts[0]["balance"] == "1180.00"


def test_non_object_input_is_refused_and_audited(client: TestClient) -> None:
    session_id, headers = login_session(client, "liming")

    response = client.post("/operations/query_balance", headers=headers, json={"input": [1]})

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"
    entries = admin(client, "get", f"/admin/sessions/{session_id}/audit")["entries"]
    assert [(e["operation"], e["input"], e["reason"]) for e in entries] == [
        ("query_balance", [1], "invalid_input")
    ]


def test_malformed_request_from_a_session_is_refused_and_audited(client: TestClient) -> None:
    session_id, headers = login_session(client, "liming")

    response = client.post(
        "/operations/query_balance", headers=headers, json={"input": {}, "token": "x"}
    )

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"
    entries = admin(client, "get", f"/admin/sessions/{session_id}/audit")["entries"]
    assert [(e["operation"], e["reason"]) for e in entries] == [("query_balance", "invalid_input")]
