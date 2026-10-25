"""Payees as the Bank's Payee lookup (`find_payee`) found them in this conversation.

The Agent never guesses with the Customer's money: when the lookup finds several Payees
(`ambiguous_payee`), the Agent itself asks which one, listing the Bank's candidates by
name, nickname and masked phone number only. The Payees found so far are what a follow-up
("再给她转两百") refers to, and say which Payee on a Plan card has never been paid.
"""

from dataclasses import dataclass
from typing import Any

from agent.events import CardPayee, ClarificationOption, ClarificationQuestion, PlanStep
from agent.executor import StepResult
from agent.language import Language

LOOKUP = "find_payee"
PAYS = "transfer"
"""The Operation that pays a Payee: once it ran, the Payee is no longer new."""


@dataclass(frozen=True)
class Payee:
    """What the Agent keeps of a Payee the Bank found: never its account, its full phone
    number or the free-text notes of past transfers."""

    payee_id: str
    name: str
    nickname: str | None
    phone: str | None
    """Masked by the Bank, e.g. 135****4444."""
    is_new: bool

    def label(self, language: Language) -> str:
        """How the Customer tells this Payee apart from others with the same name."""
        extra = [part for part in (self.nickname, self.phone) if part]
        if not extra:
            return self.name
        return f"{self.name} ({', '.join(extra)})" if language == "en" else (
            f"{self.name}（{'，'.join(extra)}）"
        )

    def as_data(self) -> dict[str, Any]:
        return {
            "payee_id": self.payee_id,
            "name": self.name,
            "nickname": self.nickname,
            "phone": self.phone,
            "is_new": self.is_new,
        }


def _payee(candidate: dict[str, Any]) -> Payee:
    return Payee(
        payee_id=candidate["payee_id"],
        name=candidate["name"],
        nickname=candidate.get("nickname"),
        phone=candidate.get("phone"),
        is_new=bool(candidate.get("is_new")),
    )


def known(results: list[StepResult]) -> dict[str, Payee]:
    """The Payees the Bank's lookups found in `results`, by ID, candidates of an ambiguous
    lookup included; a Payee paid since is no longer new."""
    found: dict[str, Payee] = {}
    for result in results:
        if result.operation == LOOKUP and result.output is not None:
            payee = _payee(result.output["payee"])
            found[payee.payee_id] = payee
        elif result.operation == LOOKUP and ambiguous(result) is not None:
            for candidate in result.refusal["detail"]["candidates"]:  # type: ignore[index]
                payee = _payee(candidate)
                found.setdefault(payee.payee_id, payee)
        elif result.operation == PAYS and result.output is not None:
            paid = found.get(result.output.get("payee_id", ""))
            if paid is not None:
                found[paid.payee_id] = Payee(**{**paid.as_data(), "is_new": False})
    return found


def ambiguous(result: StepResult) -> list[Payee] | None:
    """The candidates, if the Bank refused this lookup because several Payees match."""
    refusal = result.refusal
    if result.operation != LOOKUP or refusal is None or refusal["reason"] != "ambiguous_payee":
        return None
    return [_payee(candidate) for candidate in refusal["detail"].get("candidates", [])]


def which_one(results: list[StepResult], language: Language) -> ClarificationQuestion | None:
    """The question to ask when a lookup in `results` found several Payees, or None."""
    for result in results:
        candidates = ambiguous(result)
        if candidates:
            query = str(result.refusal["detail"].get("query", ""))  # type: ignore[index]
            text = (
                f"I found {len(candidates)} Payees matching “{query}”. Which one do you mean?"
                if language == "en"
                else f"有 {len(candidates)} 位收款人符合“{query}”，您要转给哪一位？"
            )
            options = [
                ClarificationOption(label=payee.label(language), value=payee.payee_id)
                for payee in candidates
            ]
            return ClarificationQuestion(text=text, options=options)
    return None


def on_card(steps: list[PlanStep], found: dict[str, Payee]) -> list[CardPayee]:
    """Who each step paying a Payee the lookup found pays, in card order."""
    return [
        CardPayee(step_id=step.id, **found[payee_id].as_data())
        for step in steps
        if isinstance(payee_id := step.input.get("payee_id"), str) and payee_id in found
    ]
