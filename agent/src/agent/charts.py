"""Charts of spending (#40): a category summary, or a monthly or annual report, drawn by the
Channel from the Bank's output for the step, unchanged. The model writes nothing in it.

The categories are drawn as a pie when the Customer asked for one ("show me a pie chart",
"饼图"): the Agent reads that from the Customer's own words, not from the model, which
otherwise says it drew a pie chart the Channel showed as bars."""

import re
from typing import Any, cast, get_args

from agent.events import Chart, ChartForm, ChartOperation
from agent.language import Language

CHART_OPERATIONS: frozenset[str] = frozenset(get_args(ChartOperation))
CATEGORY_OPERATIONS = frozenset({"spending_by_category", "monthly_report"})
"""The charts of spending by category: those that can be drawn as a pie."""

PIE = re.compile(r"\bpie\b|piechart|饼图|饼状图|圆饼图", re.IGNORECASE)
BARS = re.compile(r"\bbars?\b|bar ?chart|柱状图|条形图|柱形图", re.IGNORECASE)


def asked_form(history: list[dict[str, Any]]) -> ChartForm:
    """The form of chart the Customer last asked for in words: a pie, or bars (the default)."""
    for message in reversed(history):
        if message["role"] != "user":
            continue
        if PIE.search(message["content"]):
            return "pie"
        if BARS.search(message["content"]):
            return "bars"
    return "bars"


def chart(
    step_id: str,
    operation: str,
    output: dict[str, Any],
    language: Language,
    form: ChartForm = "bars",
) -> Chart | None:
    """The chart of a step's Bank output, if its Operation is one the Channel draws. Only
    a chart of categories is drawn as a pie."""
    if operation not in CHART_OPERATIONS:
        return None
    return Chart(
        step_id=step_id,
        operation=cast(ChartOperation, operation),
        title=title(operation, output, language),
        data=output,
        form=form if operation in CATEGORY_OPERATIONS else "bars",
    )


def title(operation: str, output: dict[str, Any], language: Language) -> str:
    """What the chart shows, for the period the Bank's output gives."""
    if operation == "monthly_report":
        year, month = output["year"], output["month"]
        return f"Monthly report, {year}-{month:02d}" if language == "en" else f"{year}年{month}月收支报告"
    if operation == "annual_report":
        year = output["year"]
        return f"Annual report, {year}" if language == "en" else f"{year}年收支报告"
    start, end = output.get("start_date"), output.get("end_date")
    if language == "en":
        period = (
            f"{start} to {end}" if start and end
            else f"from {start}" if start
            else f"to {end}" if end
            else ""
        )
        return f"Spending by category ({period})" if period else "Spending by category"
    period = (
        f"{start} 至 {end}" if start and end
        else f"{start} 起" if start
        else f"截至 {end}" if end
        else ""
    )
    return f"分类支出（{period}）" if period else "分类支出"
