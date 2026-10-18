"""The hallucination guard: the Customer is never told a made-up balance or result.

Every amount, date, card and Payee an answer mentions must appear in a Bank result from
this turn or earlier in the conversation. Otherwise the answer is regenerated, and if
the new one still fails, the Customer gets the raw Bank result instead.
"""

import json

import pytest
from fastapi.testclient import TestClient

from conftest import event_types, log_in, send, start_conversation
from fake_model import FakeModel, plan, say, step

BALANCE_QUESTION = "我卡里还有多少钱？"


def answer_text(events: list[dict[str, object]]) -> str:
    answers = [e for e in events if e["type"] == "answer"]
    assert len(answers) == 1, events
    text = answers[0]["text"]
    assert isinstance(text, str)
    return text


# --- An invented fact is never sent --------------------------------------------------


def test_an_answer_with_an_invented_balance_is_regenerated_and_never_sent(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        plan(step("s1", "query_balance")),
        say("您的活期账户余额为 5,000.00 元。"),
        say("您的活期账户余额为 1180.00 元。"),
    )

    events = send(agent, start_conversation(agent, log_in(bank, "liming")), BALANCE_QUESTION)

    assert event_types(events) == ["step_started", "step_succeeded", "answer"]
    assert answer_text(events) == "您的活期账户余额为 1180.00 元。"
    assert "5,000.00" not in json.dumps(events, ensure_ascii=False)


def test_when_the_regenerated_answer_still_fails_the_customer_gets_the_raw_bank_result(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        plan(step("s1", "query_balance")),
        say("您的活期账户余额为 5,000.00 元。"),
        say("您的余额大约 ¥3,200。"),
    )

    events = send(agent, start_conversation(agent, log_in(bank, "liming")), BALANCE_QUESTION)

    text = answer_text(events)
    assert "ACC-LM-001" in text
    assert "1180.00" in text
    assert "5,000.00" not in text
    assert "3,200" not in text
    assert len(model.requests) == 3  # the Plan, the draft and one regeneration


# --- Amounts match however they're written -------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        "您的余额为 ¥1,180。",
        "您的余额为 1180元。",
        "您的余额为 1180.00。",
        "您的余额为￥1,180.00 元。",
        "您还有 1180 块钱。",
        "Your balance is RMB 1,180.00.",
        "Your balance is 1180 yuan.",
    ],
)
def test_an_amount_the_bank_returned_matches_however_it_is_written(
    agent: TestClient, bank: TestClient, model: FakeModel, reply: str
) -> None:
    model.script(plan(step("s1", "query_balance")), say(reply))

    events = send(agent, start_conversation(agent, log_in(bank, "liming")), BALANCE_QUESTION)

    assert answer_text(events) == reply


@pytest.mark.parametrize("reply", ["您的储蓄账户有 5万元。", "您的储蓄账户有 5 万。"])
def test_an_amount_in_wan_matches_the_banks(
    agent: TestClient, bank: TestClient, model: FakeModel, reply: str
) -> None:
    model.script(plan(step("s1", "query_balance")), say(reply))

    events = send(agent, start_conversation(agent, log_in(bank, "hanmei")), BALANCE_QUESTION)

    assert answer_text(events) == reply


@pytest.mark.parametrize(
    "invented", ["¥1,108", "1810元", "1180.50", "1,180.01 元", "11.80 元", "1.18万元", "2万元", "3 万块"]
)
def test_an_amount_close_to_the_banks_but_different_is_not_sent(
    agent: TestClient, bank: TestClient, model: FakeModel, invented: str
) -> None:
    model.script(
        plan(step("s1", "query_balance")),
        say(f"您的余额为 {invented}。"),
        say("您的余额为 1180.00 元。"),
    )

    events = send(agent, start_conversation(agent, log_in(bank, "liming")), BALANCE_QUESTION)

    assert answer_text(events) == "您的余额为 1180.00 元。"


# --- Dates, cards and Payees are checked too -----------------------------------------

BIRTHDAY_QUESTION = "我老婆生日是哪天？"


@pytest.mark.parametrize(
    "reply",
    [
        "韩梅梅的生日是10月28日。",
        "韩梅梅的生日是 1994年10月28日。",
        "她的生日是 1994-10-28。",
        "她的生日是10月28号。",
        "Her birthday is October 28.",
        "Her birthday is 28 Oct 1994.",
    ],
)
def test_a_date_the_bank_returned_is_sent(
    agent: TestClient, bank: TestClient, model: FakeModel, reply: str
) -> None:
    model.script(plan(step("s1", "query_profile")), say(reply))

    events = send(agent, start_conversation(agent, log_in(bank, "liming")), BIRTHDAY_QUESTION)

    assert answer_text(events) == reply


@pytest.mark.parametrize(
    "invented",
    [
        "韩梅梅的生日是10月30日。",
        "韩梅梅的生日是 1995年10月28日。",
        "她的生日是 1994-10-29。",
        "Her birthday is October 30.",
    ],
)
def test_an_invented_date_is_not_sent(
    agent: TestClient, bank: TestClient, model: FakeModel, invented: str
) -> None:
    model.script(
        plan(step("s1", "query_profile")), say(invented), say("韩梅梅的生日是10月28日。")
    )

    events = send(agent, start_conversation(agent, log_in(bank, "liming")), BIRTHDAY_QUESTION)

    assert answer_text(events) == "韩梅梅的生日是10月28日。"


CARDS_QUESTION = "我有哪些卡？"


@pytest.mark.parametrize(
    "reply",
    [
        "您的工商借记卡尾号 1234，招商借记卡尾号5678。",
        "信用卡（**** 9012）的月额度为 20000.00 元。",
        "Your credit card ending in 9012 is active.",
        "CARD-LM-002 已锁定境外交易。",
    ],
)
def test_a_card_the_bank_returned_is_sent(
    agent: TestClient, bank: TestClient, model: FakeModel, reply: str
) -> None:
    model.script(plan(step("s1", "list_cards")), say(reply))

    events = send(agent, start_conversation(agent, log_in(bank, "liming")), CARDS_QUESTION)

    assert answer_text(events) == reply


@pytest.mark.parametrize(
    "invented",
    [
        "您的工商借记卡尾号 4321。",
        "信用卡（**** 3456）可以正常使用。",  # 韩梅梅's card, not in 李明's results
        "Your card ending in 8888 is active.",
        "您的卡号是 6222020200119012。",
        "CARD-LM-009 已锁定境外交易。",
    ],
)
def test_an_invented_card_is_not_sent(
    agent: TestClient, bank: TestClient, model: FakeModel, invented: str
) -> None:
    model.script(plan(step("s1", "list_cards")), say(invented), say("您有三张卡。"))

    events = send(agent, start_conversation(agent, log_in(bank, "liming")), CARDS_QUESTION)

    assert answer_text(events) == "您有三张卡。"


@pytest.mark.parametrize(
    "reply",
    [
        "您的配偶韩梅梅可以作为收款人，需要我向韩梅梅转账吗？",
        "要转给韩梅梅吗？",
        "收款人：韩梅梅。",
        "Shall I send it to 韩梅梅?",
    ],
)
def test_a_payee_the_bank_returned_is_sent(
    agent: TestClient, bank: TestClient, model: FakeModel, reply: str
) -> None:
    model.script(plan(step("s1", "query_profile")), say(reply))

    events = send(agent, start_conversation(agent, log_in(bank, "liming")), BIRTHDAY_QUESTION)

    assert answer_text(events) == reply


@pytest.mark.parametrize(
    "invented",
    [
        "已向张伟转账。",
        "已转给王芳，对方已收到。",
        "收款人：张伟。",
        "收款人是王秀英。",  # 李明's Payee, but in no Bank result of this conversation
        "I've sent it to 张伟.",
    ],
)
def test_an_invented_payee_is_not_sent(
    agent: TestClient, bank: TestClient, model: FakeModel, invented: str
) -> None:
    model.script(
        plan(step("s1", "query_profile")), say(invented), say("韩梅梅的生日是10月28日。")
    )

    events = send(agent, start_conversation(agent, log_in(bank, "liming")), BIRTHDAY_QUESTION)

    assert answer_text(events) == "韩梅梅的生日是10月28日。"
