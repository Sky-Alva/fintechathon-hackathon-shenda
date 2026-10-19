"""The fact check: which amounts, dates, cards and Payees in an answer the Bank never returned.

`Facts` is everything the Bank's results say; `unsupported` lists each mention in an
answer that no fact backs. It reads the Bank's outputs only: never the Customer's words,
the Plan's inputs or the model's earlier answers.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

NUMBER = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
AMOUNT = re.compile(
    rf"(?P<pre>[¥￥]|RMB|CNY|人民币)?\s*(?P<num>{NUMBER})\s*(?P<wan>万)?\s*"
    r"(?P<post>元|块|RMB|CNY|yuan)?",
    re.IGNORECASE,
)

ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
MONTH = r"(?P<month>" + "|".join(MONTHS) + r")[a-z]*\.?"
DAY = r"(?P<day>\d{1,2})(?!\d)(?:st|nd|rd|th)?"
DATES = [
    re.compile(
        r"(?P<year>\d{4})\s*[-/.年]\s*(?P<month>\d{1,2})\s*[-/.月]\s*(?P<day>\d{1,2})\s*[日号]?"
    ),
    re.compile(r"(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*[日号]"),
    re.compile(rf"\b{MONTH}\s+{DAY}(?:,?\s+(?P<year>\d{{4}}))?", re.IGNORECASE),
    re.compile(rf"\b{DAY}\s+{MONTH}(?:,?\s+(?P<year>\d{{4}}))?", re.IGNORECASE),
]

Date = tuple[int, int, int]

# A card is mentioned by its last four digits, its full number or its Bank ID.
MASKED = re.compile(r"\*{2,}[\s*]*(\d{4})(?!\d)")
LAST4 = re.compile(
    r"(?:尾号|末四位|后四位|ending(?:\s+in)?|ends?\s+(?:in|with))\s*[:：]?\s*(\d{4})(?!\d)",
    re.IGNORECASE,
)
LONG_NUMBER = re.compile(r"(?<!\d)\d{8,19}(?!\d)")
IDENTIFIER = re.compile(r"(?<![A-Za-z0-9-])[A-Z]{2,}(?:-[A-Z0-9]+)+(?![A-Za-z0-9-])")

# A Payee is mentioned by name (or nickname) where money goes to someone.
HAN = r"[\u4e00-\u9fff]"
PAYEES = [
    re.compile(rf"(?:转给|转账给|汇给|付给|打给|收款人|收款方)\s*(?:是|为|[:：])?\s*(?P<name>{HAN}{{2,6}})"),
    re.compile(rf"向(?P<name>{HAN}{{2,6}}?)(?:转账|转|汇款|汇|付款|付)"),
    re.compile(rf"\b(?:to|payee:?)\s+(?P<name>{HAN}{{2,6}})", re.IGNORECASE),
]


def _month(text: str) -> int:
    return int(text) if text.isdigit() else MONTHS.index(text[:3].lower()) + 1


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def _leaves(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from _leaves(item)
    else:
        yield value


@dataclass
class Facts:
    amounts: set[Decimal] = field(default_factory=set)
    dates: set[Date] = field(default_factory=set)
    last4: set[str] = field(default_factory=set)
    tokens: set[str] = field(default_factory=set)
    """Identifiers and long numbers (card and account numbers), exactly as returned."""
    names: set[str] = field(default_factory=set)
    """Every run of Chinese text: names and nicknames among them."""

    def has_name(self, mention: str) -> bool:
        """Whether `mention` starts with a name the Bank returned ("韩梅梅吗" does)."""
        return any(mention.startswith(name) for name in self.names)

    def has_date(self, year: int | None, month: int, day: int) -> bool:
        return any(
            (year is None or year == y) and (month, day) == (m, d) for y, m, d in self.dates
        )

    @classmethod
    def of(cls, outputs: Iterable[dict[str, Any]]) -> "Facts":
        """The facts in these Bank outputs."""
        facts = cls()
        for output in outputs:
            for leaf in _leaves(output):
                if isinstance(leaf, bool) or leaf is None:
                    continue
                if isinstance(leaf, int | float):
                    facts.amounts.add(Decimal(str(leaf)))
                elif isinstance(leaf, str):
                    facts.names.update(re.findall(rf"{HAN}{{2,}}", leaf))
                    facts.last4.update(MASKED.findall(leaf))
                    for number in LONG_NUMBER.findall(leaf):
                        facts.tokens.add(number)
                        facts.last4.add(number[-4:])
                    facts.tokens.update(IDENTIFIER.findall(leaf))
                    for y, m, d in ISO_DATE.findall(leaf):
                        facts.dates.add((int(y), int(m), int(d)))
                    for match in re.finditer(NUMBER, leaf):
                        if (number := _decimal(match.group())) is not None:
                            facts.amounts.add(number)
        return facts


def unsupported(text: str, facts: Facts) -> list[str]:
    """Each amount, date, card or Payee `text` mentions that no Bank fact backs."""
    found: list[str] = []
    for pattern in PAYEES:
        for match in pattern.finditer(text):
            if not facts.has_name(match.group("name")):
                found.append(match.group().strip())
    for pattern in DATES:
        for match in pattern.finditer(text):
            year = match.groupdict().get("year")
            if not facts.has_date(
                int(year) if year else None, _month(match.group("month")), int(match.group("day"))
            ):
                found.append(match.group().strip())
        text = pattern.sub(" ", text)
    for pattern in (MASKED, LAST4):
        for match in pattern.finditer(text):
            if match.group(1) not in facts.last4:
                found.append(match.group().strip())
        text = pattern.sub(" ", text)
    for pattern in (LONG_NUMBER, IDENTIFIER):
        for match in pattern.finditer(text):
            if match.group() not in facts.tokens:
                found.append(match.group())
        text = pattern.sub(" ", text)
    for match in AMOUNT.finditer(text):
        num = match.group("num")
        is_amount = any(match.group(g) for g in ("pre", "wan", "post")) or "." in num or "," in num
        amount = _decimal(num)
        if amount is not None and match.group("wan"):
            amount *= 10000
        if is_amount and amount not in facts.amounts:
            found.append(match.group().strip())
    return found
