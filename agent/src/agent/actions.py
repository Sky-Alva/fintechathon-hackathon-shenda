"""The Customer's actions: their answers to the prompts the Agent sends (a Plan card, a
Red step's verification, a stopped Plan, an offer, a question), and asking for a human.

`POST /conversations/{id}/actions` takes one; docs/conversation-events.md is the contract.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class ConfirmPlan(BaseModel):
    """One tap on the Plan card: confirm every Yellow step on it."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["confirm"]
    plan_id: str


class RemoveSteps(BaseModel):
    """Take these steps off the Plan card: the rest, re-tiered by the Bank, become a new
    Plan card for the Customer to confirm. Nothing runs."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["remove_steps"]
    plan_id: str
    step_ids: list[str] = Field(min_length=1)


class RejectPlan(BaseModel):
    """Reject the Plan card: nothing on it runs."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["reject"]
    plan_id: str


class AskForHuman(BaseModel):
    """The Customer wants a Staff Member: at any time, whatever else is going on."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["ask_for_human"]


class VerifiedStep(BaseModel):
    """The Customer typed the right code into the Channel's SMS verification box, which
    sent it straight to the Bank and hands the Agent only the verification tokens it got
    back: one per step the challenge covers, by step ID."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["verified"]
    plan_id: str
    step_id: str
    verification_tokens: dict[str, Annotated[str, Field(min_length=1)]] = Field(min_length=1)


class WrongCode(BaseModel):
    """The Bank refused the code the Customer typed into the SMS verification box
    (`token_invalid`); `failed_attempts` is the Bank's count of wrong codes this session.
    The Agent asks the Bank itself whether that locked the session."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["wrong_code"]
    plan_id: str
    step_id: str
    failed_attempts: int = Field(ge=1)


class ReverseSteps(BaseModel):
    """Accept the reversal offer, for these of its steps: the undo becomes a new Plan
    card."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["reverse"]
    plan_id: str
    step_ids: list[str] = Field(min_length=1)


class ChooseOption(BaseModel):
    """The Customer tapped one of the clarifying question's options."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["choose"]
    value: str


class StopPlan(BaseModel):
    """The stop button: no further step of the Plan starts."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["stop"]
    plan_id: str


class ResumePlan(BaseModel):
    """Run the rest of a stopped Plan, as confirmed."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["resume"]
    plan_id: str


class CancelPlan(BaseModel):
    """Drop the rest of a stopped Plan: nothing more of it runs."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["cancel"]
    plan_id: str


class CancelSubscriptions(BaseModel):
    """The Customer's pick from the Subscriptions offered for cancelling: these become
    one Plan card, one cancellation each. None picked cancels nothing."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["cancel_subscriptions"]
    offer_id: str
    subscription_ids: list[str]


CustomerAction = Annotated[
    ConfirmPlan
    | RemoveSteps
    | CancelSubscriptions
    | RejectPlan
    | AskForHuman
    | VerifiedStep
    | WrongCode
    | ReverseSteps
    | StopPlan
    | ResumePlan
    | CancelPlan
    | ChooseOption,
    Field(discriminator="action"),
]
