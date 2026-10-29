"""Past transfers found by their note (#58): "find my transfer with the remark 家用" lists
the transfer from the Bank's transaction history, also when the Customer says the note in
English ("house uses")."""

from typing import Any

from fastapi.testclient import TestClient

from conftest import Login, event_types, latest_code, log_in, send, start_conversation, type_code
from fake_model import FakeModel, plan, say, step

HOUSEHOLD = {"payee_id": "PAY-HM-001", "amount": "2000.00", "note": "家用"}  # to 李明


def send_household_money(bank: TestClient, login: Login) -> None:
    """韩梅梅 sends 李明 ¥2,000 for 家用 in the Channel, passing Strong Verification."""
    headers = {"Authorization": f"Bearer {login.session_token}"}
    challenge = bank.post(
        "/verifications",
        headers=headers,
        json={"operations": [{"operation": "transfer", "input": HOUSEHOLD}]},
    )
    assert challenge.status_code == 200, challenge.text
    challenge_id = challenge.json()["challenge_id"]
    verified = type_code(bank, login, challenge_id, latest_code(bank, login))
    token = verified["verifications"][0]["verification_token"]
    sent = bank.post(
        "/operations/transfer",
        headers=headers,
        json={"input": HOUSEHOLD, "verification_token": token},
    )
    assert sent.status_code == 200, sent.text


def found(events: list[dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    [succeeded] = [e for e in events if e["type"] == "step_succeeded"]
    return [
        (t["occurred_at"][:10], t["merchant"], t["description"], t["amount"])
        for t in succeeded["output"]["transactions"]
    ]


def test_a_transfer_is_found_by_its_remark(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    login = log_in(bank, "hanmei")
    send_household_money(bank, login)
    model.script(
        plan(step("s1", "list_transactions", {"description": "家用", "start_date": "2026-10-20"})),
        say("找到了您今天给李明的家用转账。"),
    )

    events = send(agent, start_conversation(agent, login), "帮我找一下备注是家用的那笔转账")

    assert event_types(events)[:2] == ["step_started", "step_succeeded"]
    assert found(events) == [("2026-10-20", "李明", "家用", "2000.00")]
    assert events[-1]["type"] == "answer"


def test_a_remark_said_in_english_is_searched_as_it_was_written(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    login = log_in(bank, "hanmei")
    send_household_money(bank, login)
    model.script(
        plan(step("s1", "list_transactions", {"description": "家用"})),
        say("Here are your transfers with the remark 家用."),
    )

    events = send(agent, start_conversation(agent, login),
                  "find my transfer that remarks as house uses")

    # The Planner is told to search by the note as it was likely written.
    assert "house uses" in model.requests[0].text()
    assert "家用" in model.requests[0].text()
    assert found(events) == [
        ("2026-10-20", "李明", "家用", "2000.00"), ("2026-10-01", "李明", "家用", "800.00"),
    ]
