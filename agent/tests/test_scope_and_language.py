from typing import Any

from fastapi.testclient import TestClient

from conftest import bank_audit, event_types, log_in, send, start_conversation
from fake_model import FakeModel, decline, plan, say, step

WEALTH_REQUEST = "帮我买点理财产品"


def catalogue(bank: TestClient) -> dict[str, dict[str, Any]]:
    operations: list[dict[str, Any]] = bank.get("/operations").json()["operations"]
    return {operation["name"]: operation for operation in operations}


# --- Out-of-scope requests get a polite refusal and what the Agent can do -----------


def test_a_wealth_request_is_refused_with_what_the_agent_can_do(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(decline("out_of_scope", "transfer", "query_balance"))
    login = log_in(bank, "liming")

    events = send(agent, start_conversation(agent, login), WEALTH_REQUEST)

    assert event_types(events) == ["refusal"]
    refusal = events[-1]
    assert refusal["reason"] == "out_of_scope"
    assert refusal["text"].startswith("抱歉")
    operations = catalogue(bank)
    assert operations["transfer"]["description_zh"] in refusal["text"]
    assert operations["query_balance"]["description_zh"] in refusal["text"]
    assert bank_audit(bank, login) == []


def test_a_non_banking_question_gets_the_same_kind_of_answer(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(decline("out_of_scope", "list_cards"))
    login = log_in(bank, "liming")

    events = send(agent, start_conversation(agent, login), "今天天气怎么样？推荐个电影吧")

    assert event_types(events) == ["refusal"]
    assert events[-1]["reason"] == "out_of_scope"
    assert catalogue(bank)["list_cards"]["description_zh"] in events[-1]["text"]
    assert bank_audit(bank, login) == []


# --- What the Agent offers comes from the Bank's Operation catalogue ----------------


def test_the_agent_may_offer_only_operations_in_the_catalogue(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(decline("out_of_scope", "buy_wealth_product", "transfer"))

    events = send(agent, start_conversation(agent, log_in(bank, "liming")), WEALTH_REQUEST)

    (decline_tool,) = [
        tool
        for tool in model.requests[0].tools or []
        if tool["function"]["name"] == "decline_request"
    ]
    offered = decline_tool["function"]["parameters"]["properties"]["can_do"]["items"]["enum"]
    assert offered == list(catalogue(bank))
    assert "buy_wealth_product" not in events[-1]["text"]
    assert catalogue(bank)["transfer"]["description_zh"] in events[-1]["text"]


def test_when_the_model_offers_nothing_the_customer_still_hears_what_the_agent_can_do(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(decline("out_of_scope"))

    events = send(agent, start_conversation(agent, log_in(bank, "liming")), WEALTH_REQUEST)

    listed = [op for op in catalogue(bank).values() if op["description_zh"] in events[-1]["text"]]
    assert 1 <= len(listed) <= 5


def test_the_next_turn_is_planned_knowing_what_the_customer_was_told(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        decline("out_of_scope", "query_balance"),
        plan(step("s1", "query_balance")),
        say("您的余额为 1180.00 元。"),
    )
    conversation = start_conversation(agent, log_in(bank, "liming"))
    refusal = send(agent, conversation, WEALTH_REQUEST)[-1]

    events = send(agent, conversation, "那查一下余额吧")

    assert event_types(events) == ["step_started", "step_succeeded", "answer"]
    second_planning = model.requests[1]
    assert WEALTH_REQUEST in second_planning.text()
    assert any(m["content"] == refusal["text"] for m in second_planning.messages)


# --- The Customer's language ------------------------------------------------------


def test_an_english_request_is_refused_in_english(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(decline("out_of_scope", "transfer"))

    events = send(
        agent,
        start_conversation(agent, log_in(bank, "liming")),
        "Can you buy me some wealth management products?",
    )

    text = events[-1]["text"]
    transfer = catalogue(bank)["transfer"]
    assert text.startswith("Sorry")
    assert transfer["description_en"] in text
    assert transfer["description_zh"] not in text


def test_an_english_question_is_answered_in_english(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(step("s1", "query_balance")), say("Your balance is 1180.00 yuan."))

    send(agent, start_conversation(agent, log_in(bank, "liming")), "How much money do I have?")

    answering = model.requests[1]
    assert "Reply in English." in answering.text()
    assert "Reply in Chinese." not in answering.text()


def test_a_chinese_question_is_answered_in_chinese(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(step("s1", "query_balance")), say("您的余额为 1180.00 元。"))

    send(agent, start_conversation(agent, log_in(bank, "liming")), "我卡里还有多少钱？")

    answering = model.requests[1]
    assert "Reply in Chinese." in answering.text()
    assert "Reply in English." not in answering.text()


def test_a_reply_without_words_keeps_the_conversations_language(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(decline("out_of_scope", "transfer"), decline("out_of_scope", "transfer"))
    conversation = start_conversation(agent, log_in(bank, "liming"))
    send(agent, conversation, "Buy me some funds")

    events = send(agent, conversation, "10000")

    assert events[-1]["text"].startswith("Sorry")
