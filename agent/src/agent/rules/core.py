"""The shared core of the rules Planner: what an intent is, and the request it is given."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent import planner
from agent.language import Language

Reply = (
    planner.Plan
    | planner.Declined
    | planner.HumanAsked
    | planner.Clarification
    | planner.CancellationsAsked
)
"""What an intent answers with: the model Planner's result union."""


@dataclass(frozen=True)
class Request:
    """What `make_plan` was given, plus the Customer's latest message ready for matching."""

    text: str
    """The Customer's latest message as written."""
    matching: str
    """The same, normalised for matching only (`guard.normalise`: full-width made plain, disguises undone, lower case)."""
    language: Language
    history: list[dict[str, Any]]
    catalogue: list[dict[str, Any]]
    looked_up: list[dict[str, Any]] | None
    """The Bank's results for the Plan's look-ups: set in the second stage."""
    interrupted: dict[str, Any] | None
    failed: dict[str, Any] | None
    now: str
    payees: list[dict[str, Any]]
    cards: list[dict[str, Any]]
    subscriptions: list[dict[str, Any]]

    def say(self, zh: str, en: str) -> str:
        return zh if self.language == "zh" else en


@dataclass(frozen=True)
class Intent:
    """One thing the Customer can ask for, recognised by patterns on their message."""

    name: str
    label: dict[Language, str]
    """Short name of the intent in each language, offered when two intents both match."""
    triggers: tuple[re.Pattern[str], ...]
    """Any of these matching the (normalised) message makes the intent a candidate."""
    plan: Callable[[Request], Reply]
    """First stage: the Plan of look-ups (or the whole Plan), or the question to ask. Called
    only with the slots still unknown, so it extracts them itself and asks if one is
    missing or ambiguous."""
    complete: Callable[[Request], Reply] | None = None
    """Second stage, with `request.looked_up` set: the rest of the Plan from the Bank's
    results. Leave out for an intent whose Plan is complete after the first stage."""
    excludes: tuple[re.Pattern[str], ...] = ()
    """Any of these matching means the message is about something else: not a candidate."""

    def matches(self, request: Request) -> bool:
        text = request.matching
        return any(t.search(text) for t in self.triggers) and not any(
            e.search(text) for e in self.excludes
        )


def pattern(expression: str) -> re.Pattern[str]:
    """A trigger pattern. The message is lower-cased and in half-width before matching."""
    return re.compile(expression, re.IGNORECASE)
