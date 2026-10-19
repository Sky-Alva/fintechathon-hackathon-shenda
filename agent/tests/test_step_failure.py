"""Step failure (#42): the Bank's reason, a new Plan for what's left, a reversal offer,
and irreversible steps last.

A failed step is explained by the Bank's refusal reason alone. The Agent then offers to
undo the Plan's completed reversible steps, in reverse order, and proposes a new Plan for
what's left of the goal. Nothing is undone unless the Customer accepts, and an accepted
undo is a new Plan with its own Plan card and Permission Tiers.
"""

from typing import Any

from fastapi.testclient import TestClient

from conftest import (
    BankLink,
    Login,
    act,
    bank_audit,
    event_types,
    latest_code,
    log_in,
    send,
    start_conversation,
    take_over,
    type_code,
)
from fake_model import FakeModel, plan, say, step

TO_MUM_300 = {"payee_id": "PAY-LM-002", "amount": "300.00", "note": ""}
LOCK_ONLINE = {"card_id": "CARD-LM-001", "scope": "online"}
LOCK_AND_PAY = "把工商卡的网上交易锁上，再给老妈转300"


def shown(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The turn's events as the chat shows them, leaving out the decision trace."""
    return [event for event in events if event["type"] != "trace"]


def only(events: list[dict[str, Any]], type: str) -> dict[str, Any]:
    [event] = [event for event in events if event["type"] == type]
    return event


def operations_run(bank: TestClient, login: Any) -> list[str]:
    return [
        e["operation"]
        for e in bank_audit(bank, login)
        if e["event"] == "operation" and e["outcome"] == "ok"
    ]


# --- Irreversible steps last --------------------------------------------------------


def test_a_card_lock_runs_before_a_transfer_that_doesnt_depend_on_it(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    # The model plans the transfer first; nothing ties the two together.
    model.script(
        plan(step("s1", "transfer", TO_MUM_300), step("s2", "add_card_lock", LOCK_ONLINE)),
        say("已锁定网上交易，并向王秀英转账 300.00 元。"),
    )
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)

    proposed = only(send(agent, conversation, LOCK_AND_PAY), "plan_proposed")
    events = shown(act(agent, conversation, "confirm", plan_id=proposed["plan_id"]))

    assert [s["id"] for s in proposed["steps"]] == ["s2", "s1"]
    assert [e["step"]["id"] for e in events if e["type"] == "step_started"] == ["s2", "s1"]
    assert operations_run(bank, login) == ["add_card_lock", "transfer"]


def test_a_step_that_depends_on_a_transfer_still_runs_after_it(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        plan(
            step("s1", "transfer", TO_MUM_300),
            step("s2", "add_card_lock", LOCK_ONLINE, depends_on=["s1"]),
        )
    )
    conversation = start_conversation(agent, log_in(bank, "liming"))

    proposed = only(send(agent, conversation, LOCK_AND_PAY), "plan_proposed")

    assert [s["id"] for s in proposed["steps"]] == ["s1", "s2"]


# --- The Bank's reason ----------------------------------------------------------------


TO_WIFE_900 = {"payee_id": "PAY-LM-001", "amount": "900.00", "note": ""}


def spend_elsewhere(bank: TestClient, login: Login, input: dict[str, str]) -> None:
    """The Customer confirms a transfer with the Bank on another device, not via the Agent."""
    auth = {"Authorization": f"Bearer {login.session_token}"}
    operation = {"operation": "transfer", "input": input}
    confirmed = bank.post("/confirmations", headers=auth, json={"operations": [operation]})
    token = confirmed.json()["confirmations"][0]["confirmation_token"]
    ran = bank.post(
        "/operations/transfer", headers=auth, json={"input": input, "confirmation_token": token}
    )
    assert ran.status_code == 200, ran.text


def lock_then_pay(
    agent: TestClient,
    bank: TestClient,
    bank_link: BankLink,
    request: str = LOCK_AND_PAY,
) -> tuple[Login, str, str, list[dict[str, Any]]]:
    """The Customer confirms a Card Lock and a ¥300 transfer; while the lock runs, ¥900
    leaves on another device, so the Bank refuses the transfer: not enough money."""
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    proposed = only(send(agent, conversation, request), "plan_proposed")
    bank_link.after_first(
        "/operations/add_card_lock", lambda: spend_elsewhere(bank, login, TO_WIFE_900)
    )
    events = shown(act(agent, conversation, "confirm", plan_id=proposed["plan_id"]))
    return login, conversation, proposed["plan_id"], events


LOCK_AND_PAY_PLAN = plan(
    step("s1", "transfer", TO_MUM_300), step("s2", "add_card_lock", LOCK_ONLINE)
)


def test_a_failed_step_is_explained_by_the_banks_refusal_reason_alone(
    agent: TestClient, bank: TestClient, bank_link: BankLink, model: FakeModel
) -> None:
    model.script(
        LOCK_AND_PAY_PLAN,
        say("网上交易已锁定；转账未执行：余额不足。"),
        plan(),  # nothing else to propose
    )

    _, _, _, events = lock_then_pay(agent, bank, bank_link)

    failed = only(events, "step_failed")
    assert (failed["step_id"], failed["reason"]) == ("s1", "insufficient_funds")
    assert failed["detail"]["balance"] == "280.00"
    assert failed["text"] == "银行未执行这一步：账户余额不足。"


def test_the_explanation_is_in_the_customers_language(
    agent: TestClient, bank: TestClient, bank_link: BankLink, model: FakeModel
) -> None:
    model.script(
        LOCK_AND_PAY_PLAN,
        say("Online payments are locked; the transfer didn't run."),
        plan(),
    )

    _, _, _, events = lock_then_pay(
        agent, bank, bank_link, "Lock online payments on my ICBC card and send Mum 300"
    )

    assert only(events, "step_failed")["text"] == (
        "The Bank didn't run this step: there isn't enough money in the account."
    )


# --- A new Plan for what's left ---------------------------------------------------------


TO_MUM_280 = {"payee_id": "PAY-LM-002", "amount": "280.00", "note": ""}


def test_after_a_failure_the_agent_proposes_a_new_plan_for_whats_left(
    agent: TestClient, bank: TestClient, bank_link: BankLink, model: FakeModel
) -> None:
    model.script(
        LOCK_AND_PAY_PLAN,
        say("网上交易已锁定；转账未执行：余额不足。"),
        plan(step("s1", "transfer", TO_MUM_280)),  # what the balance allows
    )

    _, _, plan_id, events = lock_then_pay(agent, bank, bank_link)

    assert event_types(events)[-4:] == [
        "answer", "reversal_offered", "plan_proposed", "confirmation_requested"
    ]
    proposed = only(events, "plan_proposed")
    assert proposed["plan_id"] != plan_id
    assert [(s["operation"], s["input"]) for s in proposed["steps"]] == [
        ("transfer", TO_MUM_280)
    ]
    # The Planner knew what failed, why, and what ran.
    replanning = model.requests[-1].text()
    assert "insufficient_funds" in replanning
    assert "add_card_lock" in replanning


def test_the_failed_call_is_not_retried_as_it_was(
    agent: TestClient, bank: TestClient, bank_link: BankLink, model: FakeModel
) -> None:
    model.script(
        LOCK_AND_PAY_PLAN,
        say("网上交易已锁定；转账未执行：余额不足。"),
        plan(step("s1", "transfer", TO_MUM_300)),  # the very call the Bank just refused
    )

    login, _, _, events = lock_then_pay(agent, bank, bank_link)

    assert event_types(events)[-2:] == ["answer", "reversal_offered"]
    tried = [e for e in bank_audit(bank, login) if e.get("operation") == "transfer"]
    assert [e["input"]["amount"] for e in tried if e["event"] == "operation"] == [
        "900.00",  # on the other device
        "300.00",  # the Agent's, refused
    ]


# --- The reversal offer ---------------------------------------------------------------


LOCK_OVERSEAS = {"card_id": "CARD-LM-001", "scope": "overseas"}
TWO_LOCKS_AND_PAY = plan(
    step("s1", "transfer", TO_MUM_300),
    step("s2", "add_card_lock", LOCK_ONLINE),
    step("s3", "add_card_lock", LOCK_OVERSEAS),
)


def cards(bank: TestClient, login: Login) -> dict[str, Any]:
    """The Customer's cards by card_id, as the Bank lists them."""
    auth = {"Authorization": f"Bearer {login.session_token}"}
    response = bank.post("/operations/list_cards", headers=auth, json={"input": {}})
    return {card["card_id"]: card for card in response.json()["output"]["cards"]}


def test_completed_reversible_steps_are_offered_for_reversal_in_reverse_order(
    agent: TestClient, bank: TestClient, bank_link: BankLink, model: FakeModel
) -> None:
    model.script(TWO_LOCKS_AND_PAY, say("两项卡片锁定已完成；转账未执行：余额不足。"), plan())

    login, _, plan_id, events = lock_then_pay(agent, bank, bank_link)

    assert event_types(events) == [
        "step_started", "step_succeeded",  # s2: lock online
        "step_started", "step_succeeded",  # s3: lock overseas
        "step_started", "step_failed",  # s1: the transfer, last
        "answer",
        "reversal_offered",
    ]
    offered = only(events, "reversal_offered")
    assert offered["plan_id"] == plan_id
    assert offered["steps"] == [
        {"step_id": "s3", "operation": "lift_card_lock", "input": LOCK_OVERSEAS},
        {"step_id": "s2", "operation": "lift_card_lock", "input": LOCK_ONLINE},
    ]
    assert "第 2、1 步" in offered["text"]
    # Offered, not done: both Card Locks stand.
    assert operations_run(bank, login) == ["add_card_lock", "transfer", "add_card_lock"]
    assert sorted(cards(bank, login)["CARD-LM-001"]["locks"]) == ["online", "overseas"]


def test_declining_the_reversal_leaves_the_completed_steps_as_they_are(
    agent: TestClient, bank: TestClient, bank_link: BankLink, model: FakeModel
) -> None:
    model.script(LOCK_AND_PAY_PLAN, say("网上交易已锁定；转账未执行：余额不足。"), plan())
    login, conversation, plan_id, _ = lock_then_pay(agent, bank, bank_link)
    audit_before = bank_audit(bank, login)

    events = shown(act(agent, conversation, "reject", plan_id=plan_id))

    assert event_types(events) == ["answer"]
    assert "不撤销" in events[0]["text"]
    assert bank_audit(bank, login) == audit_before  # the Agent asked the Bank nothing
    assert cards(bank, login)["CARD-LM-001"]["locks"] == ["online"]
    again = agent.post(
        f"/conversations/{conversation}/actions",
        json={"action": "reverse", "plan_id": plan_id, "step_ids": ["s2"]},
    )
    assert again.status_code == 409
    assert again.json() == {"error": "plan_not_pending"}


def test_an_accepted_reversal_is_a_new_plan_card_and_lifting_the_lock_asks_for_verification(
    agent: TestClient, bank: TestClient, bank_link: BankLink, model: FakeModel
) -> None:
    model.script(
        LOCK_AND_PAY_PLAN,
        say("网上交易已锁定；转账未执行：余额不足。"),
        plan(),
        say("已解除网上交易锁定。"),
    )
    login, conversation, plan_id, _ = lock_then_pay(agent, bank, bank_link)

    card = shown(act(agent, conversation, "reverse", plan_id=plan_id, step_ids=["s2"]))

    assert event_types(card) == ["plan_proposed", "confirmation_requested"]
    proposed, requested = card
    assert proposed["plan_id"] != plan_id
    [undo] = proposed["steps"]
    assert (undo["operation"], undo["input"], undo["tier"]) == (
        "lift_card_lock", LOCK_ONLINE, "red"
    )
    assert [w["reason"] for w in proposed["warnings"]] == ["strong_verification"]
    assert requested["step_ids"] == []  # nothing Yellow: the tap leads to the verification
    assert "lift_card_lock" not in operations_run(bank, login)

    verification = shown(act(agent, conversation, "confirm", plan_id=proposed["plan_id"]))

    assert event_types(verification) == ["verification_requested"]
    assert "lift_card_lock" not in operations_run(bank, login)
    typed = type_code(bank, login, verification[0]["challenge_id"], latest_code(bank, login))
    events = shown(
        act(
            agent,
            conversation,
            "verified",
            plan_id=proposed["plan_id"],
            step_id=undo["id"],
            verification_token=typed["verification_token"],
        )
    )
    assert event_types(events) == ["step_started", "step_succeeded", "answer"]
    assert cards(bank, login)["CARD-LM-001"]["locks"] == []


def test_only_offered_steps_can_be_reversed(
    agent: TestClient, bank: TestClient, bank_link: BankLink, model: FakeModel
) -> None:
    model.script(LOCK_AND_PAY_PLAN, say("网上交易已锁定；转账未执行：余额不足。"), plan())
    _, conversation, plan_id, _ = lock_then_pay(agent, bank, bank_link)

    response = agent.post(
        f"/conversations/{conversation}/actions",
        json={"action": "reverse", "plan_id": plan_id, "step_ids": ["s1"]},  # the transfer
    )

    assert response.status_code == 422
    assert response.json() == {"error": "unknown_step"}


def test_no_reversal_starts_while_a_staff_member_has_the_conversation(
    agent: TestClient, bank: TestClient, bank_link: BankLink, model: FakeModel
) -> None:
    model.script(LOCK_AND_PAY_PLAN, say("网上交易已锁定；转账未执行：余额不足。"), plan())
    login, conversation, plan_id, _ = lock_then_pay(agent, bank, bank_link)
    take_over(agent, conversation, "王经理")
    audit_before = bank_audit(bank, login)

    response = agent.post(
        f"/conversations/{conversation}/actions",
        json={"action": "reverse", "plan_id": plan_id, "step_ids": ["s2"]},
    )

    assert response.status_code == 409
    assert response.json() == {"error": "already_handed_over", "staff": "王经理"}
    assert bank_audit(bank, login) == audit_before
