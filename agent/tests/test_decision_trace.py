"""The decision trace: `trace` events show what the Agent understood, planned and was told
by the Bank, so that judges can check nothing was made up (docs/conversation-events.md)."""

import json
import re

from fastapi.testclient import TestClient

from conftest import Login, audit_trail, log_in, send, start_conversation, traces
from fake_model import FakeModel, decline, plan, say, step

BALANCE_QUESTION = "我卡里还有多少钱？"
TO_MUM_500 = {"payee_id": "PAY-LM-002", "amount": "500.00"}
TO_MUM_1100 = {"payee_id": "PAY-LM-002", "amount": "1100.00"}


def test_the_trace_shows_the_intent_the_agent_understood(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        plan(step("s1", "query_balance"), intent="查询活期账户余额"),
        say("您的余额为 1180.00 元。"),
    )

    events = send(agent, start_conversation(agent, log_in(bank, "liming")), BALANCE_QUESTION)

    assert traces(events, "intent") == [{"intent": "查询活期账户余额"}]


def test_the_trace_shows_why_a_request_was_declined(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(decline("out_of_scope", "query_balance"))

    events = send(agent, start_conversation(agent, log_in(bank, "liming")), "帮我买点基金")

    assert traces(events, "intent") == [
        {"intent": "", "declined": {"reason": "out_of_scope", "can_do": ["query_balance"]}}
    ]


def test_the_trace_shows_the_plan_graph_with_the_banks_tier_for_each_step(
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

    assert traces(events, "plan") == [
        {
            "steps": [
                {
                    "id": "s2",
                    "operation": "query_balance",
                    "input": {"account_id": "ACC-HM-001"},
                    "depends_on": [],
                    "tier": "green",
                },
                {
                    "id": "s1",
                    "operation": "query_balance",
                    "input": {"account_id": "ACC-HM-002"},
                    "depends_on": ["s2"],
                    "tier": "green",
                },
            ]
        }
    ]


def test_the_trace_shows_every_bank_request_and_the_banks_raw_response(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(step("s1", "query_balance")), say("您的余额为 1180.00 元。"))

    events = send(agent, start_conversation(agent, log_in(bank, "liming")), BALANCE_QUESTION)

    requests = traces(events, "bank_request")
    assert requests == [
        {
            "method": "POST",
            "path": "/tier-preview",
            "body": {"operations": [{"operation": "query_balance", "input": {}}]},
        },
        {"method": "POST", "path": "/operations/query_balance", "body": {"input": {}}},
    ]
    preview, balance = traces(events, "bank_response")
    assert (preview["path"], preview["status"]) == ("/tier-preview", 200)
    assert [(s["operation"], s["tier"]) for s in preview["body"]["steps"]] == [
        ("query_balance", "green")
    ]
    assert (balance["path"], balance["status"]) == ("/operations/query_balance", 200)
    assert balance["body"]["operation"] == "query_balance"
    assert balance["body"]["tier"] == "green"
    accounts = balance["body"]["output"]["accounts"]
    assert [(a["account_id"], a["balance"]) for a in accounts] == [("ACC-LM-001", "1180.00")]


def test_a_steps_bank_exchange_is_traced_between_its_start_and_its_result(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(step("s1", "query_balance")), say("您的余额为 1180.00 元。"))

    events = send(agent, start_conversation(agent, log_in(bank, "liming")), BALANCE_QUESTION)

    assert [event.get("stage", event["type"]) for event in events] == [
        "intent",
        "bank_request",
        "bank_response",
        "plan",
        "step_started",
        "bank_request",
        "bank_response",
        "step_succeeded",
        "answer",
    ]


def test_the_trace_shows_the_sessions_audit_trail_as_the_bank_recorded_it(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(step("s1", "query_balance")), say("您的余额为 1180.00 元。"))
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    send(agent, conversation, BALANCE_QUESTION)

    event = audit_trail(agent, conversation)

    assert (event["type"], event["stage"]) == ("trace", "audit_trail")
    assert event["data"]["session_id"] == login.session_id
    assert [
        (e["event"], e["operation"], e["tier"], e["outcome"]) for e in event["data"]["entries"]
    ] == [
        ("tier_preview", None, "green", "ok"),
        ("operation", "query_balance", "green", "ok"),
    ]


# --- No token or code is ever traced ------------------------------------------------


def approve_on_the_bank(bank: TestClient, login: Login) -> list[str]:
    """Confirm a Yellow transfer and pass Strong Verification for a Red one in this
    session, as the Channel and simulated phone do: the tokens and the code it took."""
    session = {"Authorization": f"Bearer {login.session_token}"}
    phone = {"Authorization": f"Bearer {login.phone_token}"}
    confirmed = bank.post(
        "/confirmations",
        headers=session,
        json={"operations": [{"operation": "transfer", "input": TO_MUM_500}]},
    )
    assert confirmed.status_code == 200, confirmed.text
    challenge = bank.post(
        "/verifications", headers=session, json={"operation": "transfer", "input": TO_MUM_1100}
    )
    assert challenge.status_code == 200, challenge.text
    sms = bank.get("/phone/messages", headers=phone).json()["messages"][-1]["text"]
    match = re.search(r"\b(\d{6})\b", sms)
    assert match, sms
    code = match.group(1)
    verified = bank.post(
        f"/verifications/{challenge.json()['challenge_id']}", headers=phone, json={"code": code}
    )
    assert verified.status_code == 200, verified.text
    return [
        login.session_token,
        login.phone_token,
        confirmed.json()["confirmations"][0]["confirmation_token"],
        verified.json()["verification_token"],
        code,
    ]


def test_no_token_or_code_appears_in_any_trace(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(step("s1", "query_balance")), say("您的余额为 1180.00 元。"))
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    secrets = approve_on_the_bank(bank, login)

    turn = traces(send(agent, conversation, BALANCE_QUESTION))
    trail = audit_trail(agent, conversation)

    assert [e["event"] for e in trail["data"]["entries"]] == [
        "confirmation_issued",
        "verification_challenge",
        "verification_attempt",
        "tier_preview",
        "operation",
    ]
    shown = json.dumps([turn, trail], ensure_ascii=False)
    for secret in secrets:
        assert secret not in shown
