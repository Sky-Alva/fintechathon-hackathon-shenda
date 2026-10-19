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
    act,
    send,
    staff,
    staff_reply,
    start_conversation,
    take_over,
    traces,
    updates,
)
from fake_model import FakeModel, ask_for_human, plan, say, step


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


def test_a_plan_card_cant_be_confirmed_while_a_staff_member_has_the_conversation(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    to_mum = {"payee_id": "PAY-LM-002", "amount": "200.00", "note": ""}
    model.script(plan(step("s1", "transfer", to_mum)))
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    card = send(agent, conversation, "给老妈转200")[-1]
    take_over(agent, conversation, "王经理")
    audit_before = bank_audit(bank, login)

    response = agent.post(
        f"/conversations/{conversation}/actions",
        json={"action": "confirm", "plan_id": card["plan_id"]},
    )

    assert response.status_code == 409
    assert response.json() == {"error": "already_handed_over", "staff": "王经理"}
    assert bank_audit(bank, login) == audit_before  # no Confirmation, no transfer


# --- Asking for a human ---------------------------------------------------------------


def flagged(agent: TestClient) -> list[tuple[str, bool, str | None]]:
    """Each session in the staff console: whether its Customer is waiting for a human,
    and who has taken it over."""
    return [
        (c["conversation_id"], c["human_requested"], c["handed_over_to"])
        for c in staff(agent, "/staff/conversations")["conversations"]
    ]


def test_asking_for_a_human_with_the_button_flags_the_session_in_the_staff_console(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    liming = start_conversation(agent, log_in(bank, "liming"))
    hanmei = start_conversation(agent, log_in(bank, "hanmei"))

    events = act(agent, liming, "ask_for_human")

    assert events == [{"type": "human_requested", "text": "已为您呼叫人工客服，请稍候。"}]
    assert model.requests == []
    assert flagged(agent) == [(liming, True, None), (hanmei, False, None)]
    entries = staff(agent, f"/staff/conversations/{liming}")["entries"]
    assert entries == [
        {"type": "customer_action", "action": "ask_for_human", "plan_id": None},
        *events,
    ]


def test_taking_over_answers_the_request_for_a_human(
    agent: TestClient, bank: TestClient
) -> None:
    conversation = start_conversation(agent, log_in(bank, "liming"))
    act(agent, conversation, "ask_for_human")

    take_over(agent, conversation, "王经理")

    assert flagged(agent) == [(conversation, False, "王经理")]
    hand_back(agent, conversation)
    assert flagged(agent) == [(conversation, False, None)]


def test_asking_for_a_human_in_words_flags_the_session_and_runs_nothing(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(ask_for_human())
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    audit_before = bank_audit(bank, login)

    events = send(agent, conversation, "转人工")

    assert event_types(events) == ["human_requested"]
    assert events[-1]["text"] == "已为您呼叫人工客服，请稍候。"
    assert traces(events, "intent") == [{"intent": "", "ask_for_human": True}]
    assert flagged(agent) == [(conversation, True, None)]
    assert bank_audit(bank, login) == audit_before
    assert any(t["function"]["name"] == "ask_for_human" for t in model.requests[0].tools or [])


def test_a_customer_writing_in_english_is_told_in_english(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(ask_for_human())
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = send(agent, conversation, "a human, please")

    assert events[-1] == {
        "type": "human_requested",
        "text": "I've asked for a Staff Member to join you. Please wait a moment.",
    }
