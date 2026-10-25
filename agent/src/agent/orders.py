"""Merchant orders the Bank pays later: an order is accepted when the Customer approves it
and paid from its Reserved Funds at 09:00 on its delivery date, when the Bank clock gets
there (#13, #48). The Agent notes the orders it placed, and tells the Customer once the
Bank has paid them, or couldn't.

What the Customer is told is written by the Agent from the Bank's order list, never by the
model: the Bank's words (product names, amounts, dates, refusal reasons) only.
"""

from collections.abc import Iterable
from datetime import date, datetime, time
from typing import Any

from agent import failures
from agent.executor import StepResult
from agent.language import Language

PAY_TIME = time(9, 0)
"""When, on its delivery date, the Bank pays an order (Bank time)."""


def placed(results: Iterable[StepResult]) -> dict[str, str]:
    """The orders among these results that the Bank will pay later: delivery date by ID."""
    found: dict[str, str] = {}
    for result in results:
        if result.operation != "order_from_merchant" or result.output is None:
            continue
        order = result.output.get("order") or {}
        if order.get("status") == "pending":
            found[order["order_id"]] = order["delivery_date"]
    return found


def any_due(awaiting: dict[str, str], now: str) -> bool:
    """Whether the Bank clock has reached the payment time of any awaited order."""
    clock = datetime.fromisoformat(now)
    return any(
        datetime.combine(date.fromisoformat(day), PAY_TIME, clock.tzinfo) <= clock
        for day in awaiting.values()
    )


def settled(awaiting: dict[str, str], listed: dict[str, Any]) -> list[dict[str, Any]]:
    """The awaited orders the Bank has paid or failed to pay, as it lists them."""
    return [
        order
        for order in listed.get("orders", [])
        if order.get("order_id") in awaiting and order.get("status") in ("paid", "failed")
    ]


def _why(reason: str | None, language: Language) -> str:
    """The Bank's reason, in plain words when the Agent has them."""
    words = failures.PLAIN_WORDS.get(reason or "")
    if words is not None:
        return words[language]
    return f"银行给出的原因：{reason}" if language == "zh" else f"the Bank's reason: {reason}"


def settled_text(orders: list[dict[str, Any]], language: Language) -> str:
    """What the Customer is told about orders the Bank has just paid, or couldn't."""
    paid = [order for order in orders if order["status"] == "paid"]
    unpaid = [order for order in orders if order["status"] == "failed"]
    lines = []
    if language == "en":
        if paid:
            items = "; ".join(
                f"{o['product_name']} from {o['merchant_name']}, ¥{o['amount']}" for o in paid
            )
            lines.append(
                f"Your order{'s' if len(paid) > 1 else ''} for {paid[0]['delivery_date']}"
                f" {'have' if len(paid) > 1 else 'has'} been paid from your Reserved Funds,"
                f" as you approved: {items}."
            )
        for o in unpaid:
            why = _why(o.get("failure_reason"), language)
            lines.append(
                f"Your order {o['order_id']} ({o['product_name']}, ¥{o['amount']}) couldn't be"
                f" paid: {why}."
            )
        return " ".join(lines)
    if paid:
        items = "；".join(
            f"{o['merchant_name']} 的 {o['product_name']} ¥{o['amount']}" for o in paid
        )
        lines.append(
            f"您在 {paid[0]['delivery_date']} 送达的订单已按您确认的计划，从预留资金付款：{items}。"
        )
    for o in unpaid:
        why = _why(o.get("failure_reason"), language)
        lines.append(f"订单 {o['order_id']}（{o['product_name']} ¥{o['amount']}）未能付款：{why}。")
    return "".join(lines)
