from typing import Any

from fastapi.testclient import TestClient

from conftest import (
    Login,
    act,
    bank_audit,
    event_types,
    log_in,
    send,
    start_conversation,
    traces,
)
from fake_model import FakeModel, ModelReply, plan, say, step

TWO_TRANSFERS = "给老妈转200，再给老婆转300"
TO_MUM = {"payee_id": "PAY-LM-002", "amount": "200.00", "note": ""}
TO_WIFE = {"payee_id": "PAY-LM-001", "amount": "300.00", "note": ""}


def shown(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The turn's events as the chat shows them, leaving out the decision trace."""
    return [event for event in events if event["type"] != "trace"]


def two_transfers() -> ModelReply:
    return plan(step("s1", "transfer", TO_MUM), step("s2", "transfer", TO_WIFE))


# --- A Plan with Yellow steps gets one Plan card --------------------------------------


def test_a_two_transfer_request_yields_one_plan_card_with_both_yellow_steps(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(two_transfers())
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = shown(send(agent, conversation, TWO_TRANSFERS))

    assert event_types(events) == ["plan_proposed", "confirmation_requested"]
    proposed, requested = events
    assert proposed["steps"] == [
        {"id": "s1", "operation": "transfer", "input": TO_MUM, "depends_on": [], "tier": "yellow"},
        {"id": "s2", "operation": "transfer", "input": TO_WIFE, "depends_on": [], "tier": "yellow"},
    ]
    assert proposed["refusals"] == []
    assert requested == {
        "type": "confirmation_requested",
        "plan_id": proposed["plan_id"],
        "step_ids": ["s1", "s2"],
    }


def test_the_plan_card_shows_a_step_the_bank_says_can_never_run_with_its_reason(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    too_much = {"payee_id": "PAY-LM-001", "amount": "5000.00", "note": ""}
    model.script(plan(step("s1", "transfer", TO_MUM), step("s2", "transfer", too_much)))
    conversation = start_conversation(agent, log_in(bank, "liming"))

    proposed, requested = shown(send(agent, conversation, "给老妈转200，给老婆转5000"))

    assert [(s["id"], s["tier"]) for s in proposed["steps"]] == [("s1", "yellow"), ("s2", None)]
    assert [(r["step_id"], r["reason"]) for r in proposed["refusals"]] == [
        ("s2", "insufficient_funds")
    ]
    assert proposed["refusals"][0]["detail"]["balance"] == "1180.00"
    assert requested["step_ids"] == ["s1"]


def test_confirming_runs_the_rest_and_the_refused_step_fails_without_being_sent(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    too_much = {"payee_id": "PAY-LM-001", "amount": "5000.00", "note": ""}
    model.script(
        plan(step("s1", "transfer", TO_MUM), step("s2", "transfer", too_much)),
        say("已向王秀英转账 200.00 元；另一笔余额不足，未执行。"),
    )
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    plan_id = shown(send(agent, conversation, "给老妈转200，给老婆转5000"))[0]["plan_id"]

    events = shown(act(agent, conversation, "confirm", plan_id=plan_id))

    assert event_types(events) == ["step_started", "step_succeeded", "step_failed", "answer"]
    assert (events[2]["step_id"], events[2]["reason"]) == ("s2", "insufficient_funds")
    entries = bank_audit(bank, login)
    sent = [e for e in entries if e["event"] in ("confirmation_issued", "operation")]
    assert [(e["event"], e["input"]["amount"]) for e in sent] == [
        ("confirmation_issued", "200.00"),
        ("operation", "200.00"),
    ]


# --- Only the Customer's tap gets a Confirmation --------------------------------------


def test_no_confirmation_token_is_requested_before_the_customer_confirms(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(two_transfers())
    login = log_in(bank, "liming")

    send(agent, start_conversation(agent, login), TWO_TRANSFERS)

    assert [e["event"] for e in bank_audit(bank, login)] == ["tier_preview"]


def test_one_confirm_requests_one_token_per_yellow_step_and_runs_them_all(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(two_transfers(), say("已向王秀英转账 200.00 元，向韩梅梅转账 300.00 元。"))
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    plan_id = shown(send(agent, conversation, TWO_TRANSFERS))[0]["plan_id"]

    events = shown(act(agent, conversation, "confirm", plan_id=plan_id))

    assert event_types(events) == [
        "step_started", "step_succeeded", "step_started", "step_succeeded", "answer"
    ]
    succeeded = [e for e in events if e["type"] == "step_succeeded"]
    assert [(e["step_id"], e["tier"], e["output"]["amount"]) for e in succeeded] == [
        ("s1", "yellow", "200.00"),
        ("s2", "yellow", "300.00"),
    ]
    assert succeeded[-1]["output"]["balance_after"] == "680.00"
    entries = bank_audit(bank, login)
    assert [(e["event"], e["operation"], e["outcome"]) for e in entries] == [
        ("tier_preview", None, "ok"),
        ("confirmation_issued", "transfer", "ok"),
        ("confirmation_issued", "transfer", "ok"),
        ("operation", "transfer", "ok"),
        ("operation", "transfer", "ok"),
    ]
    issued = [e["detail"]["confirmation_id"] for e in entries[1:3]]
    redeemed = [e["detail"]["confirmation_id"] for e in entries[3:]]
    assert redeemed == issued


def test_the_model_is_never_shown_a_confirmation_token_nor_given_a_way_to_get_one(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(two_transfers(), say("已完成两笔转账。"))
    conversation = start_conversation(agent, log_in(bank, "liming"))
    plan_id = shown(send(agent, conversation, TWO_TRANSFERS))[0]["plan_id"]

    events = shown(act(agent, conversation, "confirm", plan_id=plan_id))

    for request in model.requests:
        assert "confirmation_token" not in request.text()
        assert "CNF-" not in request.text()  # nor a Confirmation's ID
        tools = [tool["function"]["name"] for tool in request.tools or []]
        assert set(tools) <= {
            "propose_plan",
            "decline_request",
            "ask_for_human",
            "ask_clarification",
            "offer_cancellations",
        }
    assert "confirmation_token" not in str(events)
    assert "CNF-" not in str(events)


def test_a_plan_card_token_never_appears_in_the_decision_trace(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(two_transfers(), say("已完成两笔转账。"))
    conversation = start_conversation(agent, log_in(bank, "liming"))
    plan_id = shown(send(agent, conversation, TWO_TRANSFERS))[0]["plan_id"]

    events = act(agent, conversation, "confirm", plan_id=plan_id)

    exchanges = {
        (data["method"], data["path"]): data for data in traces(events, "bank_response")
    }
    confirmations = exchanges[("POST", "/confirmations")]["body"]["confirmations"]
    assert [c["confirmation_token"] for c in confirmations] == ["[redacted]", "[redacted]"]
    assert all(c["confirmation_id"].startswith("CNF-") for c in confirmations)  # IDs show
    sent = [
        data["body"]
        for data in traces(events, "bank_request")
        if data["path"] == "/operations/transfer"
    ]
    assert [body["confirmation_token"] for body in sent] == ["[redacted]", "[redacted]"]
    assert [body["input"] for body in sent] == [TO_MUM, TO_WIFE]


# --- Exact details first: Green look-ups run before the Plan card ---------------------

HALF_TO_MUM = "把余额的一半转给老妈"


def half_my_balance_to_mum() -> list[ModelReply]:
    """The model plans a balance look-up feeding a transfer whose amount it can't know yet,
    then, shown the Bank's balance, plans the transfer with the exact amount."""
    return [
        plan(
            step("s1", "query_balance"),
            step("s2", "transfer", {"payee_id": "PAY-LM-002"}, depends_on=["s1"]),
        ),
        plan(
            step("s2", "transfer", {"payee_id": "PAY-LM-002", "amount": "590.00", "note": ""},
                 depends_on=["s1"]),
        ),
    ]


def test_a_green_look_up_runs_first_so_the_plan_card_shows_the_exact_amount(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(*half_my_balance_to_mum())
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = shown(send(agent, conversation, HALF_TO_MUM))

    assert event_types(events) == [
        "step_started", "step_succeeded", "plan_proposed", "confirmation_requested"
    ]
    looked_up, proposed = events[1], events[2]
    assert looked_up["step_id"] == "s1"
    assert looked_up["output"]["accounts"][0]["balance"] == "1180.00"
    card = [(s["id"], s["operation"], s["input"]["amount"], s["tier"]) for s in proposed["steps"]]
    assert card == [("s2", "transfer", "590.00", "yellow")]
    assert events[3]["step_ids"] == ["s2"]


def test_a_completed_step_numbered_like_the_look_up_is_on_the_plan_card_a_repeat_isnt(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    # The model numbers the completed Plan afresh ("s1" for the transfer) and repeats the
    # look-up that already ran: only the repeat (same Operation, same input) is left out.
    model.script(
        half_my_balance_to_mum()[0],
        plan(
            step("s0", "query_balance"),
            step("s1", "transfer", {"payee_id": "PAY-LM-002", "amount": "590.00", "note": ""}),
        ),
    )
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = shown(send(agent, conversation, HALF_TO_MUM))

    assert event_types(events) == [
        "step_started", "step_succeeded", "plan_proposed", "confirmation_requested"
    ]
    card = [(s["id"], s["operation"], s["input"]["amount"]) for s in events[2]["steps"]]
    assert card == [("s1", "transfer", "590.00")]


def test_the_plan_is_completed_from_the_banks_look_up_results(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(*half_my_balance_to_mum())

    send(agent, start_conversation(agent, log_in(bank, "liming")), HALF_TO_MUM)

    first, completing = model.requests
    assert "1180.00" not in first.text()
    assert "1180.00" in completing.text()
    assert HALF_TO_MUM in completing.text()
    assert [tool["function"]["name"] for tool in completing.tools or []] == [
        "propose_plan", "ask_clarification"
    ]


def test_the_answer_after_confirming_draws_on_the_look_up_and_the_transfer(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(*half_my_balance_to_mum(), say("您的余额 1180.00 元，已向王秀英转账 590.00 元。"))
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    plan_id = shown(send(agent, conversation, HALF_TO_MUM))[2]["plan_id"]

    events = shown(act(agent, conversation, "confirm", plan_id=plan_id))

    assert event_types(events) == ["step_started", "step_succeeded", "answer"]
    assert events[1]["output"]["balance_after"] == "590.00"
    answering = model.requests[-1].text()
    assert "1180.00" in answering and "590.00" in answering
    assert [(e["event"], e["operation"]) for e in bank_audit(bank, login)] == [
        ("tier_preview", None),
        ("operation", "query_balance"),
        ("tier_preview", None),
        ("confirmation_issued", "transfer"),
        ("operation", "transfer"),
    ]


# --- Progress: each step streams its start and its outcome -----------------------------


def test_a_step_the_bank_refuses_streams_step_failed_with_the_banks_reason_and_the_rest_wait(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    # Both Card Locks pass the tier preview, but once the card is locked for everything
    # the Bank refuses to lock it again for online payments.
    model.script(
        plan(
            step("s1", "add_card_lock", {"card_id": "CARD-LM-001", "scope": "all"}),
            step("s2", "add_card_lock", {"card_id": "CARD-LM-001", "scope": "online"}),
            step("s3", "transfer", TO_MUM),
        ),
        say("已锁定该卡的全部交易；网上交易锁未能添加：该卡已锁定。转账未执行。"),
        plan(),  # nothing else to propose for what's left (#42)
    )
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    request = "把工商卡全部锁了，网上交易也锁上，再给老妈转200"
    plan_id = shown(send(agent, conversation, request))[0]["plan_id"]

    events = shown(act(agent, conversation, "confirm", plan_id=plan_id))

    assert event_types(events) == [
        "step_started", "step_succeeded", "step_started", "step_failed", "answer",
        "reversal_offered",  # the first Card Lock ran and can be lifted (#42)
    ]
    failed = events[3]
    assert failed["step_id"] == "s2"
    assert failed["operation"] == "add_card_lock"
    assert failed["reason"] == "invalid_input"
    assert failed["detail"]["locks"] == ["all"]
    ran = [e["operation"] for e in bank_audit(bank, login) if e["event"] == "operation"]
    assert ran == ["add_card_lock", "add_card_lock"]  # the second refused; no transfer
    answering = model.requests[1].text()  # after the Plan; before the re-plan
    assert "invalid_input" in answering


def test_a_step_the_bank_will_no_longer_confirm_fails_and_nothing_runs(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    six_hundred_to_mum = {"payee_id": "PAY-LM-002", "amount": "600.00", "note": ""}
    model.script(
        plan(step("s1", "transfer", six_hundred_to_mum)),
        say("这笔转账现在需要短信验证，尚未执行。"),
    )
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    plan_id = shown(send(agent, conversation, "给老妈转600"))[0]["plan_id"]
    # Before the Customer taps confirm, ¥500 leaves on another device, so the ¥600 would
    # now take the Daily Transfer Total past ¥1,000.
    transfer_directly(bank, login, {"payee_id": "PAY-LM-001", "amount": "500.00", "note": ""})

    events = shown(act(agent, conversation, "confirm", plan_id=plan_id))

    assert event_types(events) == ["step_failed", "answer"]
    assert events[0]["step_id"] == "s1"
    assert events[0]["reason"] == "needs_verification"
    operations = [e for e in bank_audit(bank, login) if e["event"] == "operation"]
    assert len(operations) == 1  # only the transfer made on the other device


def transfer_directly(bank: TestClient, login: Login, input: dict[str, str]) -> None:
    """The Customer confirms a transfer with the Bank on another device, not via the Agent."""
    auth = {"Authorization": f"Bearer {login.session_token}"}
    operation = {"operation": "transfer", "input": input}
    confirmed = bank.post("/confirmations", headers=auth, json={"operations": [operation]})
    token = confirmed.json()["confirmations"][0]["confirmation_token"]
    ran = bank.post(
        "/operations/transfer", headers=auth, json={"input": input, "confirmation_token": token}
    )
    assert ran.status_code == 200, ran.text


# --- Rejecting the Plan card runs nothing ---------------------------------------------


def test_rejecting_the_plan_card_runs_nothing_and_the_agent_acknowledges_it(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(two_transfers())
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    plan_id = shown(send(agent, conversation, TWO_TRANSFERS))[0]["plan_id"]

    events = shown(act(agent, conversation, "reject", plan_id=plan_id))

    assert event_types(events) == ["answer"]
    assert "取消" in events[0]["text"]
    assert [e["event"] for e in bank_audit(bank, login)] == ["tier_preview"]
    assert len(model.requests) == 1  # nothing more to plan or to answer from
    balance = bank.post(
        "/operations/query_balance",
        headers={"Authorization": f"Bearer {login.session_token}"},
        json={"input": {}},
    ).json()["output"]["accounts"][0]["balance"]
    assert balance == "1180.00"


def test_a_rejection_is_acknowledged_in_the_customers_language(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(two_transfers())
    conversation = start_conversation(agent, log_in(bank, "liming"))
    plan_id = shown(send(agent, conversation, "Send Mum 200 and my wife 300"))[0]["plan_id"]

    events = shown(act(agent, conversation, "reject", plan_id=plan_id))

    assert "cancelled" in events[0]["text"]


def test_after_a_rejection_the_next_turn_knows_the_plan_was_cancelled(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(two_transfers(), plan(step("s1", "query_balance")), say("余额 1180.00 元。"))
    conversation = start_conversation(agent, log_in(bank, "liming"))
    plan_id = shown(send(agent, conversation, TWO_TRANSFERS))[0]["plan_id"]
    acknowledgement = shown(act(agent, conversation, "reject", plan_id=plan_id))[0]["text"]

    send(agent, conversation, "那我还剩多少钱？")

    assert acknowledgement in model.requests[1].text()


def test_a_plan_card_is_answered_once(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(two_transfers(), say("已完成两笔转账。"))
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    plan_id = shown(send(agent, conversation, TWO_TRANSFERS))[0]["plan_id"]
    act(agent, conversation, "confirm", plan_id=plan_id)

    again = agent.post(
        f"/conversations/{conversation}/actions", json={"action": "confirm", "plan_id": plan_id}
    )

    assert again.status_code == 409
    assert again.json() == {"error": "plan_not_pending"}
    events = [e["event"] for e in bank_audit(bank, login)]
    assert events.count("confirmation_issued") == 2
    assert events.count("operation") == 2


def test_a_new_request_replaces_an_unanswered_plan_card(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(two_transfers(), plan(step("s1", "query_balance")), say("余额 1180.00 元。"))
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    plan_id = shown(send(agent, conversation, TWO_TRANSFERS))[0]["plan_id"]
    send(agent, conversation, "算了，先看看余额")

    late = agent.post(
        f"/conversations/{conversation}/actions", json={"action": "confirm", "plan_id": plan_id}
    )

    assert late.status_code == 409
    assert "confirmation_issued" not in [e["event"] for e in bank_audit(bank, login)]
