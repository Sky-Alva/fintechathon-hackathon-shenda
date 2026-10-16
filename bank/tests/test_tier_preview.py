"""The tier preview: what each step of a proposed Plan will need, before the Plan card."""

from typing import Any

import httpx
from fastapi.testclient import TestClient

from conftest import admin, balance, call, confirm, login, login_session, preview_tiers

HUSBAND = "PAY-HM-001"
MUM = "PAY-LM-002"


def transfer(payee_id: str, amount: str) -> dict[str, str]:
    return {"payee_id": payee_id, "amount": amount}


def steps(response: httpx.Response) -> list[dict[str, Any]]:
    assert response.status_code == 200, response.text
    found: list[dict[str, Any]] = response.json()["steps"]
    return found


def test_preview_returns_a_tier_per_operation_in_submitted_order(client: TestClient) -> None:
    headers = login(client, "hanmei")

    response = preview_tiers(
        client,
        headers,
        ("query_balance", {}),
        ("transfer", transfer(HUSBAND, "200.00")),
        ("query_balance", {}),
    )

    assert steps(response) == [
        {"index": 0, "operation": "query_balance", "tier": "green"},
        {"index": 1, "operation": "transfer", "tier": "yellow"},
        {"index": 2, "operation": "query_balance", "tier": "green"},
    ]


def test_two_600_transfers_in_one_preview_come_back_yellow_then_red(client: TestClient) -> None:
    headers = login(client, "hanmei")

    response = preview_tiers(
        client,
        headers,
        ("transfer", transfer(HUSBAND, "600.00")),
        ("transfer", transfer(HUSBAND, "600.00")),
    )

    assert [step["tier"] for step in steps(response)] == ["yellow", "red"]


def test_preview_accounts_for_transfers_already_made_today(client: TestClient) -> None:
    headers = login(client, "hanmei")
    token = confirm(client, headers, "transfer", transfer(HUSBAND, "600.00"))
    assert call(client, headers, "transfer", transfer(HUSBAND, "600.00"), token).status_code == 200

    response = preview_tiers(
        client,
        headers,
        ("transfer", transfer(HUSBAND, "300.00")),
        ("transfer", transfer(HUSBAND, "200.00")),
    )

    assert [step["tier"] for step in steps(response)] == ["yellow", "red"]


def test_preview_changes_nothing_in_the_bank(client: TestClient) -> None:
    headers = login(client, "hanmei")

    preview_tiers(client, headers, ("transfer", transfer(HUSBAND, "900.00")))

    assert balance(client, headers, "ACC-HM-001") == "8650.00"
    # The total is unchanged: ¥900 alone is still Yellow, and nothing was confirmed.
    token = confirm(client, headers, "transfer", transfer(HUSBAND, "900.00"))
    assert call(client, headers, "transfer", transfer(HUSBAND, "900.00"), token).status_code == 200
    assert call(client, headers, "transfer", transfer(HUSBAND, "100.00")).json()["refusal"][
        "reason"
    ] == "needs_confirmation"


def test_preview_issues_no_tokens(client: TestClient) -> None:
    headers = login(client, "hanmei")

    body = preview_tiers(client, headers, ("transfer", transfer(HUSBAND, "200.00"))).json()

    assert "confirmation_token" not in str(body) and "verification_token" not in str(body)
    response = call(client, headers, "transfer", transfer(HUSBAND, "200.00"))
    assert response.json()["refusal"]["reason"] == "needs_confirmation"


def test_preview_is_green_and_audited_as_one_entry(client: TestClient) -> None:
    session_id, headers = login_session(client, "hanmei")

    preview_tiers(
        client,
        headers,
        ("transfer", transfer(HUSBAND, "600.00")),
        ("transfer", transfer(HUSBAND, "600.00")),
    )

    entries = admin(client, "get", f"/admin/sessions/{session_id}/audit")["entries"]
    assert [(e["event"], e["operation"], e["tier"], e["outcome"]) for e in entries] == [
        ("tier_preview", None, "green", "ok")
    ]
    assert [step["tier"] for step in entries[0]["detail"]["steps"]] == ["yellow", "red"]


def test_invalid_entries_get_their_own_refusal_and_the_rest_is_still_tiered(
    client: TestClient,
) -> None:
    liming = login(client, "liming")

    response = preview_tiers(
        client,
        liming,
        ("no_such_operation", {}),
        ("transfer", transfer(MUM, "abc")),
        ("transfer", transfer("PAY-NOBODY", "10.00")),
        ("transfer", transfer(MUM, "5000.00")),
        ("transfer", "not an object"),
        ("transfer", transfer(MUM, "200.00")),
    )

    result = steps(response)
    assert [step.get("refusal", {}).get("reason") for step in result] == [
        "not_found", "invalid_input", "not_found", "insufficient_funds", "invalid_input", None,
    ]
    assert all("tier" not in step for step in result[:5])
    assert result[5] == {"index": 5, "operation": "transfer", "tier": "yellow"}


def test_a_refused_step_does_not_count_toward_the_daily_total(client: TestClient) -> None:
    headers = login(client, "hanmei")

    response = preview_tiers(
        client,
        headers,
        ("transfer", transfer("PAY-NOBODY", "900.00")),
        ("transfer", transfer(HUSBAND, "900.00")),
    )

    assert steps(response)[1]["tier"] == "yellow"


def test_a_malformed_preview_request_is_refused_as_a_whole(client: TestClient) -> None:
    headers = login(client, "hanmei")
    bodies: list[Any] = [
        None, {}, {"operations": []}, {"operations": "x"}, {"operations": [], "x": 1},
    ]
    for body in bodies:
        response = client.post("/tier-preview", headers=headers, json=body)

        assert response.status_code == 422, body
        assert response.json()["refusal"]["reason"] == "invalid_input"


def test_preview_needs_a_session(client: TestClient) -> None:
    response = client.post(
        "/tier-preview", json={"operations": [{"operation": "query_balance", "input": {}}]}
    )

    assert response.status_code == 401
    assert response.json()["refusal"]["reason"] == "not_authenticated"
