"""The Agent's internal data format never reaches the Customer.

The model reads the Bank's results in `<bank_data>` blocks, with step IDs and the Bank's
card IDs. None of that is for the Customer: an answer that echoes a block, a Bank result
as JSON or a card's Bank ID is written again, and whatever reaches the Channel has none of
it. Cards are named by their last four digits.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent import responder
from agent.fact_check import Facts
from conftest import Login, act, latest_code, log_in, send, start_conversation, type_code
from fake_model import FakeModel, plan, say, step

CARD_2 = {"card_id": "CARD-LM-002"}

ECHOED = (
    "<bank_data>\nBank results for this turn:\n"
    '[{"step_id": "s1", "operation": "withdraw_loss_report", "input": {"card_id": '
    '"CARD-LM-002"}, "output": {"card_id": "CARD-LM-002", "status": "active"}, '
    '"refusal": null, "reversal": {"operation": "file_loss_report", "input": '
    '{"card_id": "CARD-LM-002"}}}]\n</bank_data>\n\n'
    "The loss report on your card ending 5678 has been withdrawn, and the card is active."
)
PLAIN = "The loss report on your card ending 5678 has been withdrawn, and the card is active."


def answers(events: list[dict[str, Any]]) -> list[str]:
    return [event["text"] for event in events if event["type"] == "answer"]


def run_red_plan(
    agent: TestClient, bank: TestClient, login: Login, conversation: str, text: str
) -> list[dict[str, Any]]:
    """Send `text`, confirm the one-step Red Plan it gets and type the SMS code: the events
    of the run, up to the answer."""
    [card] = [e for e in send(agent, conversation, text) if e["type"] == "plan_proposed"]
    [requested] = [
        e
        for e in act(agent, conversation, "confirm", plan_id=card["plan_id"])
        if e["type"] == "verification_requested"
    ]
    verified = type_code(bank, login, requested["challenge_id"], latest_code(bank, login))
    return act(
        agent,
        conversation,
        "verified",
        plan_id=requested["plan_id"],
        step_id=requested["step_id"],
        verification_token=verified["verification_token"],
    )


def lost_card_two(agent: TestClient, bank: TestClient, model: FakeModel) -> tuple[Login, str]:
    """李明 has listed his cards and filed a Loss Report on the one ending 5678."""
    model.script(
        plan(step("s1", "list_cards")),
        say("Your cards end in 1234, 5678 and 9012."),
        plan(step("s1", "file_loss_report", CARD_2)),
        say("The loss report on your card ending 5678 is filed; the card is disabled."),
    )
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    send(agent, conversation, "list my cards")
    run_red_plan(agent, bank, login, conversation, "i lost my card ending 5678")
    return login, conversation


def withdraw(
    agent: TestClient, bank: TestClient, model: FakeModel, *drafts: str
) -> tuple[str, list[dict[str, Any]]]:
    """File a Loss Report, then withdraw it, the model answering with `drafts` in turn:
    the conversation, and the events of the withdrawal's run."""
    login, conversation = lost_card_two(agent, bank, model)
    model.script(plan(step("s1", "withdraw_loss_report", CARD_2)), *map(say, drafts))
    ran = run_red_plan(agent, bank, login, conversation, "i found it, withdraw the report")
    return conversation, ran


def test_an_answer_echoing_a_bank_data_block_is_written_again_in_plain_sentences(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    _, events = withdraw(agent, bank, model, ECHOED, PLAIN)

    assert answers(events) == [PLAIN]
    rewrite = model.requests[-1].messages[-1]
    assert rewrite["role"] == "system" and "<bank_data>" in rewrite["content"]


def assert_plain(text: str) -> None:
    """Nothing of the Agent's internal data format: no block, no Bank JSON, no card ID."""
    assert "bank_data" not in text
    assert '"step_id"' not in text and '"refusal"' not in text
    assert "CARD-" not in text


def test_when_the_answer_written_again_still_echoes_it_the_customer_sees_none_of_it(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    _, events = withdraw(agent, bank, model, ECHOED, ECHOED)

    [text] = answers(events)
    assert_plain(text)
    assert "5678" in text  # the card, named by its last four digits


def test_the_model_never_reads_a_bank_data_block_as_something_it_wrote(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    # The raw Bank result was shown instead of an answer: the model reads it back later
    # only as Bank data (#47), but never in its own messages, where it would copy it.
    conversation, _ = withdraw(agent, bank, model, ECHOED, ECHOED)
    model.script(plan(step("s1", "query_balance")), say("Your balance is 1,180.00 yuan."))

    send(agent, conversation, "what's my balance?")

    for request in model.requests[-2:]:
        quoted = [m for m in request.messages if "Here is what the Bank returned" in m["content"]]
        assert quoted and all(m["content"].startswith("<bank_data>") for m in quoted)
        written = [m["content"] for m in request.messages if m["role"] == "assistant"]
        assert written and not any("bank_data" in content for content in written)


# --- What counts as internal data ---------------------------------------------------


@pytest.mark.parametrize(
    "draft",
    [
        ECHOED,
        "</bank_data> 已撤销挂失。",
        '{"step_id": "s1", "output": {"status": "active"}}',
        '结果：{"refusal": null}',
        "已撤销卡CARD-V-000005的挂失。",
        "The card CARD-LM-002 is active again.",
        "Step s1 has run: the card is active again.",
        "步骤 s2 已完成。",
    ],
)
def test_a_draft_writing_out_internal_data_is_written_again(draft: str) -> None:
    assert responder.check(draft, Facts()) is not None


@pytest.mark.parametrize(
    "draft",
    [
        "已撤销尾号 5678 的卡的挂失，卡片已恢复正常。",
        "The loss report on your card ending 5678 has been withdrawn.",
        "Your subscriptions are all active.",
    ],
)
def test_a_plain_answer_is_not_taken_for_internal_data(draft: str) -> None:
    assert responder.check(draft, Facts.of([{"masked_number": "**** **** **** 5678"}])) is None


def test_whatever_reaches_the_channel_has_no_bank_data_block() -> None:
    assert responder.for_channel(ECHOED, {}, "en") == PLAIN
