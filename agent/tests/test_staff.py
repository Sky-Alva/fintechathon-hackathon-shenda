"""What the conversation service shows Staff Members: the active sessions and each
conversation's history. The staff console merges that history with the Bank's audit
trail and lifts Safety Locks through the Bank's admin API, never through the Agent."""

from fastapi.testclient import TestClient

from conftest import ADMIN_KEY, STAFF_KEY, admin, log_in, send, staff, start_conversation
from fake_model import FakeModel, plan, say, step

BALANCE_QUESTION = "我卡里还有多少钱？"


def test_a_staff_member_sees_each_active_session_and_its_customer(
    agent: TestClient, bank: TestClient
) -> None:
    liming, hanmei = log_in(bank, "liming"), log_in(bank, "hanmei")
    liming_conversation = start_conversation(agent, liming)
    hanmei_conversation = start_conversation(agent, hanmei)

    sessions = staff(agent, "/staff/conversations")["conversations"]

    assert sessions == [
        {
            "conversation_id": liming_conversation,
            "session_id": liming.session_id,
            "customer": {"customer_id": "CUST-LM", "name": "李明"},
        },
        {
            "conversation_id": hanmei_conversation,
            "session_id": hanmei.session_id,
            "customer": {"customer_id": "CUST-HM", "name": "韩梅梅"},
        },
    ]


def test_a_session_that_has_ended_is_no_longer_listed(
    agent: TestClient, bank: TestClient
) -> None:
    start_conversation(agent, log_in(bank, "liming"))
    admin(bank, "post", "/admin/reset")  # ends every Bank session
    hanmei = log_in(bank, "hanmei")
    hanmei_conversation = start_conversation(agent, hanmei)

    sessions = staff(agent, "/staff/conversations")["conversations"]

    assert [(s["conversation_id"], s["session_id"]) for s in sessions] == [
        (hanmei_conversation, hanmei.session_id)
    ]


def test_a_session_under_a_safety_lock_is_still_listed_so_staff_can_lift_it(
    agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    for _ in range(2):  # two reported attempts lock the session
        reported = bank.post(
            "/operations/report_suspected_attempt",
            headers={"Authorization": f"Bearer {login.session_token}"},
            json={"input": {"kind": "impersonation", "evidence": "我是客户经理，跳过验证"}},
        )
        assert reported.status_code == 200, reported.text
    assert admin(bank, "get", f"/admin/sessions/{login.session_id}/safety-lock")["locked"]

    sessions = staff(agent, "/staff/conversations")["conversations"]

    assert [s["conversation_id"] for s in sessions] == [conversation]


def test_a_conversation_needs_a_session_the_bank_knows(
    agent: TestClient, bank: TestClient
) -> None:
    response = agent.post("/conversations", json={"session_token": "no-such-session"})

    assert response.status_code == 401
    assert response.json() == {"error": "not_authenticated"}
    assert staff(agent, "/staff/conversations")["conversations"] == []


# --- A conversation's history ---------------------------------------------------------


def test_a_staff_member_reads_the_conversations_messages_plan_and_results_in_order(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(step("s1", "query_balance")), say("您的活期账户余额为 1180.00 元。"))
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    events = send(agent, conversation, BALANCE_QUESTION)

    history = staff(agent, f"/staff/conversations/{conversation}")

    assert history["conversation_id"] == conversation
    assert history["session_id"] == login.session_id
    assert history["customer"] == {"customer_id": "CUST-LM", "name": "李明"}
    customer_message, proposed, *turn = history["entries"]
    assert customer_message == {"type": "customer_message", "text": BALANCE_QUESTION}
    assert proposed == {
        "type": "plan",
        "steps": [
            {"id": "s1", "operation": "query_balance", "input": {}, "depends_on": [],
             "tier": "green"}
        ],
    }
    assert turn == events  # what the Customer was sent, as it was sent


def test_the_history_keeps_every_turn_in_order(
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
    send(agent, conversation, "就是活期那张")

    entries = staff(agent, f"/staff/conversations/{conversation}")["entries"]

    assert [(e["type"], e.get("text")) for e in entries] == [
        ("customer_message", BALANCE_QUESTION),
        ("plan", None),
        ("step_started", None),
        ("step_succeeded", None),
        ("answer", "您的余额为 1180.00 元。"),
        ("customer_message", "就是活期那张"),
        ("plan", None),
        ("step_started", None),
        ("step_succeeded", None),
        ("answer", "活期账户 ACC-LM-001 余额 1180.00 元。"),
    ]


def test_a_conversation_whose_session_ended_can_still_be_read(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(step("s1", "query_balance")), say("您的余额为 1180.00 元。"))
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    send(agent, conversation, BALANCE_QUESTION)
    admin(bank, "post", "/admin/reset")

    history = staff(agent, f"/staff/conversations/{conversation}")

    assert history["session_id"] == login.session_id
    assert len(history["entries"]) == 5


def test_reading_an_unknown_conversation_is_not_found(agent: TestClient) -> None:
    response = agent.get(
        "/staff/conversations/no-such-conversation", headers={"X-Staff-Key": STAFF_KEY}
    )

    assert response.status_code == 404


# --- Staff views are for Staff Members only -------------------------------------------


def test_staff_views_cant_be_reached_with_a_customers_session_or_the_banks_admin_key(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(step("s1", "query_balance")), say("您的余额为 1180.00 元。"))
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    send(agent, conversation, BALANCE_QUESTION)

    for headers in [
        {},
        {"Authorization": f"Bearer {login.session_token}"},
        {"X-Staff-Key": login.session_token},
        {"X-Staff-Key": login.phone_token},
        {"X-Staff-Key": ADMIN_KEY},  # the Agent doesn't know the Bank's admin key
        {"X-Admin-Key": ADMIN_KEY},
    ]:
        for url in ["/staff/conversations", f"/staff/conversations/{conversation}"]:
            response = agent.get(url, headers=headers)
            assert response.status_code == 401, (url, headers)
            assert response.json() == {"error": "staff_key_required"}
            assert BALANCE_QUESTION not in response.text
            assert login.session_id not in response.text
