"""Card Management (#14): list cards, virtual card, lower Card Limit, add Card Lock."""

from typing import Any

import httpx
from fastapi.testclient import TestClient

from conftest import admin, call, confirm, login, login_session, request_confirmations

LM_DEBIT = "CARD-LM-001"
LM_LOCKED_DEBIT = "CARD-LM-002"  # seeded with an overseas Card Lock
LM_CREDIT = "CARD-LM-003"
HM_DEBIT = "CARD-HM-001"  # 韩梅梅's card


def refusal(response: httpx.Response) -> Any:
    return response.json()["refusal"]


def cards(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    """The Customer's cards by card_id, as the Agent would see them."""
    response = call(client, headers, "list_cards")
    assert response.status_code == 200, response.text
    return {c["card_id"]: c for c in response.json()["output"]["cards"]}


def confirmed_call(
    client: TestClient, headers: dict[str, str], operation: str, input: dict[str, Any]
) -> httpx.Response:
    return call(client, headers, operation, input, confirm(client, headers, operation, input))


# --- Listing cards (Green) ----------------------------------------------------------


def test_listing_cards_runs_without_approval_and_is_green(client: TestClient) -> None:
    response = call(client, login(client, "liming"), "list_cards")

    assert response.status_code == 200, response.text
    assert response.json()["tier"] == "green"


def test_customer_has_two_debit_cards_and_a_credit_card_with_status_and_limits(
    client: TestClient,
) -> None:
    found = cards(client, login(client, "liming"))

    assert sorted(found) == [LM_DEBIT, LM_LOCKED_DEBIT, LM_CREDIT]
    assert [found[c]["kind"] for c in sorted(found)] == ["debit", "debit", "credit"]
    assert all(c["status"] == "active" for c in found.values())
    assert found[LM_DEBIT]["card_limit"] == "5000.00"
    assert found[LM_DEBIT]["limit_period"] == "daily"
    assert found[LM_CREDIT]["card_limit"] == "20000.00"
    assert found[LM_CREDIT]["limit_period"] == "monthly"


def test_one_card_is_already_under_a_card_lock(client: TestClient) -> None:
    found = cards(client, login(client, "liming"))

    assert found[LM_LOCKED_DEBIT]["locks"] == ["overseas"]
    assert found[LM_DEBIT]["locks"] == []
    assert found[LM_CREDIT]["locks"] == []


def test_listing_shows_only_the_last_four_digits_of_each_card(client: TestClient) -> None:
    found = cards(client, login(client, "liming"))

    assert found[LM_DEBIT]["masked_number"] == "**** **** **** 1234"
    assert all(c["masked_number"].startswith("**** **** **** ") for c in found.values())


def test_listing_shows_only_the_session_customers_cards(client: TestClient) -> None:
    liming = cards(client, login(client, "liming"))
    hanmei = cards(client, login(client, "hanmei"))

    assert HM_DEBIT not in liming
    assert list(hanmei) == [HM_DEBIT]


def test_listing_refuses_a_customer_id_instead_of_listing_someone_elses_cards(
    client: TestClient,
) -> None:
    response = call(client, login(client, "liming"), "list_cards", {"customer_id": "CUST-HM"})

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"


# --- Virtual card (Yellow) ----------------------------------------------------------


def test_virtual_card_application_needs_confirmation_and_creates_nothing(
    client: TestClient,
) -> None:
    headers = login(client, "liming")

    response = call(client, headers, "apply_virtual_card", {})

    assert response.status_code == 403
    assert refusal(response)["reason"] == "needs_confirmation"
    assert refusal(response)["detail"] == {"operation": "apply_virtual_card", "tier": "yellow"}
    assert len(cards(client, headers)) == 3


def test_confirmed_virtual_card_application_issues_a_small_limit_virtual_card(
    client: TestClient,
) -> None:
    headers = login(client, "liming")

    response = confirmed_call(client, headers, "apply_virtual_card", {"name": "网购专用"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tier"] == "yellow"
    card = body["output"]["card"]
    assert card["kind"] == "virtual"
    assert card["name"] == "网购专用"
    assert card["status"] == "active"
    assert card["card_limit"] == "1000.00"
    assert card["locks"] == []
    assert card["linked_account_id"] == "ACC-LM-001"
    assert cards(client, headers)[card["card_id"]]["kind"] == "virtual"


def test_virtual_card_cannot_be_applied_for_with_a_bigger_limit(client: TestClient) -> None:
    # Raising a Card Limit is Red, so the application must not take a limit at all.
    headers = login(client, "liming")

    response = call(client, headers, "apply_virtual_card", {"card_limit": "50000.00"})

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"
    assert len(cards(client, headers)) == 3


def test_virtual_card_belongs_to_the_applicant_only(client: TestClient) -> None:
    liming = login(client, "liming")
    confirmed_call(client, liming, "apply_virtual_card", {})

    assert len(cards(client, liming)) == 4
    assert len(cards(client, login(client, "hanmei"))) == 1


def test_a_customer_can_hold_at_most_three_virtual_cards(client: TestClient) -> None:
    headers = login(client, "liming")
    for _ in range(3):
        assert confirmed_call(client, headers, "apply_virtual_card", {}).status_code == 200

    # Refused up front, so the Customer is never asked to confirm it.
    response = request_confirmations(client, headers, ("apply_virtual_card", {}))

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"
    assert len(cards(client, headers)) == 6


def test_virtual_card_confirmation_cannot_be_reused(client: TestClient) -> None:
    headers = login(client, "liming")
    token = confirm(client, headers, "apply_virtual_card", {})
    assert call(client, headers, "apply_virtual_card", {}, token).status_code == 200

    response = call(client, headers, "apply_virtual_card", {}, token)

    assert response.status_code == 403
    assert refusal(response)["reason"] == "token_invalid"
    assert len(cards(client, headers)) == 4


def test_virtual_card_confirmation_for_another_name_is_refused(client: TestClient) -> None:
    headers = login(client, "liming")
    token = confirm(client, headers, "apply_virtual_card", {"name": "旅行"})

    response = call(client, headers, "apply_virtual_card", {"name": "别的名字"}, token)

    assert response.status_code == 403
    assert refusal(response)["reason"] == "token_invalid"
    assert len(cards(client, headers)) == 3


# --- Lowering a Card Limit (Yellow) -------------------------------------------------


def test_lowering_a_limit_needs_confirmation_and_changes_nothing(client: TestClient) -> None:
    headers = login(client, "liming")

    response = call(
        client, headers, "lower_card_limit", {"card_id": LM_DEBIT, "new_limit": "2000.00"}
    )

    assert response.status_code == 403
    assert refusal(response)["reason"] == "needs_confirmation"
    assert cards(client, headers)[LM_DEBIT]["card_limit"] == "5000.00"


def test_confirmed_lower_limit_takes_effect_and_reports_the_previous_limit(
    client: TestClient,
) -> None:
    headers = login(client, "liming")

    response = confirmed_call(
        client, headers, "lower_card_limit", {"card_id": LM_DEBIT, "new_limit": "2000"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tier"] == "yellow"
    assert body["output"] == {
        "card_id": LM_DEBIT,
        "previous_limit": "5000.00",
        "card_limit": "2000.00",
        "limit_period": "daily",
    }
    assert cards(client, headers)[LM_DEBIT]["card_limit"] == "2000.00"
    assert cards(client, headers)[LM_CREDIT]["card_limit"] == "20000.00"  # other cards untouched


def test_raising_a_limit_through_the_lower_limit_operation_is_refused_with_invalid_input(
    client: TestClient,
) -> None:
    headers = login(client, "liming")

    response = call(
        client, headers, "lower_card_limit", {"card_id": LM_DEBIT, "new_limit": "9000.00"}
    )

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"
    assert cards(client, headers)[LM_DEBIT]["card_limit"] == "5000.00"


def test_raise_is_refused_even_with_a_confirmation_for_a_lower_limit(client: TestClient) -> None:
    headers = login(client, "liming")
    token = confirm(
        client, headers, "lower_card_limit", {"card_id": LM_DEBIT, "new_limit": "2000.00"}
    )

    response = call(
        client,
        headers,
        "lower_card_limit",
        {"card_id": LM_DEBIT, "new_limit": "9000.00"},
        token,
    )

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"
    assert cards(client, headers)[LM_DEBIT]["card_limit"] == "5000.00"


def test_an_unchanged_limit_is_not_a_lower_limit(client: TestClient) -> None:
    headers = login(client, "liming")

    response = call(
        client, headers, "lower_card_limit", {"card_id": LM_DEBIT, "new_limit": "5000.00"}
    )

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"


def test_a_raise_is_refused_when_asking_for_confirmation_so_nobody_confirms_it(
    client: TestClient,
) -> None:
    headers = login(client, "liming")

    response = request_confirmations(
        client, headers, ("lower_card_limit", {"card_id": LM_DEBIT, "new_limit": "9000.00"})
    )

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"
    assert refusal(response)["detail"]["index"] == 0


def test_lowering_the_limit_of_someone_elses_card_is_refused(client: TestClient) -> None:
    headers = login(client, "liming")

    response = call(
        client, headers, "lower_card_limit", {"card_id": HM_DEBIT, "new_limit": "100.00"}
    )

    assert response.status_code == 403
    assert refusal(response)["reason"] == "forbidden_other_customer"
    assert cards(client, login(client, "hanmei"))[HM_DEBIT]["card_limit"] == "5000.00"


def test_lowering_the_limit_of_an_unknown_card_is_not_found(client: TestClient) -> None:
    response = call(
        client,
        login(client, "liming"),
        "lower_card_limit",
        {"card_id": "CARD-NOPE", "new_limit": "100.00"},
    )

    assert response.status_code == 404
    assert refusal(response)["reason"] == "not_found"


def test_a_limit_must_be_a_positive_amount(client: TestClient) -> None:
    headers = login(client, "liming")

    for bad in ("0", "-5.00", "abc"):
        response = call(
            client, headers, "lower_card_limit", {"card_id": LM_DEBIT, "new_limit": bad}
        )
        assert response.status_code == 422, bad
        assert refusal(response)["reason"] == "invalid_input"


# --- Card Lock (Yellow) -------------------------------------------------------------


def test_adding_a_lock_needs_confirmation_and_changes_nothing(client: TestClient) -> None:
    headers = login(client, "liming")

    response = call(client, headers, "add_card_lock", {"card_id": LM_DEBIT, "scope": "online"})

    assert response.status_code == 403
    assert refusal(response)["reason"] == "needs_confirmation"
    assert cards(client, headers)[LM_DEBIT]["locks"] == []


def test_confirmed_card_lock_blocks_overseas_online_or_all_transactions(
    client: TestClient,
) -> None:
    headers = login(client, "liming")

    for scope in ("overseas", "online"):
        response = confirmed_call(
            client, headers, "add_card_lock", {"card_id": LM_CREDIT, "scope": scope}
        )
        assert response.status_code == 200, response.text
        assert response.json()["tier"] == "yellow"
        assert response.json()["output"]["scope"] == scope

    assert cards(client, headers)[LM_CREDIT]["locks"] == ["overseas", "online"]
    response = confirmed_call(client, headers, "add_card_lock", {"card_id": LM_DEBIT, "scope": "all"})
    assert response.status_code == 200, response.text
    assert response.json()["output"] == {"card_id": LM_DEBIT, "scope": "all", "locks": ["all"]}


def test_a_lock_on_one_card_does_not_touch_the_others(client: TestClient) -> None:
    headers = login(client, "liming")

    confirmed_call(client, headers, "add_card_lock", {"card_id": LM_DEBIT, "scope": "online"})

    found = cards(client, headers)
    assert found[LM_DEBIT]["locks"] == ["online"]
    assert found[LM_LOCKED_DEBIT]["locks"] == ["overseas"]
    assert found[LM_CREDIT]["locks"] == []


def test_a_card_cannot_be_locked_twice_for_the_same_scope(client: TestClient) -> None:
    headers = login(client, "liming")

    # The seeded card is already locked overseas. Refused up front: nothing to confirm.
    response = request_confirmations(
        client, headers, ("add_card_lock", {"card_id": LM_LOCKED_DEBIT, "scope": "overseas"})
    )

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"
    assert cards(client, headers)[LM_LOCKED_DEBIT]["locks"] == ["overseas"]


def test_a_scope_already_covered_by_an_all_lock_is_refused(client: TestClient) -> None:
    headers = login(client, "liming")
    confirmed_call(client, headers, "add_card_lock", {"card_id": LM_DEBIT, "scope": "all"})

    response = call(client, headers, "add_card_lock", {"card_id": LM_DEBIT, "scope": "online"})

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"


def test_a_wider_lock_can_be_added_on_top_of_a_narrower_one(client: TestClient) -> None:
    headers = login(client, "liming")

    response = confirmed_call(
        client, headers, "add_card_lock", {"card_id": LM_LOCKED_DEBIT, "scope": "all"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["output"]["locks"] == ["overseas", "all"]


def test_an_unknown_lock_scope_is_refused(client: TestClient) -> None:
    response = call(
        client, login(client, "liming"), "add_card_lock", {"card_id": LM_DEBIT, "scope": "atm"}
    )

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"


def test_locking_someone_elses_card_is_refused(client: TestClient) -> None:
    headers = login(client, "liming")

    response = call(client, headers, "add_card_lock", {"card_id": HM_DEBIT, "scope": "all"})

    assert response.status_code == 403
    assert refusal(response)["reason"] == "forbidden_other_customer"
    assert cards(client, login(client, "hanmei"))[HM_DEBIT]["locks"] == []


def test_a_confirmation_for_one_card_does_not_cover_another(client: TestClient) -> None:
    headers = login(client, "liming")
    token = confirm(client, headers, "add_card_lock", {"card_id": LM_DEBIT, "scope": "online"})

    response = call(
        client, headers, "add_card_lock", {"card_id": LM_CREDIT, "scope": "online"}, token
    )

    assert response.status_code == 403
    assert refusal(response)["reason"] == "token_invalid"
    assert cards(client, headers)[LM_CREDIT]["locks"] == []


def test_one_plan_card_confirms_a_lock_and_a_lower_limit_together(client: TestClient) -> None:
    headers = login(client, "liming")
    plan = [
        ("add_card_lock", {"card_id": LM_DEBIT, "scope": "overseas"}),
        ("lower_card_limit", {"card_id": LM_DEBIT, "new_limit": "1000.00"}),
        ("apply_virtual_card", {}),
    ]

    response = request_confirmations(client, headers, *plan)

    assert response.status_code == 200, response.text
    issued = response.json()["confirmations"]
    assert [i["tier"] for i in issued] == ["yellow", "yellow", "yellow"]
    for (operation, input), item in zip(plan, issued, strict=True):
        result = call(client, headers, operation, input, item["confirmation_token"])
        assert result.status_code == 200, result.text
    found = cards(client, headers)
    assert found[LM_DEBIT]["locks"] == ["overseas"]
    assert found[LM_DEBIT]["card_limit"] == "1000.00"
    assert len(found) == 4


# --- Reversibility and the catalogue ------------------------------------------------


def test_catalogue_declares_tiers_and_reversibility_of_the_card_operations(
    client: TestClient,
) -> None:
    entries = {op["name"]: op for op in client.get("/operations").json()["operations"]}

    assert entries["list_cards"]["tier"] == {"kind": "fixed", "tier": "green"}
    assert entries["list_cards"]["reversibility"] == {"kind": "read_only"}
    for name in ("apply_virtual_card", "lower_card_limit", "add_card_lock"):
        assert entries[name]["description_zh"] and entries[name]["description_en"]
        assert entries[name]["tier"]["kind"] == "rule"
        assert "Yellow" in entries[name]["tier"]["description_en"]
    assert entries["apply_virtual_card"]["reversibility"] == {"kind": "irreversible"}
    assert entries["lower_card_limit"]["reversibility"] == {
        "kind": "reversed_by",
        "operation": "raise_card_limit",
    }
    assert entries["add_card_lock"]["reversibility"] == {
        "kind": "reversed_by",
        "operation": "lift_card_lock",
    }


# --- Audit and reset ----------------------------------------------------------------


def test_card_operations_are_audited_with_their_tier_and_outcome(client: TestClient) -> None:
    session_id, headers = login_session(client, "liming")
    call(client, headers, "list_cards")
    call(client, headers, "add_card_lock", {"card_id": LM_DEBIT, "scope": "all"})
    confirmed_call(client, headers, "add_card_lock", {"card_id": LM_DEBIT, "scope": "all"})

    entries = admin(client, "get", f"/admin/sessions/{session_id}/audit")["entries"]

    operations = [e for e in entries if e["event"] == "operation"]
    assert [(e["operation"], e["tier"], e["outcome"], e["reason"]) for e in operations] == [
        ("list_cards", "green", "ok", None),
        ("add_card_lock", "yellow", "refused", "needs_confirmation"),
        ("add_card_lock", "yellow", "ok", None),
    ]


def test_reset_restores_the_seeded_cards_and_locks(client: TestClient) -> None:
    headers = login(client, "liming")
    confirmed_call(client, headers, "add_card_lock", {"card_id": LM_DEBIT, "scope": "all"})
    confirmed_call(
        client, headers, "lower_card_limit", {"card_id": LM_CREDIT, "new_limit": "100.00"}
    )
    confirmed_call(client, headers, "apply_virtual_card", {})

    admin(client, "post", "/admin/reset")

    found = cards(client, login(client, "liming"))
    assert sorted(found) == [LM_DEBIT, LM_LOCKED_DEBIT, LM_CREDIT]
    assert found[LM_DEBIT]["locks"] == []
    assert found[LM_CREDIT]["card_limit"] == "20000.00"
    assert found[LM_LOCKED_DEBIT]["locks"] == ["overseas"]
