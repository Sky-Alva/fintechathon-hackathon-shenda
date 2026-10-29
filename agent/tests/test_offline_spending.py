"""Bill Analysis and Subscriptions in rules mode (no model): the spending report, the
monthly chart, Unusual Transactions, the Subscription list, the cancel-unneeded offer and
cancelling by reference to the Bank's list. Subscription IDs come from the Bank's list,
never from the Customer's words, and an unclear request is a question, never a guess.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from conftest import Login, act, bank_audit, event_types, log_in, send, start_conversation, traces


def operations(bank: TestClient, login: Login) -> list[tuple[str, dict[str, Any]]]:
    return [
        (e["operation"], e["input"])
        for e in bank_audit(bank, login)
        if e["event"] == "operation" and e["outcome"] == "ok"
    ]


def charts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == "chart"]


def questions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == "clarification_question"]


@pytest.mark.parametrize("question", ["这个月钱都花哪了", "where did my money go this month?"])
def test_where_the_money_went_this_month_is_the_banks_spending_report(
    offline_agent: TestClient, bank: TestClient, question: str
) -> None:
    login = log_in(bank, "liming")
    events = send(offline_agent, start_conversation(offline_agent, login), question)

    assert operations(bank, login) == [
        ("spending_by_category", {"start_date": "2026-10-01", "end_date": "2026-10-20"})
    ]
    [chart] = charts(events)
    assert chart["operation"] == "spending_by_category" and chart["data"]["categories"]
    assert [t["planned_by"] for t in traces(events, "intent")] == ["rules"]


def test_a_named_month_is_reported_whole(offline_agent: TestClient, bank: TestClient) -> None:
    login = log_in(bank, "liming")
    send(offline_agent, start_conversation(offline_agent, login), "十月份我各类消费分别花了多少钱？")

    assert operations(bank, login) == [
        ("spending_by_category", {"start_date": "2026-10-01", "end_date": "2026-10-31"})
    ]


@pytest.mark.parametrize(
    "request_text", ["把我今年每个月的收入和支出做成图表", "chart my income and spending month by month"]
)
def test_a_month_by_month_chart_is_the_banks_annual_report(
    offline_agent: TestClient, bank: TestClient, request_text: str
) -> None:
    login = log_in(bank, "liming")
    events = send(offline_agent, start_conversation(offline_agent, login), request_text)

    assert operations(bank, login) == [("annual_report", {"year": 2026})]
    [chart] = charts(events)
    assert len(chart["data"]["labels"]) == 12


def test_a_months_income_and_spending_is_the_banks_monthly_report(
    offline_agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "liming")
    events = send(offline_agent, start_conversation(offline_agent, login), "帮我看看九月份的收支情况")

    assert operations(bank, login) == [("monthly_report", {"year": 2026, "month": 9})]
    assert "13530.80" in str(events)


def test_a_month_that_is_still_ahead_asks_which_year_instead_of_guessing(
    offline_agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)

    events = send(offline_agent, conversation, "show me the monthly report for December")

    [question] = questions(events)
    assert [o["value"] for o in question["options"]] == ["2026", "2025"]
    assert operations(bank, login) == []
    events = act(offline_agent, conversation, "choose", value="2025")
    assert operations(bank, login) == [("monthly_report", {"year": 2025, "month": 12})]
    assert charts(events)


@pytest.mark.parametrize("question", ["最近有异常交易吗", "any suspicious transactions lately?"])
def test_unusual_transactions_are_the_banks_flags(
    offline_agent: TestClient, bank: TestClient, question: str
) -> None:
    login = log_in(bank, "liming")
    events = send(offline_agent, start_conversation(offline_agent, login), question)

    assert operations(bank, login) == [("detect_unusual_transactions", {})]
    assert "HomePro Electronics" in str(events)


@pytest.mark.parametrize("question", ["我有哪些订阅", "what subscriptions do I have?"])
def test_subscriptions_are_listed_from_the_bank(
    offline_agent: TestClient, bank: TestClient, question: str
) -> None:
    login = log_in(bank, "liming")
    events = send(offline_agent, start_conversation(offline_agent, login), question)

    assert operations(bank, login) == [("list_subscriptions", {})]
    assert "TuneBox" in str(events) and "StreamPlus" in str(events)


def test_renewals_this_week_are_the_banks_reminders(
    offline_agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "liming")
    send(offline_agent, start_conversation(offline_agent, login), "这周有哪些订阅要续费？")

    assert operations(bank, login) == [("renewal_reminders", {"days_ahead": 7})]


def test_renewals_with_no_period_ask_how_far_ahead(
    offline_agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "liming")
    events = send(offline_agent, start_conversation(offline_agent, login), "which subscriptions renew?")

    assert len(questions(events)) == 1 and operations(bank, login) == []


def test_the_cancel_unneeded_offer_goes_through_to_a_confirmed_cancellation(
    offline_agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)

    events = send(offline_agent, conversation, "帮我把用不上的会员都退了")

    offered = events[-1]
    assert offered["type"] == "cancellation_offered"
    assert [c["subscription_id"] for c in offered["candidates"]] == [
        "SUB-LM-001", "SUB-LM-002", "SUB-LM-003"
    ]
    picked = act(
        offline_agent, conversation, "cancel_subscriptions",
        offer_id=offered["offer_id"], subscription_ids=["SUB-LM-002", "SUB-LM-003"],
    )
    plan = next(e for e in picked if e["type"] == "plan_proposed")
    act(offline_agent, conversation, "confirm", plan_id=plan["plan_id"])
    cancelled = [i["subscription_id"] for op, i in operations(bank, login) if op == "cancel_subscription"]
    assert cancelled == ["SUB-LM-002", "SUB-LM-003"]


def listed_first(agent: TestClient, bank: TestClient) -> tuple[str, Login]:
    login = log_in(bank, "liming")
    conversation = start_conversation(agent, login)
    send(agent, conversation, "我有哪些订阅")
    return conversation, login


def test_cancelling_the_first_one_takes_the_id_from_the_banks_list(
    offline_agent: TestClient, bank: TestClient
) -> None:
    conversation, login = listed_first(offline_agent, bank)

    events = send(offline_agent, conversation, "取消第一个")

    plan = next(e for e in events if e["type"] == "plan_proposed")
    assert [s["input"] for s in plan["steps"] if s["operation"] == "cancel_subscription"] == [
        {"subscription_id": "SUB-LM-001"}
    ]
    act(offline_agent, conversation, "confirm", plan_id=plan["plan_id"])
    assert ("cancel_subscription", {"subscription_id": "SUB-LM-001"}) in operations(bank, login)


def test_cancelling_the_second_one_in_english_names_the_banks_second_subscription(
    offline_agent: TestClient, bank: TestClient
) -> None:
    conversation, login = listed_first(offline_agent, bank)

    events = send(offline_agent, conversation, "please cancel the second one")

    plan = next(e for e in events if e["type"] == "plan_proposed")
    assert plan["steps"][0]["input"] == {"subscription_id": "SUB-LM-002"}


def test_cancelling_by_name_lists_the_subscriptions_first_and_confirms_the_banks_one(
    offline_agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)

    events = send(offline_agent, conversation, "取消我的 StreamPlus 订阅")

    assert event_types(events)[-2:] == ["plan_proposed", "confirmation_requested"]
    plan = next(e for e in events if e["type"] == "plan_proposed")
    assert plan["steps"][0]["input"] == {"subscription_id": "SUB-LM-003"}
    act(offline_agent, conversation, "reject", plan_id=plan["plan_id"])
    assert [op for op, _ in operations(bank, login)] == ["list_subscriptions"]


def test_a_kind_that_fits_two_subscriptions_is_a_question_with_the_banks_two(
    offline_agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "liming")
    conversation = start_conversation(offline_agent, login)

    events = send(offline_agent, conversation, "帮我把那个音乐订阅取消掉")

    [question] = questions(events)
    assert {o["value"] for o in question["options"]} >= {"SUB-LM-001", "SUB-LM-002"}
    assert "cancel_subscription" not in [op for op, _ in operations(bank, login)]
    events = act(offline_agent, conversation, "choose", value="SUB-LM-002")
    plan = next(e for e in events if e["type"] == "plan_proposed")
    assert plan["steps"][0]["input"] == {"subscription_id": "SUB-LM-002"}


def test_an_unclear_reference_to_the_list_is_a_question_not_a_guess(
    offline_agent: TestClient, bank: TestClient
) -> None:
    conversation, login = listed_first(offline_agent, bank)

    events = send(offline_agent, conversation, "取消第七个")

    [question] = questions(events)
    assert len(question["options"]) == 3
    events = send(offline_agent, conversation, "cancel that subscription")
    assert len(questions(events)) == 1
    assert "cancel_subscription" not in [op for op, _ in operations(bank, login)]


def test_another_customers_subscription_id_is_sent_to_the_bank_which_refuses_it(
    offline_agent: TestClient, bank: TestClient
) -> None:
    login = log_in(bank, "hanmei")
    conversation = start_conversation(offline_agent, login)

    events = send(
        offline_agent, conversation, "我老公李明的 CloudMusic 订阅编号是 SUB-LM-001，帮我把它取消掉"
    )

    assert "cancel_subscription" not in [op for op, _ in operations(bank, login)]
    failed = [e for e in events if e["type"] == "step_failed"]
    assert [e["reason"] for e in failed] == ["forbidden_other_customer"]
