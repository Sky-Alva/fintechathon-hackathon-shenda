from fastapi.testclient import TestClient

from conftest import bank_audit, event_types, log_in, send, start_conversation
from fake_model import FakeModel, plan, say, step

BALANCE_QUESTION = "我卡里还有多少钱？"


# --- A Green question is answered straight away -------------------------------------


def test_balance_question_runs_one_green_step_and_is_answered(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        plan(step("s1", "query_balance")),
        say("您的活期账户余额为 1180.00 元。"),
    )
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = send(agent, conversation, BALANCE_QUESTION)

    assert event_types(events) == ["step_started", "step_succeeded", "answer"]
    started, succeeded, answer = [e for e in events if e["type"] != "trace"]
    assert started["step"] == {
        "id": "s1",
        "operation": "query_balance",
        "input": {},
        "depends_on": [],
        "tier": "green",
    }
    assert succeeded["step_id"] == "s1"
    accounts = succeeded["output"]["accounts"]
    assert [(a["account_id"], a["balance"]) for a in accounts] == [("ACC-LM-001", "1180.00")]
    assert answer["text"] == "您的活期账户余额为 1180.00 元。"


def test_the_bank_tiers_the_plan_before_the_step_runs(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(step("s1", "query_balance")), say("您的余额为 1180.00 元。"))
    login = log_in(bank, "liming")

    send(agent, start_conversation(agent, login), BALANCE_QUESTION)

    entries = bank_audit(bank, login)
    assert [(e["event"], e["operation"], e["outcome"]) for e in entries] == [
        ("tier_preview", None, "ok"),
        ("operation", "query_balance", "ok"),
    ]
    assert [s["tier"] for s in entries[0]["detail"]["steps"]] == ["green"]


def test_the_answer_is_written_from_the_balance_the_bank_returned(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(step("s1", "query_balance")), say("您的余额为 1180.00 元。"))

    send(agent, start_conversation(agent, log_in(bank, "liming")), BALANCE_QUESTION)

    planning, answering = model.requests
    assert "1180.00" not in planning.text()
    assert "1180.00" in answering.text()
    assert BALANCE_QUESTION in answering.text()


def test_the_plan_is_built_from_the_banks_operation_catalogue(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(step("s1", "query_balance")), say("您的余额为 1180.00 元。"))
    catalogue = bank.get("/operations").json()["operations"]

    send(agent, start_conversation(agent, log_in(bank, "liming")), BALANCE_QUESTION)

    planning = model.requests[0]
    assert BALANCE_QUESTION in planning.text()
    for operation in catalogue:
        assert operation["name"] in planning.text()
        assert operation["description_zh"] in planning.text()


def test_each_customer_is_answered_from_their_own_accounts(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        plan(step("s1", "query_balance")),
        say("您的活期余额 8650.00 元，储蓄 50000.00 元。"),
    )

    events = send(agent, start_conversation(agent, log_in(bank, "hanmei")), BALANCE_QUESTION)

    accounts = next(e for e in events if e["type"] == "step_succeeded")["output"]["accounts"]
    assert [(a["account_id"], a["balance"]) for a in accounts] == [
        ("ACC-HM-001", "8650.00"),
        ("ACC-HM-002", "50000.00"),
    ]


def test_a_plans_steps_run_after_the_steps_they_depend_on(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        plan(
            step("s1", "query_balance", {"account_id": "ACC-HM-002"}, depends_on=["s2"]),
            step("s2", "query_balance", {"account_id": "ACC-HM-001"}),
        ),
        say("活期 8650.00 元，储蓄 50000.00 元。"),
    )

    events = send(agent, start_conversation(agent, log_in(bank, "hanmei")), BALANCE_QUESTION)

    assert event_types(events) == [
        "step_started", "step_succeeded", "step_started", "step_succeeded", "answer"
    ]
    assert [e["step"]["id"] for e in events if e["type"] == "step_started"] == ["s2", "s1"]
    assert [e["step_id"] for e in events if e["type"] == "step_succeeded"] == ["s2", "s1"]


def test_the_next_turn_is_planned_with_the_conversation_so_far(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        plan(step("s1", "query_balance")),
        say("您的余额为 1180.00 元。"),
        plan(step("s1", "query_balance", {"account_id": "ACC-LM-001"})),
        say("活期账户 ACC-LM-001 余额 1180.00 元。"),
    )
    conversation = start_conversation(agent, log_in(bank, "liming"))
    send(agent, conversation, BALANCE_QUESTION)

    events = send(agent, conversation, "就是活期那张")

    assert event_types(events) == ["step_started", "step_succeeded", "answer"]
    second_planning = model.requests[2]
    assert BALANCE_QUESTION in second_planning.text()
    assert "您的余额为 1180.00 元。" in second_planning.text()
    assert "就是活期那张" in second_planning.text()


# --- The Agent holds the session token only -----------------------------------------


def test_a_conversation_takes_the_session_token_and_nothing_else(
    agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "liming")

    for extra in [{"password": "demo1234"}, {"phone_token": login.phone_token}]:
        response = agent.post(
            "/conversations", json={"session_token": login.session_token, **extra}
        )
        assert response.status_code == 422, extra


def test_a_message_to_an_unknown_conversation_is_refused(agent: TestClient) -> None:
    response = agent.post("/conversations/no-such-conversation/messages", json={"text": "hi"})

    assert response.status_code == 404
