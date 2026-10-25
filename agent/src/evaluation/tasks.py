"""Scripted test tasks: the YAML files under tasks/<scenario>/, in the format of
docs/scripted-test-tasks.md. Every field is required, so a task says what it doesn't check.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

Category = Literal["normal", "clarification", "attack"]
Attack = Literal[
    "induced_transfer", "information_extraction", "privilege_escalation", "prompt_injection"
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _no_floats(value: Any, where: str) -> Any:
    """Money is a string with two decimals ("200.00"), never a YAML number with a point."""
    if isinstance(value, float):
        raise ValueError(f'{where}: {value!r} is a number; write money as a string like "200.00"')
    if isinstance(value, dict):
        for key, item in value.items():
            _no_floats(item, f"{where}.{key}")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _no_floats(item, f"{where}[{index}]")
    return value


class ExpectedOperation(_Strict):
    name: str
    input: dict[str, Any] | None = None
    """Matched as a subset; left out, any input matches."""

    @field_validator("input")
    @classmethod
    def money_is_a_string(cls, value: Any, info: ValidationInfo) -> Any:
        return _no_floats(value, str(info.field_name))


class FinalState(_Strict):
    operation: str
    input: dict[str, Any] = {}
    output: dict[str, Any]

    @field_validator("input", "output")
    @classmethod
    def money_is_a_string(cls, value: Any, info: ValidationInfo) -> Any:
        return _no_floats(value, str(info.field_name))


class Approval(_Strict):
    """One scripted answer: to a Plan card's Confirmation, to a verification request, to
    an offer to undo a failed Plan's completed steps, or to a clarifying question (the
    option it names, or else words the Customer types)."""

    confirmation: Literal["approve", "reject"] | None = None
    verification: Literal["correct_code", "wrong_code", "reject"] | None = None
    reversal: Literal["accept", "decline"] | None = None
    clarification: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def exactly_one(self) -> "Approval":
        given = [self.confirmation, self.verification, self.reversal, self.clarification]
        if sum(answer is not None for answer in given) != 1:
            raise ValueError(
                "each approval is one of `confirmation:`, `verification:`, `reversal:` or"
                " `clarification:`"
            )
        return self

    @property
    def kind(self) -> Literal["confirmation", "verification", "reversal", "clarification"]:
        if self.confirmation:
            return "confirmation"
        if self.verification:
            return "verification"
        return "reversal" if self.reversal else "clarification"

    @property
    def answer(self) -> str:
        return self.confirmation or self.verification or self.reversal or self.clarification or ""


class Expect(_Strict):
    operations_run: list[ExpectedOperation]
    operations_not_run: list[ExpectedOperation]
    refusal: str | None
    clarifying_question: bool
    final_state: list[FinalState]


class Task(_Strict):
    id: str
    title: str
    category: Category
    attack: Attack | None
    customer: str
    clock: str
    turns: list[str]
    approvals: list[Approval]
    expect: Expect
    scenario: str = ""
    """The directory the task is in (tasks/<scenario>/), filled in on loading."""

    @field_validator("clock")
    @classmethod
    def clock_has_an_offset(cls, value: str) -> str:
        if datetime.fromisoformat(value).tzinfo is None:
            raise ValueError("write the clock with its offset, e.g. 2026-10-20T09:00:00+08:00")
        return value

    @model_validator(mode="after")
    def attacks_name_their_type(self) -> "Task":
        if (self.category == "attack") != (self.attack is not None):
            raise ValueError("`attack` names the attack type exactly when category is attack")
        return self


class InvalidTask(Exception):
    def __init__(self, path: Path, problem: str):
        super().__init__(f"{path}: {problem}")
        self.path = path
        self.problem = problem


def load_task(path: Path) -> Task:
    """One task file; `InvalidTask` says what's wrong with it."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise InvalidTask(path, f"not YAML: {error}") from error
    if not isinstance(data, dict):
        raise InvalidTask(path, "not a mapping of task fields")
    if "scenario" in data:
        raise InvalidTask(path, "`scenario` is the task's directory, not a field")
    try:
        task = Task.model_validate({**data, "scenario": path.parent.name})
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in e['loc']) or 'task'}: {e['msg']}"
            for e in error.errors()
        )
        raise InvalidTask(path, problems) from error
    if task.id != path.stem:
        raise InvalidTask(path, f"id {task.id!r} doesn't match the file name")
    return task


def load_tasks(root: Path) -> tuple[list[Task], list[InvalidTask]]:
    """Every task under `root` (tasks/<scenario>/<id>.yaml), in order, and the files that
    aren't valid tasks."""
    tasks: list[Task] = []
    invalid: list[InvalidTask] = []
    for path in sorted(root.glob("*/*.yaml")):
        try:
            tasks.append(load_task(path))
        except InvalidTask as problem:
            invalid.append(problem)
    return tasks, invalid
