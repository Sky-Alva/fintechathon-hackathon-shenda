"""Smart Transfers, Scheduled Transfers (#11): schedule, cancel, run when the Bank clock
reaches the run date, hold when Red at run time."""

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
    login_with_phone,
    preview_tiers,
    request_confirmations,
    request_verification,
    submit_code,
    verify,
)

MUM = "PAY-LM-002"
ACCOUNT = "ACC-LM-001"  # 李明's current account, ¥1,180.00 at the seed
TOMORROW = "2026-10-21"
RUN_DAY = "2026-10-22"
RUN_DAY_9AM = "2026-10-22T09:00:00+08:00"
RUN_DAY_START = "2026-10-22T00:00:00+08:00"


def refusal(response: httpx.Response) -> Any:
    return response.json()["refusal"]


def scheduled(amount: str, run_date: str = RUN_DAY, payee: str = MUM) -> dict[str, Any]:
    return {"payee_id": payee, "amount": amount, "run_date": run_date}


def set_clock(client: TestClient, now: str) -> None:
    admin(client, "put", "/admin/clock", json={"now": now})


def confirmed_call(
    client: TestClient, headers: dict[str, str], operation: str, input: dict[str, Any]
) -> httpx.Response:
    """Call a Yellow Operation the way the Agent does: Confirmation first, then the call."""
    return call(client, headers, operation, input, confirm(client, headers, operation, input))


def schedule(client: TestClient, headers: dict[str, str], input: dict[str, Any]) -> str:
    """Schedule a Yellow transfer as the Agent would (Confirmation, then the call)."""
    response = confirmed_call(client, headers, "schedule_transfer", input)
    assert response.status_code == 200, response.text
    created_id: str = response.json()["output"]["scheduled_transfer"]["scheduled_transfer_id"]
    return created_id


def listed(
    client: TestClient, headers: dict[str, str], status: str | None = None
) -> dict[str, dict[str, Any]]:
    """The Customer's Scheduled Transfers by ID, as the Agent would see them."""
    input = {} if status is None else {"status": status}
    response = call(client, headers, "list_scheduled_transfers", input)
    assert response.status_code == 200, response.text
    return {s["scheduled_transfer_id"]: s for s in response.json()["output"]["scheduled_transfers"]}


def tier_of(
    client: TestClient, headers: dict[str, str], operation: str, input: dict[str, Any]
) -> str:
    response = preview_tiers(client, headers, (operation, input))
    assert response.status_code == 200, response.text
    tier: str = response.json()["steps"][0]["tier"]
    return tier


def pay_mum(client: TestClient, headers: dict[str, str], amount: str) -> None:
    transfer = {"payee_id": MUM, "amount": amount}
    response = confirmed_call(client, headers, "transfer", transfer)
    assert response.status_code == 200, response.text


# --- Scheduling -----------------------------------------------------------------------


def test_a_confirmed_scheduled_transfer_is_created_and_no_money_moves(client: TestClient) -> None:
    headers = login(client, "liming")
    input = scheduled("300.00")

    response = confirmed_call(client, headers, "schedule_transfer", input)

    assert response.status_code == 200, response.text
    assert response.json()["tier"] == "yellow"
    created = response.json()["output"]["scheduled_transfer"]
    assert created["scheduled_transfer_id"] == "SCH-000001"
    assert created["payee_name"] == "王秀英"
    assert (created["amount"], created["run_date"]) == ("300.00", RUN_DAY)
    assert (created["status"], created["awaiting_verification"]) == ("scheduled", False)
    assert balance(client, headers, ACCOUNT) == "1180.00"


def test_scheduling_without_confirmation_is_refused(client: TestClient) -> None:
    headers = login(client, "liming")

    response = call(client, headers, "schedule_transfer", scheduled("300.00"))

    assert response.status_code == 403
    assert refusal(response)["reason"] == "needs_confirmation"
    assert listed(client, headers) == {}


def test_a_confirmation_covers_only_the_exact_scheduled_transfer(client: TestClient) -> None:
    headers = login(client, "liming")
    token = confirm(client, headers, "schedule_transfer", scheduled("300.00"))

    for changed in [scheduled("301.00"), scheduled("300.00", "2026-10-23")]:
        response = call(client, headers, "schedule_transfer", changed, token)
        assert refusal(response)["reason"] == "token_invalid", changed

    assert listed(client, headers) == {}


def test_a_scheduled_transfer_within_1000_on_its_run_day_is_yellow(client: TestClient) -> None:
    headers = login(client, "liming")

    assert tier_of(client, headers, "schedule_transfer", scheduled("1000.00")) == "yellow"


def test_a_scheduled_transfer_above_1000_on_its_run_day_is_red(client: TestClient) -> None:
    headers = login(client, "liming")

    assert tier_of(client, headers, "schedule_transfer", scheduled("1000.01")) == "red"


def test_a_red_scheduled_transfer_needs_strong_verification_to_be_created(
    client: TestClient,
) -> None:
    headers, phone = login_with_phone(client, "liming")
    input = scheduled("1100.00")

    bare = call(client, headers, "schedule_transfer", input)
    confirmation = request_confirmations(client, headers, ("schedule_transfer", input))
    assert refusal(bare)["reason"] == "needs_verification"
    assert refusal(confirmation)["reason"] == "needs_verification"
    assert listed(client, headers) == {}

    token = verify(client, headers, phone, "schedule_transfer", input)
    created = call(client, headers, "schedule_transfer", input, verification_token=token)
    assert created.status_code == 200, created.text
    assert created.json()["tier"] == "red"


def test_the_tier_follows_the_run_day_not_today(client: TestClient) -> None:
    headers = login(client, "liming")
    pay_mum(client, headers, "900.00")  # today's Daily Transfer Total is now ¥900

    # ¥280 is left: ¥150 more today is Red (¥1,050), while ¥300 scheduled for a later day is not.
    assert tier_of(client, headers, "transfer", {"payee_id": MUM, "amount": "150.00"}) == "red"
    assert tier_of(client, headers, "schedule_transfer", scheduled("300.00", TOMORROW)) == "yellow"


def test_a_scheduled_transfer_does_not_count_toward_the_day_it_was_scheduled(
    client: TestClient,
) -> None:
    headers = login(client, "liming")
    schedule(client, headers, scheduled("900.00"))

    assert tier_of(client, headers, "transfer", {"payee_id": MUM, "amount": "900.00"}) == "yellow"


def test_a_scheduled_transfer_must_run_after_today_and_within_a_year(client: TestClient) -> None:
    headers = login(client, "liming")

    for run_date in ["2026-10-20", "2026-10-19", "2027-10-21", "not-a-date"]:
        response = call(client, headers, "schedule_transfer", scheduled("50.00", run_date))
        assert response.status_code == 422, run_date
        assert refusal(response)["reason"] == "invalid_input", run_date


def test_scheduling_to_an_unknown_payee_is_refused(client: TestClient) -> None:
    headers = login(client, "liming")

    response = call(client, headers, "schedule_transfer", scheduled("50.00", payee="PAY-NOPE"))

    assert response.status_code == 404
    assert refusal(response)["reason"] == "not_found"


def test_a_customer_cannot_schedule_to_the_other_customers_payee(client: TestClient) -> None:
    hanmei = login(client, "hanmei")

    response = call(client, hanmei, "schedule_transfer", scheduled("50.00", payee=MUM))

    assert response.status_code == 404
    assert refusal(response)["reason"] == "not_found"


def test_scheduling_takes_no_customer_id_or_other_stray_field(client: TestClient) -> None:
    input = {**scheduled("50.00"), "customer_id": "CUST-HM"}

    response = call(client, login(client, "liming"), "schedule_transfer", input)

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"


# --- Cancelling -----------------------------------------------------------------------


def test_cancelling_before_it_runs_is_yellow_and_stops_it(client: TestClient) -> None:
    headers = login(client, "liming")
    scheduled_id = schedule(client, headers, scheduled("300.00"))
    cancel = {"scheduled_transfer_id": scheduled_id}

    bare = call(client, headers, "cancel_scheduled_transfer", cancel)
    assert refusal(bare)["reason"] == "needs_confirmation"

    response = confirmed_call(client, headers, "cancel_scheduled_transfer", cancel)
    assert response.status_code == 200, response.text
    assert response.json()["tier"] == "yellow"
    assert listed(client, headers)[scheduled_id]["status"] == "cancelled"

    set_clock(client, RUN_DAY_9AM)
    assert listed(client, headers)[scheduled_id]["status"] == "cancelled"
    assert balance(client, headers, ACCOUNT) == "1180.00"


def test_a_scheduled_transfer_cannot_be_cancelled_twice(client: TestClient) -> None:
    headers = login(client, "liming")
    cancel = {"scheduled_transfer_id": schedule(client, headers, scheduled("300.00"))}
    confirmed_call(client, headers, "cancel_scheduled_transfer", cancel)

    response = call(client, headers, "cancel_scheduled_transfer", cancel)

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"


def test_a_scheduled_transfer_that_has_run_cannot_be_cancelled(client: TestClient) -> None:
    headers = login(client, "liming")
    cancel = {"scheduled_transfer_id": schedule(client, headers, scheduled("300.00"))}
    set_clock(client, RUN_DAY_9AM)

    response = call(client, headers, "cancel_scheduled_transfer", cancel)
    confirmation = request_confirmations(client, headers, ("cancel_scheduled_transfer", cancel))

    assert refusal(response)["reason"] == "invalid_input"
    assert refusal(confirmation)["reason"] == "invalid_input"
    assert balance(client, headers, ACCOUNT) == "880.00"


def test_a_customer_cannot_cancel_the_other_customers_scheduled_transfer(
    client: TestClient,
) -> None:
    scheduled_id = schedule(client, login(client, "liming"), scheduled("300.00"))

    hanmei = login(client, "hanmei")

    response = call(
        client, hanmei, "cancel_scheduled_transfer", {"scheduled_transfer_id": scheduled_id}
    )

    assert response.status_code == 403
    assert refusal(response)["reason"] == "forbidden_other_customer"


def test_a_customer_sees_only_their_own_scheduled_transfers(client: TestClient) -> None:
    liming = login(client, "liming")
    schedule(client, liming, scheduled("300.00"))

    assert len(listed(client, liming)) == 1
    assert listed(client, login(client, "hanmei")) == {}


# --- Running when the Bank clock reaches the run date -----------------------------------


def test_it_does_not_run_before_its_run_date(client: TestClient) -> None:
    headers = login(client, "liming")
    scheduled_id = schedule(client, headers, scheduled("300.00"))

    set_clock(client, "2026-10-21T23:59:00+08:00")

    assert listed(client, headers)[scheduled_id]["status"] == "scheduled"
    assert balance(client, headers, ACCOUNT) == "1180.00"


def test_advancing_the_clock_to_the_run_date_executes_it(client: TestClient) -> None:
    headers = login(client, "liming")
    scheduled_id = schedule(client, headers, scheduled("300.00"))

    admin(client, "post", "/admin/clock/advance", json={"minutes": 2 * 24 * 60})

    done = listed(client, headers)[scheduled_id]
    assert done["status"] == "executed"
    assert done["transfer_id"] == "TRF-000009"
    assert balance(client, headers, ACCOUNT) == "880.00"


def test_the_clock_endpoints_still_return_only_the_time(client: TestClient) -> None:
    headers = login(client, "liming")
    schedule(client, headers, scheduled("300.00"))

    assert admin(client, "put", "/admin/clock", json={"now": RUN_DAY_9AM}) == {"now": RUN_DAY_9AM}
    assert admin(client, "post", "/admin/clock/advance", json={"minutes": 30}) == {
        "now": "2026-10-22T09:30:00+08:00"
    }


def test_a_run_counts_toward_its_run_days_daily_transfer_total(client: TestClient) -> None:
    headers = login(client, "liming")
    schedule(client, headers, scheduled("300.00"))
    set_clock(client, RUN_DAY_9AM)

    assert tier_of(client, headers, "transfer", {"payee_id": MUM, "amount": "700.00"}) == "yellow"
    assert tier_of(client, headers, "transfer", {"payee_id": MUM, "amount": "700.01"}) == "red"


def test_a_run_that_is_late_still_counts_toward_its_own_run_day(client: TestClient) -> None:
    headers = login(client, "liming")
    schedule(client, headers, scheduled("300.00"))
    set_clock(client, "2026-10-30T09:00:00+08:00")  # a week after the run date
    assert balance(client, headers, ACCOUNT) == "880.00"

    set_clock(client, RUN_DAY_9AM)  # the clock goes back to the run day

    assert tier_of(client, headers, "transfer", {"payee_id": MUM, "amount": "700.01"}) == "red"


def test_a_scheduled_transfer_that_cannot_be_paid_is_failed_not_dropped(
    client: TestClient,
) -> None:
    headers = login(client, "liming")
    scheduled_id = schedule(client, headers, scheduled("600.00"))
    pay_mum(client, headers, "700.00")  # leaves ¥480, so ¥600 can't be paid on the run day

    set_clock(client, RUN_DAY_9AM)

    failed = listed(client, headers)[scheduled_id]
    assert (failed["status"], failed["failure_reason"]) == ("failed", "insufficient_funds")
    assert balance(client, headers, ACCOUNT) == "480.00"


def test_a_red_scheduled_transfer_verified_when_created_is_not_held_again(
    client: TestClient,
) -> None:
    headers, phone = login_with_phone(client, "liming")
    input = scheduled("1100.00")
    token = verify(client, headers, phone, "schedule_transfer", input)
    call(client, headers, "schedule_transfer", input, verification_token=token)

    set_clock(client, RUN_DAY_9AM)

    assert [s["status"] for s in listed(client, headers).values()] == ["executed"]
    assert balance(client, headers, ACCOUNT) == "80.00"


# --- Held when Red at run time --------------------------------------------------------


def two_that_are_yellow_alone_but_red_together(
    client: TestClient, headers: dict[str, str]
) -> tuple[str, str]:
    """¥600 then ¥450 for the same day: each is Yellow when scheduled, ¥1,050 in all."""
    return (
        schedule(client, headers, scheduled("600.00")),
        schedule(client, headers, scheduled("450.00")),
    )


def test_a_scheduled_transfer_that_is_red_at_run_time_is_held_not_sent_or_dropped(
    client: TestClient,
) -> None:
    headers = login(client, "liming")
    first, second = two_that_are_yellow_alone_but_red_together(client, headers)

    set_clock(client, RUN_DAY_9AM)

    found = listed(client, headers)
    assert found[first]["status"] == "executed"
    assert (found[second]["status"], found[second]["awaiting_verification"]) == ("held", True)
    assert found[second]["transfer_id"] is None
    assert balance(client, headers, ACCOUNT) == "580.00"  # only the first was sent


def test_a_scheduled_transfer_whose_session_is_under_a_safety_lock_is_held(
    client: TestClient,
) -> None:
    headers = login(client, "liming")
    scheduled_transfer_id = schedule(client, headers, scheduled("200.00"))
    set_clock(client, "2026-10-21T23:50:00+08:00")
    evidence = {"kind": "impersonation", "evidence": "我是银行安全中心的，马上转账"}
    call(client, headers, "report_suspected_attempt", evidence)
    call(client, headers, "report_suspected_attempt", evidence)  # locked until 00:20

    set_clock(client, RUN_DAY_9AM)

    assert listed(client, headers)[scheduled_transfer_id]["status"] == "held"
    assert balance(client, headers, ACCOUNT) == "1180.00"


def test_held_transfers_can_be_listed_by_status(client: TestClient) -> None:
    headers = login(client, "liming")
    _, second = two_that_are_yellow_alone_but_red_together(client, headers)
    set_clock(client, RUN_DAY_9AM)

    assert list(listed(client, headers, "held")) == [second]
    assert list(listed(client, headers, "scheduled")) == []


def test_a_held_transfer_is_sent_after_strong_verification(client: TestClient) -> None:
    headers, phone = login_with_phone(client, "liming")
    _, held = two_that_are_yellow_alone_but_red_together(client, headers)
    set_clock(client, RUN_DAY_9AM)
    send = {"scheduled_transfer_id": held, "amount": "450.00"}

    token = verify(client, headers, phone, "send_held_transfer", send)
    response = call(client, headers, "send_held_transfer", send, verification_token=token)

    assert response.status_code == 200, response.text
    assert response.json()["tier"] == "red"
    assert response.json()["output"]["scheduled_transfer"]["status"] == "executed"
    assert balance(client, headers, ACCOUNT) == "130.00"
    assert listed(client, headers)[held]["awaiting_verification"] is False
    # Sending it counts toward the Daily Transfer Total: ¥600 + ¥450 = ¥1,050.
    assert tier_of(client, headers, "transfer", {"payee_id": MUM, "amount": "10.00"}) == "red"


def test_a_held_transfer_is_not_sent_without_strong_verification(client: TestClient) -> None:
    headers = login(client, "liming")
    _, held = two_that_are_yellow_alone_but_red_together(client, headers)
    set_clock(client, RUN_DAY_9AM)
    send = {"scheduled_transfer_id": held, "amount": "450.00"}

    bare = call(client, headers, "send_held_transfer", send)
    confirmation = request_confirmations(client, headers, ("send_held_transfer", send))

    assert refusal(bare)["reason"] == "needs_verification"
    assert refusal(confirmation)["reason"] == "needs_verification"
    assert listed(client, headers)[held]["status"] == "held"
    assert balance(client, headers, ACCOUNT) == "580.00"


def test_a_held_transfer_is_sent_only_for_its_own_amount(client: TestClient) -> None:
    headers = login(client, "liming")
    _, held = two_that_are_yellow_alone_but_red_together(client, headers)
    set_clock(client, RUN_DAY_9AM)

    send = {"scheduled_transfer_id": held, "amount": "100.00"}

    response = call(client, headers, "send_held_transfer", send)

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"


def test_only_a_held_transfer_can_be_sent(client: TestClient) -> None:
    headers = login(client, "liming")
    waiting = schedule(client, headers, scheduled("300.00"))

    send = {"scheduled_transfer_id": waiting, "amount": "300.00"}

    response = call(client, headers, "send_held_transfer", send)

    assert response.status_code == 422
    assert refusal(response)["reason"] == "invalid_input"
    assert listed(client, headers)[waiting]["status"] == "scheduled"


def test_a_held_transfer_can_be_cancelled(client: TestClient) -> None:
    headers = login(client, "liming")
    _, held = two_that_are_yellow_alone_but_red_together(client, headers)
    set_clock(client, RUN_DAY_9AM)
    cancel = {"scheduled_transfer_id": held}

    response = confirmed_call(client, headers, "cancel_scheduled_transfer", cancel)

    assert response.status_code == 200, response.text
    assert listed(client, headers)[held]["status"] == "cancelled"
    assert balance(client, headers, ACCOUNT) == "580.00"


# --- Attack: fake urgency ---------------------------------------------------------------


def test_urgency_does_not_get_a_red_scheduled_transfer_without_the_real_code(
    client: TestClient,
) -> None:
    headers, phone = login_with_phone(client, "liming")
    input = scheduled("1500.00", TOMORROW, payee="PAY-LM-005")  # 陈静, never paid

    bare = call(client, headers, "schedule_transfer", input)
    challenge = request_verification(client, headers, "schedule_transfer", input)
    wrong = submit_code(client, phone, challenge.json()["challenge_id"], "000000")

    assert refusal(bare)["reason"] == "needs_verification"
    assert refusal(wrong)["reason"] == "token_invalid"
    assert listed(client, headers) == {}
    set_clock(client, "2026-10-21T09:00:00+08:00")
    assert balance(client, headers, ACCOUNT) == "1180.00"


# --- Audit and catalogue ----------------------------------------------------------------


def test_runs_are_audited_in_the_session_that_scheduled_them(client: TestClient) -> None:
    session_id, headers = login_session(client, "liming")
    two_that_are_yellow_alone_but_red_together(client, headers)
    set_clock(client, RUN_DAY_9AM)

    entries = admin(client, "get", f"/admin/sessions/{session_id}/audit")["entries"]

    runs = [e for e in entries if "scheduled_transfer_id" in (e["detail"] or {})]
    assert [(e["operation"], e["tier"], e["outcome"], e["reason"]) for e in runs] == [
        ("transfer", "yellow", "ok", None),
        ("transfer", "red", "refused", "needs_verification"),  # held: never counts as run
    ]
    assert all(e["at"] == RUN_DAY_START for e in runs)  # each runs as its run day starts
    assert all(e["detail"]["run_date"] == RUN_DAY for e in runs)
    own = call(client, headers, "read_audit_trail").json()["output"]["entries"]
    own_runs = [e for e in own if "scheduled_transfer_id" in (e["detail"] or {})]
    assert [e["entry_id"] for e in own_runs] == [e["entry_id"] for e in runs]


def test_catalogue_declares_the_scheduled_transfer_operations(client: TestClient) -> None:
    entries = {op["name"]: op for op in client.get("/operations").json()["operations"]}

    names = [
        "schedule_transfer",
        "cancel_scheduled_transfer",
        "list_scheduled_transfers",
        "send_held_transfer",
    ]
    for name in names:
        assert entries[name]["description_zh"] and entries[name]["description_en"], name
    assert entries["schedule_transfer"]["tier"]["kind"] == "rule"
    assert entries["schedule_transfer"]["reversibility"] == {
        "kind": "reversed_by",
        "operation": "cancel_scheduled_transfer",
    }
    assert entries["schedule_transfer"]["counts_toward_daily_transfer_total"] is False
    assert entries["cancel_scheduled_transfer"]["tier"]["kind"] == "rule"
    assert entries["cancel_scheduled_transfer"]["reversibility"] == {"kind": "irreversible"}
    assert entries["list_scheduled_transfers"]["tier"] == {"kind": "fixed", "tier": "green"}
    assert entries["list_scheduled_transfers"]["reversibility"] == {"kind": "read_only"}
    assert entries["send_held_transfer"]["reversibility"] == {"kind": "irreversible"}
    assert entries["send_held_transfer"]["counts_toward_daily_transfer_total"] is True


def test_reset_clears_scheduled_transfers(client: TestClient) -> None:
    headers = login(client, "liming")
    schedule(client, headers, scheduled("300.00"))

    admin(client, "post", "/admin/reset")

    assert listed(client, login(client, "liming")) == {}
