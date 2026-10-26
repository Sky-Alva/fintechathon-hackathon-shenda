"""When the model can't be asked (its API is down, refuses or times out) or replies with
nothing the Agent can act on, the turn still ends with an answer: the Customer is never
left with a turn that just stops.

A request that couldn't be planned is answered by the Agent itself, and nothing runs. A
Plan that already ran is reported from the Bank's results as returned, so what moved
money is never left unsaid.
"""

from typing import Any

from fastapi.testclient import TestClient

from conftest import act, bank_audit, event_types, log_in, send, staff, start_conversation
from fake_model import FakeModel, ModelReply, ToolCall, plan, say, step, unavailable

BALANCE_QUESTION = "我卡里还有多少钱？"
TO_MUM_300 = {"payee_id": "PAY-LM-002", "amount": "300.00", "note": ""}


def answers(events: list[dict[str, Any]]) -> list[str]:
    return [event["text"] for event in events if event["type"] == "answer"]


def operations_run(bank: TestClient, login: Any) -> list[str]:
    return [
        e["operation"]
        for e in bank_audit(bank, login)
        if e["event"] == "operation" and e["outcome"] == "ok"
    ]


def test_a_request_the_model_cant_be_asked_about_is_answered_and_nothing_runs(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(unavailable())
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)

    events = send(agent, conversation, BALANCE_QUESTION)

    assert event_types(events) == ["answer"]
    assert "没有执行任何操作" in answers(events)[0]
    assert operations_run(bank, login) == []


def test_the_conversation_carries_on_once_the_model_is_back(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        unavailable(),
        plan(step("s1", "query_balance")),
        say("您的活期账户余额为 1180.00 元。"),
    )
    conversation = start_conversation(agent, log_in(bank, "liming"))
    send(agent, conversation, BALANCE_QUESTION)

    events = send(agent, conversation, BALANCE_QUESTION)

    assert event_types(events) == ["step_started", "step_succeeded", "answer"]
    assert answers(events) == ["您的活期账户余额为 1180.00 元。"]


def test_a_reply_that_is_no_plan_is_answered_the_same_way(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(say("好的，马上帮您查。"))  # plain text where a Plan was asked for
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = send(agent, conversation, BALANCE_QUESTION)

    assert event_types(events) == ["answer"]
    assert "没有执行任何操作" in answers(events)[0]


def test_a_plan_whose_steps_dont_fit_is_answered_the_same_way(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    garbled = ModelReply(
        tool_calls=[ToolCall(id="call-plan", name="propose_plan", arguments={"steps": "s1"})]
    )
    model.script(garbled)
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = send(agent, conversation, BALANCE_QUESTION)

    assert event_types(events) == ["answer"]
    assert "没有执行任何操作" in answers(events)[0]


def test_the_customer_writing_in_english_is_told_in_english(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(unavailable())
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = send(agent, conversation, "How much money do I have?")

    assert "nothing was done" in answers(events)[0]


def test_a_confirmed_transfer_is_reported_from_the_bank_when_the_model_cant_answer(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(step("s1", "transfer", TO_MUM_300)), unavailable())
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    [proposed] = [e for e in send(agent, conversation, "给老妈转300") if e["type"] == "plan_proposed"]

    events = act(agent, conversation, "confirm", plan_id=proposed["plan_id"])

    assert event_types(events) == ["step_started", "step_succeeded", "answer"]
    [answer] = answers(events)
    assert answer.startswith("以下是银行返回的结果：")
    assert "transfer" in answer and "300.00" in answer
    assert operations_run(bank, login) == ["transfer"]
    # Staff Members read the same answer in the conversation's history.
    history = staff(agent, f"/staff/conversations/{conversation}")["entries"]
    assert answer in [entry.get("text") for entry in history if entry["type"] == "answer"]
