"""Charts of spending (#40): a category summary, or a monthly or annual report, drawn by the
Channel from the Bank's output for the step, unchanged. The model writes nothing in it."""

from typing import Any, cast, get_args

from agent.events import Chart, ChartOperation
from agent.language import Language

CHART_OPERATIONS: frozenset[str] = frozenset(get_args(ChartOperation))


def chart(step_id: str, operation: str, output: dict[str, Any], language: Language) -> Chart | None:
    """The chart of a step's Bank output, if its Operation is one the Channel draws."""
    if operation not in CHART_OPERATIONS:
        return None
    return Chart(
        step_id=step_id,
        operation=cast(ChartOperation, operation),
        title=title(operation, output, language),
        data=output,
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
