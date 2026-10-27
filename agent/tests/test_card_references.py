"""Cards the way the Customer names them: by their number in the list the Agent showed
("#2"), their last four digits or their name. The Planner gets the Bank's IDs for the cards
listed so far, as Bank data, instead of making one up; the Plan card names the card a step
acts on, so the Customer can check it before the SMS code."""

from typing import Any

from fastapi.testclient import TestClient

from conftest import (
    act,
    bank_audit,
    event_types,
    latest_code,
    log_in,
    send,
    start_conversation,
    type_code,
)
from fake_model import FakeModel, plan, say, step

LISTED = (
    "Here are your cards:\n\n"
    "1. 工商借记卡 (debit), ending 1234\n"
    "2. 招商借记卡 (debit), ending 5678\n"
    "3. 信用卡 (credit), ending 9012\n\n"
    "Which one did you lose?"
)

CARD_2 = {"card_id": "CARD-LM-002", "name": "招商借记卡 Debit card", "last4": "5678"}


def shown(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event["type"] != "trace"]


def cards_seen(model: FakeModel) -> list[dict[str, Any]]:
    """The messages of the model's latest request that carry the listed cards."""
    return [m for m in model.requests[-1].messages if "The cards listed so far" in m["content"]]


def test_card_number_two_from_the_list_is_the_banks_card_and_the_plan_card_names_it(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(step("s1", "list_cards")), say(LISTED))
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    send(agent, conversation, "i lost one of my cards")
    model.script(
        plan(step("s1", "file_loss_report", {"card_id": "CARD-LM-002"})),
        say("The loss report on your 招商借记卡 ending 5678 is filed; the card is disabled."),
    )

    events = shown(send(agent, conversation, "i lost card #2"))

    # The Planner had the Bank's IDs for the listed cards: Bank data, in a marked data
    # block, never in the instructions (a virtual card's name is the Customer's text).
    seen = cards_seen(model)
    assert len(seen) == 1 and seen[0]["role"] != "system"
    assert seen[0]["content"].startswith("<bank_data>")
    assert "CARD-LM-002" in seen[0]["content"] and "5678" in seen[0]["content"]
    assert event_types(events) == ["plan_proposed", "confirmation_requested"]
    assert events[0]["cards"] == [{"step_id": "s1", **CARD_2}]

    requested = shown(act(agent, conversation, "confirm", plan_id=events[0]["plan_id"]))[-1]
    assert requested["type"] == "verification_requested"
    verified = type_code(bank, login, requested["challenge_id"], latest_code(bank, login))
    act(
        agent,
        conversation,
        "verified",
        plan_id=requested["plan_id"],
        step_id="s1",
        verification_token=verified["verification_token"],
    )
    reports = [
        (e["input"]["card_id"], e["outcome"])
        for e in bank_audit(bank, login)
        if e["event"] == "operation" and e["operation"] == "file_loss_report"
    ]
    assert reports == [("CARD-LM-002", "ok")]


def test_with_no_cards_listed_yet_they_are_listed_first_and_the_plan_card_names_the_card(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        plan(
            step("s1", "list_cards"),
            step("s2", "file_loss_report", depends_on=["s1"]),
        ),
        plan(step("s2", "file_loss_report", {"card_id": "CARD-LM-002"})),
    )
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = shown(send(agent, conversation, "我的招商卡丢了"))

    assert "The cards listed so far" not in model.requests[0].text()
    assert "CARD-LM-002" in model.requests[-1].text()  # completed from the Bank's list
    assert event_types(events) == [
        "step_started", "step_succeeded", "plan_proposed", "confirmation_requested"
    ]
    assert events[2]["cards"] == [{"step_id": "s2", **CARD_2}]


def test_a_virtual_card_the_bank_issued_earlier_is_one_the_customer_can_name(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(step("s1", "apply_virtual_card")), say("已为您申请一张虚拟卡。"))
    conversation = start_conversation(agent, log_in(bank, "liming"))
    proposed = shown(send(agent, conversation, "申请一张虚拟卡"))[0]
    ran = shown(act(agent, conversation, "confirm", plan_id=proposed["plan_id"]))
    issued = next(e for e in ran if e["type"] == "step_succeeded")["output"]["card"]
    model.script(
        plan(step("s1", "add_card_lock", {"card_id": issued["card_id"], "scope": "online"}))
    )

    events = shown(send(agent, conversation, "把刚申请的虚拟卡锁住网上交易"))

    assert issued["card_id"] in cards_seen(model)[0]["content"]
    assert events[0]["cards"] == [
        {
            "step_id": "s1",
            "card_id": issued["card_id"],
            "name": issued["name"],
            "last4": issued["masked_number"][-4:],
        }
    ]


def test_a_card_the_bank_has_not_listed_is_not_named_on_the_plan_card(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(step("s1", "add_card_lock", {"card_id": "CARD-LM-001", "scope": "online"})))
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = shown(send(agent, conversation, "锁住尾号1234的卡的网上交易"))

    assert event_types(events) == ["plan_proposed", "confirmation_requested"]
    assert events[0]["cards"] == []
