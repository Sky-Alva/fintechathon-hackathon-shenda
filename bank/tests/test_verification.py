"""Red transfers and Strong Verification: a simulated SMS code to the Customer's phone."""

from typing import Any

import httpx
from fastapi.testclient import TestClient

from conftest import (
    ADMIN_KEY,
    admin,
    balance,
    call,
    confirm,
    latest_code,
    login,
    login_session_with_phone,
    login_with_phone,
    phone_messages,
    request_verification,
    request_verifications,
    submit_code,
    verify,
)

HUSBAND = "PAY-HM-001"
MUM = "PAY-LM-002"


def refusal(response: httpx.Response) -> Any:
    return response.json()["refusal"]


def transfer(payee_id: str, amount: str) -> dict[str, str]:
    return {"payee_id": payee_id, "amount": amount}


def send_600_to_husband(client: TestClient, headers: dict[str, str]) -> None:
    """A confirmed Yellow ¥600 transfer, bringing hanmei's Daily Transfer Total to ¥600."""
    token = confirm(client, headers, "transfer", transfer(HUSBAND, "600.00"))
    response = call(client, headers, "transfer", transfer(HUSBAND, "600.00"), token)
    assert response.status_code == 200, response.text


def wrong_code(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


# --- Tiering ------------------------------------------------------------------------


def test_two_600_transfers_on_one_day_make_the_second_red(client: TestClient) -> None:
    headers = login(client, "hanmei")
    token = confirm(client, headers, "transfer", transfer(HUSBAND, "600.00"))

    first = call(client, headers, "transfer", transfer(HUSBAND, "600.00"), token)
    second = call(client, headers, "transfer", transfer(HUSBAND, "600.00"))

    assert first.json()["tier"] == "yellow"
    assert second.status_code == 403
    assert refusal(second)["reason"] == "needs_verification"
    assert refusal(second)["detail"] == {"operation": "transfer", "tier": "red"}
    assert balance(client, headers, "ACC-HM-001") == "8050.00"


def test_red_transfer_with_only_a_confirmation_token_needs_verification(
    client: TestClient,
) -> None:
    headers = login(client, "hanmei")
    yellow_token = confirm(client, headers, "transfer", transfer(HUSBAND, "600.00"))
    send_600_to_husband(client, headers)

    response = call(client, headers, "transfer", transfer(HUSBAND, "600.00"), yellow_token)

    assert refusal(response)["reason"] == "needs_verification"
    assert balance(client, headers, "ACC-HM-001") == "8050.00"


# --- The challenge and the simulated SMS ------------------------------------------------


def test_a_challenge_sends_a_code_to_the_customers_simulated_phone(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "hanmei")

    response = request_verification(client, headers, "transfer", transfer(HUSBAND, "1200.00"))

    assert response.status_code == 200, response.text
    challenge = response.json()
    assert challenge["challenge_id"]
    assert challenge["operations"] == ["transfer"]
    assert challenge["tier"] == "red"
    assert challenge["sent_to"] == "138****0002"
    assert challenge["expires_at"] == "2026-10-20T09:05:00+08:00"
    messages = phone_messages(client, phone)
    assert len(messages) == 1
    assert messages[0]["sent_to"] == "138****0002"
    assert messages[0]["at"] == "2026-10-20T09:00:00+08:00"
    assert len(latest_code(client, phone)) == 6


def test_the_staff_console_can_read_a_customers_simulated_phone(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "hanmei")
    request_verification(client, headers, "transfer", transfer(HUSBAND, "1200.00"))

    messages = admin(client, "get", "/admin/customers/CUST-HM/phone/messages")["messages"]

    assert messages == phone_messages(client, phone)


def test_the_staff_console_gets_not_found_for_an_unknown_customers_phone(
    client: TestClient,
) -> None:
    response = client.get(
        "/admin/customers/CUST-NOBODY/phone/messages", headers={"X-Admin-Key": ADMIN_KEY}
    )

    assert response.status_code == 404
    assert refusal(response)["reason"] == "not_found"


def test_the_session_token_cannot_read_the_simulated_phone(client: TestClient) -> None:
    headers = login(client, "hanmei")
    request_verification(client, headers, "transfer", transfer(HUSBAND, "1200.00"))

    response = client.get("/phone/messages", headers=headers)

    assert response.status_code == 401
    assert refusal(response)["reason"] == "not_authenticated"


def test_a_customers_phone_shows_only_their_own_messages(client: TestClient) -> None:
    hanmei = login(client, "hanmei")
    _, liming_phone = login_with_phone(client, "liming")
    request_verification(client, hanmei, "transfer", transfer(HUSBAND, "1200.00"))

    assert phone_messages(client, liming_phone) == []


def test_no_challenge_is_issued_for_a_yellow_or_green_operation(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "hanmei")
    for operation, input in [("transfer", transfer(HUSBAND, "200.00")), ("query_balance", {})]:
        response = request_verification(client, headers, operation, input)

        assert response.status_code == 422, operation
        assert refusal(response)["reason"] == "invalid_input"
    assert phone_messages(client, phone) == []


def test_no_challenge_is_issued_for_a_transfer_that_can_never_run(client: TestClient) -> None:
    headers = login(client, "liming")

    response = request_verification(client, headers, "transfer", transfer(MUM, "1500.00"))

    assert refusal(response)["reason"] == "insufficient_funds"


# --- Codes and verification tokens -----------------------------------------------------


def test_a_verified_red_transfer_runs(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "hanmei")
    send_600_to_husband(client, headers)
    token = verify(client, headers, phone, "transfer", transfer(HUSBAND, "600.00"))

    response = call(
        client, headers, "transfer", transfer(HUSBAND, "600.00"), verification_token=token
    )

    assert response.status_code == 200, response.text
    assert response.json()["tier"] == "red"
    assert response.json()["output"]["daily_transfer_total"] == "1200.00"
    assert balance(client, headers, "ACC-HM-001") == "7450.00"


def test_a_correct_code_returns_a_single_use_token(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "hanmei")
    token = verify(client, headers, phone, "transfer", transfer(HUSBAND, "1200.00"))
    call(client, headers, "transfer", transfer(HUSBAND, "1200.00"), verification_token=token)

    response = call(
        client, headers, "transfer", transfer(HUSBAND, "1200.00"), verification_token=token
    )

    assert refusal(response)["reason"] == "token_invalid"
    assert balance(client, headers, "ACC-HM-001") == "7450.00"


def test_a_verification_token_covers_only_the_exact_inputs(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "hanmei")
    token = verify(client, headers, phone, "transfer", transfer(HUSBAND, "1200.00"))

    response = call(
        client, headers, "transfer", transfer(HUSBAND, "5000.00"), verification_token=token
    )

    assert response.status_code == 403
    assert refusal(response)["reason"] == "token_invalid"
    assert balance(client, headers, "ACC-HM-001") == "8650.00"


def test_a_verification_token_cannot_be_used_from_another_session(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "hanmei")
    stolen = verify(client, headers, phone, "transfer", transfer(HUSBAND, "1200.00"))

    response = call(
        client, login(client, "hanmei"), "transfer", transfer(HUSBAND, "1200.00"),
        verification_token=stolen,
    )

    assert refusal(response)["reason"] == "token_invalid"


def test_a_verification_token_expires_after_five_bank_clock_minutes(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "hanmei")
    token = verify(client, headers, phone, "transfer", transfer(HUSBAND, "1200.00"))
    admin(client, "post", "/admin/clock/advance", json={"minutes": 5})

    response = call(
        client, headers, "transfer", transfer(HUSBAND, "1200.00"), verification_token=token
    )

    assert refusal(response)["reason"] == "token_invalid"


def test_a_wrong_code_is_refused_and_counted(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "hanmei")
    challenge_id = request_verification(
        client, headers, "transfer", transfer(HUSBAND, "1200.00")
    ).json()["challenge_id"]
    code = latest_code(client, phone)

    first = submit_code(client, phone, challenge_id, wrong_code(code))
    second = submit_code(client, phone, challenge_id, "abc")
    right = submit_code(client, phone, challenge_id, code)

    assert first.status_code == 403
    assert refusal(first)["reason"] == "token_invalid"
    assert refusal(first)["detail"] == {"challenge_id": challenge_id, "failed_attempts": 1}
    assert refusal(second)["detail"]["failed_attempts"] == 2
    assert right.status_code == 200, right.text


def test_three_wrong_codes_close_the_challenge(client: TestClient) -> None:
    session_id, headers, phone = login_session_with_phone(client, "hanmei")
    challenge_id = request_verification(
        client, headers, "transfer", transfer(HUSBAND, "1200.00")
    ).json()["challenge_id"]
    code = latest_code(client, phone)
    for _ in range(3):
        submit_code(client, phone, challenge_id, wrong_code(code))
    # Three wrong codes also lock the session; the challenge stays closed after a lift.
    admin(client, "delete", f"/admin/sessions/{session_id}/safety-lock")

    response = submit_code(client, phone, challenge_id, code)

    assert refusal(response)["reason"] == "token_invalid"
    assert "verifications" not in response.json()


def test_the_agents_session_token_cannot_answer_a_challenge(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "hanmei")
    challenge_id = request_verification(
        client, headers, "transfer", transfer(HUSBAND, "1200.00")
    ).json()["challenge_id"]

    response = submit_code(client, headers, challenge_id, latest_code(client, phone))

    assert response.status_code == 401
    assert refusal(response)["reason"] == "not_authenticated"


def test_wrong_codes_are_counted_across_challenges_in_a_session(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "hanmei")
    for amount in ["1200.00", "1300.00"]:
        challenge_id = request_verification(
            client, headers, "transfer", transfer(HUSBAND, amount)
        ).json()["challenge_id"]
        code = latest_code(client, phone)
        response = submit_code(client, phone, challenge_id, wrong_code(code))

    assert refusal(response)["detail"]["failed_attempts"] == 2


def test_a_challenge_is_answered_once_and_only_from_its_own_session(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "hanmei")
    challenge_id = request_verification(
        client, headers, "transfer", transfer(HUSBAND, "1200.00")
    ).json()["challenge_id"]
    code = latest_code(client, phone)

    _, other_phone = login_with_phone(client, "hanmei")
    from_other_session = submit_code(client, other_phone, challenge_id, code)
    submit_code(client, phone, challenge_id, code)
    again = submit_code(client, phone, challenge_id, code)

    assert refusal(from_other_session)["reason"] == "token_invalid"
    assert refusal(again)["reason"] == "token_invalid"


def test_a_challenge_expires_after_five_bank_clock_minutes(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "hanmei")
    challenge_id = request_verification(
        client, headers, "transfer", transfer(HUSBAND, "1200.00")
    ).json()["challenge_id"]
    admin(client, "post", "/admin/clock/advance", json={"minutes": 5})

    response = submit_code(client, phone, challenge_id, latest_code(client, phone))

    assert refusal(response)["reason"] == "token_invalid"


# --- Tier re-checked at execution --------------------------------------------------------


def test_a_yellow_token_from_before_the_total_crossed_1000_gives_way_to_verification(
    client: TestClient,
) -> None:
    headers, phone = login_with_phone(client, "hanmei")
    stale = confirm(client, headers, "transfer", transfer(HUSBAND, "600.00"))
    send_600_to_husband(client, headers)

    refused = call(client, headers, "transfer", transfer(HUSBAND, "600.00"), stale)
    token = verify(client, headers, phone, "transfer", transfer(HUSBAND, "600.00"))
    verified = call(
        client, headers, "transfer", transfer(HUSBAND, "600.00"), verification_token=token
    )

    assert refusal(refused)["reason"] == "needs_verification"
    assert verified.status_code == 200, verified.text


# --- Audit ------------------------------------------------------------------------------


def test_challenges_attempts_and_outcomes_are_audited(client: TestClient) -> None:
    session_id, headers, phone = login_session_with_phone(client, "hanmei")
    challenge_id = request_verification(
        client, headers, "transfer", transfer(HUSBAND, "1200.00")
    ).json()["challenge_id"]
    code = latest_code(client, phone)
    submit_code(client, phone, challenge_id, wrong_code(code))
    token = submit_code(client, phone, challenge_id, code).json()["verifications"][0]["verification_token"]
    call(client, headers, "transfer", transfer(HUSBAND, "1200.00"), verification_token=token)

    entries = admin(client, "get", f"/admin/sessions/{session_id}/audit")["entries"]

    summary = [(e["event"], e["operation"], e["tier"], e["outcome"], e["reason"]) for e in entries]
    assert summary == [
        ("verification_challenge", "transfer", "red", "ok", None),
        ("verification_attempt", "transfer", "red", "refused", "token_invalid"),
        ("verification_attempt", "transfer", "red", "ok", None),
        ("operation", "transfer", "red", "ok", None),
    ]
    assert entries[0]["detail"]["challenge_id"] == challenge_id
    assert entries[1]["detail"] == {"challenge_id": challenge_id, "failed_attempts": 1}
    verification_id = entries[2]["detail"]["verification_id"]
    assert entries[2]["detail"]["challenge_id"] == challenge_id
    assert entries[3]["detail"] == {"verification_id": verification_id}
    assert code not in str(entries)
    assert token not in str(entries)


# --- One challenge for several Red calls (#55) -------------------------------------------

LM_DEBIT, LM_CREDIT = "CARD-LM-001", "CARD-LM-003"
HM_DEBIT = "CARD-HM-001"
TWO_LOSS_REPORTS = [
    ("file_loss_report", {"card_id": LM_CREDIT}),
    ("file_loss_report", {"card_id": LM_DEBIT}),
]


def tokens(response: httpx.Response) -> list[str]:
    assert response.status_code == 200, response.text
    return [v["verification_token"] for v in response.json()["verifications"]]


def test_one_challenge_covers_several_red_calls_with_one_code(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "liming")

    response = request_verifications(client, headers, *TWO_LOSS_REPORTS)

    assert response.status_code == 200, response.text
    challenge = response.json()
    assert challenge["operations"] == ["file_loss_report", "file_loss_report"]
    assert challenge["tier"] == "red"
    [sms] = phone_messages(client, phone)
    assert "2 项操作" in sms["text"]


def test_the_right_code_earns_one_token_per_call_and_each_runs(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "liming")
    challenge_id = request_verifications(client, headers, *TWO_LOSS_REPORTS).json()["challenge_id"]

    answered = submit_code(client, phone, challenge_id, latest_code(client, phone))

    assert [v["operation"] for v in answered.json()["verifications"]] == [
        "file_loss_report",
        "file_loss_report",
    ]
    for (operation, input), token in zip(TWO_LOSS_REPORTS, tokens(answered), strict=True):
        ran = call(client, headers, operation, input, verification_token=token)
        assert ran.status_code == 200, ran.text
        assert ran.json()["tier"] == "red"


def test_a_token_from_a_multi_call_challenge_covers_only_its_own_call_once(
    client: TestClient,
) -> None:
    headers, phone = login_with_phone(client, "hanmei")
    large, other = transfer(HUSBAND, "1200.00"), transfer(HUSBAND, "1300.00")
    challenge_id = request_verifications(
        client, headers, ("transfer", large), ("transfer", other)
    ).json()["challenge_id"]
    first, _ = tokens(submit_code(client, phone, challenge_id, latest_code(client, phone)))

    other_call = call(client, headers, "transfer", other, verification_token=first)
    own_call = call(client, headers, "transfer", large, verification_token=first)
    again = call(client, headers, "transfer", large, verification_token=first)

    assert refusal(other_call)["reason"] == "token_invalid"
    assert own_call.status_code == 200, own_call.text
    assert refusal(again)["reason"] == "token_invalid"
    assert balance(client, headers, "ACC-HM-001") == "7450.00"


def test_a_raised_card_limit_and_a_large_transfer_share_one_code(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "hanmei")
    calls = [
        ("raise_card_limit", {"card_id": HM_DEBIT, "new_limit": "20000.00"}),
        ("transfer", transfer(HUSBAND, "2000.00")),
    ]
    challenge_id = request_verifications(client, headers, *calls).json()["challenge_id"]

    answered = submit_code(client, phone, challenge_id, latest_code(client, phone))

    for (operation, input), token in zip(calls, tokens(answered), strict=True):
        ran = call(client, headers, operation, input, verification_token=token)
        assert ran.status_code == 200, ran.text
    assert len(phone_messages(client, phone)) == 1


def test_three_wrong_codes_close_a_multi_call_challenge_and_lock_the_customer(
    client: TestClient,
) -> None:
    session_id, headers, phone = login_session_with_phone(client, "liming")
    challenge_id = request_verifications(client, headers, *TWO_LOSS_REPORTS).json()["challenge_id"]
    code = latest_code(client, phone)

    attempts = [submit_code(client, phone, challenge_id, wrong_code(code)) for _ in range(3)]

    assert [refusal(a)["detail"]["failed_attempts"] for a in attempts] == [1, 2, 3]
    assert "locked_until" in refusal(attempts[-1])["detail"]
    lock = admin(client, "get", f"/admin/sessions/{session_id}/safety-lock")
    assert lock["trigger"] == "wrong_codes"
    admin(client, "delete", f"/admin/sessions/{session_id}/safety-lock")
    assert refusal(submit_code(client, phone, challenge_id, code))["reason"] == "token_invalid"


def test_calls_are_tiered_in_order_and_one_that_is_not_red_gets_no_code_for_any(
    client: TestClient,
) -> None:
    headers, phone = login_with_phone(client, "hanmei")

    # ¥200 is Red after ¥1,200; the first ¥600 of two is Yellow on its own.
    after_a_large_one = request_verifications(
        client,
        headers,
        ("transfer", transfer(HUSBAND, "1200.00")),
        ("transfer", transfer(HUSBAND, "200.00")),
    )
    yellow_first = request_verifications(
        client,
        headers,
        ("transfer", transfer(HUSBAND, "600.00")),
        ("transfer", transfer(HUSBAND, "600.00")),
    )

    assert after_a_large_one.status_code == 200, after_a_large_one.text
    assert yellow_first.status_code == 422
    assert refusal(yellow_first)["reason"] == "invalid_input"
    assert refusal(yellow_first)["detail"]["index"] == 0
    assert len(phone_messages(client, phone)) == 1


def test_a_multi_call_challenge_is_audited_per_call(client: TestClient) -> None:
    session_id, headers, phone = login_session_with_phone(client, "liming")
    challenge_id = request_verifications(client, headers, *TWO_LOSS_REPORTS).json()["challenge_id"]
    code = latest_code(client, phone)
    submit_code(client, phone, challenge_id, wrong_code(code))
    submit_code(client, phone, challenge_id, code)

    entries = admin(client, "get", f"/admin/sessions/{session_id}/audit")["entries"]

    summary = [(e["event"], e["operation"], e["outcome"]) for e in entries]
    assert summary == [
        ("verification_challenge", "file_loss_report", "ok"),
        ("verification_challenge", "file_loss_report", "ok"),
        ("verification_attempt", None, "refused"),
        ("verification_attempt", "file_loss_report", "ok"),
        ("verification_attempt", "file_loss_report", "ok"),
    ]
    assert {e["detail"]["challenge_id"] for e in entries} == {challenge_id}
    assert [e["input"] for e in entries[:2]] == [input for _, input in TWO_LOSS_REPORTS]
    assert len({e["detail"]["verification_id"] for e in entries[3:]}) == 2
