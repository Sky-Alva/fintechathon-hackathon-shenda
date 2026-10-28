"""Spending answers with charts (#40): "where did my money go?" and monthly and annual
reports come with a chart the Channel draws.

Every number in a chart is the Bank's: the `chart` event carries the Bank's output for
that step, unchanged, never anything the model wrote.
"""

from typing import Any

from fastapi.testclient import TestClient

from conftest import Login, act, event_types, log_in, send, start_conversation
from fake_model import FakeModel, clarify, plan, say, step

THIS_MONTH = {"start_date": "2026-10-01", "end_date": "2026-10-20"}


def bank_output(bank: TestClient, login: Login, operation: str, input: dict[str, Any]) -> Any:
    """What the Bank returns for this Operation, asked directly with the Customer's session."""
    response = bank.post(
        f"/operations/{operation}",
        headers={"Authorization": f"Bearer {login.session_token}"},
        json={"input": input},
    )
    assert response.status_code == 200, response.text
    return response.json()["output"]


def charts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event["type"] == "chart"]


def test_where_did_my_money_go_comes_with_the_banks_category_summary_as_a_chart(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        plan(step("s1", "spending_by_category", THIS_MONTH)),
        say("这是您本月的分类支出。"),
    )
    login = log_in(bank, "liming")

    events = send(agent, start_conversation(agent, login), "这个月钱都花哪了？")

    assert event_types(events) == ["step_started", "step_succeeded", "chart", "answer"]
    [chart] = charts(events)
    assert chart["step_id"] == "s1"
    assert chart["operation"] == "spending_by_category"
    assert chart["data"] == bank_output(bank, login, "spending_by_category", THIS_MONTH)
    assert chart["data"]["categories"], "the seed has spending this month"


def test_a_monthly_report_comes_as_a_chart_of_the_banks_series_titled_by_its_month(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    september = {"year": 2026, "month": 9}
    model.script(plan(step("s1", "monthly_report", september)), say("这是您9月的收支报告。"))
    login = log_in(bank, "liming")

    events = send(agent, start_conversation(agent, login), "给我看看9月的月报")

    [chart] = charts(events)
    assert chart["operation"] == "monthly_report"
    assert chart["title"] == "2026年9月收支报告"
    assert chart["data"] == bank_output(bank, login, "monthly_report", september)
    assert chart["data"]["series"]


def test_an_annual_report_comes_as_a_chart_titled_in_the_customers_language(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    year = {"year": 2026}
    model.script(plan(step("s1", "annual_report", year)), say("Here is your report."))
    login = log_in(bank, "liming")

    events = send(agent, start_conversation(agent, login), "Show me my annual report")

    [chart] = charts(events)
    assert chart["title"] == "Annual report, 2026"
    assert chart["data"] == bank_output(bank, login, "annual_report", year)
    assert len(chart["data"]["labels"]) == 12


def test_a_figure_the_model_makes_up_never_reaches_the_chart(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        plan(step("s1", "spending_by_category", THIS_MONTH)),
        say("您本月外卖花了 9,999.00 元。"),
        say("您本月外卖花了 8,888.00 元。"),
    )
    login = log_in(bank, "liming")

    events = send(agent, start_conversation(agent, login), "这个月钱都花哪了？")

    [chart] = charts(events)
    assert chart["data"] == bank_output(bank, login, "spending_by_category", THIS_MONTH)
    assert "9999" not in str(chart) and "8888" not in str(chart)


def test_a_category_summary_the_bank_refuses_gets_no_chart(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    backwards = {"start_date": "2026-10-20", "end_date": "2026-10-01"}
    model.script(plan(step("s1", "spending_by_category", backwards)), say("没能查到。"))
    login = log_in(bank, "liming")

    events = send(agent, start_conversation(agent, login), "这个月钱都花哪了？")

    assert "step_failed" in event_types(events)
    assert charts(events) == []


def test_a_chart_of_categories_is_drawn_as_bars_unless_the_customer_asks_for_a_pie(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(step("s1", "spending_by_category", THIS_MONTH)), say("Here you go."))
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = send(agent, conversation, "where did my money go?")

    assert [chart["form"] for chart in charts(events)] == ["bars"]


def test_a_pie_chart_asked_for_is_drawn_as_a_pie_even_after_a_question_about_the_month(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(
        clarify(
            "Which month's pie chart would you like?",
            ("September 2026", "2026-09"),
            ("August 2026", "2026-08"),
        )
    )
    conversation = start_conversation(agent, log_in(bank, "liming"))
    send(agent, conversation, "can you show me the piechart of my montly transactions")
    model.script(
        plan(step("s1", "monthly_report", {"year": 2026, "month": 9})),
        say("Here is September 2026."),
    )

    events = act(agent, conversation, "choose", value="2026-09")

    [chart] = charts(events)
    assert chart["operation"] == "monthly_report"
    assert chart["form"] == "pie"


def test_a_pie_asked_for_in_chinese_is_drawn_as_a_pie(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(step("s1", "spending_by_category", THIS_MONTH)), say("这是本月的分类支出。"))
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = send(agent, conversation, "用饼图看看这个月的支出")

    assert [chart["form"] for chart in charts(events)] == ["pie"]


def test_an_annual_report_stays_monthly_bars_even_when_a_pie_is_asked_for(
    agent: TestClient, bank: TestClient, model: FakeModel
) -> None:
    model.script(plan(step("s1", "annual_report", {"year": 2026})), say("Here is 2026."))
    conversation = start_conversation(agent, log_in(bank, "liming"))

    events = send(agent, conversation, "2026 as a pie chart")

    assert [chart["form"] for chart in charts(events)] == ["bars"]
