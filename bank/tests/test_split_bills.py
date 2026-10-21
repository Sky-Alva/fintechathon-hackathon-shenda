"""Smart Transfers, Split-bill Request (#12): ask several Payees to each pay a share."""

from typing import Any

import httpx
from fastapi.testclient import TestClient

from conftest import (
    admin,
    balance,
    call,
    confirm,
    login,
    preview_tiers,
    request_confirmations,
)

ACCOUNT = "ACC-LM-001"  # 李明's current account, ¥1,180.00 at the seed
WIFE = "PAY-LM-001"  # 韩梅梅
MUM = "PAY-LM-002"  # 王秀英
CHEN = "PAY-LM-005"  # 陈静, never paid


def refusal(response: httpx.Response) -> Any:
    return response.json()["refusal"]


def split(total: str, *shares: tuple[str, str], note: str = "") -> dict[str, Any]:
    return {
        "total": total,
        "shares": [{"payee_id": payee, "amount": amount} for payee, amount in shares],
        "note": note,
    }


def dinner() -> dict[str, Any]:
    return split("300.00", (WIFE, "150.00"), (CHEN, "150.00"), note="水电费")


def send(client: TestClient, headers: dict[str, str], input: dict[str, Any]) -> httpx.Response:
    """Send a request the way the Agent does: Confirmation first, then the call."""
    token = confirm(client, headers, "request_split_bill", input)
    return call(client, headers, "request_split_bill", input, token)


def listed(client: TestClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    response = call(client, headers, "list_split_bill_requests", {})
    assert response.status_code == 200, response.text
    requests: list[dict[str, Any]] = response.json()["output"]["split_bill_requests"]
    return requests


def tier_of(client: TestClient, headers: dict[str, str], operation: str, input: Any) -> str:
    response = preview_tiers(client, headers, (operation, input))
    assert response.status_code == 200, response.text
    tier: str = response.json()["steps"][0]["tier"]
    return tier


# --- Sending a request ----------------------------------------------------------------


def test_a_confirmed_split_bill_request_is_created(client: TestClient) -> None:
    headers = login(client, "liming")

    response = send(client, headers, dinner())

    assert response.status_code == 200, response.text
    assert response.json()["tier"] == "yellow"
    created = response.json()["output"]["split_bill_request"]
    assert (created["split_bill_id"], created["total"], created["note"]) == (
        "SPL-000001",
        "300.00",
        "水电费",
    )
    assert [(s["payee_id"], s["payee_name"], s["amount"]) for s in created["shares"]] == [
        (WIFE, "韩梅梅", "150.00"),
        (CHEN, "陈静", "150.00"),
    ]


def test_without_a_confirmation_it_is_refused_with_needs_confirmation(
    client: TestClient,
) -> None:
    headers = login(client, "liming")

    response = call(client, headers, "request_split_bill", dinner())

    assert response.status_code == 403
    assert refusal(response)["reason"] == "needs_confirmation"
    assert listed(client, headers) == []


def test_a_confirmation_covers_only_the_exact_request(client: TestClient) -> None:
    headers = login(client, "liming")
    token = confirm(client, headers, "request_split_bill", dinner())
    # Still adds up to ¥300, but asks 陈静 for more than the Customer confirmed.
    shifted = split("300.00", (WIFE, "100.00"), (CHEN, "200.00"), note="水电费")

    response = call(client, headers, "request_split_bill", shifted, token)

    assert refusal(response)["reason"] == "token_invalid"
    assert listed(client, headers) == []


def test_a_request_moves_no_money_and_leaves_the_daily_transfer_total_alone(
    client: TestClient,
) -> None:
    headers = login(client, "liming")

    send(client, headers, split("2400.00", (WIFE, "800.00"), (CHEN, "800.00"), (MUM, "800.00")))

    assert balance(client, headers, ACCOUNT) == "1180.00"
    # Today's Daily Transfer Total is still ¥0: ¥1,000 is Yellow, one cent more is Red.
    assert tier_of(client, headers, "transfer", {"payee_id": MUM, "amount": "1000.00"}) == "yellow"
    assert tier_of(client, headers, "transfer", {"payee_id": MUM, "amount": "1000.01"}) == "red"


def test_a_request_is_yellow_however_large_the_total(client: TestClient) -> None:
    headers = login(client, "liming")
    huge = split("50000.00", (WIFE, "25000.00"), (CHEN, "25000.00"))

    assert tier_of(client, headers, "request_split_bill", huge) == "yellow"
    response = send(client, headers, huge)
    assert response.status_code == 200, response.text
    assert balance(client, headers, ACCOUNT) == "1180.00"


def test_a_request_can_ask_three_or_more_payees(client: TestClient) -> None:
    headers = login(client, "liming")

    three = split("90.00", (WIFE, "30.00"), (CHEN, "30.00"), (MUM, "30.00"))

    response = send(client, headers, three)

    assert response.status_code == 200, response.text
    assert len(response.json()["output"]["split_bill_request"]["shares"]) == 3


def test_a_request_can_leave_the_note_out(client: TestClient) -> None:
    headers = login(client, "liming")
    input = {
        "total": "60.00",
        "shares": [{"payee_id": WIFE, "amount": "30.00"}, {"payee_id": CHEN, "amount": "30.00"}],
    }

    response = send(client, headers, input)

    assert response.status_code == 200, response.text
    assert response.json()["output"]["split_bill_request"]["note"] == ""


# --- Shares that don't make sense -------------------------------------------------------


def test_shares_that_do_not_add_up_to_the_total_are_refused_with_invalid_input(
    client: TestClient,
) -> None:
    headers = login(client, "liming")
    short = split("300.00", (WIFE, "100.00"), (CHEN, "100.00"))

    response = call(client, headers, "request_split_bill", short)

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"
    assert refusal(response)["detail"] == {"total": "300.00", "shares_total": "200.00"}


def test_it_is_refused_before_the_customer_is_asked_to_confirm(client: TestClient) -> None:
    headers = login(client, "liming")
    short = split("300.00", (WIFE, "100.00"), (CHEN, "100.00"))

    response = request_confirmations(client, headers, ("request_split_bill", short))

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"
    assert listed(client, headers) == []


def test_one_cent_too_much_or_too_little_is_refused(client: TestClient) -> None:
    headers = login(client, "liming")

    for second_share in ["149.99", "150.01"]:
        uneven = split("300.00", (WIFE, "150.00"), (CHEN, second_share))
        response = call(client, headers, "request_split_bill", uneven)
        assert refusal(response)["reason"] == "invalid_input", second_share

    assert listed(client, headers) == []


def test_a_payee_cannot_be_given_two_shares(client: TestClient) -> None:
    headers = login(client, "liming")

    twice = split("300.00", (WIFE, "150.00"), (WIFE, "150.00"))

    response = call(client, headers, "request_split_bill", twice)

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"
    assert refusal(response)["detail"] == {"duplicate_payee_ids": [WIFE]}


def test_a_split_needs_at_least_two_payees(client: TestClient) -> None:
    headers = login(client, "liming")

    for shares in [[], [{"payee_id": WIFE, "amount": "300.00"}]]:
        input = {"total": "300.00", "shares": shares}
        response = call(client, headers, "request_split_bill", input)
        assert response.status_code == 422, shares
        assert refusal(response)["reason"] == "invalid_input", shares


def test_a_share_must_be_a_positive_amount_in_whole_cents(client: TestClient) -> None:
    headers = login(client, "liming")

    for bad in ["0.00", "-50.00", "49.999"]:
        input = split("100.00", (WIFE, bad), (CHEN, "100.00"))
        response = call(client, headers, "request_split_bill", input)
        assert response.status_code == 422, bad
        assert refusal(response)["reason"] == "invalid_input", bad


def test_a_note_longer_than_100_characters_is_refused(client: TestClient) -> None:
    headers = login(client, "liming")

    response = call(client, headers, "request_split_bill", {**dinner(), "note": "x" * 101})

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"


def test_a_request_to_an_unknown_payee_is_refused(client: TestClient) -> None:
    headers = login(client, "liming")

    unknown = split("300.00", (WIFE, "150.00"), ("PAY-NOPE", "150.00"))

    response = call(client, headers, "request_split_bill", unknown)

    assert response.status_code == 404
    assert refusal(response)["reason"] == "not_found"
    assert listed(client, headers) == []


def test_a_customer_cannot_ask_the_other_customers_payees(client: TestClient) -> None:
    headers = login(client, "liming")
    # PAY-HM-001 (李明 as 韩梅梅's Payee) belongs to the other Customer.
    input = split("300.00", (WIFE, "150.00"), ("PAY-HM-001", "150.00"))

    response = call(client, headers, "request_split_bill", input)

    assert response.status_code == 404
    assert refusal(response)["reason"] == "not_found"


def test_a_request_takes_no_customer_id_or_other_stray_field(client: TestClient) -> None:
    headers = login(client, "liming")
    stray_top = {**dinner(), "customer_id": "CUST-HM"}
    stray_share = dinner()
    stray_share["shares"][0]["from_account_id"] = "ACC-HM-001"

    for input in [stray_top, stray_share]:
        response = call(client, headers, "request_split_bill", input)
        assert response.status_code == 422
        assert refusal(response)["reason"] == "invalid_input"


# --- Listing ----------------------------------------------------------------------------


def test_a_customer_lists_their_requests_in_order_with_shares_as_sent(
    client: TestClient,
) -> None:
    headers = login(client, "liming")
    send(client, headers, dinner())
    send(client, headers, split("90.00", (MUM, "60.00"), (WIFE, "30.00")))

    found = listed(client, headers)

    assert [r["split_bill_id"] for r in found] == ["SPL-000001", "SPL-000002"]
    assert [s["payee_id"] for s in found[1]["shares"]] == [MUM, WIFE]


def test_a_customer_sees_only_their_own_requests(client: TestClient) -> None:
    send(client, login(client, "liming"), dinner())

    assert listed(client, login(client, "hanmei")) == []


# --- Catalogue and reset -----------------------------------------------------------------


def test_catalogue_declares_the_split_bill_operations(client: TestClient) -> None:
    entries = {op["name"]: op for op in client.get("/operations").json()["operations"]}

    for name in ["request_split_bill", "list_split_bill_requests"]:
        assert entries[name]["description_zh"] and entries[name]["description_en"], name
    assert entries["request_split_bill"]["tier"]["kind"] == "rule"
    assert "Yellow" in entries["request_split_bill"]["tier"]["description_en"]
    assert entries["request_split_bill"]["reversibility"] == {"kind": "irreversible"}
    assert entries["request_split_bill"]["counts_toward_daily_transfer_total"] is False
    assert entries["list_split_bill_requests"]["tier"] == {"kind": "fixed", "tier": "green"}
    assert entries["list_split_bill_requests"]["reversibility"] == {"kind": "read_only"}


def test_reset_clears_split_bill_requests(client: TestClient) -> None:
    send(client, login(client, "liming"), dinner())

    admin(client, "post", "/admin/reset")

    assert listed(client, login(client, "liming")) == []
