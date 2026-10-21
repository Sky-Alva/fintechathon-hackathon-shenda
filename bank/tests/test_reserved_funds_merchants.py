"""Smart Transfers, Reserved Funds and Merchants (#13)."""

from typing import Any

import httpx
from fastapi.testclient import TestClient

from conftest import (
    admin,
    call,
    confirm,
    login,
    login_with_phone,
    preview_tiers,
    request_confirmations,
    verify,
)

LIMING_ACCOUNT = "ACC-LM-001"  # ¥1,180.00 at the seed
HANMEI_ACCOUNT = "ACC-HM-001"  # ¥8,650.00 at the seed
HANMEI_SAVINGS = "ACC-HM-002"  # ¥50,000.00 at the seed
MUM = "PAY-LM-002"
CHEN = "PAY-LM-005"
FLOWER_SHOP, CAKE_SHOP = "MER-FLOWER-001", "MER-CAKE-001"
ROSES, SUNFLOWERS = "PRD-FLOWER-001", "PRD-FLOWER-002"  # ¥299, ¥199
CAKE_6, CAKE_8 = "PRD-CAKE-001", "PRD-CAKE-002"  # ¥268, ¥388
BIRTHDAY = "2026-10-26"


def refusal(response: httpx.Response) -> Any:
    return response.json()["refusal"]


def reasons(response: httpx.Response) -> str:
    reason: str = refusal(response)["reason"]
    return reason


def confirmed_call(
    client: TestClient, headers: dict[str, str], operation: str, input: dict[str, Any]
) -> httpx.Response:
    """Call a Yellow Operation the way the Agent does: Confirmation first, then the call."""
    return call(client, headers, operation, input, confirm(client, headers, operation, input))


def accounts(client: TestClient, headers: dict[str, str]) -> dict[str, tuple[str, str]]:
    """Each account's (balance, available balance)."""
    found = call(client, headers, "query_balance").json()["output"]["accounts"]
    return {a["account_id"]: (a["balance"], a["available_balance"]) for a in found}


def reserve(
    client: TestClient,
    headers: dict[str, str],
    amount: str,
    categories: list[str] | None = None,
    purpose: str = "给韩梅梅的生日礼物",
) -> str:
    input: dict[str, Any] = {"amount": amount, "purpose": purpose}
    if categories is not None:
        input["merchant_categories"] = categories
    response = confirmed_call(client, headers, "reserve_funds", input)
    assert response.status_code == 200, response.text
    reservation_id: str = response.json()["output"]["reservation"]["reservation_id"]
    return reservation_id


def order_input(
    reservation_id: str,
    product: str = ROSES,
    amount: str = "299.00",
    merchant: str = FLOWER_SHOP,
    delivery_date: str = BIRTHDAY,
    quantity: int = 1,
) -> dict[str, Any]:
    return {
        "merchant_id": merchant,
        "product_id": product,
        "quantity": quantity,
        "amount": amount,
        "delivery_date": delivery_date,
        "reservation_id": reservation_id,
    }


def order(client: TestClient, headers: dict[str, str], input: dict[str, Any]) -> httpx.Response:
    return confirmed_call(client, headers, "order_from_merchant", input)


def attempt(client: TestClient, headers: dict[str, str], input: dict[str, Any]) -> httpx.Response:
    """An order with no approval, for the cases the Bank refuses before approval matters."""
    return call(client, headers, "order_from_merchant", input)


def reservations(
    client: TestClient, headers: dict[str, str], status: str | None = None
) -> dict[str, dict[str, Any]]:
    input = {} if status is None else {"status": status}
    response = call(client, headers, "list_reserved_funds", input)
    assert response.status_code == 200, response.text
    return {r["reservation_id"]: r for r in response.json()["output"]["reservations"]}


def tier_of(
    client: TestClient, headers: dict[str, str], operation: str, input: dict[str, Any]
) -> str:
    response = preview_tiers(client, headers, (operation, input))
    assert response.status_code == 200, response.text
    tier: str = response.json()["steps"][0]["tier"]
    return tier


# --- Reserve and release: Yellow, and only the available balance changes ----------------


def test_reserving_is_yellow_and_needs_confirmation(client: TestClient) -> None:
    headers = login(client, "liming")
    input = {"amount": "1000.00", "purpose": "给韩梅梅的生日礼物"}

    bare = call(client, headers, "reserve_funds", input)
    response = confirmed_call(client, headers, "reserve_funds", input)

    assert bare.status_code == 403
    assert reasons(bare) == "needs_confirmation"
    assert response.status_code == 200, response.text
    assert response.json()["tier"] == "yellow"
    output = response.json()["output"]
    assert output["reservation"]["reservation_id"] == "RSV-000001"
    reservation = output["reservation"]
    assert (reservation["amount"], reservation["status"]) == ("1000.00", "active")
    assert output["available_balance"] == "180.00"


def test_reserving_changes_the_available_balance_not_the_total(client: TestClient) -> None:
    headers = login(client, "liming")
    assert accounts(client, headers)[LIMING_ACCOUNT] == ("1180.00", "1180.00")

    reserve(client, headers, "1000.00")

    assert accounts(client, headers)[LIMING_ACCOUNT] == ("1180.00", "180.00")


def test_releasing_is_yellow_and_changes_the_available_balance_not_the_total(
    client: TestClient,
) -> None:
    headers = login(client, "liming")
    reservation_id = reserve(client, headers, "1000.00")
    release = {"reservation_id": reservation_id}

    bare = call(client, headers, "release_reserved_funds", release)
    response = confirmed_call(client, headers, "release_reserved_funds", release)

    assert reasons(bare) == "needs_confirmation"
    assert response.status_code == 200, response.text
    assert response.json()["tier"] == "yellow"
    assert response.json()["output"]["released"] == "1000.00"
    assert response.json()["output"]["reservation"]["status"] == "released"
    assert accounts(client, headers)[LIMING_ACCOUNT] == ("1180.00", "1180.00")


def test_only_available_money_can_be_reserved(client: TestClient) -> None:
    headers = login(client, "liming")
    reserve(client, headers, "1000.00")

    too_much = {"amount": "200.00", "purpose": "again"}  # only ¥180 is left to reserve
    response = call(client, headers, "reserve_funds", too_much)
    confirmation = request_confirmations(client, headers, ("reserve_funds", too_much))

    assert reasons(response) == "insufficient_funds"
    assert reasons(confirmation) == "insufficient_funds"
    assert refusal(response)["detail"]["available_balance"] == "180.00"
    assert accounts(client, headers)[LIMING_ACCOUNT] == ("1180.00", "180.00")


def test_a_confirmation_covers_only_the_exact_reservation(client: TestClient) -> None:
    headers = login(client, "liming")
    token = confirm(client, headers, "reserve_funds", {"amount": "1000.00", "purpose": "礼物"})

    response = call(client, headers, "reserve_funds", {"amount": "1100.00", "purpose": "礼物"}, token)

    assert reasons(response) == "token_invalid"
    assert reservations(client, headers) == {}


def test_a_reservation_needs_a_positive_amount_a_purpose_and_a_known_kind_of_merchant(
    client: TestClient,
) -> None:
    headers = login(client, "liming")
    bad_inputs = [
        {"amount": "0.00", "purpose": "礼物"},
        {"amount": "-10.00", "purpose": "礼物"},
        {"amount": "10.005", "purpose": "礼物"},
        {"amount": "100.00", "purpose": ""},
        {"amount": "100.00", "purpose": "礼物", "merchant_categories": ["casino"]},
        {"amount": "100.00", "purpose": "礼物", "customer_id": "CUST-HM"},
    ]

    for input in bad_inputs:
        response = call(client, headers, "reserve_funds", input)
        assert response.status_code == 422, input
        assert reasons(response) == "invalid_input", input


def test_reserved_funds_can_be_listed_with_their_purpose_and_status(client: TestClient) -> None:
    headers = login(client, "liming")
    first = reserve(client, headers, "400.00", ["flowers"], purpose="鲜花")
    second = reserve(client, headers, "300.00", None, purpose="其他")
    confirmed_call(client, headers, "release_reserved_funds", {"reservation_id": second})

    everything = reservations(client, headers)

    assert list(everything) == [first, second]
    assert everything[first]["purpose"] == "鲜花"
    assert everything[first]["merchant_categories"] == ["flowers"]
    assert everything[first]["remaining"] == "400.00"
    assert (everything[second]["status"], everything[second]["remaining"]) == ("released", "0.00")
    assert list(reservations(client, headers, "active")) == [first]


def test_a_customer_sees_and_releases_only_their_own_reservations(client: TestClient) -> None:
    liming = login(client, "liming")
    hanmei = login(client, "hanmei")
    reservation_id = reserve(client, liming, "500.00")

    assert reservations(client, hanmei) == {}
    response = call(client, hanmei, "release_reserved_funds", {"reservation_id": reservation_id})
    assert response.status_code == 403
    assert reasons(response) == "forbidden_other_customer"
    assert accounts(client, hanmei)[HANMEI_ACCOUNT] == ("8650.00", "8650.00")


def test_unknown_or_already_released_reserved_funds_cannot_be_released(
    client: TestClient,
) -> None:
    headers = login(client, "liming")
    reservation_id = reserve(client, headers, "500.00")
    release = {"reservation_id": reservation_id}
    confirmed_call(client, headers, "release_reserved_funds", release)

    again = call(client, headers, "release_reserved_funds", release)
    unknown = call(client, headers, "release_reserved_funds", {"reservation_id": "RSV-999999"})

    assert again.status_code == 422
    assert reasons(again) == "invalid_input"
    assert reasons(unknown) == "not_found"


def test_reserving_leaves_the_other_accounts_alone(client: TestClient) -> None:
    headers = login(client, "hanmei")
    reserve(client, headers, "3000.00")

    found = accounts(client, headers)

    assert found[HANMEI_ACCOUNT] == ("8650.00", "5650.00")
    assert found[HANMEI_SAVINGS] == ("50000.00", "50000.00")


# --- Reserved Funds cannot be spent on anything except their purpose -----------------


def test_reserved_funds_cannot_be_transferred_away(client: TestClient) -> None:
    headers = login(client, "liming")
    reserve(client, headers, "1000.00")
    # The balance is ¥1,180, but ¥1,000 of it is reserved.
    transfer = {"payee_id": MUM, "amount": "500.00"}

    response = call(client, headers, "transfer", transfer)
    confirmation = request_confirmations(client, headers, ("transfer", transfer))

    assert reasons(response) == "insufficient_funds"
    assert reasons(confirmation) == "insufficient_funds"
    assert refusal(response)["detail"]["available_balance"] == "180.00"
    assert accounts(client, headers)[LIMING_ACCOUNT] == ("1180.00", "180.00")


def test_the_available_part_can_still_be_transferred(client: TestClient) -> None:
    headers = login(client, "liming")
    reserve(client, headers, "1000.00")

    response = confirmed_call(client, headers, "transfer", {"payee_id": MUM, "amount": "180.00"})

    assert response.status_code == 200, response.text
    assert accounts(client, headers)[LIMING_ACCOUNT] == ("1000.00", "0.00")


def test_a_safe_account_scam_cannot_reach_reserved_funds(client: TestClient) -> None:
    headers = login(client, "liming")
    reserve(client, headers, "1000.00")
    everything = {"payee_id": CHEN, "amount": "1180.00"}  # "move it all to the safe account"

    response = call(client, headers, "transfer", everything)

    assert reasons(response) == "insufficient_funds"  # refused before it is even Red
    assert accounts(client, headers)[LIMING_ACCOUNT] == ("1180.00", "180.00")


def test_a_scheduled_transfer_cannot_spend_reserved_funds_when_it_runs(
    client: TestClient,
) -> None:
    headers = login(client, "liming")
    schedule = {"payee_id": MUM, "amount": "500.00", "run_date": "2026-10-22"}
    scheduled = confirmed_call(client, headers, "schedule_transfer", schedule)
    scheduled_id = scheduled.json()["output"]["scheduled_transfer"]["scheduled_transfer_id"]
    reserve(client, headers, "1000.00")

    admin(client, "put", "/admin/clock", json={"now": "2026-10-22T09:00:00+08:00"})

    listed = call(client, headers, "list_scheduled_transfers", {}).json()["output"]
    done = next(
        s for s in listed["scheduled_transfers"] if s["scheduled_transfer_id"] == scheduled_id
    )
    assert (done["status"], done["failure_reason"]) == ("failed", "insufficient_funds")
    assert accounts(client, headers)[LIMING_ACCOUNT] == ("1180.00", "180.00")


def test_released_funds_can_be_spent_again(client: TestClient) -> None:
    headers = login(client, "liming")
    reservation_id = reserve(client, headers, "1000.00")
    confirmed_call(client, headers, "release_reserved_funds", {"reservation_id": reservation_id})

    response = confirmed_call(client, headers, "transfer", {"payee_id": MUM, "amount": "500.00"})

    assert response.status_code == 200, response.text


def test_reserved_funds_can_only_pay_a_kind_of_merchant_they_allow(client: TestClient) -> None:
    headers = login(client, "hanmei")
    cake_only = reserve(client, headers, "1000.00", ["cake"], purpose="蛋糕")
    nothing = reserve(client, headers, "1000.00", [], purpose="房租")
    anything = reserve(client, headers, "1000.00", None, purpose="随便")

    for reservation_id, product, merchant, amount, expected in [
        (cake_only, ROSES, FLOWER_SHOP, "299.00", "invalid_input"),
        (nothing, CAKE_6, CAKE_SHOP, "268.00", "invalid_input"),
    ]:
        wrong = order_input(reservation_id, product, amount, merchant)
        response = attempt(client, headers, wrong)
        assert reasons(response) == expected, reservation_id
        assert "allowed_merchant_categories" in refusal(response)["detail"], reservation_id
    right = order(client, headers, order_input(cake_only, CAKE_6, "268.00", CAKE_SHOP))
    anywhere = order(client, headers, order_input(anything))
    assert (right.status_code, anywhere.status_code) == (200, 200)


# --- Merchants ------------------------------------------------------------------------


def test_the_flower_shop_and_the_cake_shop_are_listed_with_their_products(
    client: TestClient,
) -> None:
    headers = login(client, "liming")

    response = call(client, headers, "list_merchants", {})

    assert response.status_code == 200, response.text
    assert response.json()["tier"] == "green"
    merchants = {m["merchant_id"]: m for m in response.json()["output"]["merchants"]}
    assert {mid: m["category"] for mid, m in merchants.items()} == {
        FLOWER_SHOP: "flowers",
        CAKE_SHOP: "cake",
    }
    assert [(p["product_id"], p["price"]) for p in merchants[FLOWER_SHOP]["products"]] == [
        (ROSES, "299.00"),
        (SUNFLOWERS, "199.00"),
    ]
    assert [(p["product_id"], p["price"]) for p in merchants[CAKE_SHOP]["products"]] == [
        (CAKE_6, "268.00"),
        (CAKE_8, "388.00"),
    ]


def test_merchants_can_be_listed_by_kind(client: TestClient) -> None:
    found = call(client, login(client, "liming"), "list_merchants", {"category": "cake"})

    assert [m["merchant_id"] for m in found.json()["output"]["merchants"]] == [CAKE_SHOP]


def test_ordering_flowers_and_a_cake_for_a_delivery_date_works_end_to_end(
    client: TestClient,
) -> None:
    headers = login(client, "liming")
    reservation_id = reserve(client, headers, "1000.00", ["flowers", "cake"])

    flowers = order(client, headers, order_input(reservation_id))
    cake = order(
        client, headers, order_input(reservation_id, CAKE_8, "388.00", CAKE_SHOP, "2026-10-26")
    )

    assert (flowers.status_code, cake.status_code) == (200, 200), flowers.text + cake.text
    assert flowers.json()["tier"] == "yellow"
    placed = flowers.json()["output"]["order"]
    assert placed["order_id"] == "ORD-000001"
    assert (placed["merchant_name"], placed["product_name"]) == (
        "花语鲜花店 Petal Florist",
        "红玫瑰花束 Red rose bouquet",
    )
    assert (placed["delivery_date"], placed["amount"]) == (BIRTHDAY, "299.00")
    assert flowers.json()["output"]["reservation"]["remaining"] == "701.00"
    assert cake.json()["output"]["reservation"]["remaining"] == "313.00"
    # The orders were paid from the reserved ¥1,000, so ¥180 is still available to spend.
    assert accounts(client, headers)[LIMING_ACCOUNT] == ("493.00", "180.00")


def test_listed_orders_show_what_was_ordered_and_when_it_arrives(client: TestClient) -> None:
    headers = login(client, "liming")
    order(client, headers, order_input(reserve(client, headers, "1000.00")))

    listed = call(client, headers, "list_merchant_orders", {}).json()["output"]["orders"]

    assert [(o["order_id"], o["merchant_id"], o["delivery_date"], o["amount"]) for o in listed] == [
        ("ORD-000001", FLOWER_SHOP, BIRTHDAY, "299.00")
    ]
    assert call(client, login(client, "hanmei"), "list_merchant_orders", {}).json()["output"] == {
        "orders": []
    }


def test_an_order_needs_a_confirmation_for_exactly_that_order(client: TestClient) -> None:
    headers = login(client, "liming")
    reservation_id = reserve(client, headers, "1000.00")
    input = order_input(reservation_id)

    bare = call(client, headers, "order_from_merchant", input)
    token = confirm(client, headers, "order_from_merchant", input)
    changed = {**input, "delivery_date": "2026-10-27"}
    later = call(client, headers, "order_from_merchant", changed, token)

    assert reasons(bare) == "needs_confirmation"
    assert reasons(later) == "token_invalid"
    assert call(client, headers, "list_merchant_orders", {}).json()["output"] == {"orders": []}


def test_a_payment_that_stays_within_1000_for_the_day_is_yellow(client: TestClient) -> None:
    headers = login(client, "hanmei")
    reservation_id = reserve(client, headers, "3000.00")

    two_cakes = order_input(reservation_id, CAKE_8, "776.00", CAKE_SHOP, quantity=2)

    assert tier_of(client, headers, "order_from_merchant", two_cakes) == "yellow"


def test_a_merchant_payment_counts_toward_the_daily_transfer_total(client: TestClient) -> None:
    headers = login(client, "hanmei")
    reservation_id = reserve(client, headers, "3000.00")
    order(client, headers, order_input(reservation_id, CAKE_8, "776.00", CAKE_SHOP, quantity=2))

    another = order_input(reservation_id, CAKE_8, "388.00", CAKE_SHOP)  # ¥776 + ¥388 = ¥1,164
    transfer = {"payee_id": "PAY-HM-001", "amount": "300.00"}  # ¥776 + ¥300 = ¥1,076

    assert tier_of(client, headers, "order_from_merchant", another) == "red"
    assert tier_of(client, headers, "transfer", transfer) == "red"
    assert tier_of(client, headers, "transfer", {**transfer, "amount": "224.00"}) == "yellow"


def test_a_merchant_payment_that_takes_the_total_above_1000_is_red(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "hanmei")
    reservation_id = reserve(client, headers, "3000.00")
    big = order_input(reservation_id, CAKE_8, "1164.00", CAKE_SHOP, quantity=3)

    bare = call(client, headers, "order_from_merchant", big)
    confirmation = request_confirmations(client, headers, ("order_from_merchant", big))
    assert reasons(bare) == "needs_verification"
    assert reasons(confirmation) == "needs_verification"

    token = verify(client, headers, phone, "order_from_merchant", big)
    response = call(client, headers, "order_from_merchant", big, verification_token=token)

    assert response.status_code == 200, response.text
    assert response.json()["tier"] == "red"
    assert response.json()["output"]["daily_transfer_total"] == "1164.00"
    assert accounts(client, headers)[HANMEI_ACCOUNT] == ("7486.00", "5650.00")


def test_an_order_cannot_spend_more_than_is_left_in_the_reserved_funds(
    client: TestClient,
) -> None:
    headers = login(client, "hanmei")
    small = reserve(client, headers, "300.00")

    response = attempt(client, headers, order_input(small, CAKE_8, "388.00", CAKE_SHOP))

    assert reasons(response) == "insufficient_funds"
    assert refusal(response)["detail"]["remaining"] == "300.00"
    assert accounts(client, headers)[HANMEI_ACCOUNT] == ("8650.00", "8350.00")


def test_a_reservation_that_is_used_up_closes(client: TestClient) -> None:
    headers = login(client, "liming")
    reservation_id = reserve(client, headers, "199.00")

    order(client, headers, order_input(reservation_id, SUNFLOWERS, "199.00"))

    closed = reservations(client, headers)[reservation_id]
    assert (closed["status"], closed["remaining"], closed["spent"]) == ("used", "0.00", "199.00")
    again = attempt(client, headers, order_input(reservation_id, SUNFLOWERS, "199.00"))
    assert reasons(again) == "invalid_input"


def test_an_order_cannot_use_released_reserved_funds(client: TestClient) -> None:
    headers = login(client, "liming")
    reservation_id = reserve(client, headers, "1000.00")
    confirmed_call(client, headers, "release_reserved_funds", {"reservation_id": reservation_id})

    response = call(client, headers, "order_from_merchant", order_input(reservation_id))

    assert response.status_code == 422
    assert reasons(response) == "invalid_input"


def test_a_customer_cannot_pay_with_the_other_customers_reserved_funds(
    client: TestClient,
) -> None:
    reservation_id = reserve(client, login(client, "liming"), "1000.00")

    hanmei = login(client, "hanmei")

    response = attempt(client, hanmei, order_input(reservation_id))

    assert response.status_code == 403
    assert reasons(response) == "forbidden_other_customer"


def test_the_amount_must_be_the_listed_price_times_the_quantity(client: TestClient) -> None:
    headers = login(client, "hanmei")
    reservation_id = reserve(client, headers, "3000.00")

    for amount, quantity in [("298.00", 1), ("1.00", 1), ("299.00", 2), ("598.00", 1)]:
        wrong = order_input(reservation_id, amount=amount, quantity=quantity)
        response = attempt(client, headers, wrong)
        assert response.status_code == 422, (amount, quantity)
        assert reasons(response) == "invalid_input", (amount, quantity)
        assert refusal(response)["detail"]["price_total"] == f"{299 * quantity}.00"
    right = order(client, headers, order_input(reservation_id, amount="598.00", quantity=2))
    assert right.status_code == 200, right.text


def test_the_delivery_date_cannot_be_in_the_past_or_over_a_year_away(client: TestClient) -> None:
    headers = login(client, "hanmei")
    reservation_id = reserve(client, headers, "3000.00")

    for bad in ["2026-10-19", "2027-10-21"]:
        response = attempt(client, headers, order_input(reservation_id, delivery_date=bad))
        assert response.status_code == 422, bad
        assert reasons(response) == "invalid_input", bad
    today = order(client, headers, order_input(reservation_id, delivery_date="2026-10-20"))
    assert today.status_code == 200, today.text  # same-day delivery is fine


def test_an_order_names_a_real_merchant_and_one_of_its_products(client: TestClient) -> None:
    headers = login(client, "hanmei")
    reservation_id = reserve(client, headers, "3000.00")

    unknowns = [("MER-NOPE", ROSES), (FLOWER_SHOP, "PRD-NOPE"), (FLOWER_SHOP, CAKE_6)]
    for merchant, product in unknowns:
        wrong = order_input(reservation_id, product, "268.00", merchant)
        response = attempt(client, headers, wrong)
        assert response.status_code == 404, (merchant, product)
        assert reasons(response) == "not_found", (merchant, product)


def test_an_order_takes_no_customer_id_or_other_stray_field(client: TestClient) -> None:
    headers = login(client, "liming")
    input = {**order_input(reserve(client, headers, "1000.00")), "customer_id": "CUST-HM"}

    response = call(client, headers, "order_from_merchant", input)

    assert response.status_code == 422
    assert reasons(response) == "invalid_input"


# --- Catalogue and reset ----------------------------------------------------------------


def test_catalogue_declares_reversibility_and_descriptions(client: TestClient) -> None:
    entries = {op["name"]: op for op in client.get("/operations").json()["operations"]}

    names = [
        "reserve_funds",
        "release_reserved_funds",
        "list_reserved_funds",
        "list_merchants",
        "order_from_merchant",
        "list_merchant_orders",
    ]
    for name in names:
        assert entries[name]["description_zh"] and entries[name]["description_en"], name
    assert entries["reserve_funds"]["reversibility"] == {
        "kind": "reversed_by",
        "operation": "release_reserved_funds",
    }
    assert entries["release_reserved_funds"]["reversibility"] == {
        "kind": "reversed_by",
        "operation": "reserve_funds",
    }
    assert entries["order_from_merchant"]["reversibility"] == {"kind": "irreversible"}
    assert entries["order_from_merchant"]["counts_toward_daily_transfer_total"] is True
    assert entries["reserve_funds"]["counts_toward_daily_transfer_total"] is False
    assert entries["order_from_merchant"]["tier"]["kind"] == "rule"
    for name in ["list_reserved_funds", "list_merchants", "list_merchant_orders"]:
        assert entries[name]["tier"] == {"kind": "fixed", "tier": "green"}, name
        assert entries[name]["reversibility"] == {"kind": "read_only"}, name


def test_reset_clears_reservations_and_orders_but_keeps_the_merchants(
    client: TestClient,
) -> None:
    headers = login(client, "liming")
    order(client, headers, order_input(reserve(client, headers, "1000.00")))

    admin(client, "post", "/admin/reset")

    fresh = login(client, "liming")
    assert reservations(client, fresh) == {}
    assert call(client, fresh, "list_merchant_orders", {}).json()["output"] == {"orders": []}
    assert accounts(client, fresh)[LIMING_ACCOUNT] == ("1180.00", "1180.00")
    merchants = call(client, fresh, "list_merchants", {}).json()["output"]["merchants"]
    assert len(merchants) == 2
