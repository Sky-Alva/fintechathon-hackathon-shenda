"""Payees the way the Customer names them (#39): the Bank's Payee lookup turns 老妈, a name,
a phone number or a past transfer note into a Payee before a transfer is proposed; one
clarifying question when the Bank finds several, never a guess."""

import json
from typing import Any

from fastapi.testclient import TestClient

from conftest import (
    Login,
    act,
    bank_audit,
    event_types,
    log_in,
    send,
    staff,
    start_conversation,
    take_over,
)
from fake_model import FakeModel, clarify, plan, say, step


def shown(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event["type"] != "trace"]


def find(query: str) -> dict[str, Any]:
    return step("s1", "find_payee", {"query": query})


def pay(amount: str, payee_id: str | None = None, id: str = "s2") -> dict[str, Any]:
    """A transfer; without `payee_id` it waits on the lookup s1."""
    if payee_id is None:
        return step(id, "transfer", {"amount": amount}, depends_on=["s1"])
    return step(id, "transfer", {"payee_id": payee_id, "amount": amount, "note": ""})


# --- Two Payees named 张伟: one question, never a guess --------------------------------


def test_two_payees_with_the_same_name_get_one_clarifying_question_listing_both(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(find("张伟"), pay("100.00")))
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)

    events = shown(send(agent, conversation, "给张伟转100"))

    assert event_types(events) == [
        "step_started", "step_failed", "clarification_question"
    ]
    question = events[-1]
    assert "张伟" in question["text"]
    assert [option["value"] for option in question["options"]] == ["PAY-LM-003", "PAY-LM-004"]
    labels = " ".join(option["label"] for option in question["options"])
    assert "137****2222" in labels and "135****4444" in labels
    # No full phone or account number, and the model wasn't asked to pick one.
    for secret in ("13711112222", "13533334444", "6217000011112222", "6217000033334444"):
        assert secret not in json.dumps(question, ensure_ascii=False)
    assert len(model.requests) == 1
    assert not [e for e in bank_audit(bank, login) if e["operation"] == "transfer"]


def ask_which_zhang_wei(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> tuple[Login, str]:
    model.script(plan(find("张伟"), pay("100.00")))
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    send(agent, conversation, "给张伟转100")
    return login, conversation


def transfers_run(bank: TestClient, login: Login) -> list[tuple[str, str]]:
    return [
        (e["input"]["payee_id"], e["input"]["amount"])
        for e in bank_audit(bank, login)
        if e["event"] == "operation" and e["operation"] == "transfer" and e["outcome"] == "ok"
    ]


def test_choosing_a_candidate_leads_to_a_plan_card_for_that_payee(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    login, conversation = ask_which_zhang_wei(agent, bank, model)
    model.script(plan(pay("100.00", "PAY-LM-004", id="s1")), say("已向张伟转账 100.00 元。"))

    events = shown(act(agent, conversation, "choose", value="PAY-LM-004"))

    assert event_types(events) == ["plan_proposed", "confirmation_requested"]
    assert events[0]["steps"][0]["input"]["payee_id"] == "PAY-LM-004"
    # The Planner was told which candidate the Customer picked.
    assert "PAY-LM-004" in model.requests[-1].text()
    assert "135****4444" in model.requests[-1].text()
    act(agent, conversation, "confirm", plan_id=events[0]["plan_id"])
    assert transfers_run(bank, login) == [("PAY-LM-004", "100.00")]


def test_a_choice_that_is_not_an_option_is_refused_and_the_question_still_waits(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    _, conversation = ask_which_zhang_wei(agent, bank, model)

    response = agent.post(
        f"/conversations/{conversation}/actions", json={"action": "choose", "value": "PAY-LM-001"}
    )

    assert (response.status_code, response.json()) == (422, {"error": "unknown_option"})
    model.script(plan(pay("100.00", "PAY-LM-003", id="s1")))
    events = act(agent, conversation, "choose", value="PAY-LM-003")
    assert event_types(events)[-1] == "confirmation_requested"


def test_a_choice_with_no_question_waiting_is_a_conflict(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    conversation = start_conversation(agent, log_in(bank, "liming"))

    response = agent.post(
        f"/conversations/{conversation}/actions", json={"action": "choose", "value": "PAY-LM-003"}
    )

    assert (response.status_code, response.json()) == (409, {"error": "plan_not_pending"})


def test_the_customer_can_answer_the_question_in_words_instead(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    _, conversation = ask_which_zhang_wei(agent, bank, model)
    model.script(plan(pay("100.00", "PAY-LM-003", id="s1")))

    events = shown(send(agent, conversation, "尾号2222的那个"))

    assert event_types(events) == ["plan_proposed", "confirmation_requested"]
    # The question, with both candidates' IDs, is in the model's context.
    seen = model.requests[-1].text()
    assert "PAY-LM-003" in seen and "PAY-LM-004" in seen
    gone = agent.post(
        f"/conversations/{conversation}/actions", json={"action": "choose", "value": "PAY-LM-004"}
    )
    assert gone.status_code == 409


# --- Who the Plan card pays, and whether they're new ----------------------------------


def test_the_plan_card_names_the_payee_the_lookup_found_and_asks_nothing(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(find("老妈"), pay("200.00")), plan(pay("200.00", "PAY-LM-002")))
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = shown(send(agent, conversation, "给老妈转200"))

    assert event_types(events) == [
        "step_started", "step_succeeded", "plan_proposed", "confirmation_requested"
    ]
    proposed = events[2]
    assert proposed["steps"][0]["input"] == {
        "payee_id": "PAY-LM-002", "amount": "200.00", "note": ""
    }
    assert proposed["payees"] == [
        {
            "step_id": "s2",
            "payee_id": "PAY-LM-002",
            "name": "王秀英",
            "nickname": "老妈",
            "phone": "139****0003",
            "is_new": False,
        }
    ]


def test_a_payee_never_paid_before_is_marked_new_on_the_plan_card(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(find("陈静"), pay("50.00")), plan(pay("50.00", "PAY-LM-005")))
    conversation = start_conversation(agent, log_in(bank, "liming"))

    proposed = shown(send(agent, conversation, "给陈静转50块"))[-2]

    assert [(p["payee_id"], p["is_new"]) for p in proposed["payees"]] == [("PAY-LM-005", True)]


# --- Follow-ups go to the same Payee --------------------------------------------------


def test_a_follow_up_goes_to_the_payee_found_earlier_without_asking_again(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        plan(find("陈静"), pay("50.00")),
        plan(pay("50.00", "PAY-LM-005")),
        say("已向陈静转账 50.00 元。"),
    )
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    first = shown(send(agent, conversation, "给陈静转50块"))[-2]
    act(agent, conversation, "confirm", plan_id=first["plan_id"])
    model.script(plan(pay("200.00", "PAY-LM-005", id="s1")), say("已向陈静转账 200.00 元。"))

    events = shown(send(agent, conversation, "再给她转两百"))

    # The Planner knows who "her" is: the Payee the Bank found, now paid once.
    # Bank data, so in a marked data block, never in the instructions.
    payees_seen = [m for m in model.requests[-1].messages if "PAY-LM-005" in m["content"]]
    assert any("陈静" in m["content"] for m in payees_seen)
    assert all(m["role"] != "system" for m in payees_seen)
    assert any(m["content"].startswith("<bank_data>") for m in payees_seen)
    assert event_types(events) == ["plan_proposed", "confirmation_requested"]
    assert [(p["payee_id"], p["is_new"]) for p in events[0]["payees"]] == [
        ("PAY-LM-005", False)
    ]
    act(agent, conversation, "confirm", plan_id=events[0]["plan_id"])
    assert transfers_run(bank, login) == [("PAY-LM-005", "50.00"), ("PAY-LM-005", "200.00")]


# --- "The usual rent to my landlord": found by past transfer note ----------------------


def test_the_landlord_is_found_by_past_rent_notes_with_the_last_rent_amount(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(find("房租"), pay("800.00")), plan(pay("800.00", "PAY-LM-007")))
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = shown(send(agent, conversation, "老样子给房东转房租"))

    # The Planner completes the transfer from the lookup: the landlord and last rent.
    completing = model.requests[-1].text()
    assert "PAY-LM-007" in completing and "800.00" in completing
    assert event_types(events)[-2:] == ["plan_proposed", "confirmation_requested"]
    assert [(p["payee_id"], p["name"]) for p in events[-2]["payees"]] == [
        ("PAY-LM-007", "赵建国")
    ]


def test_the_planner_can_ask_for_what_the_bank_does_not_show(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(find("陈静"), pay("50.00")), clarify("您要给陈静转多少钱？"))
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)

    events = shown(send(agent, conversation, "老样子给陈静转钱"))

    # Asking is on offer while completing the Plan from the lookup.
    tools = [tool["function"]["name"] for tool in model.requests[-1].tools or []]
    assert "ask_clarification" in tools
    assert events[-1] == {
        "type": "clarification_question", "text": "您要给陈静转多少钱？", "options": []
    }
    model.script(plan(pay("50.00", "PAY-LM-005", id="s1")))
    answered = shown(send(agent, conversation, "50"))
    assert "您要给陈静转多少钱？" in model.requests[-1].text()
    assert event_types(answered) == ["plan_proposed", "confirmation_requested"]
    assert transfers_run(bank, login) == []


def test_an_unambiguous_request_gets_no_clarifying_question(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(find("13533334444"), pay("30.00")), plan(pay("30.00", "PAY-LM-004")))
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = shown(send(agent, conversation, "给手机号 13533334444 的张伟转30块"))

    assert "clarification_question" not in event_types(events)
    assert events[-2]["payees"][0]["payee_id"] == "PAY-LM-004"


# --- Pinyin names and relationship words (#57) ----------------------------------------


def payees_on_the_card(events: list[dict[str, Any]]) -> list[tuple[str, str]]:
    assert event_types(events)[-2:] == ["plan_proposed", "confirmation_requested"]
    return [(p["payee_id"], p["name"]) for p in events[-2]["payees"]]


def test_a_payee_named_in_pinyin_gets_a_plan_card_for_their_chinese_name(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(find("Han Meimei"), pay("100.00")), plan(pay("100.00", "PAY-LM-001")))
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = shown(send(agent, conversation, "transfer 100 to Han Meimei"))

    assert payees_on_the_card(events) == [("PAY-LM-001", "韩梅梅")]
    assert events[-2]["steps"][0]["input"]["amount"] == "100.00"


def test_my_wife_is_looked_up_by_her_chinese_nickname_without_a_question(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(find("老婆"), pay("100.00")), plan(pay("100.00", "PAY-LM-001")))
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = shown(send(agent, conversation, "send 100 to my wife"))

    # The Planner is told which Chinese nicknames an English relationship word means.
    instructions = model.requests[0].messages[0]["content"]
    assert "wife" in instructions and "老婆" in instructions
    assert "clarification_question" not in event_types(events)
    assert payees_on_the_card(events) == [("PAY-LM-001", "韩梅梅")]


def test_my_husband_is_found_by_his_nickname_for_the_other_customer(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(find("老公"), pay("100.00")), plan(pay("100.00", "PAY-HM-001")))
    conversation = start_conversation(agent, log_in(bank, "hanmei"))

    events = shown(send(agent, conversation, "send 100 to my husband"))

    assert payees_on_the_card(events) == [("PAY-HM-001", "李明")]


def test_after_a_payee_is_not_found_a_relationship_word_is_looked_up_again(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        plan(find("Lily"), pay("1000.00")), clarify("I couldn't find Lily. Who is she?")
    )
    conversation = start_conversation(agent, log_in(bank, "liming"))
    first = shown(send(agent, conversation, "transfer 1000 to Lily"))
    assert event_types(first) == ["step_started", "step_failed", "clarification_question"]
    assert first[1]["reason"] == "not_found"
    model.script(plan(find("老婆"), pay("1000.00")), plan(pay("1000.00", "PAY-LM-001")))

    events = shown(send(agent, conversation, "she's my wife"))

    # The Planner plans the lookup again, knowing the first one found no "Lily".
    assert "Lily" in model.requests[-2].text()
    assert "clarification_question" not in event_types(events)
    assert payees_on_the_card(events) == [("PAY-LM-001", "韩梅梅")]


# --- The question in the Customer's language, the staff history, a takeover ------------


def test_the_question_is_in_english_for_a_customer_writing_in_english(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(find("张伟"), pay("100.00")))
    conversation = start_conversation(agent, log_in(bank, "liming"))

    question = shown(send(agent, conversation, "Send 100 to Zhang Wei"))[-1]

    assert question["text"] == "I found 2 Payees matching “张伟”. Which one do you mean?"
    assert question["options"][0]["label"] == "张伟 (137****2222)"


def test_the_staff_history_records_the_question_and_the_choice(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    _, conversation = ask_which_zhang_wei(agent, bank, model)
    model.script(plan(pay("100.00", "PAY-LM-004", id="s1")))
    act(agent, conversation, "choose", value="PAY-LM-004")

    entries = staff(agent, f"/staff/conversations/{conversation}")["entries"]

    kinds = [e["type"] for e in entries if e["type"] != "trace"]
    asked = kinds.index("clarification_question")
    assert kinds[asked + 1] == "customer_action"
    assert {"type": "customer_action", "action": "choose", "plan_id": None} in entries


def test_no_choice_is_acted_on_while_a_staff_member_has_the_conversation(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    _, conversation = ask_which_zhang_wei(agent, bank, model)
    take_over(agent, conversation, "王经理")

    response = agent.post(
        f"/conversations/{conversation}/actions", json={"action": "choose", "value": "PAY-LM-004"}
    )

    assert response.status_code == 409
    assert response.json() == {"error": "already_handed_over", "staff": "王经理"}
