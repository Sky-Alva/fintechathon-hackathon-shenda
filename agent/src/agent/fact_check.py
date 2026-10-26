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

HAN_CLASS = r"[\u4e00-\u9fff]"
NUMBER = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
AMOUNT = re.compile(
    rf"(?P<pre>[¥￥]|RMB|CNY|人民币)?\s*(?P<num>{NUMBER})\s*(?P<wan>万)?\s*"
    r"(?P<post>元|块|RMB|CNY|yuan)?",
    re.IGNORECASE,
)

# A bare number is an amount when the words before it say so ("余额为5000").
MONEY_WORDS = (
    r"余额|金额|总额|合计|共计|总计|花了|花费|支出|消费|收入|限额|额度|价格|售价|"
    r"balance|total|amount|spent|spending|paid|cost|price|limit"
)
COUNTERS = r"\s*(?:笔|张|个|次|项|天|日|号|月|年|步|位|%|％|percent|times|cards?|days?)"
BARE_AMOUNT = re.compile(
    rf"(?:{MONEY_WORDS})\s*(?:是|为|有|约|大约|共|:|：|is|was|of|about|around)?\s*"
    rf"(?P<num>\d+)(?!\d|[.,]\d|{COUNTERS}|\s*(?:万|元|块|RMB|CNY|yuan))",
    re.IGNORECASE,
)

# Amounts in Chinese numerals: "一千二百元", "人民币两千", "五千块".
CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNITS = {"十": 10, "百": 100, "千": 1000}
CN_NUMBER = r"[零〇一二两三四五六七八九十][零〇一二两三四五六七八九十百千万亿]*(?:点[零〇一二三四五六七八九]+)?"
CN_AMOUNT = re.compile(
    rf"(?:(?:人民币|[¥￥])\s*(?P<pre>{CN_NUMBER})|(?:{MONEY_WORDS})\s*(?:是|为|有|约|大约|共|:|：)?"
    rf"\s*(?P<ctx>{CN_NUMBER})(?!{COUNTERS})|(?P<post>{CN_NUMBER})\s*(?:元|圆|块钱|块(?!{HAN_CLASS})))"
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
    re.compile(rf"给(?P<name>{HAN}{{2,8}}?)(?:转账|转|汇款|汇|付款|付|打款|打钱)"),
    re.compile(rf"\b(?:to|payee:?)\s+(?P<name>{HAN}{{2,6}})", re.IGNORECASE),
]
# A Payee's name in Latin letters ("to Zhao Qiang"): a full name, two capitalised words or
# more, which the Bank's data must contain.
LATIN_PAYEES = [
    re.compile(r"\b(?:[Tt]o|[Pp]ayee:?)\s+(?P<name>[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)"),
]


def _month(text: str) -> int:
    return int(text) if text.isdigit() else MONTHS.index(text[:3].lower()) + 1


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def chinese_number(text: str) -> Decimal | None:
    """The value of a number in Chinese numerals ("一千二百" → 1200), or None."""
    whole, _, fraction = text.partition("点")
    total = section = digit = 0
    for char in whole:
        if char in CN_DIGITS:
            digit = CN_DIGITS[char]
        elif char in CN_UNITS:
            section += (digit or 1) * CN_UNITS[char]
            digit = 0
        elif char in ("万", "亿"):
            total = (total + section + digit) * (10000 if char == "万" else 100_000_000)
            section = digit = 0
        else:
            return None
    value = Decimal(total + section + digit)
    if fraction:
        if any(char not in CN_DIGITS for char in fraction):
            return None
        value += Decimal("0." + "".join(str(CN_DIGITS[c]) for c in fraction))
    return value


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

    latin: set[str] = field(default_factory=set)
    """Every text the Bank returned, lower-cased: names in Latin letters among them."""

    def has_name(self, mention: str) -> bool:
        """Whether `mention` starts with a name the Bank returned ("韩梅梅吗" does)."""
        return any(mention.startswith(name) for name in self.names)

    def has_latin_name(self, mention: str) -> bool:
        """Whether a Bank text contains this name in Latin letters."""
        return any(mention.lower() in text for text in self.latin)

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
                    facts.latin.add(leaf.lower())
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
    for pattern in LATIN_PAYEES:
        for match in pattern.finditer(text):
            if not facts.has_latin_name(match.group("name")):
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
    for match in CN_AMOUNT.finditer(text):
        written = match.group("pre") or match.group("ctx") or match.group("post")
        if chinese_number(written) not in facts.amounts:
            found.append(match.group().strip())
    for match in BARE_AMOUNT.finditer(text):
        if _decimal(match.group("num")) not in facts.amounts:
            found.append(match.group().strip())
    for match in AMOUNT.finditer(text):
        num = match.group("num")
        is_amount = any(match.group(g) for g in ("pre", "wan", "post")) or "." in num or "," in num
        amount = _decimal(num)
        if amount is not None and match.group("wan"):
            amount *= 10000
        if is_amount and amount not in facts.amounts:
            found.append(match.group().strip())
    return found
