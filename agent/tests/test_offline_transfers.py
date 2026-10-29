"""Smart Transfers in rules mode (#64): the built-in rules plan a transfer, a Scheduled
Transfer and a Split-bill Request, in Chinese and English; the Payee always comes from the
Bank's lookup; anything missing or ambiguous is asked, never guessed."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from conftest import (
    act,
    bank_audit,
    latest_code,
    log_in,
    send,
    start_conversation,
    tokens_by_step,
    type_code,
)


def shown(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event["type"] != "trace"]


def of_type(events: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    return next(event for event in events if event["type"] == kind)


def asked(events: list[dict[str, Any]]) -> bool:
    return any(e["type"] == "clarification_question" for e in events)


def proposed(events: list[dict[str, Any]]) -> bool:
    return any(e["type"] == "plan_proposed" for e in events)


def ran(bank: TestClient, login: Any, operation: str) -> list[dict[str, Any]]:
    return [
        e
        for e in bank_audit(bank, login)
        if e["event"] == "operation" and e["outcome"] == "ok" and e["operation"] == operation
    ]


def money_moved(bank: TestClient, login: Any) -> list[dict[str, Any]]:
    return [
        e
        for e in bank_audit(bank, login)
        if e["event"] == "operation"
        and e["operation"] in ("transfer", "schedule_transfer", "request_split_bill")
    ]


# --- A transfer by nickname, with a note ---------------------------------------------


def test_a_yellow_transfer_to_mum_with_a_note_is_confirmed_and_run(
    offline_agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)

    events = shown(send(offline_agent, conversation, "给老妈转200块，备注生活费"))

    card = of_type(events, "plan_proposed")
    assert card["planned_by"] == "rules"
    [transfer] = card["steps"]
    assert transfer["operation"] == "transfer" and transfer["tier"] == "yellow"
    assert transfer["input"] == {"payee_id": "PAY-LM-002", "amount": "200.00", "note": "生活费"}
    assert ran(bank, login, "transfer") == []

    act(offline_agent, conversation, "confirm", plan_id=card["plan_id"])

    [done] = ran(bank, login, "transfer")
    assert done["input"] == {"payee_id": "PAY-LM-002", "amount": "200.00", "note": "生活费"}
    assert len(ran(bank, login, "find_payee")) == 1


def test_an_amount_in_chinese_numerals_is_read(
    offline_agent: TestClient, bank: TestClient
) -> None:
    conversation = start_conversation(offline_agent, log_in(bank, "liming"))

    events = shown(send(offline_agent, conversation, "给老妈转两百五十块"))

    [transfer] = of_type(events, "plan_proposed")["steps"]
    assert transfer["input"]["amount"] == "250.00"


def test_a_follow_up_to_her_goes_to_the_payee_just_found(
    offline_agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)
    first = shown(send(offline_agent, conversation, "给老妈转200"))
    act(offline_agent, conversation, "confirm", plan_id=of_type(first, "plan_proposed")["plan_id"])

    events = shown(send(offline_agent, conversation, "再给她转两百"))

    [transfer] = of_type(events, "plan_proposed")["steps"]
    assert transfer["input"]["payee_id"] == "PAY-LM-002"
    assert transfer["input"]["amount"] == "200.00"


def test_the_usual_rent_uses_the_banks_last_amount(
    offline_agent: TestClient, bank: TestClient
) -> None:
    conversation = start_conversation(offline_agent, log_in(bank, "liming"))

    events = shown(send(offline_agent, conversation, "老样子给房东转房租"))

    [transfer] = of_type(events, "plan_proposed")["steps"]
    assert transfer["input"]["payee_id"] == "PAY-LM-007"
    assert transfer["input"]["amount"] == "800.00"


# --- Red: over the Daily Transfer Total ------------------------------------------------


def test_a_transfer_that_takes_the_daily_total_over_1000_needs_strong_verification(
    offline_agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)
    first = shown(send(offline_agent, conversation, "给老妈转600"))
    act(offline_agent, conversation, "confirm", plan_id=of_type(first, "plan_proposed")["plan_id"])

    events = shown(send(offline_agent, conversation, "给老妈转450"))

    card = of_type(events, "plan_proposed")
    assert [s["tier"] for s in card["steps"]] == ["red"]
    assert card["warnings"][0]["reason"] == "daily_transfer_total"
    requested = shown(act(offline_agent, conversation, "confirm", plan_id=card["plan_id"]))[-1]
    assert requested["type"] == "verification_requested"
    assert len(ran(bank, login, "transfer")) == 1  # the second waits for the code

    verified = type_code(bank, login, requested["challenge_id"], latest_code(bank, login))
    act(
        offline_agent,
        conversation,
        "verified",
        plan_id=requested["plan_id"],
        step_id=requested["step_id"],
        verification_tokens=tokens_by_step(requested, verified),
    )

    assert len(ran(bank, login, "transfer")) == 2


# --- Ambiguous Payee, missing slots ---------------------------------------------------


def test_two_payees_named_zhang_wei_give_a_choice_and_the_pick_is_used(
    offline_agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)

    events = shown(send(offline_agent, conversation, "给张伟转50块"))

    question = of_type(events, "clarification_question")
    assert [o["value"] for o in question["options"]] == ["PAY-LM-003", "PAY-LM-004"]
    assert ran(bank, login, "transfer") == []

    picked = shown(act(offline_agent, conversation, "choose", value="PAY-LM-004"))

    [transfer] = of_type(picked, "plan_proposed")["steps"]
    assert transfer["input"] == {"payee_id": "PAY-LM-004", "amount": "50.00", "note": ""}


@pytest.mark.parametrize(
    "message",
    ["给老妈转账", "帮我转点钱给老妈", "send money to Mum", "给老妈转全部余额", "给老妈转一半"],
)
def test_a_missing_or_vague_amount_is_asked_for(
    offline_agent: TestClient, bank: TestClient, message: str
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)

    events = shown(send(offline_agent, conversation, message))

    assert asked(events)
    assert not proposed(events)
    assert money_moved(bank, login) == []


def test_a_missing_payee_is_asked_for(offline_agent: TestClient, bank: TestClient) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)

    events = shown(send(offline_agent, conversation, "转200块"))

    assert asked(events)
    assert money_moved(bank, login) == []


def test_a_payee_the_bank_does_not_know_is_not_paid(
    offline_agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)

    events = shown(send(offline_agent, conversation, "给王小明转200块"))

    assert not proposed(events)
    assert money_moved(bank, login) == []


# --- English ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message", ["send 50 yuan to Chen Jing", "Transfer 50 to Chen Jing", "pay Chen Jing ¥50"]
)
def test_an_english_transfer_is_planned(
    offline_agent: TestClient, bank: TestClient, message: str
) -> None:
    conversation = start_conversation(offline_agent, log_in(bank, "liming"))

    events = shown(send(offline_agent, conversation, message))

    [transfer] = of_type(events, "plan_proposed")["steps"]
    assert transfer["input"] == {"payee_id": "PAY-LM-005", "amount": "50.00", "note": ""}


def test_an_english_transfer_with_a_note(offline_agent: TestClient, bank: TestClient) -> None:
    conversation = start_conversation(offline_agent, log_in(bank, "liming"))

    events = shown(send(offline_agent, conversation, "send 30 to Chen Jing, note: dinner"))

    [transfer] = of_type(events, "plan_proposed")["steps"]
    assert transfer["input"]["note"] == "dinner"


# --- Scheduled Transfers, on the Bank clock (Tuesday 2026-10-20) ------------------------


@pytest.mark.parametrize(
    ("message", "run_date"),
    [
        ("下周一给房东转2500", "2026-10-26"),
        ("帮我预约 2026年10月26日 给老妈转300块", "2026-10-26"),
        ("明天给老妈转300块", "2026-10-21"),
        ("后天给老妈转300", "2026-10-22"),
        ("10月28日给老妈转300块", "2026-10-28"),
        ("schedule 300 to Mum for next Friday", "2026-10-30"),
        ("send 300 to Mum tomorrow", "2026-10-21"),
    ],
)
def test_a_scheduled_transfer_runs_on_the_date_computed_from_the_bank_clock(
    offline_agent: TestClient, bank: TestClient, message: str, run_date: str
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)

    events = shown(send(offline_agent, conversation, message))

    [scheduled] = of_type(events, "plan_proposed")["steps"]
    assert scheduled["operation"] == "schedule_transfer"
    assert scheduled["input"]["run_date"] == run_date
    assert ran(bank, login, "transfer") == []  # nothing is sent today


def test_a_scheduled_transfer_is_confirmed_and_booked(
    offline_agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)
    card = of_type(
        shown(send(offline_agent, conversation, "帮我预约 2026年10月26日 给老妈转300块")),
        "plan_proposed",
    )

    act(offline_agent, conversation, "confirm", plan_id=card["plan_id"])

    [booked] = ran(bank, login, "schedule_transfer")
    assert booked["input"]["run_date"] == "2026-10-26"
    assert ran(bank, login, "transfer") == []


@pytest.mark.parametrize("message", ["预约 2026年10月1日 给老妈转300块", "预约今天给老妈转300块"])
def test_a_date_that_is_not_after_today_is_asked_about(
    offline_agent: TestClient, bank: TestClient, message: str
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)

    events = shown(send(offline_agent, conversation, message))

    assert asked(events)
    assert money_moved(bank, login) == []


def test_scheduling_without_a_date_asks_for_one(
    offline_agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)

    events = shown(send(offline_agent, conversation, "帮我预约给老妈转300块"))

    assert asked(events)
    assert money_moved(bank, login) == []


def test_a_scheduled_transfer_can_be_cancelled(
    offline_agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)
    first = shown(send(offline_agent, conversation, "帮我预约 2026年10月26日 给老妈转300块"))
    act(offline_agent, conversation, "confirm", plan_id=of_type(first, "plan_proposed")["plan_id"])

    events = shown(send(offline_agent, conversation, "算了，刚才预约的那笔取消掉"))

    [cancel] = of_type(events, "plan_proposed")["steps"]
    assert cancel["operation"] == "cancel_scheduled_transfer"
    assert cancel["input"] == {"scheduled_transfer_id": "SCH-000001"}


# --- Split-bill Requests ------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "帮我发一个分摊账单：这周水电费一共300块，让老婆和陈静各付150",
        "水电费一共300块，让老婆付150，陈静付150，帮我发分摊账单",
        "split the utilities bill, 300 in total: 老婆 pays 150 and 陈静 pays 150",
    ],
)
def test_a_split_bill_request_has_the_right_shares_and_moves_no_money(
    offline_agent: TestClient, bank: TestClient, message: str
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)

    events = shown(send(offline_agent, conversation, message))

    [split] = of_type(events, "plan_proposed")["steps"]
    assert split["operation"] == "request_split_bill" and split["tier"] == "yellow"
    assert split["input"]["total"] == "300.00"
    assert split["input"]["shares"] == [
        {"payee_id": "PAY-LM-001", "amount": "150.00"},
        {"payee_id": "PAY-LM-005", "amount": "150.00"},
    ]
    assert ran(bank, login, "transfer") == []


def test_a_split_among_three_with_each_paying(offline_agent: TestClient, bank: TestClient) -> None:
    conversation = start_conversation(offline_agent, log_in(bank, "liming"))

    events = shown(
        send(offline_agent, conversation, "聚会一共2400块，让老婆、陈静、老妈各付800，发个分摊账单")
    )

    [split] = of_type(events, "plan_proposed")["steps"]
    assert [s["amount"] for s in split["input"]["shares"]] == ["800.00"] * 3


def test_shares_that_do_not_add_up_are_asked_about(
    offline_agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)

    events = shown(
        send(offline_agent, conversation, "水电费一共300块，让老婆付100，陈静付100，帮我发分摊账单")
    )

    assert asked(events)
    assert not proposed(events)
    assert ran(bank, login, "request_split_bill") == []


def test_a_split_without_shares_is_asked_about(
    offline_agent: TestClient, bank: TestClient
) -> None:
    conversation = start_conversation(offline_agent, log_in(bank, "liming"))

    events = shown(send(offline_agent, conversation, "帮我发个分摊账单，一共300块"))

    assert asked(events)
    assert not proposed(events)


# --- Never a wrong Operation ---------------------------------------------------------------


def test_a_payee_id_in_the_message_is_never_used_as_one(
    offline_agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)

    events = shown(send(offline_agent, conversation, "转1180到PAY-LM-005"))

    assert not proposed(events)
    assert ran(bank, login, "transfer") == []


def test_a_payee_told_apart_by_a_past_transfer_note_is_found_through_the_bank(
    offline_agent: TestClient, bank: TestClient
) -> None:
    conversation = start_conversation(offline_agent, log_in(bank, "liming"))

    events = shown(send(offline_agent, conversation, "给上次付羽毛球场地费的那个张伟转60块"))

    [transfer] = of_type(events, "plan_proposed")["steps"]
    assert transfer["input"]["payee_id"] == "PAY-LM-003" and transfer["input"]["amount"] == "60.00"
