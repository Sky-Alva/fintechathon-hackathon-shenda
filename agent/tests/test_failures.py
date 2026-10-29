"""A refused step's explanation, from the Bank's refusal and nothing else (#42): for
`invalid_input`, from what its `detail.errors` say is wrong with each value (#56)."""

from typing import Any

from agent import failures
from agent.events import BankRefusal


def invalid(*errors: tuple[str, str, str]) -> BankRefusal:
    """The Bank's `invalid_input` refusal with these (field, type, msg) errors, as it sends
    them: Pydantic's, without the input or the context."""
    detail: dict[str, Any] = {
        "errors": [{"type": type, "loc": [field], "msg": msg} for field, type, msg in errors]
    }
    return BankRefusal(reason="invalid_input", message="Invalid input", detail=detail)


NOT_ABOVE_ZERO = ("new_limit", "greater_than", "Input should be greater than 0")
TOO_MANY_PLACES = (
    "new_limit", "decimal_max_places", "Decimal input should have no more than 2 decimal places"
)
TOO_LARGE = ("amount", "less_than_equal", "Input should be less than or equal to 100000000")
NO_CARD = ("card_id", "missing", "Field required")


def test_a_limit_of_zero_or_less_must_be_greater_than_zero() -> None:
    refusal = invalid(NOT_ABOVE_ZERO)

    assert failures.explain(refusal, "en") == (
        "The Bank didn't run this step: the new limit must be greater than 0."
    )
    assert failures.explain(refusal, "zh") == "银行未执行这一步：新额度必须大于 0。"


def test_an_amount_with_more_than_two_decimal_places() -> None:
    refusal = invalid(TOO_MANY_PLACES)

    assert failures.explain(refusal, "en") == (
        "The Bank didn't run this step: the new limit can have at most 2 decimal places."
    )
    assert failures.explain(refusal, "zh") == "银行未执行这一步：新额度最多只能有 2 位小数。"


def test_an_amount_that_is_too_large() -> None:
    refusal = invalid(TOO_LARGE)

    assert failures.explain(refusal, "en") == (
        "The Bank didn't run this step: the amount is too large; it can be at most 100000000."
    )
    assert failures.explain(refusal, "zh") == "银行未执行这一步：金额过大，最多为 100000000。"


def test_only_the_errors_the_agent_has_words_for_are_named() -> None:
    refusal = invalid(NO_CARD, NOT_ABOVE_ZERO)

    assert failures.explain(refusal, "en") == (
        "The Bank didn't run this step: the new limit must be greater than 0."
    )


def test_invalid_details_without_errors_it_has_words_for_keep_the_general_words() -> None:
    assert failures.explain(invalid(NO_CARD), "en") == (
        "The Bank didn't run this step: the Bank found its details invalid."
    )
    assert failures.explain(
        BankRefusal(reason="invalid_input", message="Card is disabled", detail={}), "zh"
    ) == "银行未执行这一步：银行认为这一步的内容无效。"


def test_wrong_values_ignores_the_reference_the_customer_has_not_named_yet() -> None:
    refusal = invalid(NO_CARD, NOT_ABOVE_ZERO)

    assert failures.wrong_values(refusal, "en", ignore={"card_id"}) == (
        "Sorry, that can't be done: the new limit must be greater than 0."
    )
    assert failures.wrong_values(refusal, "zh", ignore={"card_id"}) == (
        "抱歉，无法办理：新额度必须大于 0。"
    )
    assert failures.wrong_values(invalid(NO_CARD), "en", ignore={"card_id"}) is None
    assert failures.wrong_values(
        BankRefusal(reason="not_found", message="", detail={}), "en", ignore=set()
    ) is None
