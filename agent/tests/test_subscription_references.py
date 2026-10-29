"""Subscriptions the way the Customer names them: by their merchant ("cancel TuneBox"). The
cancellation takes the Bank's subscription_id from the Bank's list, never one the model
made up, so it never fails as not_found while the Subscription is still active."""

from typing import Any

from fastapi.testclient import TestClient

from conftest import act, bank_audit, event_types, log_in, send, start_conversation
from fake_model import FakeModel, clarify, plan, say, step


def shown(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event["type"] != "trace"]


def cancelled(bank: TestClient, login: Any) -> list[tuple[str, str]]:
    """The Subscription each `cancel_subscription` the Bank received named, with its outcome."""
    return [
        (e["input"].get("subscription_id"), e["outcome"])
        for e in bank_audit(bank, login)
        if e["event"] == "operation" and e["operation"] == "cancel_subscription"
    ]


def test_with_no_subscriptions_listed_yet_they_are_listed_first_and_the_plan_card_names_the_banks_one(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        plan(
            step("s1", "list_subscriptions"),
            step("s2", "cancel_subscription", depends_on=["s1"]),
        ),
        plan(step("s2", "cancel_subscription", {"subscription_id": "SUB-LM-002"})),
    )
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = shown(send(agent, conversation, "cancel subscription of Tune Box"))

    assert "SUB-LM-002" in model.requests[-1].text()  # completed from the Bank's list
    assert event_types(events) == [
        "step_started", "step_succeeded", "plan_proposed", "confirmation_requested"
    ]
    assert events[2]["steps"][0]["input"] == {"subscription_id": "SUB-LM-002"}


def test_a_made_up_subscription_id_never_runs_the_subscriptions_are_listed_instead(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        plan(
            step("s1", "cancel_subscription", {"subscription_id": "SUB-TUNEBOX"}),
            step("s2", "cancel_subscription", {"subscription_id": "SUB-STREAMPLUS"}),
        ),
        plan(
            step("s1", "cancel_subscription", {"subscription_id": "SUB-LM-002"}),
            step("s2", "cancel_subscription", {"subscription_id": "SUB-LM-003"}),
        ),
        say("TuneBox and StreamPlus are cancelled."),
    )
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)

    events = shown(send(agent, conversation, "Cancel both TuneBox and StreamPlus."))

    # No not_found for an active Subscription: the Agent listed them and the Planner
    # completed both steps from the Bank's list.
    assert "The look-ups' results" in model.requests[-1].text()
    assert event_types(events) == [
        "step_started", "step_succeeded", "plan_proposed", "confirmation_requested"
    ]
    assert events[0]["step"]["operation"] == "list_subscriptions"
    assert [s["input"] for s in events[2]["steps"]] == [
        {"subscription_id": "SUB-LM-002"},
        {"subscription_id": "SUB-LM-003"},
    ]
    assert cancelled(bank, login) == []

    ran = shown(act(agent, conversation, "confirm", plan_id=events[2]["plan_id"]))

    assert [e["type"] for e in ran if e["type"].startswith("step_")].count("step_failed") == 0
    assert cancelled(bank, login) == [("SUB-LM-002", "ok"), ("SUB-LM-003", "ok")]


def test_a_subscription_still_made_up_after_the_list_is_asked_about_with_the_banks_ones(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        plan(step("s1", "cancel_subscription", {"subscription_id": "music"})),
        plan(step("s1", "cancel_subscription", {"subscription_id": "music"})),
    )
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)

    events = shown(send(agent, conversation, "cancel that music subscription"))

    # Never guessed: the Customer picks from the Subscriptions the Bank listed, by its IDs.
    assert event_types(events) == ["step_started", "step_succeeded", "clarification_question"]
    assert events[-1]["text"] == "Which Subscription do you mean?"
    listed = events[1]["output"]["subscriptions"]
    assert [option["value"] for option in events[-1]["options"]] == [
        s["subscription_id"] for s in listed
    ]
    tunebox = next(s for s in listed if s["merchant"] == "TuneBox")
    assert events[-1]["options"][1]["label"] == (
        f"TuneBox (¥{tunebox['current_amount']}, renews {tunebox['next_renewal_date']})"
    )
    model.script(plan(step("s1", "cancel_subscription", {"subscription_id": "SUB-LM-002"})))
    chosen = shown(act(agent, conversation, "choose", value="SUB-LM-002"))
    assert event_types(chosen) == ["plan_proposed", "confirmation_requested"]
    assert cancelled(bank, login) == []


def test_which_subscription_is_asked_in_chinese_to_a_customer_writing_chinese(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        plan(step("s1", "cancel_subscription", {"subscription_id": "音乐会员"})),
        plan(step("s1", "cancel_subscription", {"subscription_id": "音乐会员"})),
    )
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = shown(send(agent, conversation, "取消音乐会员"))

    assert events[-1]["text"] == "您指的是哪一个订阅？"
    assert events[-1]["options"][0]["label"].startswith("CloudMusic（¥")


def test_a_subscription_the_bank_listed_earlier_is_one_the_customer_can_name(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(step("s1", "list_subscriptions")), say("You have CloudMusic, TuneBox and StreamPlus."))
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    send(agent, conversation, "what subscriptions do i have")
    model.script(plan(step("s1", "cancel_subscription", {"subscription_id": "SUB-LM-002"})))

    events = shown(send(agent, conversation, "cancel TuneBox"))

    # The Planner had the Bank's IDs as Bank data, and the real ID goes straight on the
    # Plan card: no second list.
    seen = [
        m for m in model.requests[-1].messages
        if "The Subscriptions listed so far" in m["content"]
    ]
    assert len(seen) == 1 and seen[0]["content"].startswith("<bank_data>")
    assert "SUB-LM-002" in seen[0]["content"]
    assert event_types(events) == ["plan_proposed", "confirmation_requested"]


def test_a_question_asked_after_the_list_offers_the_banks_subscriptions(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        plan(
            step("s1", "list_subscriptions"),
            step("s2", "cancel_subscription", depends_on=["s1"]),
        ),
        clarify(
            "You have two music subscriptions: which one should I cancel?",
            ("CloudMusic", "SUB-LM-001"),
            ("TuneBox", "SUB-LM-002"),
        ),
    )
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = shown(send(agent, conversation, "cancel that music subscription"))

    assert event_types(events) == ["step_started", "step_succeeded", "clarification_question"]
    assert [o["value"] for o in events[-1]["options"]] == ["SUB-LM-001", "SUB-LM-002"]
