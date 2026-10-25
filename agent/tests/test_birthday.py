"""The birthday showcase (#48): the Agent notices the spouse's birthday is coming, from the
Customer's profile and last year's gift purchase, and proposes one Plan: reserve ¥1,000,
then order flowers and a cake for two days before the birthday, paid from the Reserved
Funds. The Customer approves it once; the orders are paid when the Bank clock reaches
their date, with no further approval, and the Channel is told."""

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from agent.model import ModelReply, ToolCall
from conftest import (
    ADMIN_KEY,
    act,
    admin,
    bank_audit,
    event_types,
    log_in,
    send,
    start_conversation,
    updates,
)
from evaluation import load_task, run_task
from fake_model import FakeModel, say, step

SHORTLY_BEFORE = "2026-10-24T10:00:00+08:00"  # 韩梅梅's birthday is 10-28
TWO_DAYS_BEFORE = "2026-10-26T09:00:00+08:00"
ASKED = "最近有什么需要我安排的吗？"

LAST_YEAR = {"category": "gifts", "start_date": "2025-10-01", "end_date": "2025-11-30"}
GIFT = {"amount": "1000.00", "purpose": "韩梅梅生日礼物", "merchant_categories": ["flowers", "cake"]}
ROSES = {
    "merchant_id": "MER-FLOWER-001",
    "product_id": "PRD-FLOWER-001",
    "quantity": 1,
    "amount": "299.00",
    "delivery_date": "2026-10-26",
}
CAKE = {
    "merchant_id": "MER-CAKE-001",
    "product_id": "PRD-CAKE-002",
    "quantity": 1,
    "amount": "388.00",
    "delivery_date": "2026-10-26",
}
NOTICED = "韩梅梅的生日是 10月28日；去年 2025-10-26 您在 Bloom & Co. 为她买过生日鲜花（¥268.00）。"


def plan_with_note(*steps: dict[str, Any], note: str | None = None) -> ModelReply:
    arguments: dict[str, Any] = {"intent": "为韩梅梅的生日做准备", "steps": list(steps)}
    if note is not None:
        arguments["note"] = note
    return ModelReply(
        tool_calls=[ToolCall(id="call-plan", name="propose_plan", arguments=arguments)]
    )


def birthday_plan(note: str | None = NOTICED) -> list[ModelReply]:
    """The model looks up the profile, last year's gifts and the Merchants, then, shown
    what the Bank returned, plans the reservation and both orders with exact inputs."""
    return [
        plan_with_note(
            step("s1", "query_profile"),
            step("s2", "list_transactions", LAST_YEAR),
            step("s3", "list_merchants"),
            step("s4", "reserve_funds", depends_on=["s1", "s2"]),
            step("s5", "order_from_merchant", depends_on=["s3", "s4"]),
            step("s6", "order_from_merchant", depends_on=["s3", "s4"]),
        ),
        plan_with_note(
            step("s4", "reserve_funds", GIFT, depends_on=["s1", "s2"]),
            step("s5", "order_from_merchant", ROSES, depends_on=["s3", "s4"]),
            step("s6", "order_from_merchant", CAKE, depends_on=["s3", "s4"]),
            note=note,
        ),
    ]


def shown(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event["type"] != "trace"]


def set_clock(bank: TestClient, now: str) -> None:
    admin(bank, "put", "/admin/clock", json={"now": now})


# --- The Agent brings up the birthday, from Bank results -----------------------------


def test_the_agent_brings_up_the_birthday_citing_the_profile_and_last_years_gift(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    set_clock(bank, SHORTLY_BEFORE)
    model.script(*birthday_plan())
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = shown(send(agent, conversation, ASKED))

    assert event_types(events) == [
        "step_started", "step_succeeded",  # the profile
        "step_started", "step_succeeded",  # last year's gifts
        "step_started", "step_succeeded",  # the Merchants
        "answer", "plan_proposed", "confirmation_requested",
    ]
    profile, gifts = events[1]["output"], events[3]["output"]
    assert profile["spouse"] == {"name": "韩梅梅", "birthday": "1994-10-28"}
    assert [(t["occurred_at"][:10], t["amount"]) for t in gifts["transactions"]] == [
        ("2025-10-26", "268.00")
    ]
    assert events[6]["text"] == NOTICED


def test_the_planner_knows_the_banks_date(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    set_clock(bank, SHORTLY_BEFORE)
    model.script(*birthday_plan())

    send(agent, start_conversation(agent, log_in(bank, "liming")), ASKED)

    assert "2026-10-24" in model.requests[0].text()


def test_a_note_stating_what_no_bank_result_says_is_not_shown(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    set_clock(bank, SHORTLY_BEFORE)
    model.script(*birthday_plan(note="韩梅梅的生日是 11月2日，去年您花了 ¥520.00 买礼物。"))
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = shown(send(agent, conversation, ASKED))

    assert "answer" not in event_types(events)
    assert event_types(events)[-2:] == ["plan_proposed", "confirmation_requested"]


# --- One Plan card, one tap -----------------------------------------------------------


def test_one_plan_card_covers_the_reservation_and_both_orders_each_with_its_tier(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    set_clock(bank, SHORTLY_BEFORE)
    model.script(*birthday_plan())
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = shown(send(agent, conversation, ASKED))

    proposed, requested = events[-2], events[-1]
    card = [(s["id"], s["operation"], s["input"].get("amount"), s["tier"]) for s in proposed["steps"]]
    assert card == [
        ("s4", "reserve_funds", "1000.00", "yellow"),
        ("s5", "order_from_merchant", "299.00", "yellow"),
        ("s6", "order_from_merchant", "388.00", "yellow"),
    ]
    assert requested["step_ids"] == ["s4", "s5", "s6"]


def confirmed(agent: TestClient, bank: TestClient, model: FakeModel) -> tuple[str, Any]:
    """The Customer approves the birthday Plan with one tap."""
    set_clock(bank, SHORTLY_BEFORE)
    model.script(*birthday_plan(), say("已预留 1000.00 元，并下单 299.00 元的鲜花和 388.00 元的蛋糕。"))
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    plan_id = shown(send(agent, conversation, ASKED))[-1]["plan_id"]
    events = shown(act(agent, conversation, "confirm", plan_id=plan_id))
    return conversation, (login, events)


def test_one_tap_reserves_the_money_and_places_both_orders_paid_from_it(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    _, (login, events) = confirmed(agent, bank, model)

    assert event_types(events) == [
        "step_started", "step_succeeded",
        "step_started", "step_succeeded",
        "step_started", "step_succeeded",
        "answer",
    ]
    reservation = events[1]["output"]["reservation"]["reservation_id"]
    orders = [events[3]["output"]["order"], events[5]["output"]["order"]]
    assert [(o["reservation_id"], o["status"]) for o in orders] == [
        (reservation, "pending"),
        (reservation, "pending"),
    ]
    issued = [e["operation"] for e in bank_audit(bank, login) if e["event"] == "confirmation_issued"]
    assert issued == ["reserve_funds", "order_from_merchant", "order_from_merchant"]


# --- Paid on the Bank clock, with no further approval ---------------------------------


def test_nothing_is_paid_or_told_before_the_order_date(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    conversation, _ = confirmed(agent, bank, model)
    set_clock(bank, "2026-10-26T08:59:00+08:00")

    assert updates(agent, conversation)["events"] == []


def test_moving_the_clock_to_the_order_date_pays_the_orders_and_the_channel_is_told(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    conversation, (login, _) = confirmed(agent, bank, model)
    asked_before = len(model.requests)

    set_clock(bank, TWO_DAYS_BEFORE)
    first = updates(agent, conversation)

    told = [e for e in first["events"] if e["type"] != "trace"]
    assert [e["type"] for e in told] == ["answer"]
    assert "红玫瑰花束" in told[0]["text"] and "299.00" in told[0]["text"]
    assert "生日蛋糕 8寸" in told[0]["text"] and "388.00" in told[0]["text"]
    assert updates(agent, conversation, first["next"])["events"] == []  # told once
    assert len(model.requests) == asked_before  # written by the Agent, not the model
    audit = bank_audit(bank, login)
    paid = [e for e in audit if e["operation"] == "pay_merchant_order" and e["outcome"] == "ok"]
    assert [e["at"] for e in paid] == [TWO_DAYS_BEFORE, TWO_DAYS_BEFORE]
    issued = [e["operation"] for e in audit if e["event"] == "confirmation_issued"]
    assert issued == ["reserve_funds", "order_from_merchant", "order_from_merchant"]  # no more


def test_an_order_that_could_not_be_paid_is_told_with_the_banks_reason(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    conversation, (login, events) = confirmed(agent, bank, model)
    reservation = events[1]["output"]["reservation"]["reservation_id"]
    released = {"reservation_id": reservation}
    headers = {"Authorization": f"Bearer {login.session_token}"}
    token = bank.post(
        "/confirmations",
        headers=headers,
        json={"operations": [{"operation": "release_reserved_funds", "input": released}]},
    ).json()["confirmations"][0]["confirmation_token"]
    bank.post(
        "/operations/release_reserved_funds",
        headers=headers,
        json={"input": released, "confirmation_token": token},
    )

    set_clock(bank, TWO_DAYS_BEFORE)

    [told] = [e for e in updates(agent, conversation)["events"] if e["type"] != "trace"]
    assert told["type"] == "answer"
    assert "未能付款" in told["text"]


# --- The scripted test tasks, replayed with a model that plans as the real one should ---

TASKS = Path(__file__).resolve().parents[2] / "tasks" / "orchestration"


def replay(name: str, agent: TestClient, bank: TestClient) -> list[str]:
    result = run_task(load_task(TASKS / name), agent=agent, bank=bank, admin_key=ADMIN_KEY)
    return result.failures


def test_the_birthday_task_passes(agent: TestClient, bank: TestClient, model: FakeModel) -> None:
    model.script(*birthday_plan(), say("已预留 1000.00 元，并下单鲜花和蛋糕。"))

    assert replay("orchestration-001.yaml", agent, bank) == []


def test_the_task_spending_the_reserved_funds_elsewhere_passes(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        *birthday_plan()[1:],  # asked directly: no look-ups
        say("已预留 1000.00 元，并下单鲜花和蛋糕。"),
        plan_with_note(step("s1", "transfer", {"payee_id": "PAY-LM-005", "amount": "1000.00"})),
        say("转账未能执行：账户可用余额不足。"),
    )

    assert replay("orchestration-002.yaml", agent, bank) == []


def test_the_rejected_birthday_task_passes(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(*birthday_plan())

    assert replay("orchestration-003.yaml", agent, bank) == []
