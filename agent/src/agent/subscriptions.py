"""Cancelling the Subscriptions the Customer may not need (#41): the Bank's candidates, the
offer that lists them, and the report of what the Bank shows as cancelled afterwards.

The Agent writes these itself from the Bank's `list_subscriptions` output, never the
model: which Subscriptions are candidates, and why, is the Bank's rule (`reasons_it_may_
not_be_needed`), and which were cancelled is what the Bank shows. Nothing the model says
can add a candidate, drop one or misreport a cancellation.

The Customer may also name a Subscription to cancel ("cancel TuneBox"). Its Bank ID comes
from the Bank's list, never the model: a subscription_id the Planner made up never runs
(`references`), the Agent lists the Subscriptions first and has the Planner complete the
step from that list, and if it still can't tell which is meant, the Customer picks.
"""

from dataclasses import dataclass
from typing import Any

from agent.executor import StepResult
from agent.language import Language
from agent.references import Reference

LIST = "list_subscriptions"
CANCEL = "cancel_subscription"


@dataclass(frozen=True)
class Listed:
    """What the Agent keeps of a Subscription the Bank listed: how the Customer tells it
    apart, never its status, which a later turn must read from the Bank again."""

    subscription_id: str
    merchant: str
    current_amount: str
    next_renewal_date: str | None

    def as_data(self) -> dict[str, Any]:
        return {"subscription_id": self.subscription_id, "merchant": self.merchant}

    def label(self, language: Language) -> str:
        """How the Customer tells this Subscription apart from their others."""
        renews = self.next_renewal_date
        if language == "en":
            return f"{self.merchant} (¥{self.current_amount}" + (
                f", renews {renews})" if renews else ")"
            )
        return f"{self.merchant}（¥{self.current_amount}" + (f"，{renews} 续费）" if renews else "）")


def known(results: list[StepResult]) -> dict[str, Listed]:
    """The Subscriptions the Bank listed in `results`, by ID, in the order it showed them
    first."""
    found: dict[str, Listed] = {}
    for result in results:
        if result.operation != LIST or result.output is None:
            continue
        for view in result.output["subscriptions"]:
            found[view["subscription_id"]] = Listed(
                subscription_id=view["subscription_id"],
                merchant=view["merchant"],
                current_amount=str(view["current_amount"]),
                next_renewal_date=view.get("next_renewal_date"),
            )
    return found


REFERENCE = Reference(
    lookup=LIST,
    key="subscription_id",
    prefix="subscriptions",
    known=known,
    question={"en": "Which Subscription do you mean?", "zh": "您指的是哪一个订阅？"},
)


def candidates(listed: dict[str, Any]) -> list[dict[str, Any]]:
    """The active Subscriptions the Bank flags as possibly unneeded, as the Bank listed
    them, in its order."""
    return [
        subscription
        for subscription in listed["subscriptions"]
        if subscription["status"] == "active" and subscription["reasons_it_may_not_be_needed"]
    ]


REASONS: dict[str, dict[Language, str]] = {
    "same_category": {"zh": "与 {peers} 属于同一类别", "en": "same category as {peers}"},
    "price_increase": {
        "zh": "价格比开通时上涨（¥{original} → ¥{current}）",
        "en": "price up since sign-up (¥{original} → ¥{current})",
    },
    "trial_converted": {"zh": "免费试用已转为付费", "en": "a free trial that converted to paid"},
}

OFFER: dict[Language, tuple[str, str]] = {
    "zh": (
        "银行标记了 {count} 个可能用不上的订阅：",
        "请选择要取消的订阅。选好后，我会把它们放在一张计划卡片上，您确认一次即可。",
    ),
    "en": (
        "The Bank flags {count} Subscription{s} you may not need:",
        "Pick the ones to cancel: they'll go on one Plan card for you to confirm with one tap.",
    ),
}

NONE_FLAGGED: dict[Language, str] = {
    "zh": "银行没有标记可能用不上的订阅，没有需要取消的。",
    "en": "The Bank doesn't flag any of your Subscriptions as possibly unneeded.",
}

NONE_PICKED: dict[Language, str] = {
    "zh": "好的，一个订阅都不取消，没有执行任何操作。",
    "en": "OK, I won't cancel any Subscription. Nothing was done.",
}


REPORT: dict[Language, tuple[str, str, str]] = {
    "zh": ("银行显示已取消：{names}。", "仍在生效：{names}。", "、"),
    "en": ("The Bank shows as cancelled: {names}.", " Still active: {names}.", ", "),
}


def report(results: list[Any], language: Language) -> str | None:
    """What the Bank shows after a Plan that only cancelled Subscriptions and then read
    them back: the ones this Plan cancelled that the read-back shows as cancelled, and
    those still active. None for any other Plan (the Responder answers it)."""
    if not results or any(r.operation not in (LIST, CANCEL) for r in results):
        return None
    last_cancel = max(
        (i for i, r in enumerate(results) if r.operation == CANCEL and r.output is not None),
        default=None,
    )
    read_back = results[-1]
    if last_cancel is None or read_back.operation != LIST or read_back.output is None:
        return None
    if any(r.output is None for r in results[last_cancel:]):
        return None
    shown = read_back.output["subscriptions"]
    status = {s["subscription_id"]: s["status"] for s in shown}
    cancelled_here = [r.input["subscription_id"] for r in results if r.operation == CANCEL]
    merchant = {s["subscription_id"]: s["merchant"] for s in shown}
    cancelled = [merchant[id] for id in cancelled_here if status.get(id) == "cancelled"]
    active = [s["merchant"] for s in shown if s["status"] == "active"]
    done, still, separator = REPORT[language]
    text = done.format(names=separator.join(cancelled)) if cancelled else ""
    if active:
        text += still.format(names=separator.join(active))
    return text.strip()


def reason_text(
    reason: dict[str, Any], subscription: dict[str, Any], listed: list[dict[str, Any]],
    language: Language,
) -> str:
    peers = [
        other["merchant"]
        for other in listed
        if other["status"] == "active"
        and other["category"] == subscription["category"]
        and other["subscription_id"] != subscription["subscription_id"]
    ]
    template = REASONS.get(reason["reason"], {}).get(language)
    if template is None:  # a reason this Agent doesn't know yet: the Bank's own words
        return str(reason["detail"])
    return template.format(
        peers=("、" if language == "zh" else ", ").join(peers),
        original=subscription["original_amount"],
        current=subscription["current_amount"],
    )


def offer_text(
    offered: list[dict[str, Any]], listed: list[dict[str, Any]], language: Language
) -> str:
    """The candidates, numbered, each with its price and the Bank's reasons."""
    head, tail = OFFER[language]
    lines = [head.format(count=len(offered), s="s" if len(offered) > 1 else "")]
    separator = "；" if language == "zh" else "; "
    for number, subscription in enumerate(offered, start=1):
        reasons = separator.join(
            reason_text(reason, subscription, listed, language)
            for reason in subscription["reasons_it_may_not_be_needed"]
        )
        colon = "：" if language == "zh" else ": "
        price = f"¥{subscription['current_amount']}"
        lines.append(f"{number}. {subscription['merchant']}（{price}）{colon}{reasons}"
                     if language == "zh"
                     else f"{number}. {subscription['merchant']} ({price}){colon}{reasons}")
    lines.append(tail)
    return "\n".join(lines)
