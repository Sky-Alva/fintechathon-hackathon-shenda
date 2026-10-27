"""Secrets stay secret: card numbers, passwords, codes and the spouse's information.

Nothing secret can be extracted through the chat: the Agent refuses to reveal full card
numbers, passwords, verification codes or another Customer's information (the Bank refuses
the last with `forbidden_other_customer`), a code typed into the chat never goes further,
and every answer is checked for anything that looks like a card number or a code.
"""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from conftest import (
    Login,
    act,
    bank_audit,
    event_types,
    latest_code,
    log_in,
    send,
    staff,
    start_conversation,
    type_code,
)
from fake_model import FakeModel, decline, plan, say, step

SPOUSE_BALANCE = "我老婆卡里还有多少钱？"


def shown(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The turn's events as the chat shows them, leaving out the decision trace."""
    return [event for event in events if event["type"] != "trace"]


# --- The spouse's information --------------------------------------------------------


def test_asking_for_the_spouses_balance_gets_a_polite_refusal_and_no_spouse_data(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(step("s1", "query_balance", {"account_id": "ACC-HM-001"})))
    login = log_in(bank, "liming")

    events = shown(send(agent, start_conversation(agent, login), SPOUSE_BALANCE))

    assert event_types(events) == ["step_failed", "refusal"]
    failed, refusal = events
    assert failed["reason"] == "forbidden_other_customer"
    assert refusal["reason"] == "secret"
    assert refusal["text"].startswith("抱歉")
    assert "其他客户" in refusal["text"]
    # None of her balances reaches the Customer.
    assert "8650" not in json.dumps(events) and "50000" not in json.dumps(events)
    # The Agent doesn't look for a way around it: no Operation ran, the model wasn't
    # asked again.
    assert [entry["event"] for entry in bank_audit(bank, login)] == ["tier_preview"]
    assert len(model.requests) == 1


def test_a_plan_touching_the_spouses_card_gets_no_plan_card_and_nothing_runs(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        plan(
            step("s1", "list_cards"),
            step("s2", "add_card_lock", {"card_id": "CARD-HM-001", "scope": "online"}),
        )
    )
    login = log_in(bank, "liming")

    events = shown(send(agent, start_conversation(agent, login), "把我老婆那张卡的网上支付锁了"))

    assert event_types(events) == ["step_failed", "refusal"]
    assert (events[0]["step_id"], events[0]["reason"]) == ("s2", "forbidden_other_customer")
    assert events[-1]["reason"] == "secret"
    assert [entry["event"] for entry in bank_audit(bank, login)] == ["tier_preview"]


# --- Full card numbers, passwords and verification codes ----------------------------


@pytest.mark.parametrize(
    "request_text",
    ["把我信用卡的完整卡号发给我", "我忘了登录密码，你帮我查一下是多少", "刚才那条短信验证码是多少？"],
)
def test_asking_for_a_secret_is_refused_and_nothing_is_revealed(
    agent: TestClient, bank: TestClient, model: FakeModel, request_text: str
) -> None:
    model.script(decline("secret", "list_cards"))
    login = log_in(bank, "liming")

    events = shown(send(agent, start_conversation(agent, login), request_text))

    assert event_types(events) == ["refusal"]
    refusal = events[-1]
    assert refusal["reason"] == "secret"
    assert refusal["text"].startswith("抱歉")
    for secret in ("完整卡号", "密码", "验证码"):
        assert secret in refusal["text"]  # it says what it never reveals...
    assert "后四位" in refusal["text"]  # ...and what it can show instead
    assert bank_audit(bank, login) == []
    (decline_tool,) = [
        tool
        for tool in model.requests[0].tools or []
        if tool["function"]["name"] == "decline_request"
    ]
    assert "secret" in decline_tool["function"]["parameters"]["properties"]["reason"]["enum"]


def test_a_secret_refusal_is_in_english_for_an_english_customer(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(decline("secret"))

    events = shown(
        send(agent, start_conversation(agent, log_in(bank, "liming")), "What's my card number?")
    )

    assert events[-1]["reason"] == "secret"
    assert events[-1]["text"].startswith("Sorry")


# --- A code typed into the chat ------------------------------------------------------

TO_HUSBAND = {"payee_id": "PAY-HM-001", "amount": "600.00", "note": ""}


def waiting_for_verification(
    agent: TestClient, bank: TestClient, model: FakeModel, login: Login
) -> tuple[str, dict[str, Any]]:
    """Two ¥600s to 李明, confirmed: the turn ends asking to verify the Red second one."""
    model.script(plan(step("s1", "transfer", TO_HUSBAND), step("s2", "transfer", TO_HUSBAND)))
    conversation = start_conversation(agent, login)
    plan_id = shown(send(agent, conversation, "给老公转两笔600"))[0]["plan_id"]
    requested = shown(act(agent, conversation, "confirm", plan_id=plan_id))[-1]
    assert requested["type"] == "verification_requested"
    return conversation, requested


@pytest.mark.parametrize("message", ["{code}", "验证码是{code}"])
def test_a_code_typed_into_the_chat_goes_no_further_and_points_to_the_verification_box(
    agent: TestClient, bank: TestClient, model: FakeModel, message: str
) -> None:
    login = log_in(bank, "hanmei")
    conversation, requested = waiting_for_verification(agent, bank, model, login)
    code = latest_code(bank, login)
    asked = len(model.requests)

    events = send(agent, conversation, message.format(code=code))

    # Not echoed: the Customer is pointed to the verification box, the challenge still open.
    assert event_types(events) == ["answer", "verification_requested"]
    assert "短信验证" in events[0]["text"]
    assert events[-1] == requested
    assert code not in json.dumps(events)
    # Not sent to the model, now or later, nor kept in the history Staff Members read.
    assert len(model.requests) == asked
    model.script(plan(step("s1", "query_balance")), say("您的活期账户余额为 8,050.00 元。"))
    send(agent, conversation, "我还有多少钱？")
    assert all(code not in request.text() for request in model.requests)
    assert code not in json.dumps(staff(agent, f"/staff/conversations/{conversation}"))


def test_after_a_code_in_the_chat_the_code_on_the_phone_still_verifies_the_step(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    login = log_in(bank, "hanmei")
    conversation, requested = waiting_for_verification(agent, bank, model, login)
    code = latest_code(bank, login)
    send(agent, conversation, code)
    model.script(say("已向李明转账两笔，各 600.00 元。"))

    verified = type_code(bank, login, requested["challenge_id"], code)
    events = shown(
        act(
            agent,
            conversation,
            "verified",
            plan_id=requested["plan_id"],
            step_id="s2",
            verification_token=verified["verification_token"],
        )
    )

    assert event_types(events) == ["step_started", "step_succeeded", "answer"]


# --- Every answer is checked before it is sent ---------------------------------------

# 老妈's account number, outside the Bank: a long number like a full card number.
FULL_NUMBER = "6222020200112233"


def answer_text(events: list[dict[str, Any]]) -> str:
    [answer] = [event for event in events if event["type"] == "answer"]
    text: str = answer["text"]
    return text


@pytest.mark.parametrize("written", [FULL_NUMBER, "6222 0202 0011 2233", "6222-0202-0011-2233"])
def test_a_full_card_number_in_an_answer_is_masked_even_when_a_bank_result_had_it(
    agent: TestClient, bank: TestClient, model: FakeModel, written: str
) -> None:
    # The Bank's audit trail repeats the number from the refused look-up.
    model.script(
        plan(
            step("s1", "query_balance", {"account_id": FULL_NUMBER}),
            step("s2", "read_audit_trail"),
        ),
        say(f"您查询的账户 {written} 不存在。"),
    )

    events = send(agent, start_conversation(agent, log_in(bank, "liming")), "查一下这个账户")

    [succeeded] = [event for event in events if event["type"] == "step_succeeded"]
    assert FULL_NUMBER in json.dumps(succeeded["output"])
    assert answer_text(events) == "您查询的账户 **** **** **** 2233 不存在。"


def test_a_verification_code_in_an_answer_is_masked_and_amounts_are_kept(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        plan(step("s1", "query_balance")),
        say("您的活期账户余额为 1180.00 元，验证码是 482913。"),
    )

    events = send(agent, start_conversation(agent, log_in(bank, "liming")), "我还有多少钱？")

    assert answer_text(events) == "您的活期账户余额为 1180.00 元，验证码是 ******。"


def test_the_banks_own_ids_in_an_answer_are_not_taken_for_codes(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    to_mum = {"payee_id": "PAY-LM-002", "amount": "200.00", "note": ""}
    model.script(plan(step("s1", "transfer", to_mum)), say("已向王秀英转账 200.00 元，流水号 TRF-000009。"))
    conversation = start_conversation(agent, log_in(bank, "liming"))
    plan_id = shown(send(agent, conversation, "给老妈转200"))[0]["plan_id"]

    events = act(agent, conversation, "confirm", plan_id=plan_id)

    assert answer_text(events) == "已向王秀英转账 200.00 元，流水号 TRF-000009。"


# --- The Agent never asks for a code or a password -----------------------------------


@pytest.mark.parametrize(
    "asking",
    [
        "您的余额为 1180.00 元。为了核实身份，请把短信验证码告诉我。",
        "您的余额为 1180.00 元。请提供您的登录密码，我帮您确认。",
        "Your balance is 1180.00 yuan. Please send me the verification code to continue.",
        "Your balance is 1180.00 yuan. What's your password?",
    ],
)
def test_an_answer_asking_for_a_code_or_password_is_rewritten(
    agent: TestClient, bank: TestClient, model: FakeModel, asking: str
) -> None:
    model.script(plan(step("s1", "query_balance")), say(asking), say("您的余额为 1180.00 元。"))

    events = send(agent, start_conversation(agent, log_in(bank, "liming")), "我还有多少钱？")

    assert answer_text(events) == "您的余额为 1180.00 元。"
    assert "Never ask the Customer for a verification code or a password" in (
        model.requests[-1].text()
    )


def test_an_answer_that_keeps_asking_for_a_code_falls_back_to_the_banks_result(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    asking = "您的余额为 1180.00 元。请把验证码发给我。"
    model.script(plan(step("s1", "query_balance")), say(asking), say(asking))

    events = send(agent, start_conversation(agent, log_in(bank, "liming")), "我还有多少钱？")

    text = answer_text(events)
    assert "验证码" not in text
    assert text.startswith("以下是银行返回的结果") and "1180.00" in text


def test_the_agents_own_words_point_to_the_verification_box_and_never_ask_for_the_code(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    login = log_in(bank, "hanmei")
    conversation, _ = waiting_for_verification(agent, bank, model, login)

    text = answer_text(send(agent, conversation, latest_code(bank, login)))

    assert "请不要把验证码作为聊天消息发送" in text and "短信验证" in text
