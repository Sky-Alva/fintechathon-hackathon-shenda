"""Every session is limited to its own Customer; password change needs Strong Verification."""

from typing import Any

import httpx
from fastapi.testclient import TestClient

from conftest import (
    admin,
    balance,
    call,
    confirm,
    login,
    login_session,
    login_session_with_phone,
    login_with_phone,
    phone_messages,
    preview_tiers,
    request_confirmations,
    request_verification,
    verify,
)

WIFE = "PAY-LM-001"  # 韩梅梅, as 李明's Payee
WIFE_CURRENT = "ACC-HM-001"
WIFE_SAVINGS = "ACC-HM-002"
OWN_CURRENT = "ACC-LM-001"
NEW_PASSWORD = "new-secret-2026"


def refusal(response: httpx.Response) -> Any:
    return response.json()["refusal"]


def log_in(client: TestClient, username: str, password: str) -> httpx.Response:
    response: httpx.Response = client.post(
        "/sessions", json={"username": username, "password": password}
    )
    return response


# --- The spouse's accounts ------------------------------------------------------------


def test_reading_the_spouses_balance_is_refused(client: TestClient) -> None:
    headers = login(client, "liming")
    for account_id in [WIFE_CURRENT, WIFE_SAVINGS]:
        response = call(client, headers, "query_balance", {"account_id": account_id})

        assert response.status_code == 403, account_id
        assert refusal(response)["reason"] == "forbidden_other_customer"
        assert refusal(response)["detail"] == {"field": "account_id"}
        assert "8650" not in response.text and "50000" not in response.text


def test_a_customer_can_read_one_of_their_own_accounts(client: TestClient) -> None:
    headers = login(client, "hanmei")

    response = call(client, headers, "query_balance", {"account_id": WIFE_SAVINGS})

    assert response.status_code == 200, response.text
    accounts = response.json()["output"]["accounts"]
    assert [(a["account_id"], a["balance"]) for a in accounts] == [(WIFE_SAVINGS, "50000.00")]


def test_an_account_that_does_not_exist_is_not_found(client: TestClient) -> None:
    headers = login(client, "liming")

    response = call(client, headers, "query_balance", {"account_id": "ACC-NOBODY"})

    assert response.status_code == 404
    assert refusal(response)["reason"] == "not_found"


def test_naming_another_customer_in_the_input_is_refused_not_ignored(client: TestClient) -> None:
    headers = login(client, "liming")

    response = call(client, headers, "query_balance", {"customer_id": "CUST-HM"})

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"


def test_transferring_to_the_spouse_as_a_payee_still_works(client: TestClient) -> None:
    liming = login(client, "liming")
    hanmei = login(client, "hanmei")
    token = confirm(client, liming, "transfer", {"payee_id": WIFE, "amount": "200.00"})

    response = call(client, liming, "transfer", {"payee_id": WIFE, "amount": "200.00"}, token)

    assert response.status_code == 200, response.text
    assert response.json()["tier"] == "yellow"
    assert balance(client, liming, OWN_CURRENT) == "980.00"
    assert balance(client, hanmei, WIFE_CURRENT) == "8850.00"


def test_another_customers_account_is_refused_in_preview_confirmation_and_verification(
    client: TestClient,
) -> None:
    headers = login(client, "liming")
    spouse_account = ("query_balance", {"account_id": WIFE_SAVINGS})

    preview = preview_tiers(client, headers, spouse_account)
    confirmation = request_confirmations(client, headers, spouse_account)
    challenge = request_verification(client, headers, *spouse_account)

    assert preview.json()["steps"][0]["refusal"]["reason"] == "forbidden_other_customer"
    assert refusal(confirmation)["reason"] == "forbidden_other_customer"
    assert refusal(challenge)["reason"] == "forbidden_other_customer"


def test_refusals_for_another_customers_data_are_audited(client: TestClient) -> None:
    session_id, headers = login_session(client, "liming")
    call(client, headers, "query_balance", {"account_id": WIFE_SAVINGS})

    entries = admin(client, "get", f"/admin/sessions/{session_id}/audit")["entries"]

    assert [(e["operation"], e["tier"], e["outcome"], e["reason"]) for e in entries] == [
        ("query_balance", "green", "refused", "forbidden_other_customer")
    ]
    assert entries[0]["input"] == {"account_id": WIFE_SAVINGS}


# --- Password change ------------------------------------------------------------------


def test_password_change_is_red_and_irreversible(client: TestClient) -> None:
    operations = client.get("/operations").json()["operations"]
    entry = next(op for op in operations if op["name"] == "change_password")

    assert entry["tier"] == {"kind": "fixed", "tier": "red"}
    assert entry["reversibility"] == {"kind": "irreversible"}
    assert entry["description_zh"] and entry["description_en"]


def test_password_change_without_verification_is_refused(client: TestClient) -> None:
    headers = login(client, "liming")

    response = call(client, headers, "change_password", {"new_password": NEW_PASSWORD})
    confirmation = request_confirmations(
        client, headers, ("change_password", {"new_password": NEW_PASSWORD})
    )

    assert refusal(response)["reason"] == "needs_verification"
    assert refusal(confirmation)["reason"] == "needs_verification"
    assert log_in(client, "liming", "demo1234").status_code == 200


def test_a_verified_password_change_replaces_the_old_password(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "liming")
    input = {"new_password": NEW_PASSWORD}
    token = verify(client, headers, phone, "change_password", input)

    response = call(client, headers, "change_password", input, verification_token=token)

    assert response.status_code == 200, response.text
    assert response.json()["tier"] == "red"
    assert log_in(client, "liming", NEW_PASSWORD).status_code == 200
    old = log_in(client, "liming", "demo1234")
    assert old.status_code == 401
    assert refusal(old)["reason"] == "not_authenticated"


def test_a_verification_for_one_new_password_does_not_cover_another(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "liming")
    token = verify(client, headers, phone, "change_password", {"new_password": NEW_PASSWORD})

    response = call(
        client, headers, "change_password", {"new_password": "attacker-pass"},
        verification_token=token,
    )

    assert refusal(response)["reason"] == "token_invalid"
    assert log_in(client, "liming", "attacker-pass").status_code == 401


def test_a_new_password_must_be_8_to_64_characters(client: TestClient) -> None:
    headers = login(client, "liming")
    for password in ["short", "x" * 65]:
        response = call(client, headers, "change_password", {"new_password": password})

        assert refusal(response)["reason"] == "invalid_input", password


def test_the_new_password_never_appears_in_the_audit_log_or_sms(client: TestClient) -> None:
    session_id, headers, phone = login_session_with_phone(client, "liming")
    input = {"new_password": NEW_PASSWORD}
    preview_tiers(client, headers, ("change_password", input))
    call(client, headers, "change_password", input)
    call(client, headers, "change_password", {"new_password": "short"})
    token = verify(client, headers, phone, "change_password", input)
    call(client, headers, "change_password", input, verification_token=token)

    entries = admin(client, "get", f"/admin/sessions/{session_id}/audit")["entries"]

    assert len(entries) == 6
    assert NEW_PASSWORD not in str(entries) and "short" not in str(entries)
    assert NEW_PASSWORD not in str(phone_messages(client, phone))
    assert entries[-1]["input"] == {"new_password": "**********"}
    assert entries[-1]["outcome"] == "ok"


def test_a_malformed_plan_or_verification_request_never_logs_the_new_password(
    client: TestClient,
) -> None:
    session_id, headers = login_session(client, "liming")
    step = {"operation": "change_password", "input": {"new_password": NEW_PASSWORD}}
    client.post("/tier-preview", headers=headers, json={"operations": [step], "stray": 1})
    client.post("/confirmations", headers=headers, json={"operations": [step], "stray": 1})
    client.post("/verifications", headers=headers, json={**step, "stray": 1})

    entries = admin(client, "get", f"/admin/sessions/{session_id}/audit")["entries"]

    assert [e["reason"] for e in entries] == ["invalid_input"] * 3
    assert NEW_PASSWORD not in str(entries)
