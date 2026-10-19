"""The Customer is never stuck with the Agent: they can ask for a human at any time, a
Staff Member can take the conversation over (the Agent is paused) and hand it back (the
Agent resumes, knowing everything that was said meanwhile)."""

from fastapi.testclient import TestClient

from conftest import (
    bank_audit,
    event_types,
    hand_back,
    log_in,
    STAFF_KEY,
    send,
    staff,
    staff_reply,
    start_conversation,
    take_over,
    updates,
)
from fake_model import FakeModel, plan, say, step


def test_during_a_takeover_the_agent_sends_nothing_and_starts_no_step(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    take_over(agent, conversation)
    audit_before = bank_audit(bank, login)

    events = send(agent, conversation, "给老妈转五百")

    assert events == []
    assert model.requests == []  # the Agent didn't even think about it
    assert bank_audit(bank, login) == audit_before


def test_a_staff_members_replies_reach_the_customer_marked_as_from_a_staff_member(
    agent: TestClient, bank: TestClient
) -> None:
    conversation = start_conversation(agent, log_in(bank, "liming"))
    take_over(agent, conversation, "王经理")
    send(agent, conversation, "我的卡好像被盗刷了")

    staff_reply(agent, conversation, "您好，我是人工客服王经理，我先帮您看看。")

    assert updates(agent, conversation) == {
        "events": [
            {"type": "handed_over", "staff": "王经理"},
            {"type": "staff_message", "staff": "王经理", "text": "您好，我是人工客服王经理，我先帮您看看。"},
        ],
        "next": 2,
    }


def test_the_channel_polls_only_for_what_it_hasnt_seen(
    agent: TestClient, bank: TestClient
) -> None:
    conversation = start_conversation(agent, log_in(bank, "liming"))
    assert updates(agent, conversation) == {"events": [], "next": 0}
    take_over(agent, conversation, "王经理")
    seen = updates(agent, conversation)["next"]

    staff_reply(agent, conversation, "请问是哪张卡？")

    assert updates(agent, conversation, after=seen) == {
        "events": [{"type": "staff_message", "staff": "王经理", "text": "请问是哪张卡？"}],
        "next": 2,
    }


def test_handing_back_resumes_the_agent_with_the_whole_conversation_as_context(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    conversation = start_conversation(agent, log_in(bank, "liming"))
    take_over(agent, conversation, "王经理")
    send(agent, conversation, "我想确认一下我的活期账户")
    staff_reply(agent, conversation, "好的，您的问题我已记录，接下来交给智能助手帮您查询。")

    hand_back(agent, conversation)
    model.script(plan(step("s1", "query_balance")), say("您的活期账户余额为 1180.00 元。"))
    events = send(agent, conversation, "那帮我查下余额")

    assert event_types(events) == ["step_started", "step_succeeded", "answer"]
    planner_saw = model.requests[0].messages
    assert {"role": "user", "content": "我想确认一下我的活期账户"} in planner_saw
    assert any(
        "王经理" in m["content"] and "您的问题我已记录" in m["content"] for m in planner_saw
    )
    assert planner_saw[-1] == {"role": "user", "content": "那帮我查下余额"}
    assert updates(agent, conversation)["events"][-1] == {"type": "handed_back"}


def test_the_staff_member_reads_the_customers_messages_and_the_takeover_in_the_history(
    agent: TestClient, bank: TestClient
) -> None:
    conversation = start_conversation(agent, log_in(bank, "liming"))
    take_over(agent, conversation, "王经理")
    send(agent, conversation, "我的卡好像被盗刷了")
    staff_reply(agent, conversation, "请问是哪张卡？")
    hand_back(agent, conversation)

    entries = staff(agent, f"/staff/conversations/{conversation}")["entries"]

    assert entries == [
        {"type": "handed_over", "staff": "王经理"},
        {"type": "customer_message", "text": "我的卡好像被盗刷了"},
        {"type": "staff_message", "staff": "王经理", "text": "请问是哪张卡？"},
        {"type": "handed_back"},
    ]


def test_only_a_conversation_taken_over_gets_staff_replies_or_can_be_handed_back(
    agent: TestClient, bank: TestClient
) -> None:
    conversation = start_conversation(agent, log_in(bank, "liming"))
    headers = {"X-Staff-Key": STAFF_KEY}

    replied = agent.post(
        f"/staff/conversations/{conversation}/messages", headers=headers, json={"text": "您好"}
    )
    handed_back = agent.post(f"/staff/conversations/{conversation}/hand-back", headers=headers)

    for response in [replied, handed_back]:
        assert response.status_code == 409
        assert response.json() == {"error": "not_handed_over"}
    assert updates(agent, conversation)["events"] == []


def test_a_conversation_already_taken_over_cant_be_taken_over_again(
    agent: TestClient, bank: TestClient
) -> None:
    conversation = start_conversation(agent, log_in(bank, "liming"))
    take_over(agent, conversation, "王经理")

    response = agent.post(
        f"/staff/conversations/{conversation}/takeover",
        headers={"X-Staff-Key": STAFF_KEY},
        json={"staff": "李主管"},
    )

    assert response.status_code == 409
    assert response.json() == {"error": "already_handed_over", "staff": "王经理"}
    staff_reply(agent, conversation, "我来处理")
    assert updates(agent, conversation)["events"][-1]["staff"] == "王经理"
