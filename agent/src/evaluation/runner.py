"""Replay one scripted test task end to end and check it (docs/scripted-test-tasks.md).

The runner stands in for the Bank's staff and for the Customer with their Channel: it
holds the Bank's admin key (to reset the Bank, set its clock and read the session's audit
trail) and, after logging the Customer in, the phone token, for the simulated phone. The
Agent gets only the session token, as from the Channel.
"""

import json
import re
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from evaluation.tasks import Approval, ExpectedOperation, Task

SEED_PASSWORD = "demo1234"
MAX_PROMPTS_PER_TURN = 20
"""More prompts than this in one turn means the Agent is looping; the runner stops."""


class Response(Protocol):
    @property
    def status_code(self) -> int: ...

    @property
    def text(self) -> str: ...

    def json(self) -> Any: ...


class HttpClient(Protocol):
    """An HTTP client with a base URL: `httpx.Client`, or a Starlette `TestClient`."""

    def request(
        self, method: str, url: str, *, headers: dict[str, str] | None = None, json: Any = None
    ) -> Any: ...


class RunError(Exception):
    """The run couldn't go on: a call the runner makes was refused or failed."""


@dataclass
class TaskResult:
    task: Task
    failures: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    """What the Agent asked for and how the simulated Customer answered, in order."""

    @property
    def passed(self) -> bool:
        return not self.failures


def run_task(task: Task, *, agent: HttpClient, bank: HttpClient, admin_key: str) -> TaskResult:
    """Run `task` from a reset Bank at its clock, and check what it expects."""
    result = TaskResult(task)
    try:
        _Run(task, agent, bank, admin_key, result).play()
    except Exception as error:  # noqa: BLE001 - any crash is this task's failure, not the run's
        result.failures.append(f"error: {type(error).__name__}: {error}")
    return result


def _ok(response: Any, what: str) -> Any:
    if response.status_code != 200:
        raise RunError(f"{what}: HTTP {response.status_code} {response.text[:300]}")
    return response.json()


class _Run:
    def __init__(
        self, task: Task, agent: HttpClient, bank: HttpClient, admin_key: str, result: TaskResult
    ):
        self.task = task
        self.agent = agent
        self.bank = bank
        self.admin_headers = {"X-Admin-Key": admin_key}
        self.result = result
        self.approvals: deque[Approval] = deque(task.approvals)
        self.events: list[dict[str, Any]] = []

    # --- Bank and Channel -------------------------------------------------------------

    def admin(self, method: str, url: str, body: Any = None) -> Any:
        response = self.bank.request(method, url, headers=self.admin_headers, json=body)
        return _ok(response, f"Bank admin {method} {url}")

    def log_in(self) -> None:
        body = _ok(
            self.bank.request(
                "POST",
                "/sessions",
                json={"username": self.task.customer, "password": SEED_PASSWORD},
            ),
            f"logging in as {self.task.customer}",
        )
        self.session_id: str = body["session_id"]
        self.session = {"Authorization": f"Bearer {body['session_token']}"}
        self.phone = {"Authorization": f"Bearer {body['phone_token']}"}
        started = _ok(
            self.agent.request(
                "POST", "/conversations", json={"session_token": body["session_token"]}
            ),
            "starting the conversation",
        )
        self.conversation: str = started["conversation_id"]

    def turn(self, url: str, body: Any, what: str) -> list[dict[str, Any]]:
        response = self.agent.request(
            "POST", f"/conversations/{self.conversation}/{url}", json=body
        )
        if response.status_code != 200:
            raise RunError(f"{what}: HTTP {response.status_code} {response.text[:300]}")
        events = [json.loads(line) for line in response.text.splitlines() if line]
        self.events.extend(events)
        return events

    def act(self, action: str, **fields: Any) -> list[dict[str, Any]]:
        return self.turn("actions", {"action": action, **fields}, f"answering with {action}")

    # --- The task ---------------------------------------------------------------------

    def play(self) -> None:
        self.admin("POST", "/admin/reset")
        self.admin("PUT", "/admin/clock", {"now": self.task.clock})
        self.log_in()
        for text in self.task.turns:
            self.log(f"Customer: {text}")
            self.answer_prompts(self.turn("messages", {"text": text}, "sending a message"))
        audit = self.admin("GET", f"/admin/sessions/{self.session_id}/audit")["entries"]
        self.check(audit)

    def log(self, line: str) -> None:
        self.result.log.append(line)

    def answer_prompts(self, events: list[dict[str, Any]]) -> None:
        for _ in range(MAX_PROMPTS_PER_TURN):
            shown = [event for event in events if event["type"] != "trace"]
            if not shown:
                return
            prompt = shown[-1]
            if prompt["type"] == "confirmation_requested":
                events = self.answer_card(prompt)
            elif prompt["type"] == "verification_requested":
                events = self.answer_verification(prompt)
            elif prompt["type"] == "reversal_offered":
                events = self.answer_reversal(prompt)
            elif prompt["type"] == "cancellation_offered":
                events = self.answer_offer(prompt)
            else:
                return
        raise RunError(f"the Agent asked more than {MAX_PROMPTS_PER_TURN} times in one turn")

    def take(self, kind: str) -> str | None:
        """The next scripted answer if it is to this kind of request; else none (reject)."""
        if self.approvals and self.approvals[0].kind == kind:
            return self.approvals.popleft().answer
        return None

    def answer_card(self, prompt: dict[str, Any]) -> list[dict[str, Any]]:
        plan_id = prompt["plan_id"]
        if not prompt["step_ids"] and self.approvals and self.approvals[0].kind == "verification":
            # No Yellow step: the card asks for no Confirmation, only the tap that gets to
            # the Red steps' verification, which the script answers next.
            self.log("Agent: Plan card with no Yellow step -> confirm")
            return self.act("confirm", plan_id=plan_id)
        answer = self.take("confirmation")
        self.log(
            f"Agent: confirmation of {prompt['step_ids']} -> {answer or 'reject (unscripted)'}"
        )
        return self.act("confirm" if answer == "approve" else "reject", plan_id=plan_id)

    def answer_reversal(self, prompt: dict[str, Any]) -> list[dict[str, Any]]:
        """Accept the offer to undo every step it lists, or decline it."""
        answer = self.take("reversal")
        step_ids = [step["step_id"] for step in prompt["steps"]]
        self.log(f"Agent: reversal of {step_ids} -> {answer or 'decline (unscripted)'}")
        if answer == "accept":
            return self.act("reverse", plan_id=prompt["plan_id"], step_ids=step_ids)
        return self.act("reject", plan_id=prompt["plan_id"])

    def answer_offer(self, prompt: dict[str, Any]) -> list[dict[str, Any]]:
        """Pick the scripted Subscriptions to cancel. Unscripted, the offer is left
        unanswered: the Customer may pick in words in the next turn."""
        offered = [candidate["subscription_id"] for candidate in prompt["candidates"]]
        if not self.approvals or self.approvals[0].kind != "pick":
            self.log(f"Agent: cancellation offer of {offered} -> no pick (unscripted)")
            return []
        picked = self.approvals.popleft().pick or []
        self.log(f"Agent: cancellation offer of {offered} -> pick {picked}")
        return self.act(
            "cancel_subscriptions", offer_id=prompt["offer_id"], subscription_ids=picked
        )

    def answer_verification(self, prompt: dict[str, Any]) -> list[dict[str, Any]]:
        answer = self.take("verification")
        self.log(f"Agent: verification of {prompt['step_id']} -> {answer or 'reject (unscripted)'}")
        if answer not in ("correct_code", "wrong_code"):
            return []  # Rejecting is leaving the code untyped: the Plan stays paused.
        code = self.latest_code()
        if answer == "wrong_code":
            code = f"{(int(code) + 1) % 1_000_000:06d}"
        typed = self.bank.request(
            "POST",
            f"/verifications/{prompt['challenge_id']}",
            headers=self.phone,
            json={"code": code},
        ).json()
        ids = {"plan_id": prompt["plan_id"], "step_id": prompt["step_id"]}
        if "verification_token" in typed:
            return self.act("verified", **ids, verification_token=typed["verification_token"])
        refusal = typed.get("refusal", {})
        failed_attempts = refusal.get("detail", {}).get("failed_attempts")
        if refusal.get("reason") == "token_invalid" and isinstance(failed_attempts, int):
            return self.act("wrong_code", **ids, failed_attempts=failed_attempts)
        # As on the Channel's phone: any other refusal is shown there and not sent on.
        self.log(f"Phone: the Bank refused the code ({refusal.get('reason')})")
        return []

    def latest_code(self) -> str:
        messages = _ok(self.bank.request("GET", "/phone/messages", headers=self.phone), "phone")
        match = (
            re.search(r"\b(\d{6})\b", messages["messages"][-1]["text"])
            if messages["messages"]
            else None
        )
        if not match:
            raise RunError("no code on the simulated phone")
        return match.group(1)

    # --- Checks -----------------------------------------------------------------------

    def check(self, audit: list[dict[str, Any]]) -> None:
        expect = self.task.expect
        ran = [e for e in audit if e["event"] == "operation" and e["outcome"] == "ok"]
        for expected in _unmatched(expect.operations_run, ran):
            self.fail(f"operations_run: {_describe(expected)} did not run", ran)
        for expected in expect.operations_not_run:
            if any(_ran_as(expected, entry) for entry in ran):
                self.fail(f"operations_not_run: {_describe(expected)} ran", ran)
        if expect.refusal is not None:
            reasons = _refusals(audit)
            if expect.refusal not in reasons:
                self.result.failures.append(
                    f"refusal: expected {expect.refusal}; the Bank refused with"
                    f" {', '.join(reasons) or 'nothing'}"
                )
        asked = any(event["type"] == "clarification_question" for event in self.events)
        if asked != expect.clarifying_question:
            self.result.failures.append(
                "clarifying_question: expected a clarifying question, none was asked"
                if expect.clarifying_question
                else "clarifying_question: the Agent asked one where none was expected"
            )
        for state in expect.final_state:
            response = self.bank.request(
                "POST",
                f"/operations/{state.operation}",
                headers=self.session,
                json={"input": state.input},
            )
            body = response.json()
            if response.status_code != 200 or body.get("tier") != "green":
                self.result.failures.append(
                    f"final_state: {state.operation} didn't run as a Green Operation:"
                    f" {response.text[:300]}"
                )
            elif not matches(state.output, body["output"]):
                self.result.failures.append(
                    f"final_state: {state.operation} returned {_compact(body['output'])}"
                )

    def fail(self, message: str, ran: list[dict[str, Any]]) -> None:
        names = [f"{e['operation']} {_compact(e['input'])}" for e in ran]
        self.result.failures.append(f"{message} (ran: {'; '.join(names) or 'nothing'})")


def _unmatched(
    expected: list[ExpectedOperation], ran: list[dict[str, Any]]
) -> list[ExpectedOperation]:
    """The expected runs no distinct run matches: two identical entries need two runs.
    Assigned by augmenting paths, so a loose entry can't take the run a stricter one needs."""
    owner: dict[int, int] = {}  # run index -> expected index

    def assign(i: int, seen: set[int]) -> bool:
        for j, entry in enumerate(ran):
            if j not in seen and _ran_as(expected[i], entry):
                seen.add(j)
                if j not in owner or assign(owner[j], seen):
                    owner[j] = i
                    return True
        return False

    return [e for i, e in enumerate(expected) if not assign(i, set())]


def _ran_as(expected: ExpectedOperation, entry: dict[str, Any]) -> bool:
    return entry["operation"] == expected.name and (
        expected.input is None or matches(expected.input, entry["input"])
    )


def _describe(expected: ExpectedOperation) -> str:
    return (
        expected.name if expected.input is None else f"{expected.name} {_compact(expected.input)}"
    )


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _refusals(audit: list[dict[str, Any]]) -> list[str]:
    """Every refusal reason the Bank gave in the session, in order, without repeats: of a
    call, or of a step its tier preview refused."""
    reasons: list[str] = []
    for entry in audit:
        found = [entry["reason"]] if entry["outcome"] == "refused" else []
        if entry["event"] == "tier_preview":
            found += [
                step["refusal"]["reason"]
                for step in entry["detail"].get("steps", [])
                if step.get("refusal")
            ]
        reasons += [reason for reason in found if reason and reason not in reasons]
    return reasons


MONEY = re.compile(r"-?\d+\.\d{2}")


def matches(expected: Any, actual: Any) -> bool:
    """Subset match: listed keys must match, unlisted ones are ignored; lists match element
    by element, in order. Money ("200.00") matches the same amount however the Agent wrote
    it ("200", 200), since the Bank reads those as one amount."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and matches(value, actual[key]) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(matches(e, a) for e, a in zip(expected, actual, strict=True))
        )
    if isinstance(expected, str) and MONEY.fullmatch(expected):
        return _amount(actual) == Decimal(expected)
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(expected) is type(actual) and expected == actual
    return bool(expected == actual)


def _amount(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None
