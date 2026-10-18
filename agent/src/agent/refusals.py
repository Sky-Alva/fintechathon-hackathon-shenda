"""Refusals: what the Customer is told when the Agent won't do something.

An out-of-scope refusal lists what the Agent can do, in the Bank's own words from the
Operation catalogue, so the list grows as the Bank gains Operations.
"""

from typing import Any

from agent.language import Language
from agent.planner import MAX_OFFERED


def offered(can_do: list[str], catalogue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The catalogue entries the model chose to offer, ignoring any the Bank doesn't have;
    the catalogue's first few if it chose none."""
    by_name = {op["name"]: op for op in catalogue}
    chosen = [by_name[name] for name in dict.fromkeys(can_do) if name in by_name]
    return (chosen or catalogue)[:MAX_OFFERED]


OUT_OF_SCOPE: dict[Language, str] = {
    "zh": "抱歉，这个我暂时帮不了您。我可以帮您：",
    "en": "Sorry, I can't help with that. Here's what I can do for you:",
}


def out_of_scope(can_do: list[str], catalogue: list[dict[str, Any]], language: Language) -> str:
    lines = [f"- {op[f'description_{language}']}" for op in offered(can_do, catalogue)]
    return "\n".join([OUT_OF_SCOPE[language], *lines])
