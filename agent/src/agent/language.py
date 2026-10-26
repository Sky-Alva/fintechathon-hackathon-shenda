"""The Customer's language: Chinese by default, English when they write in English.

Decided from the Customer's latest message that has any words in it, so "500" or "1" on
its own keeps the conversation's language.
"""

import re
from typing import Any, Literal

Language = Literal["zh", "en"]

HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
LATIN = re.compile(r"[A-Za-z]")


def customer_language(history: list[dict[str, Any]]) -> Language:
    for message in reversed(history):
        if message["role"] != "user":
            continue
        text = message["content"]
        if HAN.search(text):
            return "zh"
        if LATIN.search(text):
            return "en"
    return "zh"
