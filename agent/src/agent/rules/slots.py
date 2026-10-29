"""Slot extractors shared by every intent module: pull a value out of the Customer's words.

They only read text; none of them decides anything. An extractor that finds nothing returns
an empty list and the intent asks the Customer (never a default, never a guess). Payees,
cards and Subscriptions are not extracted here: they are always resolved through the Bank.
"""

import re
import unicodedata
from decimal import Decimal, InvalidOperation

DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
          "七": 7, "八": 8, "九": 9}
SMALL_UNITS = {"十": 10, "百": 100, "千": 1000}
NUMERAL = "[零〇一二两三四五六七八九十百千万]"
UNIT = r"(?:块钱|块|元|圆|rmb|cny|yuan|dollars?|usd)"
ARABIC = r"\d+(?:,\d{3})*(?:\.\d+)?"
AMOUNT = re.compile(
    rf"(?:[¥￥]\s*(?P<symbol>{ARABIC}))"
    rf"|(?P<arabic>{ARABIC})\s*(?P<arabic_scale>万)?\s*{UNIT}"
    rf"|(?P<chinese>{NUMERAL}+)\s*{UNIT}",
    re.IGNORECASE,
)
BARE = re.compile(rf"(?<![\d.]){ARABIC}(?![\d.])")


def normalise(text: str) -> str:
    """The text for matching only: full-width letters and digits made plain, lower case."""
    return unicodedata.normalize("NFKC", text).lower()


def chinese_number(text: str) -> int | None:
    """二百五十 → 250, 两千 → 2000, 一万二 → 12000, 十五 → 15; None if it isn't a number."""
    if not text or any(c not in DIGITS and c not in SMALL_UNITS and c != "万" for c in text):
        return None
    if "万" in text:
        head, _, tail = text.partition("万")
        high = chinese_number(head) if head else 1
        low = chinese_number(tail) if tail else 0
        if high is None or low is None:
            return None
        if tail and len(tail) == 1 and tail in DIGITS:  # 一万二 = 12000
            low = DIGITS[tail] * 1000
        return high * 10000 + low
    total = 0
    digit: int | None = None
    for char in text:
        if char in DIGITS:
            if digit is not None:  # 一二: not a quantity, a string of digits
                return None
            digit = DIGITS[char]
        else:
            total += (1 if digit is None else digit) * SMALL_UNITS[char]
            digit = None
    return total + (digit or 0)  # 二百五 is read literally: 205, never guessed as 250


def amounts(text: str) -> list[Decimal]:
    """The money amounts in the message, in order, as the Customer wrote them: a number
    with a unit (200块, 2,500元, 三百元, 1.5万元) or after ¥; failing that, bare Arabic
    numbers. Empty if there are none; the caller asks. Amounts of zero or less are kept
    out, as the Bank would refuse them anyway."""
    text = unicodedata.normalize("NFKC", text)
    found: list[Decimal] = []
    for match in AMOUNT.finditer(text):
        try:
            if (raw := match["symbol"] or match["arabic"]) is not None:
                value = Decimal(raw.replace(",", ""))
                if match["arabic_scale"]:
                    value *= 10000
            else:
                number = chinese_number(match["chinese"])
                if number is None:
                    continue
                value = Decimal(number)
        except InvalidOperation:
            continue
        found.append(value)
    if not found:
        for match in BARE.finditer(text):
            try:
                found.append(Decimal(match[0].replace(",", "")))
            except InvalidOperation:
                continue
    return [value for value in found if value > 0]


def money(value: Decimal) -> str:
    """The amount as the Bank takes it: two decimals."""
    return f"{value:.2f}"
