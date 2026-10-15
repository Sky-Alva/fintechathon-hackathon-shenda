"""Yellow transfers and Confirmation: the ¥200 to 老妈 reference flow."""

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
    request_confirmations,
)

MUM = "PAY-LM-002"
SPOUSE = "PAY-LM-001"
HUSBAND = "PAY-HM-001"


def refusal(response: httpx.Response) -> Any:
    return response.json()["refusal"]


def transfer(payee_id: str, amount: str) -> dict[str, str]:
    return {"payee_id": payee_id, "amount": amount}


# --- The happy path -----------------------------------------------------------------


def test_transfer_without_a_token_needs_confirmation_and_moves_nothing(
    client: TestClient,
) -> None:
    headers = login(client, "liming")

    response = call(client, headers, "transfer", transfer(MUM, "200.00"))

    assert response.status_code == 403
    assert refusal(response)["reason"] == "needs_confirmation"
    assert refusal(response)["detail"] == {"operation": "transfer", "tier": "yellow"}
    assert balance(client, headers, "ACC-LM-001") == "1180.00"


def test_confirmed_transfer_to_mum_runs_once(client: TestClient) -> None:
    headers = login(client, "liming")
    token = confirm(client, headers, "transfer", transfer(MUM, "200.00"))

    response = call(client, headers, "transfer", transfer(MUM, "200.00"), token)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tier"] == "yellow"
    assert body["output"]["amount"] == "200.00"
    assert body["output"]["payee_id"] == MUM
    assert body["output"]["daily_transfer_total"] == "200.00"
    assert balance(client, headers, "ACC-LM-001") == "980.00"


def test_the_same_amount_written_differently_matches_the_token(client: TestClient) -> None:
    headers = login(client, "liming")
    token = confirm(client, headers, "transfer", transfer(MUM, "200"))

    response = call(client, headers, "transfer", transfer(MUM, "200.00"), token)

    assert response.status_code == 200, response.text


def test_transfer_to_the_spouse_credits_their_account(client: TestClient) -> None:
    liming = login(client, "liming")
    token = confirm(client, liming, "transfer", transfer(SPOUSE, "300.00"))

    call(client, liming, "transfer", transfer(SPOUSE, "300.00"), token)

    assert balance(client, liming, "ACC-LM-001") == "880.00"
    assert balance(client, login(client, "hanmei"), "ACC-HM-001") == "8950.00"


# --- Issuing tokens -----------------------------------------------------------------


def test_tokens_for_several_operations_are_issued_in_one_call(client: TestClient) -> None:
    headers = login(client, "liming")

    response = request_confirmations(
        client,
        headers,
        ("transfer", transfer(MUM, "200.00")),
        ("transfer", transfer(SPOUSE, "100.00")),
    )

    assert response.status_code == 200, response.text
    confirmations = response.json()["confirmations"]
    assert [(c["operation"], c["tier"]) for c in confirmations] == [
        ("transfer", "yellow"),
        ("transfer", "yellow"),
    ]
    assert all(c["expires_at"] == "2026-10-20T09:05:00+08:00" for c in confirmations)
    assert len({c["confirmation_token"] for c in confirmations}) == 2
    for confirmation, payee in zip(confirmations, [MUM, SPOUSE], strict=True):
        amount = "200.00" if payee == MUM else "100.00"
        token = confirmation["confirmation_token"]
        assert call(client, headers, "transfer", transfer(payee, amount), token).status_code == 200


def test_no_token_is_issued_for_a_red_operation(client: TestClient) -> None:
    headers = login(client, "liming")

    response = request_confirmations(client, headers, ("transfer", transfer(MUM, "1000.01")))

    assert response.status_code == 403
    assert refusal(response)["reason"] == "needs_verification"


def test_no_token_is_issued_for_a_green_operation(client: TestClient) -> None:
    response = request_confirmations(client, login(client, "liming"), ("query_balance", {}))

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"


def test_one_bad_operation_means_no_tokens_at_all(client: TestClient) -> None:
    response = request_confirmations(
        client,
        login(client, "liming"),
        ("transfer", transfer(MUM, "200.00")),
        ("transfer", transfer("PAY-NOBODY", "10.00")),
    )

    assert response.status_code == 404
    assert refusal(response)["reason"] == "not_found"
    assert refusal(response)["detail"]["index"] == 1


# --- Token misuse -------------------------------------------------------------------


def test_a_token_for_200_is_refused_for_900(client: TestClient) -> None:
    headers = login(client, "liming")
    token = confirm(client, headers, "transfer", transfer(MUM, "200.00"))

    response = call(client, headers, "transfer", transfer(MUM, "900.00"), token)

    assert response.status_code == 403
    assert refusal(response)["reason"] == "token_invalid"
    assert balance(client, headers, "ACC-LM-001") == "1180.00"


def test_a_token_for_mum_is_refused_for_a_different_payee(client: TestClient) -> None:
    headers = login(client, "liming")
    token = confirm(client, headers, "transfer", transfer(MUM, "200.00"))

    response = call(client, headers, "transfer", transfer(SPOUSE, "200.00"), token)

    assert refusal(response)["reason"] == "token_invalid"


def test_a_token_cannot_be_reused(client: TestClient) -> None:
    headers = login(client, "liming")
    token = confirm(client, headers, "transfer", transfer(MUM, "200.00"))
    call(client, headers, "transfer", transfer(MUM, "200.00"), token)

    response = call(client, headers, "transfer", transfer(MUM, "200.00"), token)

    assert refusal(response)["reason"] == "token_invalid"
    assert balance(client, headers, "ACC-LM-001") == "980.00"


def test_a_token_expires_after_five_bank_clock_minutes(client: TestClient) -> None:
    headers = login(client, "liming")
    token = confirm(client, headers, "transfer", transfer(MUM, "200.00"))
    admin(client, "post", "/admin/clock/advance", json={"minutes": 5})

    response = call(client, headers, "transfer", transfer(MUM, "200.00"), token)

    assert refusal(response)["reason"] == "token_invalid"


def test_a_token_cannot_be_used_from_another_session(client: TestClient) -> None:
    token = confirm(client, login(client, "liming"), "transfer", transfer(MUM, "200.00"))

    response = call(client, login(client, "liming"), "transfer", transfer(MUM, "200.00"), token)

    assert refusal(response)["reason"] == "token_invalid"


def test_a_made_up_token_is_refused(client: TestClient) -> None:
    headers = login(client, "liming")

    response = call(client, headers, "transfer", transfer(MUM, "200.00"), "guess")

    assert refusal(response)["reason"] == "token_invalid"


def test_a_refused_transfer_does_not_use_up_its_token(client: TestClient) -> None:
    headers = login(client, "liming")
    token = confirm(client, headers, "transfer", transfer(MUM, "200.00"))
    call(client, headers, "transfer", transfer(MUM, "900.00"), token)

    response = call(client, headers, "transfer", transfer(MUM, "200.00"), token)

    assert response.status_code == 200, response.text


# --- Daily Transfer Total -------------------------------------------------------------


def test_a_yellow_token_is_not_enough_once_the_transfer_has_become_red(
    client: TestClient,
) -> None:
    headers = login(client, "hanmei")
    first = confirm(client, headers, "transfer", transfer(HUSBAND, "600.00"))
    second = confirm(client, headers, "transfer", transfer(HUSBAND, "600.00"))
    call(client, headers, "transfer", transfer(HUSBAND, "600.00"), first)

    response = call(client, headers, "transfer", transfer(HUSBAND, "600.00"), second)

    assert response.status_code == 403
    assert refusal(response)["reason"] == "needs_verification"
    assert balance(client, headers, "ACC-HM-001") == "8050.00"


def test_a_plan_card_counts_its_earlier_transfers_toward_the_daily_total(
    client: TestClient,
) -> None:
    headers = login(client, "hanmei")

    response = request_confirmations(
        client,
        headers,
        ("transfer", transfer(HUSBAND, "600.00")),
        ("transfer", transfer(HUSBAND, "600.00")),
    )

    assert response.status_code == 403
    assert refusal(response)["reason"] == "needs_verification"
    assert refusal(response)["detail"]["index"] == 1


def test_the_daily_transfer_total_starts_again_the_next_day(client: TestClient) -> None:
    headers = login(client, "liming")
    token = confirm(client, headers, "transfer", transfer(MUM, "600.00"))
    call(client, headers, "transfer", transfer(MUM, "600.00"), token)
    admin(client, "put", "/admin/clock", json={"now": "2026-10-21T00:00:00+08:00"})

    response = request_confirmations(client, headers, ("transfer", transfer(MUM, "500.00")))

    assert response.status_code == 200, response.text
    assert response.json()["confirmations"][0]["tier"] == "yellow"


# --- Refusals that need no approval ---------------------------------------------------


def test_transfer_above_the_balance_is_refused_with_insufficient_funds(client: TestClient) -> None:
    headers = login(client, "liming")

    response = call(client, headers, "transfer", transfer(MUM, "1500.00"))

    assert response.status_code == 409
    assert refusal(response)["reason"] == "insufficient_funds"
    assert balance(client, headers, "ACC-LM-001") == "1180.00"


def test_transfer_to_a_payee_the_customer_does_not_have_is_refused(client: TestClient) -> None:
    headers = login(client, "liming")
    for payee_id in ["PAY-NOBODY", "PAY-HM-001"]:  # unknown, and the spouse's own Payee
        response = call(client, headers, "transfer", transfer(payee_id, "10.00"))

        assert response.status_code == 404
        assert refusal(response)["reason"] == "not_found"


def test_a_transfer_amount_must_be_positive_with_at_most_two_decimals(client: TestClient) -> None:
    headers = login(client, "liming")
    for amount in ["0", "-5.00", "10.001", "abc"]:
        response = call(client, headers, "transfer", transfer(MUM, amount))
        assert refusal(response)["reason"] == "invalid_input", amount


# --- Catalogue and audit ------------------------------------------------------------


def test_transfer_is_declared_irreversible_with_a_tier_rule(client: TestClient) -> None:
    operations = client.get("/operations").json()["operations"]
    entry = next(op for op in operations if op["name"] == "transfer")

    assert entry["reversibility"] == {"kind": "irreversible"}
    assert entry["counts_toward_daily_transfer_total"] is True
    assert entry["tier"]["kind"] == "rule"
    assert "1,000" in entry["tier"]["description_en"]
    assert entry["description_zh"] and entry["description_en"]


def test_confirmation_issue_use_and_refusal_are_audited(client: TestClient) -> None:
    session_id, headers = login_session(client, "liming")
    token = confirm(client, headers, "transfer", transfer(MUM, "200.00"))
    call(client, headers, "transfer", transfer(MUM, "900.00"), token)
    call(client, headers, "transfer", transfer(MUM, "200.00"), token)

    entries = admin(client, "get", f"/admin/sessions/{session_id}/audit")["entries"]

    summary = [(e["event"], e["operation"], e["tier"], e["outcome"], e["reason"]) for e in entries]
    assert summary == [
        ("confirmation_issued", "transfer", "yellow", "ok", None),
        ("operation", "transfer", "yellow", "refused", "token_invalid"),
        ("operation", "transfer", "yellow", "ok", None),
    ]
    confirmation_id = entries[0]["detail"]["confirmation_id"]
    assert entries[1]["detail"] == {"confirmation_id": confirmation_id}
    assert entries[2]["detail"] == {"confirmation_id": confirmation_id}
    assert token not in str(entries)
