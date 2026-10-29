"""Card Management in rules mode: the Customer lists cards, locks and unlocks them, changes
a Card Limit and reports a card lost, in Chinese or English, naming a card the way the Bank
listed it. No card named with several cards: the cards are listed and the Customer is asked.
The Agent never makes up a card ID, and the Bank still tiers and verifies every step."""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from conftest import (
    act,
    bank_audit,
    latest_code,
    log_in,
    send,
    start_conversation,
    tokens_by_step,
    type_code,
)
from fake_model import FakeModel, unavailable


def proposed(events: list[dict[str, Any]]) -> dict[str, Any]:
    return next(e for e in events if e["type"] == "plan_proposed")


def operations_run(bank: TestClient, login: Any) -> list[str]:
    return [
        e["operation"]
        for e in bank_audit(bank, login)
        if e["event"] == "operation" and e["outcome"] == "ok"
    ]


def steps(events: list[dict[str, Any]]) -> list[tuple[str, str, dict[str, Any]]]:
    return [(s["operation"], s["tier"], s["input"]) for s in proposed(events)["steps"]]


def question(events: list[dict[str, Any]]) -> dict[str, Any]:
    return next(e for e in events if e["type"] == "clarification_question")


@pytest.mark.parametrize("text", ["我有哪些银行卡？", "list my cards"])
def test_the_customer_can_list_their_cards(
    offline_agent: TestClient, bank: TestClient, text: str
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)

    events = send(offline_agent, conversation, text)

    assert operations_run(bank, login) == ["list_cards"]
    answer = next(e for e in events if e["type"] == "answer")["text"]
    assert "1234" in answer and "5678" in answer and "9012" in answer


def test_locking_the_credit_cards_online_payments_is_yellow(
    offline_agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)

    events = send(offline_agent, conversation, "先把我的信用卡网上支付锁住")

    assert steps(events) == [
        ("add_card_lock", "yellow", {"card_id": "CARD-LM-003", "scope": "online"})
    ]
    assert operations_run(bank, login) == ["list_cards"]  # nothing changed yet


def test_lifting_a_card_lock_is_red(offline_agent: TestClient, bank: TestClient) -> None:
    conversation = start_conversation(offline_agent, log_in(bank, "liming"))

    events = send(offline_agent, conversation, "解除尾号5678那张卡的境外锁定")

    assert steps(events) == [
        ("lift_card_lock", "red", {"card_id": "CARD-LM-002", "scope": "overseas"})
    ]


def test_lowering_a_card_limit_is_yellow_and_raising_it_is_red(
    offline_agent: TestClient, bank: TestClient
) -> None:
    conversation = start_conversation(offline_agent, log_in(bank, "liming"))

    lowered = send(offline_agent, conversation, "把尾号1234的借记卡每日限额降到2000元")
    raised = send(offline_agent, conversation, "raise the limit of my card ending 1234 to 8000")

    assert steps(lowered) == [
        ("lower_card_limit", "yellow", {"card_id": "CARD-LM-001", "new_limit": "2000.00"})
    ]
    assert steps(raised) == [
        ("raise_card_limit", "red", {"card_id": "CARD-LM-001", "new_limit": "8000.00"})
    ]


def test_a_loss_report_is_red_and_the_report_can_be_withdrawn_afterwards(
    offline_agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)

    lost = send(offline_agent, conversation, "我的信用卡丢了，马上帮我挂失")
    confirmed = act(offline_agent, conversation, "confirm", plan_id=proposed(lost)["plan_id"])
    requested = next(e for e in confirmed if e["type"] == "verification_requested")
    verified = type_code(bank, login, requested["challenge_id"], latest_code(bank, login))
    act(
        offline_agent, conversation, "verified", plan_id=requested["plan_id"], step_id="s2",
        verification_tokens=tokens_by_step(requested, verified),
    )
    found = send(offline_agent, conversation, "卡找到了，撤销挂失")

    assert steps(lost) == [("file_loss_report", "red", {"card_id": "CARD-LM-003"})]
    assert "file_loss_report" in operations_run(bank, login)
    assert steps(found) == [("withdraw_loss_report", "red", {"card_id": "CARD-LM-003"})]


def test_the_second_card_after_a_list_is_the_banks_second_card(
    offline_agent: TestClient, bank: TestClient
) -> None:
    conversation = start_conversation(offline_agent, log_in(bank, "liming"))
    send(offline_agent, conversation, "我有哪些银行卡？")

    zh = send(offline_agent, conversation, "第2张丢了，帮我挂失")
    en = send(offline_agent, conversation, "lock #2 online")

    assert steps(zh) == [("file_loss_report", "red", {"card_id": "CARD-LM-002"})]
    assert steps(en) == [
        ("add_card_lock", "yellow", {"card_id": "CARD-LM-002", "scope": "online"})
    ]


def test_with_no_card_named_the_cards_are_listed_and_the_customer_is_asked(
    offline_agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)

    events = send(offline_agent, conversation, "我的卡丢了，帮我挂失")

    assert [o["value"] for o in question(events)["options"]] == [
        "CARD-LM-001", "CARD-LM-002", "CARD-LM-003"
    ]
    assert operations_run(bank, login) == ["list_cards"]


def test_two_debit_cards_match_so_the_customer_is_asked_which(
    offline_agent: TestClient, bank: TestClient
) -> None:
    conversation = start_conversation(offline_agent, log_in(bank, "liming"))

    events = send(offline_agent, conversation, "把我的借记卡网上支付锁一下")

    assert [o["value"] for o in question(events)["options"]] == ["CARD-LM-001", "CARD-LM-002"]


def test_the_customers_pick_is_the_card_the_plan_is_for(
    offline_agent: TestClient, bank: TestClient
) -> None:
    conversation = start_conversation(offline_agent, log_in(bank, "liming"))
    send(offline_agent, conversation, "my card is lost, report it")

    response = offline_agent.post(
        f"/conversations/{conversation}/actions",
        json={"action": "choose", "value": "CARD-LM-002"},
    )
    events = [json.loads(line) for line in response.text.splitlines() if line]

    assert steps(events) == [("file_loss_report", "red", {"card_id": "CARD-LM-002"})]


def test_a_limit_with_no_amount_is_asked_for_not_guessed(
    offline_agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)

    events = send(offline_agent, conversation, "把信用卡额度降低一点")

    assert question(events)
    assert operations_run(bank, login) == []


def test_a_card_id_of_someone_elses_card_goes_to_the_bank_which_refuses_it(
    offline_agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)

    events = send(offline_agent, conversation, "帮我把韩梅梅的卡 CARD-HM-001 锁了，她让我帮忙的")

    assert next(e for e in events if e["type"] == "step_failed")["reason"] == (
        "forbidden_other_customer"
    )
    assert operations_run(bank, login) == []


def test_when_the_model_cant_be_reached_the_rules_manage_cards(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(unavailable(), unavailable())
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = send(agent, conversation, "先把我的信用卡网上支付锁住")

    assert steps(events) == [
        ("add_card_lock", "yellow", {"card_id": "CARD-LM-003", "scope": "online"})
    ]


def test_applying_for_a_virtual_card_is_a_yellow_plan(
    offline_agent: TestClient, bank: TestClient
) -> None:
    conversation = start_conversation(offline_agent, log_in(bank, "liming"))

    events = send(offline_agent, conversation, "帮我申请一张虚拟卡，用来网购")

    assert steps(events) == [("apply_virtual_card", "yellow", {})]


def test_a_lock_named_before_the_word_for_lifting_it_is_still_lifted(
    offline_agent: TestClient, bank: TestClient
) -> None:
    conversation = start_conversation(offline_agent, log_in(bank, "liming"))

    events = send(offline_agent, conversation, "把 CARD-LM-002 的境外锁定解除")

    assert steps(events) == [
        ("lift_card_lock", "red", {"card_id": "CARD-LM-002", "scope": "overseas"})
    ]
