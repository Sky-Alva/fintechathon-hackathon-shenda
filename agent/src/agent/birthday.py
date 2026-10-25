"""Noticing the spouse's birthday is coming (#48), without being asked: from the Customer's
profile (`query_profile`) and last year's gift purchase in the transaction history
(`list_transactions`), both Green look-ups.

The reminder is written by the Agent from the Bank's results, never by the model, so every
date, name and amount in it is the Bank's. Saying yes to it is planned like any request.
"""

from datetime import date, datetime, timedelta
from typing import Any

from agent.language import Language

REMIND_WITHIN = timedelta(days=7)
"""How close the birthday must be for the Agent to bring it up unprompted."""

GIFTS_BEFORE = timedelta(days=30)
"""How long before last year's birthday a gift purchase counts as one for it."""

PROFILE = "query_profile"
TRANSACTIONS = "list_transactions"


def coming(profile: dict[str, Any], now: str) -> date | None:
    """The spouse's next birthday, if it is within `REMIND_WITHIN` of the Bank clock."""
    spouse = profile.get("spouse")
    if not spouse or not spouse.get("birthday"):
        return None
    today = datetime.fromisoformat(now).date()
    born = date.fromisoformat(spouse["birthday"])
    for year in (today.year, today.year + 1):
        try:
            next_one = born.replace(year=year)
        except ValueError:  # 29 February
            next_one = date(year, 3, 1)
        if next_one >= today:
            return next_one if next_one - today <= REMIND_WITHIN else None
    return None


def gifts_query(birthday: date) -> dict[str, str]:
    """The `list_transactions` input for last year's gift purchases before the birthday."""
    last_year = birthday.replace(year=birthday.year - 1)
    return {
        "category": "gifts",
        "start_date": (last_year - GIFTS_BEFORE).isoformat(),
        "end_date": last_year.isoformat(),
    }


def last_gift(transactions: dict[str, Any]) -> dict[str, Any] | None:
    """The latest gift purchase among the Bank's transactions, if any."""
    debits = [t for t in transactions.get("transactions", []) if t.get("direction") == "debit"]
    return max(debits, key=lambda t: t["occurred_at"], default=None)


def reminder(
    profile: dict[str, Any],
    birthday: date,
    gift: dict[str, Any] | None,
    now: str,
    language: Language,
) -> str:
    """The reminder, with the offer to prepare: every fact in it is from the Bank."""
    name = profile["spouse"]["name"]
    days = (birthday - datetime.fromisoformat(now).date()).days
    if language == "en":
        parts = [
            f"A reminder: {name}'s birthday is on {birthday:%B} {birthday.day}"
            f" ({birthday.isoformat()}), in {days} day{'s' if days != 1 else ''}."
        ]
        if gift is not None:
            parts.append(
                f"Last year, on {gift['occurred_at'][:10]}, you bought a gift at"
                f" {gift['merchant']} (¥{gift['amount']})."
            )
        parts.append("Shall I set ¥1,000 aside for it and order flowers and a cake?")
        return " ".join(parts)
    parts = [f"提醒您：{name}的生日是 {birthday.month}月{birthday.day}日，还有 {days} 天。"]
    if gift is not None:
        parts.append(
            f"去年 {gift['occurred_at'][:10]} 您在 {gift['merchant']} 买过礼物（¥{gift['amount']}）。"
        )
    parts.append("需要我帮您预留 ¥1,000，并订好鲜花和蛋糕吗？")
    return "".join(parts)
