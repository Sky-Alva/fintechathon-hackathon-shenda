"""The First-time Payee signal: over ¥500 to a Payee the Customer has never paid is Red."""

from typing import Any

import httpx
from fastapi.testclient import TestClient

from conftest import (
    admin,
    balance,
    call,
    confirm,
    login,
    login_with_phone,
    preview_tiers,
    request_confirmations,
    verify,
)

MUM = "PAY-LM-002"  # paid before
CHEN = "PAY-LM-005"  # 陈静, never paid
ACCOUNT = "ACC-LM-001"
RUN_DAY = "2026-10-22"


def transfer(payee_id: str, amount: str) -> dict[str, str]:
    return {"payee_id": payee_id, "amount": amount}


def scheduled(amount: str) -> dict[str, str]:
    return {"payee_id": CHEN, "amount": amount, "run_date": RUN_DAY}


def steps(response: httpx.Response) -> list[dict[str, Any]]:
    assert response.status_code == 200, response.text
    found: list[dict[str, Any]] = response.json()["steps"]
    return found


def test_501_to_a_never_paid_payee_is_red_and_says_why(client: TestClient) -> None:
    headers = login(client, "liming")

    [step] = steps(preview_tiers(client, headers, ("transfer", transfer(CHEN, "501.00"))))

    assert step["tier"] == "red"
    assert step["reasons"] == ["first_time_payee"]


def test_500_to_a_never_paid_payee_is_yellow(client: TestClient) -> None:
    headers = login(client, "liming")

    [step] = steps(preview_tiers(client, headers, ("transfer", transfer(CHEN, "500.00"))))

    assert step["tier"] == "yellow"
    assert "reasons" not in step


def test_900_to_a_paid_before_payee_is_yellow(client: TestClient) -> None:
    headers = login(client, "liming")

    [step] = steps(preview_tiers(client, headers, ("transfer", transfer(MUM, "900.00"))))

    assert step["tier"] == "yellow"
    assert "reasons" not in step


def test_only_the_first_of_two_steps_to_the_same_new_payee_is_a_first_payment(
    client: TestClient,
) -> None:
    headers = login(client, "liming")

    found = steps(
        preview_tiers(
            client,
            headers,
            ("transfer", transfer(CHEN, "501.00")),
            ("transfer", transfer(CHEN, "100.00")),
        )
    )

    assert [step["tier"] for step in found] == ["red", "yellow"]
    assert "reasons" not in found[1]


def test_a_small_first_step_makes_a_later_large_step_regular(client: TestClient) -> None:
    headers = login(client, "liming")

    found = steps(
        preview_tiers(
            client,
            headers,
            ("transfer", transfer(CHEN, "400.00")),
            ("transfer", transfer(CHEN, "501.00")),
        )
    )

    assert [step["tier"] for step in found] == ["yellow", "yellow"]


def test_confirmations_count_earlier_steps_the_same_way(client: TestClient) -> None:
    headers = login(client, "liming")

    response = request_confirmations(
        client,
        headers,
        ("transfer", transfer(CHEN, "400.00")),
        ("transfer", transfer(CHEN, "501.00")),
    )

    assert response.status_code == 200, response.text


def test_the_transfer_needs_strong_verification_and_then_the_payee_is_regular(
    client: TestClient,
) -> None:
    headers, phone = login_with_phone(client, "liming")
    input = transfer(CHEN, "501.00")

    refused = request_confirmations(client, headers, ("transfer", input))
    assert refused.json()["refusal"]["reason"] == "needs_verification"
    token = verify(client, headers, phone, "transfer", input)
    sent = call(client, headers, "transfer", input, verification_token=token)
    assert sent.status_code == 200, sent.text
    assert sent.json()["tier"] == "red"

    admin(client, "put", "/admin/clock", json={"now": "2026-10-21T09:00:00+08:00"})
    [again] = steps(preview_tiers(client, headers, ("transfer", transfer(CHEN, "501.00"))))
    assert again["tier"] == "yellow"


def test_a_scheduled_transfer_to_a_new_payee_is_red_when_scheduled(client: TestClient) -> None:
    headers = login(client, "liming")

    [step] = steps(preview_tiers(client, headers, ("schedule_transfer", scheduled("600.00"))))

    assert (step["tier"], step["reasons"]) == ("red", ["first_time_payee"])


def test_a_scheduled_transfer_counts_as_an_earlier_step(client: TestClient) -> None:
    headers = login(client, "liming")

    found = steps(
        preview_tiers(
            client,
            headers,
            ("schedule_transfer", scheduled("600.00")),
            ("transfer", transfer(CHEN, "501.00")),
        )
    )

    assert [step["tier"] for step in found] == ["red", "yellow"]


def test_a_scheduled_transfer_is_tiered_again_when_it_runs(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "liming")
    input = scheduled("600.00")
    token = verify(client, headers, phone, "schedule_transfer", input)
    created = call(client, headers, "schedule_transfer", input, verification_token=token)
    assert created.status_code == 200, created.text
    assert created.json()["tier"] == "red"
    # Before it runs the Customer pays 陈静 a small amount, so she is no longer new.
    small = transfer(CHEN, "100.00")
    paid = call(client, headers, "transfer", small, confirm(client, headers, "transfer", small))
    assert paid.status_code == 200, paid.text
    [again] = steps(preview_tiers(client, headers, ("transfer", transfer(CHEN, "600.00"))))
    assert again["tier"] == "yellow"

    admin(client, "put", "/admin/clock", json={"now": f"{RUN_DAY}T09:00:00+08:00"})

    listing = call(client, headers, "list_scheduled_transfers", {}).json()["output"]
    assert [s["status"] for s in listing["scheduled_transfers"]] == ["executed"]
    assert balance(client, headers, ACCOUNT) == "480.00"
