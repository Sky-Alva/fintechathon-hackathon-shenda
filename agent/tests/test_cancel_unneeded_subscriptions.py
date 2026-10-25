"""The Cross-scenario Orchestration showcase (#41): "cancel the memberships I don't use
this month". The Agent lists the Subscriptions the Bank flags as possibly unneeded, each
with the Bank's reasons; the Customer picks which to cancel; one Plan card cancels exactly
those, confirmed with one tap; the answer says what the Bank shows as cancelled.

Seed (李明): CloudMusic (SUB-LM-001: same category as TuneBox, price up since sign-up),
TuneBox (SUB-LM-002: same category as CloudMusic) and StreamPlus (SUB-LM-003: a free
trial that converted to paid).
"""

from typing import Any

from fastapi.testclient import TestClient

from conftest import (
    Login,
    act,
    bank_audit,
    event_types,
    log_in,
    send,
    start_conversation,
    take_over,
)
from fake_model import FakeModel, offer_cancellations, plan, step

REQUEST = "这个月帮我把用不上的会员都退了"


def shown(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event["type"] != "trace"]


def offer(agent: TestClient, bank: TestClient, model: FakeModel) -> tuple[str, dict[str, Any]]:
    """李明 asks; the conversation and its `cancellation_offered`."""
    model.script(offer_cancellations())
    conversation = start_conversation(agent, log_in(bank, "liming"))
    offered = shown(send(agent, conversation, REQUEST))[-1]
    assert offered["type"] == "cancellation_offered"
    return conversation, offered


# --- The candidates are exactly the Bank's flags -------------------------------------


def test_the_agent_lists_the_subscriptions_the_bank_flags_with_the_banks_reasons(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(offer_cancellations())
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = shown(send(agent, conversation, REQUEST))

    assert event_types(events) == ["step_started", "step_succeeded", "cancellation_offered"]
    assert events[0]["step"]["operation"] == "list_subscriptions"
    offered = events[-1]
    assert [
        (c["subscription_id"], [r["reason"] for r in c["reasons_it_may_not_be_needed"]])
        for c in offered["candidates"]
    ] == [
        ("SUB-LM-001", ["same_category", "price_increase"]),
        ("SUB-LM-002", ["same_category"]),
        ("SUB-LM-003", ["trial_converted"]),
    ]
    # Each candidate is the Bank's Subscription, unchanged.
    listed = events[1]["output"]["subscriptions"]
    assert offered["candidates"] == [
        s for s in listed if s["subscription_id"] in {"SUB-LM-001", "SUB-LM-002", "SUB-LM-003"}
    ]


def test_the_offer_names_each_candidate_with_the_banks_reasons_in_the_customers_words(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    _, offered = offer(agent, bank, model)

    assert offered["text"] == (
        "银行标记了 3 个可能用不上的订阅：\n"
        "1. CloudMusic（¥25.00）：与 TuneBox 属于同一类别；价格比开通时上涨（¥18.00 → ¥25.00）\n"
        "2. TuneBox（¥22.00）：与 CloudMusic 属于同一类别\n"
        "3. StreamPlus（¥25.00）：免费试用已转为付费\n"
        "请选择要取消的订阅。选好后，我会把它们放在一张计划卡片上，您确认一次即可。"
    )


def test_listing_the_candidates_asks_the_bank_for_no_confirmation(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(offer_cancellations())
    login = log_in(bank, "liming")

    send(agent, start_conversation(agent, login), REQUEST)

    assert [(e["event"], e["operation"]) for e in bank_audit(bank, login)] == [
        ("tier_preview", None),
        ("operation", "list_subscriptions"),
    ]


# --- The Customer picks; one Plan card cancels exactly those -------------------------


def pick(
    agent: TestClient, conversation: str, offered: dict[str, Any], *subscription_ids: str
) -> list[dict[str, Any]]:
    return shown(
        act(
            agent, conversation, "cancel_subscriptions",
            offer_id=offered["offer_id"], subscription_ids=list(subscription_ids),
        )
    )


def test_picking_two_of_three_yields_one_plan_card_with_exactly_those_two_cancellations(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    conversation, offered = offer(agent, bank, model)

    events = pick(agent, conversation, offered, "SUB-LM-002", "SUB-LM-003")

    assert event_types(events) == ["plan_proposed", "confirmation_requested"]
    proposed, requested = events
    assert [(s["operation"], s["input"], s["tier"]) for s in proposed["steps"]] == [
        ("cancel_subscription", {"subscription_id": "SUB-LM-002"}, "yellow"),
        ("cancel_subscription", {"subscription_id": "SUB-LM-003"}, "yellow"),
        ("list_subscriptions", {}, "green"),
    ]
    cancels = [s["id"] for s in proposed["steps"] if s["operation"] == "cancel_subscription"]
    assert requested == {
        "type": "confirmation_requested", "plan_id": proposed["plan_id"], "step_ids": cancels
    }
    # The read-back of what the Bank shows runs after both cancellations.
    assert set(proposed["steps"][-1]["depends_on"]) == set(cancels)


def statuses(bank: TestClient, login: Login) -> dict[str, str]:
    """What the Bank shows, read with the Customer's own session."""
    response = bank.post(
        "/operations/list_subscriptions",
        headers={"Authorization": f"Bearer {login.session_token}"},
        json={"input": {}},
    )
    assert response.status_code == 200, response.text
    return {s["subscription_id"]: s["status"] for s in response.json()["output"]["subscriptions"]}


def test_one_tap_cancels_exactly_the_picked_subscriptions_and_the_answer_says_what_the_bank_shows(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(offer_cancellations())
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    offered = shown(send(agent, conversation, REQUEST))[-1]
    plan_id = pick(agent, conversation, offered, "SUB-LM-002", "SUB-LM-003")[0]["plan_id"]

    events = shown(act(agent, conversation, "confirm", plan_id=plan_id))

    assert event_types(events) == [
        "step_started", "step_succeeded", "step_started", "step_succeeded",
        "step_started", "step_succeeded", "answer",
    ]
    assert statuses(bank, login) == {
        "SUB-LM-001": "active", "SUB-LM-002": "cancelled", "SUB-LM-003": "cancelled"
    }
    # The Agent writes the answer from the Bank's read-back, not the model (none scripted).
    assert events[-1]["text"] == "银行显示已取消：TuneBox、StreamPlus。仍在生效：CloudMusic。"
    cancelled = [
        e["input"]["subscription_id"]
        for e in bank_audit(bank, login)
        if e["event"] == "operation" and e["operation"] == "cancel_subscription"
    ]
    assert cancelled == ["SUB-LM-002", "SUB-LM-003"]


# --- Nothing the Customer didn't pick is cancelled ------------------------------------


def test_a_subscription_the_offer_does_not_list_cannot_be_picked(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    login = log_in(bank, "liming")
    model.script(offer_cancellations())
    conversation = start_conversation(agent, login)
    offered = shown(send(agent, conversation, REQUEST))[-1]

    response = agent.post(
        f"/conversations/{conversation}/actions",
        json={
            "action": "cancel_subscriptions",
            "offer_id": offered["offer_id"],
            "subscription_ids": ["SUB-LM-002", "SUB-HM-001"],
        },
    )

    assert (response.status_code, response.json()) == (422, {"error": "unknown_step"})
    assert [e["event"] for e in bank_audit(bank, login)] == ["tier_preview", "operation"]
    # The offer still waits for a pick.
    assert event_types(pick(agent, conversation, offered, "SUB-LM-002"))[-1] == (
        "confirmation_requested"
    )


def test_removing_a_pick_from_the_plan_card_leaves_it_active(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(offer_cancellations())
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    offered = shown(send(agent, conversation, REQUEST))[-1]
    proposed = pick(agent, conversation, offered, "SUB-LM-002", "SUB-LM-003")[0]
    streamplus = next(
        s["id"] for s in proposed["steps"] if s["input"].get("subscription_id") == "SUB-LM-003"
    )

    events = shown(
        act(
            agent, conversation, "confirm",
            plan_id=proposed["plan_id"], removed_step_ids=[streamplus],
        )
    )

    assert statuses(bank, login) == {
        "SUB-LM-001": "active", "SUB-LM-002": "cancelled", "SUB-LM-003": "active"
    }
    assert events[-1]["text"] == "银行显示已取消：TuneBox。仍在生效：CloudMusic、StreamPlus。"


def test_picking_none_cancels_nothing(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(offer_cancellations())
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    offered = shown(send(agent, conversation, REQUEST))[-1]

    events = pick(agent, conversation, offered)

    assert events == [{"type": "answer", "text": "好的，一个订阅都不取消，没有执行任何操作。"}]
    assert [e["event"] for e in bank_audit(bank, login)] == ["tier_preview", "operation"]


def test_an_offer_is_answered_once(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    conversation, offered = offer(agent, bank, model)
    pick(agent, conversation, offered, "SUB-LM-002")

    response = agent.post(
        f"/conversations/{conversation}/actions",
        json={
            "action": "cancel_subscriptions",
            "offer_id": offered["offer_id"],
            "subscription_ids": ["SUB-LM-001"],
        },
    )

    assert (response.status_code, response.json()) == (409, {"error": "plan_not_pending"})


def test_a_pick_in_words_is_planned_like_any_request_and_drops_the_offer(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    conversation, offered = offer(agent, bank, model)
    model.script(plan(step("s1", "cancel_subscription", {"subscription_id": "SUB-LM-003"})))

    events = shown(send(agent, conversation, "取消 StreamPlus 就行"))

    assert event_types(events) == ["plan_proposed", "confirmation_requested"]
    # The Planner saw the offer, as the Customer did.
    assert "StreamPlus（¥25.00）：免费试用已转为付费" in model.requests[-1].text()
    response = agent.post(
        f"/conversations/{conversation}/actions",
        json={
            "action": "cancel_subscriptions",
            "offer_id": offered["offer_id"],
            "subscription_ids": ["SUB-LM-001"],
        },
    )
    assert response.status_code == 409


def test_no_pick_is_acted_on_while_a_staff_member_has_the_conversation(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    conversation, offered = offer(agent, bank, model)
    take_over(agent, conversation)

    response = agent.post(
        f"/conversations/{conversation}/actions",
        json={
            "action": "cancel_subscriptions",
            "offer_id": offered["offer_id"],
            "subscription_ids": ["SUB-LM-001"],
        },
    )

    assert (response.status_code, response.json()["error"]) == (409, "already_handed_over")


# --- Edges ---------------------------------------------------------------------------


def test_with_no_flagged_subscription_the_agent_says_so_and_offers_nothing(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(offer_cancellations())
    conversation = start_conversation(agent, log_in(bank, "hanmei"))  # no Subscriptions

    events = shown(send(agent, conversation, REQUEST))

    assert event_types(events) == ["step_started", "step_succeeded", "answer"]
    assert events[-1]["text"] == "银行没有标记可能用不上的订阅，没有需要取消的。"


def test_in_english_the_offer_and_the_report_are_in_english(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(offer_cancellations())
    conversation = start_conversation(agent, log_in(bank, "liming"))
    offered = shown(send(agent, conversation, "Cancel the memberships I don't use"))[-1]
    plan_id = pick(agent, conversation, offered, "SUB-LM-003")[0]["plan_id"]

    answer = shown(act(agent, conversation, "confirm", plan_id=plan_id))[-1]

    assert offered["text"].splitlines() == [
        "The Bank flags 3 Subscriptions you may not need:",
        "1. CloudMusic (¥25.00): same category as TuneBox; price up since sign-up"
        " (¥18.00 → ¥25.00)",
        "2. TuneBox (¥22.00): same category as CloudMusic",
        "3. StreamPlus (¥25.00): a free trial that converted to paid",
        "Pick the ones to cancel: they'll go on one Plan card for you to confirm with one tap.",
    ]
    assert answer["text"] == (
        "The Bank shows as cancelled: StreamPlus. Still active: CloudMusic, TuneBox."
    )


def test_the_planner_is_offered_the_cancellation_offer(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    offer(agent, bank, model)

    tools = [tool["function"]["name"] for tool in model.requests[0].tools or []]
    assert "offer_cancellations" in tools
