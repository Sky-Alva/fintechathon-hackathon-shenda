"""Money: stored as integer cents, exchanged as a Decimal with exactly two places."""

from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, Field

CENT = Decimal("0.01")

# An amount of money in an Operation input. "200" and "200.00" are the same amount, so
# both normalise to Decimal("200.00"); Confirmation fingerprints depend on that.
Amount = Annotated[
    Decimal,
    Field(gt=0, le=Decimal("100000000"), decimal_places=2),
    AfterValidator(lambda value: value.quantize(CENT)),
]


def to_cents(amount: Decimal) -> int:
    return int(amount.quantize(CENT) * 100)


def from_cents(cents: int) -> Decimal:
    return Decimal(cents).scaleb(-2)
