"""A value the Bank won't accept (#56): a Card Limit of 0, below 0, or with more than two
decimal places. The Agent says what is wrong with it, from the Bank's own `detail.errors`,
instead of asking which card first; and an Operation that couldn't be done is never
answered as information that couldn't be retrieved."""

from typing import Any

from fastapi.testclient import TestClient

from conftest import bank_audit, event_types, log_in, send, start_conversation
from fake_model import FakeModel, plan, say, step, unavailable

LISTED = "Your cards: 工商借记卡 Debit card ending 1234, 招商借记卡 Debit card ending 5678."


def shown(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event["type"] != "trace"]


def ran(bank: TestClient, login: Any) -> list[str]:
    """The Operations the Bank received a call of, look-ups included."""
    return [e["operation"] for e in bank_audit(bank, login) if e["event"] == "operation"]


def test_a_limit_of_zero_says_it_must_be_greater_than_zero_without_asking_which_card(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    # The Planner lists the cards for the step to take its card from, as it is told to.
    model.script(
        plan(
            step("s1", "list_cards"),
            step("s2", "lower_card_limit", {"new_limit": 0}, depends_on=["s1"]),
        )
    )
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)

    events = shown(send(agent, conversation, "can you make the limit 0?"))

    assert event_types(events) == ["answer"]
    assert events[0]["text"] == "Sorry, that can't be done: the new limit must be greater than 0."
    assert ran(bank, login) == []


def test_a_negative_limit_with_no_card_named_shows_no_card_options(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(step("s1", "lower_card_limit", {"new_limit": -100})))
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = shown(send(agent, conversation, "lower it to -100 yuan"))

    assert event_types(events) == ["answer"]
    assert events[0]["text"] == "Sorry, that can't be done: the new limit must be greater than 0."


def test_more_than_two_decimal_places_says_so_in_chinese(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(step("s1", "raise_card_limit", {"new_limit": 3.14156})))
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = shown(send(agent, conversation, "把额度提高到3.14156"))

    assert event_types(events) == ["answer"]
    assert events[0]["text"] == "抱歉，无法办理：新额度最多只能有 2 位小数。"


def test_a_limit_of_zero_in_chinese(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        plan(
            step("s1", "list_cards"),
            step("s2", "lower_card_limit", {"new_limit": 0}, depends_on=["s1"]),
        )
    )
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = shown(send(agent, conversation, "把额度设为0"))

    assert event_types(events) == ["answer"]
    assert events[0]["text"] == "抱歉，无法办理：新额度必须大于 0。"


def test_a_named_card_with_too_many_decimal_places_is_explained_even_without_the_model(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(step("s1", "list_cards")), say(LISTED))
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    send(agent, conversation, "list my cards")
    model.script(
        plan(step("s1", "raise_card_limit", {"card_id": "CARD-LM-001", "new_limit": 3.14156})),
        unavailable(),
    )

    events = shown(send(agent, conversation, "raise the 1234 card's limit to 3.14156"))

    assert event_types(events) == ["step_failed", "answer"]
    assert events[0]["text"] == (
        "The Bank didn't run this step: the new limit can have at most 2 decimal places."
    )
    assert events[1]["text"] == (
        "Sorry, that couldn't be done: the new limit can have at most 2 decimal places."
    )


def test_a_query_that_failed_is_still_information_that_couldnt_be_retrieved(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        plan(step("s1", "list_transactions", {"limit": 1000})),  # the Bank allows 100
        unavailable(),
    )
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = shown(send(agent, conversation, "show me my last 1000 transactions"))

    assert event_types(events) == ["step_failed", "answer"]
    assert events[1]["text"] == (
        "Sorry, I couldn't get that information from the Bank. Please try again later."
    )


def test_a_valid_limit_with_no_card_named_still_asks_which_card(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        plan(step("s1", "lower_card_limit", {"new_limit": 1000})),
        plan(step("s1", "lower_card_limit", {"new_limit": 1000})),
    )
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = shown(send(agent, conversation, "lower my limit to 1000"))

    assert event_types(events) == ["step_started", "step_succeeded", "clarification_question"]
    assert events[-1]["text"] == "Which card do you mean?"
    assert [option["value"] for option in events[-1]["options"]] == [
        "CARD-LM-001", "CARD-LM-002", "CARD-LM-003"
    ]
