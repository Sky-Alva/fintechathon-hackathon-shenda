"""Bill Analysis + Subscriptions (#16–#19): transactions, reports, Unusual Transactions,
Recurring Charges, Subscriptions and cancelling them."""

from decimal import Decimal
from typing import Any

import httpx
from fastapi.testclient import TestClient

from conftest import admin, call, confirm, login, request_confirmations

CLOUDMUSIC = "SUB-LM-001"  # music; price went up from CNY 18 to CNY 25
TUNEBOX = "SUB-LM-002"  # music
STREAMPLUS = "SUB-LM-003"  # video; trial converted to paid

SPENDING_OPERATIONS = [
    "list_transactions", "detect_unusual_transactions", "detect_recurring_charges",
    "list_subscriptions", "renewal_reminders", "spending_by_category", "monthly_report",
    "annual_report",
]


def output(response: httpx.Response) -> Any:
    assert response.status_code == 200, response.text
    return response.json()["output"]


def refusal(response: httpx.Response) -> Any:
    return response.json()["refusal"]


def transactions(client: TestClient, headers: dict[str, str], **filters: Any) -> list[Any]:
    found: list[Any] = output(call(client, headers, "list_transactions", {"limit": 100, **filters}))[
        "transactions"
    ]
    return found


def subscriptions(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    listed = output(call(client, headers, "list_subscriptions"))["subscriptions"]
    return {s["subscription_id"]: s for s in listed}


def reminded(client: TestClient, headers: dict[str, str], days_ahead: int = 7) -> list[str]:
    reminders = output(call(client, headers, "renewal_reminders", {"days_ahead": days_ahead}))[
        "reminders"
    ]
    return [r["subscription"]["subscription_id"] for r in reminders]


def cancel(client: TestClient, headers: dict[str, str], subscription_id: str) -> httpx.Response:
    input = {"subscription_id": subscription_id}
    return call(client, headers, "cancel_subscription", input,
                confirm(client, headers, "cancel_subscription", input))


# --- Transactions and reports (#16) -------------------------------------------------


def test_spending_queries_are_green_with_bilingual_descriptions(client: TestClient) -> None:
    headers = login(client, "liming")
    catalogue = {o["name"]: o for o in client.get("/operations", headers=headers).json()["operations"]}

    for name in SPENDING_OPERATIONS:
        assert catalogue[name]["tier"] == {"kind": "fixed", "tier": "green"}, name
        assert catalogue[name]["description_zh"] and catalogue[name]["description_en"], name
        assert call(client, headers, name, {"year": 2026, "month": 10}
                    if name == "monthly_report" else {"year": 2026}
                    if name == "annual_report" else {}).json()["tier"] == "green"


def test_seed_covers_six_months_of_everyday_spending(client: TestClient) -> None:
    found = transactions(client, login(client, "liming"), start_date="2026-04-01")
    months = {t["occurred_at"][:7] for t in found}
    categories = {t["category"] for t in found}

    assert months == {f"2026-{m:02d}" for m in range(4, 11)}
    assert {"income", "housing", "food_delivery", "transport", "subscriptions"} <= categories


def test_last_years_birthday_gift_is_in_the_history(client: TestClient) -> None:
    gifts = transactions(client, login(client, "liming"), category="gifts")

    assert [(g["occurred_at"][:10], g["merchant"]) for g in gifts] == [
        ("2025-10-26", "Bloom & Co.")
    ]


def test_transactions_filter_by_date_range_category_and_merchant(client: TestClient) -> None:
    found = transactions(client, login(client, "liming"), start_date="2026-06-01",
                         end_date="2026-07-31", category="transport", merchant="didi")

    assert [(t["occurred_at"][:10], t["amount"]) for t in found] == [
        ("2026-07-20", "38.00"), ("2026-06-20", "168.00"),
    ]


def test_category_summary_totals_match_the_transactions(client: TestClient) -> None:
    headers = login(client, "liming")
    period = {"start_date": "2026-09-01", "end_date": "2026-10-31"}
    summary = output(call(client, headers, "spending_by_category", period))
    debits = [t for t in transactions(client, headers, **period) if t["direction"] == "debit"]

    for row in summary["categories"]:
        matching = [Decimal(t["amount"]) for t in debits if t["category"] == row["category"]]
        assert Decimal(row["amount"]) == sum(matching), row["category"]
        assert row["transaction_count"] == len(matching), row["category"]
    assert Decimal(summary["total_spending"]) == sum(Decimal(t["amount"]) for t in debits)


def test_monthly_report_is_a_chart_ready_series(client: TestClient) -> None:
    report = output(call(client, login(client, "liming"), "monthly_report",
                         {"year": 2026, "month": 9}))

    assert report["labels"] == ["2026-09"]
    assert report["total_income"] == "18500.00"
    assert Decimal(report["net"]) == (
        Decimal(report["total_income"]) - Decimal(report["total_spending"])
    )
    assert all(len(s["values"]) == 1 for s in report["series"])
    assert {"category": "housing", "values": ["4200.00"]} in report["series"]


def test_annual_report_has_twelve_zero_filled_months(client: TestClient) -> None:
    report = output(call(client, login(client, "liming"), "annual_report", {"year": 2026}))

    assert report["labels"] == [f"2026-{m:02d}" for m in range(1, 13)]
    assert report["monthly_spending"][0] == "0.00"
    assert report["monthly_income"][3:10] == ["18500.00"] * 7
    assert all(len(s["values"]) == 12 for s in report["series"])


def test_a_customer_sees_only_their_own_transactions(client: TestClient) -> None:
    headers = login(client, "hanmei")

    assert transactions(client, headers) == []
    response = call(client, headers, "list_transactions", {"account_id": "ACC-LM-001"})
    assert response.status_code == 403
    assert refusal(response)["reason"] == "forbidden_other_customer"


# --- Unusual Transactions (#17) -----------------------------------------------------


def test_planted_unusual_transactions_are_flagged_each_with_its_rule(client: TestClient) -> None:
    flags = output(call(client, login(client, "liming"), "detect_unusual_transactions"))["flags"]

    assert [(f["transaction"]["merchant"], f["rule_id"]) for f in flags] == [
        ("HomePro Electronics", "amount_far_above_category"),
        ("QuickBite 24", "unfamiliar_merchant_unusual_hour"),
        ("Tokyo Duty Free", "new_overseas_location"),
    ]
    assert all(f["rule"] for f in flags)


def test_ordinary_months_have_no_unusual_transactions(client: TestClient) -> None:
    flags = output(call(client, login(client, "liming"), "detect_unusual_transactions",
                        {"start_date": "2026-04-01", "end_date": "2026-09-30"}))["flags"]

    assert flags == []


# --- Recurring Charges and Subscriptions (#18) --------------------------------------


def test_recurring_charges_are_detected_and_one_off_charges_are_not(client: TestClient) -> None:
    charges = output(call(client, login(client, "liming"), "detect_recurring_charges"))["charges"]
    merchants = {c["merchant"] for c in charges}

    assert {"CloudMusic", "TuneBox", "StreamPlus", "Evergreen Residence"} <= merchants
    assert not merchants & {"Bloom & Co.", "HomePro Electronics", "QuickBite 24",
                            "Tokyo Duty Free"}
    assert all(c["occurrence_count"] >= 3 for c in charges)


def test_frequent_purchases_at_irregular_intervals_are_not_recurring_charges(
    client: TestClient,
) -> None:
    charges = output(call(client, login(client, "liming"), "detect_recurring_charges"))["charges"]

    assert not {c["merchant"] for c in charges} & {"Meituan", "Ele.me"}


def test_subscriptions_show_their_next_renewal_date(client: TestClient) -> None:
    found = subscriptions(client, login(client, "liming"))

    assert {k: s["next_renewal_date"] for k, s in found.items()} == {
        CLOUDMUSIC: "2026-11-03", TUNEBOX: "2026-11-04", STREAMPLUS: "2026-11-05",
    }


def test_subscriptions_that_may_not_be_needed_name_their_reasons(client: TestClient) -> None:
    found = subscriptions(client, login(client, "liming"))

    def reasons(subscription_id: str) -> list[str]:
        return [r["reason"] for r in found[subscription_id]["reasons_it_may_not_be_needed"]]

    assert reasons(CLOUDMUSIC) == ["same_category", "price_increase"]
    assert reasons(TUNEBOX) == ["same_category"]
    assert reasons(STREAMPLUS) == ["trial_converted"]


def test_advancing_the_bank_clock_brings_renewals_into_reminders(client: TestClient) -> None:
    headers = login(client, "liming")
    assert reminded(client, headers) == []

    admin(client, "put", "/admin/clock", json={"now": "2026-11-01T09:00:00+08:00"})
    assert reminded(client, headers) == [CLOUDMUSIC, TUNEBOX, STREAMPLUS]
    assert reminded(client, headers, days_ahead=2) == [CLOUDMUSIC]


# --- Cancelling a Subscription (#19) -------------------------------------------------


def test_cancelling_without_confirmation_is_refused(client: TestClient) -> None:
    headers = login(client, "liming")
    response = call(client, headers, "cancel_subscription", {"subscription_id": TUNEBOX})

    assert response.status_code == 403
    assert refusal(response)["reason"] == "needs_confirmation"
    assert subscriptions(client, headers)[TUNEBOX]["status"] == "active"


def test_cancelling_is_yellow_and_irreversible(client: TestClient) -> None:
    headers = login(client, "liming")
    catalogue = {o["name"]: o for o in client.get("/operations", headers=headers).json()["operations"]}

    assert catalogue["cancel_subscription"]["tier"] == {"kind": "fixed", "tier": "yellow"}
    assert catalogue["cancel_subscription"]["reversibility"]["kind"] == "irreversible"


def test_a_cancelled_subscription_drops_out_of_reminders_and_flags(client: TestClient) -> None:
    headers = login(client, "liming")
    admin(client, "put", "/admin/clock", json={"now": "2026-11-01T09:00:00+08:00"})

    assert output(cancel(client, headers, TUNEBOX))["status"] == "cancelled"

    found = subscriptions(client, headers)
    assert found[TUNEBOX]["status"] == "cancelled"
    assert found[TUNEBOX]["next_renewal_date"] is None
    assert found[TUNEBOX]["reasons_it_may_not_be_needed"] == []
    assert list(found) == [CLOUDMUSIC, STREAMPLUS, TUNEBOX]  # cancelled ones listed last
    # With TuneBox gone, CloudMusic is no longer one of two music services.
    assert [r["reason"] for r in found[CLOUDMUSIC]["reasons_it_may_not_be_needed"]] == [
        "price_increase"
    ]
    assert reminded(client, headers) == [CLOUDMUSIC, STREAMPLUS]


def test_several_cancellations_are_confirmed_together(client: TestClient) -> None:
    headers = login(client, "liming")
    response = request_confirmations(
        client, headers,
        ("cancel_subscription", {"subscription_id": TUNEBOX}),
        ("cancel_subscription", {"subscription_id": STREAMPLUS}),
    )
    tokens = [c["confirmation_token"] for c in response.json()["confirmations"]]

    for subscription_id, token in zip([TUNEBOX, STREAMPLUS], tokens):
        cancelled = call(client, headers, "cancel_subscription",
                         {"subscription_id": subscription_id}, token)
        assert cancelled.status_code == 200, cancelled.text
    found = subscriptions(client, headers)
    assert [found[s]["status"] for s in (CLOUDMUSIC, TUNEBOX, STREAMPLUS)] == [
        "active", "cancelled", "cancelled",
    ]


def test_a_confirmation_token_covers_only_the_subscription_it_was_issued_for(
    client: TestClient,
) -> None:
    headers = login(client, "liming")
    token = confirm(client, headers, "cancel_subscription", {"subscription_id": TUNEBOX})
    response = call(client, headers, "cancel_subscription", {"subscription_id": CLOUDMUSIC}, token)

    assert response.status_code == 403
    assert refusal(response)["reason"] == "token_invalid"
    assert subscriptions(client, headers)[CLOUDMUSIC]["status"] == "active"


def test_cancelling_an_already_cancelled_subscription_is_refused(client: TestClient) -> None:
    headers = login(client, "liming")
    cancel(client, headers, TUNEBOX)
    response = cancel(client, headers, TUNEBOX)

    assert refusal(response)["reason"] == "not_found"


def test_a_customer_cannot_cancel_another_customers_subscription(client: TestClient) -> None:
    response = call(client, login(client, "hanmei"), "cancel_subscription",
                    {"subscription_id": TUNEBOX})

    assert response.status_code == 403
    assert refusal(response)["reason"] == "forbidden_other_customer"
    assert subscriptions(client, login(client, "liming"))[TUNEBOX]["status"] == "active"
