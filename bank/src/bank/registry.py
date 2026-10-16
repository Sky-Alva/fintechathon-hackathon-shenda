"""The Operation registry: how modules declare Operations, schema and seed data.

Scenario modules only declare what an Operation does. The policy gate reads these
declarations to resolve tiers, demand approvals and write the audit log.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class Tier(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class RefusalReason(StrEnum):
    NEEDS_CONFIRMATION = "needs_confirmation"
    NEEDS_VERIFICATION = "needs_verification"
    TOKEN_INVALID = "token_invalid"
    SESSION_LOCKED = "session_locked"
    FORBIDDEN_OTHER_CUSTOMER = "forbidden_other_customer"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    AMBIGUOUS_PAYEE = "ambiguous_payee"
    NOT_FOUND = "not_found"
    INVALID_INPUT = "invalid_input"
    NOT_AUTHENTICATED = "not_authenticated"


class Refusal(Exception):
    """Raised anywhere in the Bank to refuse a request with one machine-readable reason."""

    def __init__(
        self,
        reason: RefusalReason,
        message: str,
        detail: dict[str, Any] | None = None,
        *,
        audit_detail: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.detail = detail or {}
        # Recorded in the audit log but never returned to the caller.
        self.audit_detail = audit_detail or {}

    def body(self) -> dict[str, Any]:
        """The refusal as the caller sees it."""
        return {"reason": self.reason.value, "message": self.message, "detail": self.detail}


@dataclass(frozen=True)
class OperationContext:
    """What a handler or tier rule may use: the database, the session's Customer, the
    Bank clock, and the Customer's Daily Transfer Total before this Operation (including
    earlier steps of the same Plan when the Bank is simulating one)."""

    conn: sqlite3.Connection
    customer_id: str
    session_id: str
    now: datetime
    daily_transfer_total: Decimal


@dataclass(frozen=True)
class TierRule:
    """A tier that depends on the input and the current Bank state."""

    description_zh: str
    description_en: str
    resolve: Callable[[Any, OperationContext], Tier]


@dataclass(frozen=True)
class ReadOnly:
    """The Operation changes nothing, so there is nothing to reverse."""


@dataclass(frozen=True)
class Irreversible:
    pass


@dataclass(frozen=True)
class ReversedBy:
    """The Operation that undoes this one, and how to build its input from the original."""

    operation: str
    build_input: Callable[[Any, Any], BaseModel]


@dataclass(frozen=True)
class Operation:
    name: str
    description_zh: str
    description_en: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    tier: Tier | TierRule
    reversibility: ReadOnly | Irreversible | ReversedBy
    handler: Callable[[OperationContext, Any], BaseModel]
    # The amount this Operation adds to the Daily Transfer Total (transfers, Merchant
    # payments from Reserved Funds). The policy gate keeps the total; modules never do.
    daily_transfer_amount: Callable[[Any], Decimal] | None = None

    def catalogue_entry(self) -> dict[str, Any]:
        """How the Operation is described to the Agent in the Operation catalogue."""
        tier: dict[str, Any]
        if isinstance(self.tier, Tier):
            tier = {"kind": "fixed", "tier": self.tier.value}
        else:
            tier = {
                "kind": "rule",
                "description_zh": self.tier.description_zh,
                "description_en": self.tier.description_en,
            }
        reversibility: dict[str, Any]
        match self.reversibility:
            case ReadOnly():
                reversibility = {"kind": "read_only"}
            case Irreversible():
                reversibility = {"kind": "irreversible"}
            case ReversedBy(operation=operation):
                reversibility = {"kind": "reversed_by", "operation": operation}
        return {
            "name": self.name,
            "description_zh": self.description_zh,
            "description_en": self.description_en,
            "input_schema": self.input_model.model_json_schema(),
            "output_schema": self.output_model.model_json_schema(mode="serialization"),
            "tier": tier,
            "reversibility": reversibility,
            "counts_toward_daily_transfer_total": self.daily_transfer_amount is not None,
        }


def no_seed_data(conn: sqlite3.Connection) -> None:
    pass


@dataclass(frozen=True)
class BankModule:
    """A slice of the Bank: its tables, its seed data and its Operations."""

    name: str
    schema: str
    seed: Callable[[sqlite3.Connection], None] = no_seed_data
    operations: list[Operation] = field(default_factory=list)


class Registry:
    def __init__(self, modules: list[BankModule]):
        self.modules = modules
        self.operations: dict[str, Operation] = {}
        for module in modules:
            for operation in module.operations:
                if operation.name in self.operations:
                    raise ValueError(f"Operation {operation.name!r} declared twice")
                self.operations[operation.name] = operation

    def get(self, name: str) -> Operation:
        try:
            return self.operations[name]
        except KeyError:
            raise Refusal(
                RefusalReason.NOT_FOUND, f"No Operation named {name!r}", {"operation": name}
            ) from None
