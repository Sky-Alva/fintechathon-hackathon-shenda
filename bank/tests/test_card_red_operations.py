"""Red card Operations (#15): raise Card Limit, lift Card Lock, Loss Report, withdraw it.

Every one needs Strong Verification, enforced by the Bank whatever the Agent says.
"""

from typing import Any

import httpx
from fastapi.testclient import TestClient

from conftest import (
    admin,
    call,
    confirm,
    login,
    login_session_with_phone,
    login_with_phone,
    request_confirmations,
    request_verification,
    submit_code,
    verify,
)

LM_DEBIT = "CARD-LM-001"
LM_LOCKED_DEBIT = "CARD-LM-002"  # seeded with an overseas Card Lock
LM_CREDIT = "CARD-LM-003"
HM_DEBIT = "CARD-HM-001"


def refusal(response: httpx.Response) -> Any:
    return response.json()["refusal"]


def cards(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = call(client, headers, "list_cards")
    assert response.status_code == 200, response.text
    return {c["card_id"]: c for c in response.json()["output"]["cards"]}


RAISE = {"card_id": LM_DEBIT, "new_limit": "8000.00"}
LIFT = {"card_id": LM_LOCKED_DEBIT, "scope": "overseas"}
LOSS = {"card_id": LM_CREDIT}


# --- Each Operation is Red and refused without Strong Verification -------------------


def test_each_red_card_operation_is_refused_without_a_verification_token(
    client: TestClient,
) -> None:
    headers = login(client, "liming")
    for operation, input in [
        ("raise_card_limit", RAISE),
        ("lift_card_lock", LIFT),
        ("file_loss_report", LOSS),
    ]:
        response = call(client, headers, operation, input)

        assert response.status_code == 403, operation
        assert refusal(response)["reason"] == "needs_verification", operation
        assert refusal(response)["detail"] == {"operation": operation, "tier": "red"}


def test_withdrawing_a_loss_report_is_refused_without_a_verification_token(
    client: TestClient,
) -> None:
    headers, phone = login_with_phone(client, "liming")
    call(
        client,
        headers,
        "file_loss_report",
        LOSS,
        verification_token=verify(client, headers, phone, "file_loss_report", LOSS),
    )

    response = call(client, headers, "withdraw_loss_report", LOSS)

    assert response.status_code == 403
    assert refusal(response)["reason"] == "needs_verification"
    assert cards(client, headers)[LM_CREDIT]["status"] == "disabled"


def test_a_refused_red_operation_changes_nothing(client: TestClient) -> None:
    headers = login(client, "liming")
    for operation, input in [
        ("raise_card_limit", RAISE),
        ("lift_card_lock", LIFT),
        ("file_loss_report", LOSS),
    ]:
        call(client, headers, operation, input)

    found = cards(client, headers)
    assert found[LM_DEBIT]["card_limit"] == "5000.00"
    assert found[LM_LOCKED_DEBIT]["locks"] == ["overseas"]
    assert found[LM_CREDIT]["status"] == "active"


def test_a_confirmation_token_is_not_enough_for_a_red_operation(client: TestClient) -> None:
    # The Customer's own Yellow Confirmation (e.g. for lowering the limit) must not be
    # usable to raise it.
    headers = login(client, "liming")
    token = confirm(
        client, headers, "lower_card_limit", {"card_id": LM_DEBIT, "new_limit": "1000.00"}
    )

    response = call(client, headers, "raise_card_limit", RAISE, confirmation_token=token)

    assert response.status_code == 403
    assert refusal(response)["reason"] == "needs_verification"
    assert cards(client, headers)[LM_DEBIT]["card_limit"] == "5000.00"


def test_red_operations_cannot_be_put_on_a_confirmation_plan_card(client: TestClient) -> None:
    headers = login(client, "liming")

    response = request_confirmations(client, headers, ("lift_card_lock", LIFT))

    assert response.status_code == 403
    assert refusal(response)["reason"] == "needs_verification"
    assert refusal(response)["detail"]["index"] == 0


# --- Raise a Card Limit -------------------------------------------------------------


def test_raising_a_limit_works_after_strong_verification(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "liming")
    token = verify(client, headers, phone, "raise_card_limit", RAISE)

    response = call(client, headers, "raise_card_limit", RAISE, verification_token=token)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tier"] == "red"
    assert body["output"] == {
        "card_id": LM_DEBIT,
        "previous_limit": "5000.00",
        "card_limit": "8000.00",
        "limit_period": "daily",
    }
    assert cards(client, headers)[LM_DEBIT]["card_limit"] == "8000.00"


def test_the_verification_for_one_raise_does_not_cover_a_bigger_one(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "liming")
    token = verify(client, headers, phone, "raise_card_limit", RAISE)

    response = call(
        client,
        headers,
        "raise_card_limit",
        {"card_id": LM_DEBIT, "new_limit": "90000.00"},
        verification_token=token,
    )

    assert response.status_code == 403
    assert refusal(response)["reason"] == "token_invalid"
    assert cards(client, headers)[LM_DEBIT]["card_limit"] == "5000.00"


def test_the_verification_for_one_card_does_not_cover_another(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "liming")
    token = verify(client, headers, phone, "raise_card_limit", RAISE)

    response = call(
        client,
        headers,
        "raise_card_limit",
        {"card_id": LM_CREDIT, "new_limit": "8000000.00"},
        verification_token=token,
    )

    assert response.status_code == 403
    assert refusal(response)["reason"] == "token_invalid"


def test_a_verification_token_works_once(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "liming")
    token = verify(client, headers, phone, "raise_card_limit", RAISE)
    first = call(client, headers, "raise_card_limit", RAISE, verification_token=token)
    assert first.status_code == 200
    # Lower the limit again so the very same raise is valid once more. Only the used
    # token can now be the reason for a refusal.
    lower = {"card_id": LM_DEBIT, "new_limit": "1000.00"}
    lowered = call(client, headers, "lower_card_limit", lower, confirm(
        client, headers, "lower_card_limit", lower))
    assert lowered.status_code == 200

    response = call(client, headers, "raise_card_limit", RAISE, verification_token=token)

    assert response.status_code == 403
    assert refusal(response)["reason"] == "token_invalid"
    assert cards(client, headers)[LM_DEBIT]["card_limit"] == "1000.00"


def test_a_raise_that_is_not_higher_is_refused_before_a_code_is_sent(
    client: TestClient,
) -> None:
    headers, phone = login_with_phone(client, "liming")

    for new_limit in ("5000.00", "100.00"):
        response = request_verification(
            client, headers, "raise_card_limit", {"card_id": LM_DEBIT, "new_limit": new_limit}
        )
        assert response.status_code == 422, new_limit
        assert refusal(response)["reason"] == "invalid_input"

    assert client.get("/phone/messages", headers=phone).json()["messages"] == []


def test_a_wrong_code_does_not_raise_the_limit(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "liming")
    challenge = request_verification(client, headers, "raise_card_limit", RAISE).json()

    response = submit_code(client, phone, challenge["challenge_id"], "000000")

    assert response.status_code == 403
    assert refusal(response)["reason"] == "token_invalid"
    assert cards(client, headers)[LM_DEBIT]["card_limit"] == "5000.00"


def test_raising_the_limit_of_someone_elses_card_is_refused(client: TestClient) -> None:
    headers = login(client, "liming")

    response = call(
        client, headers, "raise_card_limit", {"card_id": HM_DEBIT, "new_limit": "90000.00"}
    )

    assert response.status_code == 403
    assert refusal(response)["reason"] == "forbidden_other_customer"


def test_no_verification_code_is_sent_for_someone_elses_card(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "liming")

    response = request_verification(
        client, headers, "raise_card_limit", {"card_id": HM_DEBIT, "new_limit": "90000.00"}
    )

    assert response.status_code == 403
    assert refusal(response)["reason"] == "forbidden_other_customer"
    assert client.get("/phone/messages", headers=phone).json()["messages"] == []


# --- Lift a Card Lock ---------------------------------------------------------------


def test_the_seeded_card_lock_is_lifted_only_after_strong_verification(
    client: TestClient,
) -> None:
    headers, phone = login_with_phone(client, "liming")
    assert call(client, headers, "lift_card_lock", LIFT).status_code == 403
    assert cards(client, headers)[LM_LOCKED_DEBIT]["locks"] == ["overseas"]
    token = verify(client, headers, phone, "lift_card_lock", LIFT)

    response = call(client, headers, "lift_card_lock", LIFT, verification_token=token)

    assert response.status_code == 200, response.text
    assert response.json()["tier"] == "red"
    assert response.json()["output"] == {
        "card_id": LM_LOCKED_DEBIT,
        "scope": "overseas",
        "locks": [],
    }
    assert cards(client, headers)[LM_LOCKED_DEBIT]["locks"] == []


def test_lifting_one_lock_leaves_the_others(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "liming")
    confirmation = confirm(
        client, headers, "add_card_lock", {"card_id": LM_LOCKED_DEBIT, "scope": "online"}
    )
    call(
        client,
        headers,
        "add_card_lock",
        {"card_id": LM_LOCKED_DEBIT, "scope": "online"},
        confirmation,
    )
    token = verify(client, headers, phone, "lift_card_lock", LIFT)

    call(client, headers, "lift_card_lock", LIFT, verification_token=token)

    assert cards(client, headers)[LM_LOCKED_DEBIT]["locks"] == ["online"]


def test_lifting_a_lock_that_does_not_exist_is_not_found_and_sends_no_code(
    client: TestClient,
) -> None:
    headers, phone = login_with_phone(client, "liming")
    absent = {"card_id": LM_DEBIT, "scope": "all"}

    response = request_verification(client, headers, "lift_card_lock", absent)

    assert response.status_code == 404
    assert refusal(response)["reason"] == "not_found"
    assert call(client, headers, "lift_card_lock", absent).status_code == 404
    assert client.get("/phone/messages", headers=phone).json()["messages"] == []


def test_the_verification_for_one_lock_scope_does_not_lift_another(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "liming")
    for scope in ("online", "all"):
        confirmation = confirm(
            client, headers, "add_card_lock", {"card_id": LM_LOCKED_DEBIT, "scope": scope}
        )
        call(
            client,
            headers,
            "add_card_lock",
            {"card_id": LM_LOCKED_DEBIT, "scope": scope},
            confirmation,
        )
    token = verify(client, headers, phone, "lift_card_lock", LIFT)

    response = call(
        client,
        headers,
        "lift_card_lock",
        {"card_id": LM_LOCKED_DEBIT, "scope": "all"},
        verification_token=token,
    )

    assert response.status_code == 403
    assert refusal(response)["reason"] == "token_invalid"
    assert cards(client, headers)[LM_LOCKED_DEBIT]["locks"] == ["overseas", "online", "all"]


def test_lifting_a_lock_on_someone_elses_card_is_refused(client: TestClient) -> None:
    response = call(
        client,
        login(client, "liming"),
        "lift_card_lock",
        {"card_id": HM_DEBIT, "scope": "all"},
    )

    assert response.status_code == 403
    assert refusal(response)["reason"] == "forbidden_other_customer"


def test_the_agent_session_cannot_answer_a_challenge_for_a_lock_lift(
    client: TestClient,
) -> None:
    # The Agent only holds the session token; codes go from the phone to the Bank.
    headers, phone = login_with_phone(client, "liming")
    challenge = request_verification(client, headers, "lift_card_lock", LIFT).json()

    response = submit_code(client, headers, challenge["challenge_id"], "123456")

    assert response.status_code == 401
    assert refusal(response)["reason"] == "not_authenticated"
    assert cards(client, headers)[LM_LOCKED_DEBIT]["locks"] == ["overseas"]


# --- Loss Report --------------------------------------------------------------------


def test_filing_a_loss_report_disables_the_card_immediately(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "liming")
    token = verify(client, headers, phone, "file_loss_report", LOSS)

    response = call(client, headers, "file_loss_report", LOSS, verification_token=token)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tier"] == "red"
    assert body["output"]["card_id"] == LM_CREDIT
    assert body["output"]["status"] == "disabled"
    assert body["output"]["reported_at"].startswith("2026-10-20T09:00:00")
    found = cards(client, headers)
    assert found[LM_CREDIT]["status"] == "disabled"
    assert found[LM_DEBIT]["status"] == "active"
    assert found[LM_LOCKED_DEBIT]["status"] == "active"


def test_withdrawing_a_loss_report_re_enables_the_card(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "liming")
    call(
        client,
        headers,
        "file_loss_report",
        LOSS,
        verification_token=verify(client, headers, phone, "file_loss_report", LOSS),
    )
    token = verify(client, headers, phone, "withdraw_loss_report", LOSS)

    response = call(client, headers, "withdraw_loss_report", LOSS, verification_token=token)

    assert response.status_code == 200, response.text
    assert response.json()["tier"] == "red"
    assert response.json()["output"] == {"card_id": LM_CREDIT, "status": "active"}
    assert cards(client, headers)[LM_CREDIT]["status"] == "active"


def test_a_card_cannot_be_reported_lost_twice(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "liming")
    call(
        client,
        headers,
        "file_loss_report",
        LOSS,
        verification_token=verify(client, headers, phone, "file_loss_report", LOSS),
    )

    response = request_verification(client, headers, "file_loss_report", LOSS)

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"


def test_a_loss_report_cannot_be_withdrawn_when_none_is_filed(client: TestClient) -> None:
    headers = login(client, "liming")

    response = request_verification(client, headers, "withdraw_loss_report", LOSS)

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"


def test_a_card_can_be_reported_lost_again_after_the_report_is_withdrawn(
    client: TestClient,
) -> None:
    headers, phone = login_with_phone(client, "liming")
    for operation in ("file_loss_report", "withdraw_loss_report", "file_loss_report"):
        token = verify(client, headers, phone, operation, LOSS)
        response = call(client, headers, operation, LOSS, verification_token=token)
        assert response.status_code == 200, response.text

    assert cards(client, headers)[LM_CREDIT]["status"] == "disabled"


def test_a_loss_report_on_someone_elses_card_is_refused_and_sends_no_code(
    client: TestClient,
) -> None:
    headers, phone = login_with_phone(client, "liming")

    for operation in ("file_loss_report", "withdraw_loss_report"):
        response = call(client, headers, operation, {"card_id": HM_DEBIT})
        assert response.status_code == 403
        assert refusal(response)["reason"] == "forbidden_other_customer"

    assert cards(client, login(client, "hanmei"))[HM_DEBIT]["status"] == "active"
    assert client.get("/phone/messages", headers=phone).json()["messages"] == []


def test_a_loss_report_does_not_stop_the_customer_listing_cards(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "liming")
    call(
        client,
        headers,
        "file_loss_report",
        LOSS,
        verification_token=verify(client, headers, phone, "file_loss_report", LOSS),
    )

    assert len(cards(client, headers)) == 3


def test_a_loss_report_input_with_a_customer_id_is_refused(client: TestClient) -> None:
    response = call(
        client,
        login(client, "liming"),
        "file_loss_report",
        {"card_id": LM_CREDIT, "customer_id": "CUST-HM"},
    )

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"


# --- A card under a Loss Report -----------------------------------------------------


def _report_lost(client: TestClient, headers: dict[str, str], phone: dict[str, str]) -> None:
    response = call(
        client,
        headers,
        "file_loss_report",
        LOSS,
        verification_token=verify(client, headers, phone, "file_loss_report", LOSS),
    )
    assert response.status_code == 200, response.text


def test_a_limit_cannot_be_raised_on_a_card_under_a_loss_report(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "liming")
    _report_lost(client, headers, phone)
    raise_credit = {"card_id": LM_CREDIT, "new_limit": "30000.00"}

    response = request_verification(client, headers, "raise_card_limit", raise_credit)

    # Refused up front, so no code is sent for it either.
    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"
    assert call(client, headers, "raise_card_limit", raise_credit).status_code == 422
    assert cards(client, headers)[LM_CREDIT]["card_limit"] == "20000.00"


def test_a_lock_cannot_be_lifted_on_a_card_under_a_loss_report(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "liming")
    lock = {"card_id": LM_CREDIT, "scope": "online"}
    call(client, headers, "add_card_lock", lock, confirm(client, headers, "add_card_lock", lock))
    _report_lost(client, headers, phone)

    response = request_verification(client, headers, "lift_card_lock", lock)

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"
    assert cards(client, headers)[LM_CREDIT]["locks"] == ["online"]


def test_a_raise_works_again_once_the_loss_report_is_withdrawn(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "liming")
    _report_lost(client, headers, phone)
    withdraw = call(
        client,
        headers,
        "withdraw_loss_report",
        LOSS,
        verification_token=verify(client, headers, phone, "withdraw_loss_report", LOSS),
    )
    assert withdraw.status_code == 200, withdraw.text
    raise_credit = {"card_id": LM_CREDIT, "new_limit": "30000.00"}
    token = verify(client, headers, phone, "raise_card_limit", raise_credit)

    response = call(client, headers, "raise_card_limit", raise_credit, verification_token=token)

    assert response.status_code == 200, response.text
    assert cards(client, headers)[LM_CREDIT]["card_limit"] == "30000.00"


def test_tightening_a_card_under_a_loss_report_is_still_allowed(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "liming")
    _report_lost(client, headers, phone)
    lower = {"card_id": LM_CREDIT, "new_limit": "1000.00"}
    lock = {"card_id": LM_CREDIT, "scope": "all"}

    lower_token = confirm(client, headers, "lower_card_limit", lower)
    lowered = call(client, headers, "lower_card_limit", lower, lower_token)
    lock_token = confirm(client, headers, "add_card_lock", lock)
    locked = call(client, headers, "add_card_lock", lock, lock_token)

    assert lowered.status_code == 200, lowered.text
    assert locked.status_code == 200, locked.text


# --- The gate checks card IDs (OwnedBy) ---------------------------------------------


def test_another_customers_card_is_refused_by_the_gate_on_every_card_operation(
    client: TestClient,
) -> None:
    headers = login(client, "liming")

    for operation, input in [
        ("lower_card_limit", {"card_id": HM_DEBIT, "new_limit": "100.00"}),
        ("add_card_lock", {"card_id": HM_DEBIT, "scope": "all"}),
        ("raise_card_limit", {"card_id": HM_DEBIT, "new_limit": "90000.00"}),
        ("lift_card_lock", {"card_id": HM_DEBIT, "scope": "all"}),
        ("file_loss_report", {"card_id": HM_DEBIT}),
        ("withdraw_loss_report", {"card_id": HM_DEBIT}),
    ]:
        response = call(client, headers, operation, input)
        assert response.status_code == 403, operation
        assert refusal(response)["reason"] == "forbidden_other_customer", operation
        assert refusal(response)["detail"] == {"field": "card_id"}, operation


def test_an_unknown_card_is_not_found_on_a_confirmation_and_a_verification_request(
    client: TestClient,
) -> None:
    headers = login(client, "liming")

    confirmation = request_confirmations(
        client, headers, ("add_card_lock", {"card_id": "CARD-NOPE", "scope": "all"})
    )
    verification = request_verification(
        client, headers, "file_loss_report", {"card_id": "CARD-NOPE"}
    )

    for response in (confirmation, verification):
        assert response.status_code == 404
        assert refusal(response)["reason"] == "not_found"


# --- Catalogue and audit ------------------------------------------------------------


def test_catalogue_declares_the_red_card_operations_and_their_reversals(
    client: TestClient,
) -> None:
    entries = {op["name"]: op for op in client.get("/operations").json()["operations"]}

    reversals = {
        "raise_card_limit": "lower_card_limit",
        "lift_card_lock": "add_card_lock",
        "file_loss_report": "withdraw_loss_report",
        "withdraw_loss_report": "file_loss_report",
    }
    for name, reversed_by in reversals.items():
        assert entries[name]["description_zh"] and entries[name]["description_en"]
        assert entries[name]["tier"]["kind"] == "rule"
        assert "Red" in entries[name]["tier"]["description_en"]
        assert entries[name]["reversibility"] == {"kind": "reversed_by", "operation": reversed_by}


def test_every_reversal_in_the_catalogue_names_an_existing_operation(
    client: TestClient,
) -> None:
    entries = {op["name"]: op for op in client.get("/operations").json()["operations"]}

    for name, entry in entries.items():
        if entry["reversibility"]["kind"] == "reversed_by":
            assert entry["reversibility"]["operation"] in entries, name


def test_red_card_operations_are_audited_with_red_tier_and_the_verification_used(
    client: TestClient,
) -> None:
    session_id, headers, phone = login_session_with_phone(client, "liming")
    call(client, headers, "lift_card_lock", LIFT)
    token = verify(client, headers, phone, "lift_card_lock", LIFT)
    call(client, headers, "lift_card_lock", LIFT, verification_token=token)

    entries = admin(client, "get", f"/admin/sessions/{session_id}/audit")["entries"]

    operations = [e for e in entries if e["event"] == "operation"]
    assert [(e["operation"], e["tier"], e["outcome"], e["reason"]) for e in operations] == [
        ("lift_card_lock", "red", "refused", "needs_verification"),
        ("lift_card_lock", "red", "ok", None),
    ]
    assert "verification_id" in operations[1]["detail"]


def test_reset_clears_loss_reports_and_restores_the_seeded_lock(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "liming")
    call(
        client,
        headers,
        "file_loss_report",
        LOSS,
        verification_token=verify(client, headers, phone, "file_loss_report", LOSS),
    )
    call(
        client,
        headers,
        "lift_card_lock",
        LIFT,
        verification_token=verify(client, headers, phone, "lift_card_lock", LIFT),
    )

    admin(client, "post", "/admin/reset")

    found = cards(client, login(client, "liming"))
    assert found[LM_CREDIT]["status"] == "active"
    assert found[LM_LOCKED_DEBIT]["locks"] == ["overseas"]
