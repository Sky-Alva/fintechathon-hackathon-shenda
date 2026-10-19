"""The Customer stays in control of a Plan before and while it runs: stop, remove steps
before confirming, and re-approve any change (docs/conversation-events.md)."""

from typing import Any

from fastapi.testclient import TestClient

from agent.model import ModelReply
from conftest import (
    BankLink,
    act,
    bank_audit,
    event_types,
    latest_code,
    log_in,
    phone_messages,
    send,
    staff,
    start_conversation,
    take_over,
    traces,
    type_code,
)
from fake_model import FakeModel, plan, say, step
from test_plan_card import transfer_directly

TWO_TRANSFERS = "给老妈转200，再给老婆转300"
TO_MUM = {"payee_id": "PAY-LM-002", "amount": "200.00", "note": ""}
TO_WIFE = {"payee_id": "PAY-LM-001", "amount": "300.00", "note": ""}


def shown(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The turn's events as the chat shows them, leaving out the decision trace."""
    return [event for event in events if event["type"] != "trace"]


def two_transfers() -> ModelReply:
    return plan(step("s1", "transfer", TO_MUM), step("s2", "transfer", TO_WIFE))


def transfers_run(bank: TestClient, login: Any) -> list[str]:
    """The amounts of the transfers the Bank ran for this session, in order."""
    return [
        entry["input"]["amount"]
        for entry in bank_audit(bank, login)
        if entry["event"] == "operation" and entry["operation"] == "transfer"
    ]


def stop_after_first_transfer(agent: TestClient, bank_link: BankLink, conversation: str,
                              plan_id: str) -> None:
    """The Customer taps stop while the first transfer is with the Bank."""
    bank_link.after_first(
        "/operations/transfer", lambda: act(agent, conversation, "stop", plan_id=plan_id)
    )


# --- Remove steps before confirming -----------------------------------------------------


def test_removing_a_step_before_confirming_runs_only_the_rest_re_tiered_by_the_bank(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(two_transfers(), say("已向韩梅梅转账 300.00 元。"))
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    plan_id = shown(send(agent, conversation, TWO_TRANSFERS))[0]["plan_id"]

    events = act(agent, conversation, "confirm", plan_id=plan_id, removed_step_ids=["s1"])

    assert event_types(events) == ["step_started", "step_succeeded", "answer"]
    started = shown(events)[0]
    assert (started["step"]["id"], started["step"]["tier"]) == ("s2", "yellow")
    assert transfers_run(bank, login) == ["300.00"]
    entries = bank_audit(bank, login)
    assert [(e["event"], e["outcome"]) for e in entries] == [
        ("tier_preview", "ok"),  # the card
        ("tier_preview", "ok"),  # the rest, re-tiered
        ("confirmation_issued", "ok"),
        ("operation", "ok"),
    ]
    assert [op["input"]["amount"] for op in entries[1]["input"]["operations"]] == ["300.00"]
    assert entries[2]["input"]["amount"] == "300.00"
    assert [data["steps"] for data in traces(events, "plan")] == [[started["step"]]]


def test_the_rest_are_re_tiered_in_their_new_order(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(two_transfers(), say("已向韩梅梅转账 300.00 元。"))
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    plan_id = shown(send(agent, conversation, TWO_TRANSFERS))[0]["plan_id"]
    # ¥600 leaves on another device: with both steps the second would now cross ¥1,000.
    transfer_directly(bank, login, {"payee_id": "PAY-LM-002", "amount": "600.00", "note": ""})

    events = shown(act(agent, conversation, "confirm", plan_id=plan_id, removed_step_ids=["s1"]))

    assert event_types(events) == ["step_started", "step_succeeded", "answer"]
    assert (events[1]["step_id"], events[1]["tier"]) == ("s2", "yellow")
    assert events[1]["output"]["daily_transfer_total"] == "900.00"


TO_HUSBAND = {"payee_id": "PAY-HM-001", "amount": "600.00", "note": ""}


def two_600s() -> ModelReply:
    """韩梅梅 sends her husband ¥600 twice: the second crosses ¥1,000, so it is Red."""
    return plan(step("s1", "transfer", TO_HUSBAND), step("s2", "transfer", TO_HUSBAND))


def test_removing_an_earlier_transfer_turns_a_red_step_yellow(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(two_600s(), say("已向李明转账 600.00 元。"))
    login = log_in(bank, "hanmei")
    conversation = start_conversation(agent, login)
    proposed = shown(send(agent, conversation, "给老公转两笔600"))[0]
    assert [(s["id"], s["tier"]) for s in proposed["steps"]] == [("s1", "yellow"), ("s2", "red")]

    events = shown(
        act(agent, conversation, "confirm", plan_id=proposed["plan_id"], removed_step_ids=["s1"])
    )

    assert event_types(events) == ["step_started", "step_succeeded", "answer"]
    assert (events[0]["step"]["id"], events[0]["step"]["tier"]) == ("s2", "yellow")
    assert (events[1]["tier"], events[1]["output"]["amount"]) == ("yellow", "600.00")
    entries = bank_audit(bank, login)
    assert [e["event"] for e in entries] == [
        "tier_preview", "tier_preview", "confirmation_issued", "operation"
    ]  # no verification: s2 is Yellow on its own
    assert phone_messages(bank, login) == []


def test_stopping_a_plan_waiting_for_verification_pauses_it_there(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(two_600s())
    login = log_in(bank, "hanmei")
    conversation = start_conversation(agent, login)
    plan_id = shown(send(agent, conversation, "给老公转两笔600"))[0]["plan_id"]
    act(agent, conversation, "confirm", plan_id=plan_id)  # s1 runs; s2 waits for its code

    events = shown(act(agent, conversation, "stop", plan_id=plan_id))

    assert event_types(events) == ["plan_paused"]
    assert (events[0]["ran"], events[0]["not_run"]) == (["s1"], ["s2"])
    assert transfers_run(bank, login) == ["600.00"]


def test_resuming_a_stopped_plan_asks_again_to_verify_its_red_step(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(two_600s(), say("已向李明转账两笔，各 600.00 元。"))
    login = log_in(bank, "hanmei")
    conversation = start_conversation(agent, login)
    plan_id = shown(send(agent, conversation, "给老公转两笔600"))[0]["plan_id"]
    act(agent, conversation, "confirm", plan_id=plan_id)
    act(agent, conversation, "stop", plan_id=plan_id)

    resumed = shown(act(agent, conversation, "resume", plan_id=plan_id))

    assert event_types(resumed) == ["verification_requested"]
    requested = resumed[0]
    assert (requested["plan_id"], requested["step_id"]) == (plan_id, "s2")
    verification = type_code(bank, login, requested["challenge_id"], latest_code(bank, login))
    events = shown(
        act(
            agent,
            conversation,
            "verified",
            plan_id=plan_id,
            step_id="s2",
            verification_token=verification["verification_token"],
        )
    )
    assert event_types(events) == ["step_started", "step_succeeded", "answer"]
    assert events[1]["tier"] == "red"
    assert transfers_run(bank, login) == ["600.00", "600.00"]


def test_a_verified_red_step_can_be_stopped_before_the_steps_after_it(
    agent: TestClient, bank: TestClient, bank_link: BankLink, model: FakeModel
) -> None:
    model.script(
        plan(
            step("s1", "transfer", TO_HUSBAND),
            step("s2", "transfer", TO_HUSBAND),
            step("s3", "lower_card_limit", {"card_id": "CARD-HM-001", "new_limit": "100.00"}),
        )
    )
    login = log_in(bank, "hanmei")
    conversation = start_conversation(agent, login)
    plan_id = shown(send(agent, conversation, "给老公转两笔600，再把卡限额调低到100"))[0]["plan_id"]
    requested = shown(act(agent, conversation, "confirm", plan_id=plan_id))[-1]
    verification = type_code(bank, login, requested["challenge_id"], latest_code(bank, login))
    bank_link.after_first(
        "/operations/transfer", lambda: act(agent, conversation, "stop", plan_id=plan_id)
    )

    events = shown(
        act(
            agent,
            conversation,
            "verified",
            plan_id=plan_id,
            step_id="s2",
            verification_token=verification["verification_token"],
        )
    )

    assert event_types(events) == ["step_started", "step_succeeded", "plan_paused"]
    assert (events[-1]["ran"], events[-1]["not_run"]) == (["s1", "s2"], ["s3"])
    assert "第 1、2 步" in events[-1]["text"] and "第 3 步" in events[-1]["text"]


def test_removing_a_step_the_card_does_not_have_is_refused_and_the_card_still_waits(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(two_transfers(), say("已完成两笔转账。"))
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    plan_id = shown(send(agent, conversation, TWO_TRANSFERS))[0]["plan_id"]

    response = agent.post(
        f"/conversations/{conversation}/actions",
        json={"action": "confirm", "plan_id": plan_id, "removed_step_ids": ["s9"]},
    )

    assert response.status_code == 422
    assert response.json() == {"error": "unknown_step"}
    assert [e["event"] for e in bank_audit(bank, login)] == ["tier_preview"]
    act(agent, conversation, "confirm", plan_id=plan_id)
    assert transfers_run(bank, login) == ["200.00", "300.00"]


def test_removing_every_step_runs_nothing(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(two_transfers())
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    plan_id = shown(send(agent, conversation, TWO_TRANSFERS))[0]["plan_id"]

    events = shown(
        act(agent, conversation, "confirm", plan_id=plan_id, removed_step_ids=["s1", "s2"])
    )

    assert event_types(events) == ["answer"]
    assert "取消" in events[0]["text"]
    assert [e["event"] for e in bank_audit(bank, login)] == ["tier_preview"]


# --- Stop ------------------------------------------------------------------------------


def test_stopping_after_the_first_step_runs_no_further_step_and_says_what_ran(
    agent: TestClient, bank: TestClient, bank_link: BankLink, model: FakeModel
) -> None:
    model.script(two_transfers())
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    plan_id = shown(send(agent, conversation, TWO_TRANSFERS))[0]["plan_id"]
    stop_after_first_transfer(agent, bank_link, conversation, plan_id)

    events = shown(act(agent, conversation, "confirm", plan_id=plan_id))

    assert event_types(events) == ["step_started", "step_succeeded", "plan_paused"]
    paused = events[-1]
    assert (paused["plan_id"], paused["ran"], paused["not_run"]) == (plan_id, ["s1"], ["s2"])
    assert "第 1 步" in paused["text"] and "第 2 步" in paused["text"]
    assert transfers_run(bank, login) == ["200.00"]


def test_a_stop_before_the_first_step_starts_runs_nothing(
    agent: TestClient, bank: TestClient, bank_link: BankLink, model: FakeModel
) -> None:
    model.script(two_transfers())
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    plan_id = shown(send(agent, conversation, TWO_TRANSFERS))[0]["plan_id"]
    bank_link.after_first(
        "/confirmations", lambda: act(agent, conversation, "stop", plan_id=plan_id)
    )

    events = shown(act(agent, conversation, "confirm", plan_id=plan_id))

    assert event_types(events) == ["plan_paused"]
    assert (events[0]["ran"], events[0]["not_run"]) == ([], ["s1", "s2"])
    assert transfers_run(bank, login) == []


def test_staff_see_the_stop_where_it_happened(
    agent: TestClient, bank: TestClient, bank_link: BankLink, model: FakeModel
) -> None:
    _, conversation, plan_id = stopped_after_first_transfer(agent, bank, bank_link, model)

    entries = staff(agent, f"/staff/conversations/{conversation}")["entries"]

    shown_entries = [e for e in entries if e["type"] != "trace"]
    assert [e["type"] for e in shown_entries[-5:]] == [
        "customer_action", "step_started", "customer_action", "step_succeeded", "plan_paused"
    ]
    assert shown_entries[-3] == {"type": "customer_action", "action": "stop", "plan_id": plan_id}


def stopped_after_first_transfer(
    agent: TestClient, bank: TestClient, bank_link: BankLink, model: FakeModel
) -> tuple[Any, str, str]:
    """The Customer confirmed both transfers and tapped stop once the first had run."""
    model.script(two_transfers())
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    plan_id = shown(send(agent, conversation, TWO_TRANSFERS))[0]["plan_id"]
    stop_after_first_transfer(agent, bank_link, conversation, plan_id)
    act(agent, conversation, "confirm", plan_id=plan_id)
    return login, conversation, plan_id


def test_stop_is_refused_when_that_plan_is_not_running(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(two_transfers())
    conversation = start_conversation(agent, log_in(bank, "liming"))
    plan_id = shown(send(agent, conversation, TWO_TRANSFERS))[0]["plan_id"]

    response = agent.post(
        f"/conversations/{conversation}/actions", json={"action": "stop", "plan_id": plan_id}
    )

    assert response.status_code == 409
    assert response.json() == {"error": "plan_not_running"}


# --- After a stop: resume, change or cancel -------------------------------------------


def test_resuming_a_stopped_plan_runs_the_rest_on_its_own_confirmation(
    agent: TestClient, bank: TestClient, bank_link: BankLink, model: FakeModel
) -> None:
    login, conversation, plan_id = stopped_after_first_transfer(agent, bank, bank_link, model)
    model.script(say("已向王秀英转账 200.00 元，向韩梅梅转账 300.00 元。"))

    events = shown(act(agent, conversation, "resume", plan_id=plan_id))

    assert event_types(events) == ["step_started", "step_succeeded", "answer"]
    assert (events[1]["step_id"], events[1]["output"]["amount"]) == ("s2", "300.00")
    assert transfers_run(bank, login) == ["200.00", "300.00"]
    issued = [e for e in bank_audit(bank, login) if e["event"] == "confirmation_issued"]
    assert len(issued) == 2  # both from the one confirm; none new
    answering = model.requests[-1].text()
    assert "200.00" in answering and "300.00" in answering  # the whole Plan's results


def test_a_stopped_plan_is_resumed_once(
    agent: TestClient, bank: TestClient, bank_link: BankLink, model: FakeModel
) -> None:
    login, conversation, plan_id = stopped_after_first_transfer(agent, bank, bank_link, model)
    model.script(say("已完成两笔转账。"))
    act(agent, conversation, "resume", plan_id=plan_id)

    again = agent.post(
        f"/conversations/{conversation}/actions", json={"action": "resume", "plan_id": plan_id}
    )

    assert again.status_code == 409
    assert again.json() == {"error": "plan_not_pending"}
    assert transfers_run(bank, login) == ["200.00", "300.00"]


def test_a_stopped_plan_is_not_resumed_while_a_staff_member_has_the_conversation(
    agent: TestClient, bank: TestClient, bank_link: BankLink, model: FakeModel
) -> None:
    login, conversation, plan_id = stopped_after_first_transfer(agent, bank, bank_link, model)
    take_over(agent, conversation)

    response = agent.post(
        f"/conversations/{conversation}/actions", json={"action": "resume", "plan_id": plan_id}
    )

    assert response.status_code == 409
    assert response.json()["error"] == "already_handed_over"
    assert transfers_run(bank, login) == ["200.00"]


def test_cancelling_a_stopped_plan_runs_nothing_more(
    agent: TestClient, bank: TestClient, bank_link: BankLink, model: FakeModel
) -> None:
    login, conversation, plan_id = stopped_after_first_transfer(agent, bank, bank_link, model)

    events = shown(act(agent, conversation, "cancel", plan_id=plan_id))

    assert event_types(events) == ["answer"]
    assert "取消" in events[0]["text"]
    late = agent.post(
        f"/conversations/{conversation}/actions", json={"action": "resume", "plan_id": plan_id}
    )
    assert late.status_code == 409
    assert transfers_run(bank, login) == ["200.00"]
    assert len(model.requests) == 1  # nothing more to plan or to answer from


WIFE_250 = {"payee_id": "PAY-LM-001", "amount": "250.00", "note": ""}


def test_changing_a_confirmed_plan_needs_a_new_plan_card_and_a_new_confirmation(
    agent: TestClient, bank: TestClient, bank_link: BankLink, model: FakeModel
) -> None:
    login, conversation, old_plan = stopped_after_first_transfer(agent, bank, bank_link, model)
    model.script(plan(step("s1", "transfer", WIFE_250)), say("已向韩梅梅转账 250.00 元。"))

    proposed, requested = shown(send(agent, conversation, "给老婆的那笔改成250"))

    assert (proposed["type"], requested["type"]) == ("plan_proposed", "confirmation_requested")
    assert proposed["plan_id"] != old_plan
    assert [(s["input"]["amount"], s["tier"]) for s in proposed["steps"]] == [("250.00", "yellow")]
    assert transfers_run(bank, login) == ["200.00"]  # nothing runs before the new tap
    late = agent.post(
        f"/conversations/{conversation}/actions", json={"action": "resume", "plan_id": old_plan}
    )
    assert late.status_code == 409  # the old approval is gone

    events = shown(act(agent, conversation, "confirm", plan_id=proposed["plan_id"]))

    assert event_types(events) == ["step_started", "step_succeeded", "answer"]
    assert transfers_run(bank, login) == ["200.00", "250.00"]
    entries = bank_audit(bank, login)
    issued = [e for e in entries if e["event"] == "confirmation_issued"]
    assert [e["input"]["amount"] for e in issued] == ["200.00", "300.00", "250.00"]
    redeemed = [
        e["detail"]["confirmation_id"]
        for e in entries
        if e["event"] == "operation" and e["operation"] == "transfer"
    ]
    assert redeemed == [issued[0]["detail"]["confirmation_id"], issued[2]["detail"]["confirmation_id"]]


def test_after_a_stop_the_planner_re_plans_knowing_what_ran_and_what_did_not(
    agent: TestClient, bank: TestClient, bank_link: BankLink, model: FakeModel
) -> None:
    _, conversation, _ = stopped_after_first_transfer(agent, bank, bank_link, model)
    model.script(plan(step("s1", "transfer", WIFE_250)))

    send(agent, conversation, "给老婆的那笔改成250")

    replanning = model.requests[-1].text()
    assert "TRF-" in replanning  # the transfer that ran, as the Bank returned it
    assert "300.00" in replanning  # the step that didn't run
    assert "已暂停" in replanning  # what the Customer was told
