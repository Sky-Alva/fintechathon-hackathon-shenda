"""Transfers in the transaction history (#58): every transfer the Bank completes is a debit
for the sender and, when the Payee banks here, a credit for them, under the `transfer`
category, which is not spending (docs/adr/0002)."""

from typing import Any

import httpx
from fastapi.testclient import TestClient

from conftest import admin, call, confirm, login, login_with_phone, verify

HUSBAND = "PAY-HM-001"  # 韩梅梅's Payee 李明, who banks with the Bank (ACC-LM-001)
MUM = "PAY-LM-002"  # 李明's Payee 王秀英, who banks elsewhere
NEVER_PAID = "PAY-LM-005"  # 陈静
LANDLORD = "PAY-LM-007"  # 赵建国, paid ¥800 rent on 1 September and 1 October


def output(response: httpx.Response) -> Any:
    assert response.status_code == 200, response.text
    return response.json()["output"]


def transactions(client: TestClient, headers: dict[str, str], **filters: Any) -> list[Any]:
    found: list[Any] = output(
        call(client, headers, "list_transactions", {"limit": 100, **filters})
    )["transactions"]
    return found


def send_household_money(client: TestClient) -> dict[str, str]:
    """韩梅梅 sends 李明 ¥2,000 with the note 家用 (Red: over ¥1,000 today)."""
    headers, phone = login_with_phone(client, "hanmei")
    transfer = {"payee_id": HUSBAND, "amount": "2000.00", "note": "家用"}
    token = verify(client, headers, phone, "transfer", transfer)
    response = call(client, headers, "transfer", transfer, verification_token=token)
    assert response.status_code == 200, response.text
    return headers


def test_a_transfer_is_found_in_the_senders_history_by_its_note(client: TestClient) -> None:
    headers = send_household_money(client)

    found = transactions(client, headers, description="家用", start_date="2026-10-20")

    assert len(found) == 1
    assert found[0] | {"transaction_id": None} == {
        "transaction_id": None,
        "account_id": "ACC-HM-001",
        "occurred_at": "2026-10-20T09:00:00+08:00",
        "category": "transfer",
        "merchant": "李明",
        "description": "家用",
        "direction": "debit",
        "amount": "2000.00",
        "location": "Shenzhen, CN",
    }


def test_the_payee_who_banks_here_sees_the_transfer_as_a_credit(client: TestClient) -> None:
    send_household_money(client)

    found = transactions(client, login(client, "liming"), description="家用",
                         start_date="2026-10-20")

    assert [(t["account_id"], t["merchant"], t["direction"], t["amount"], t["category"])
            for t in found] == [("ACC-LM-001", "韩梅梅", "credit", "2000.00", "transfer")]


def test_a_refused_transfer_leaves_no_history(client: TestClient) -> None:
    headers = login(client, "liming")  # ¥1,180.00 in the current account
    transfer = {"payee_id": MUM, "amount": "5000.00", "note": "孝敬妈妈"}

    refused = call(client, headers, "transfer", transfer)

    assert refused.status_code != 200
    assert transactions(client, headers, start_date="2026-10-20") == []


def test_a_scheduled_transfer_is_in_the_history_only_once_it_has_run(client: TestClient) -> None:
    headers = login(client, "liming")
    scheduled = {"payee_id": MUM, "amount": "300.00", "run_date": "2026-10-22", "note": "生活费"}
    response = call(client, headers, "schedule_transfer", scheduled,
                    confirm(client, headers, "schedule_transfer", scheduled))
    assert response.status_code == 200, response.text

    assert transactions(client, headers, start_date="2026-10-20") == []

    admin(client, "post", "/admin/clock/advance", json={"minutes": 2 * 24 * 60})

    assert [(t["occurred_at"], t["merchant"], t["description"], t["direction"], t["amount"])
            for t in transactions(client, headers, start_date="2026-10-20")] == [
        ("2026-10-22T09:00:00+08:00", "王秀英", "生活费", "debit", "300.00")
    ]


def test_past_transfers_are_in_the_history_too(client: TestClient) -> None:
    found = transactions(client, login(client, "liming"), description="房租")

    assert [(t["occurred_at"][:10], t["merchant"], t["amount"]) for t in found] == [
        ("2026-10-01", "赵建国", "800.00"), ("2026-09-01", "赵建国", "800.00"),
    ]


def test_transfers_are_not_spending_or_income(client: TestClient) -> None:
    hanmei = login(client, "hanmei")
    liming = login(client, "liming")

    def reports() -> list[Any]:
        return [
            output(call(client, hanmei, "monthly_report", {"year": 2026, "month": 10})),
            output(call(client, liming, "monthly_report", {"year": 2026, "month": 10})),
            output(call(client, hanmei, "annual_report", {"year": 2026})),
            output(call(client, liming, "annual_report", {"year": 2026})),
            output(call(client, hanmei, "spending_by_category", {"start_date": "2026-10-01"})),
            output(call(client, liming, "spending_by_category", {"start_date": "2026-10-01"})),
        ]

    before = reports()
    send_household_money(client)

    assert reports() == before


def test_spending_by_category_has_no_transfer_category(client: TestClient) -> None:
    response = call(client, login(client, "liming"), "spending_by_category",
                    {"category": "transfer"})

    assert response.status_code == 422


def test_transfers_are_never_unusual_or_recurring(client: TestClient) -> None:
    # A first payment to a never-paid Payee late at night: as spending it would fire the
    # unfamiliar-merchant-at-night rule.
    admin(client, "put", "/admin/clock", json={"now": "2026-10-20T23:30:00+08:00"})
    headers = login(client, "liming")
    transfer = {"payee_id": NEVER_PAID, "amount": "300.00"}
    response = call(client, headers, "transfer", transfer,
                    confirm(client, headers, "transfer", transfer))
    assert response.status_code == 200, response.text

    unusual = output(call(client, headers, "detect_unusual_transactions", {}))["flags"]
    assert [f for f in unusual if f["transaction"]["category"] == "transfer"] == []

    # The landlord's rent: a third monthly transfer of the same amount.
    admin(client, "put", "/admin/clock", json={"now": "2026-11-01T08:30:00+08:00"})
    rent = {"payee_id": LANDLORD, "amount": "800.00", "note": "交房东11月房租"}
    response = call(client, headers, "transfer", rent, confirm(client, headers, "transfer", rent))
    assert response.status_code == 200, response.text

    recurring = output(call(client, headers, "detect_recurring_charges", {}))
    assert [c for c in recurring["charges"] if c["category"] == "transfer"] == []


def test_a_merchant_order_is_gift_spending_once_it_is_paid(client: TestClient) -> None:
    headers = login(client, "liming")
    reserve = {"amount": "500.00", "purpose": "生日礼物", "merchant_categories": ["flowers"]}
    reserved = call(client, headers, "reserve_funds", reserve,
                    confirm(client, headers, "reserve_funds", reserve))
    assert reserved.status_code == 200, reserved.text
    order = {"merchant_id": "MER-FLOWER-001", "product_id": "PRD-FLOWER-001", "quantity": 1,
             "amount": "299.00", "delivery_date": "2026-10-26", "note": "生日快乐"}
    placed = call(client, headers, "order_from_merchant", order,
                  confirm(client, headers, "order_from_merchant", order))
    assert placed.status_code == 200, placed.text

    assert transactions(client, headers, start_date="2026-10-20") == []

    admin(client, "put", "/admin/clock", json={"now": "2026-10-26T09:00:00+08:00"})

    assert [(t["occurred_at"], t["category"], t["merchant"], t["description"], t["direction"],
             t["amount"]) for t in transactions(client, headers, start_date="2026-10-20")] == [
        ("2026-10-26T09:00:00+08:00", "gifts", "花语鲜花店 Petal Florist", "生日快乐",
         "debit", "299.00"),
    ]
    october = output(call(client, headers, "spending_by_category",
                          {"start_date": "2026-10-20", "category": "gifts"}))
    assert october["total_spending"] == "299.00"
