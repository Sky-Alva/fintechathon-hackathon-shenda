# Agent

The Banking AI Agent: it turns what the Customer says into a Plan, has the Bank tier it, runs it through the Customer's own Bank session and answers only from what the Bank returned. It is an untrusted caller: it holds the session token the Channel hands it, never the password, the phone token or the Bank's admin key ([ADR-0001](../docs/adr/0001-bank-enforces-permission-tiers.md)).

## Run

From the repo root, with the Bank and the web Channel:

```sh
cp .env.example .env      # then fill in DASHSCOPE_API_KEY (or DEEPSEEK_API_KEY)
docker compose up --build # Channel: http://localhost:8080 · Agent: :8001 · Bank: :8000
```

`AGENT_MODEL_PROVIDER` picks the model: `qwen` (`qwen-plus` on DashScope, the default), `deepseek` (`deepseek-chat`, the backup) or `offline` (no model, no key: the built-in rules Planner, `src/agent/rules/`, plans and the Responder quotes the Bank). Both providers go through one OpenAI-compatible client (`src/agent/model.py`). With a provider, the rules plan a turn when the model raises `ModelUnavailable` (no key included); a reachable model's unusable reply (`PlannerFailed`) is not replaced.

## Conversation interface

`POST /conversations {session_token}`, then `POST /conversations/{id}/messages {text}`, which streams the turn's events as NDJSON. A Plan with a Yellow step stops at its Plan card; the Customer answers it with `POST /conversations/{id}/actions {action: confirm | reject, plan_id}`, or takes steps off it first (`{action: remove_steps, ...}`), which brings back a new, re-tiered Plan card to confirm. A confirmed Plan pauses at each Red step for Strong Verification: the Channel's simulated phone sends the SMS code straight to the Bank and hands the Agent only the verification token (`{action: verified, ...}`, or `{action: wrong_code, ...}`); a Safety Lock gets a `session_locked` notice. The Customer can ask for a human at any time (`{action: ask_for_human}`, or in words). Staff Members read the active sessions and each conversation's history, and take a conversation over, reply and hand it back, at `/staff/...` with the Agent's own staff key (`AGENT_STAFF_KEY`, header `X-Staff-Key`), never the Bank's admin key. The event contract shared with the Channel, the staff console and the tests is [docs/conversation-events.md](../docs/conversation-events.md).

## Modules

| Module | Job |
|--------|-----|
| `app.py` | The HTTP conversation interface, and the staff views. |
| `conversation.py` | Conversation service: owns conversations and history (including what Staff Members read), runs a turn (plan → review → execute → answer). |
| `actions.py` | The Customer's actions: their answers to the Agent's prompts. |
| `prompts.py` | The prompts waiting for the Customer's answer (a Plan card, a verification, a stopped Plan, a question, the offers), each saying which actions answer it. |
| `notices.py` | The texts the Agent writes itself, not the model: Red-step warnings, the Safety Lock, a pause, a rejection, the offer to undo. |
| `planner.py` | Conversation + the Bank's Operation catalogue → a Plan (a dependency graph of steps). Always plans, even for one step, unless it declines an out-of-scope request. |
| `refusals.py` | The `refusal` texts: an out-of-scope refusal lists what the Agent can do, from the catalogue. |
| `language.py` | The Customer's language: Chinese by default, English when they write in English. |
| `rules/` | The rules Planner: same contract as `planner.py` without a model; one module of intents per Scenario, a shared core and slot extractors (see its docstring for how to add an intent). |
| `reviewer.py` | Asks the Bank's tier preview for the Plan, in run order. Green-only Plans run with no Plan card. On the Customer's confirm, and only then, collects one Confirmation token per Yellow step. |
| `executor.py` | Runs the steps against the Bank, as events; a step the Bank refuses or can't be reached for fails with `step_failed`, and once a step fails while running, the steps after it don't run. |
| `responder.py` | Writes the answer from the Bank's results only: a draft that fails the fact check is regenerated once, then replaced by the raw Bank result. |
| `birthday.py` | Notices, between turns, that the spouse's birthday is a week away or less, from the profile and last year's gift purchase, and writes the reminder. |
| `fact_check.py` | Which amounts, dates, cards and Payees in an answer no Bank result backs. |
| `bank.py` | The Agent's view of the Bank: one Customer's session. |
| `model.py` | The model client interface and the OpenAI-compatible implementation. |
| `events.py` | The event models. |

## Evaluation runner

`src/evaluation/` (a separate package, not part of the Agent): replays the scripted test tasks under `tasks/` with the configured real model and prints a scoreboard. `.venv/bin/python -m evaluation [scenario-or-task-id ...] [--json results.json]`; see [docs/scripted-test-tasks.md](../docs/scripted-test-tasks.md#running-them).

## Tests

```sh
cd agent
python -m venv .venv && .venv/bin/pip install -e ../bank -e '.[dev]'
.venv/bin/pytest
.venv/bin/mypy src tests
```

Tests drive the conversation interface only (see `tests/conftest.py`): every test gets a real, freshly reset Bank in-process with the Bank clock fixed at the seed time, and an Agent whose model is a fake (`tests/fake_model.py`). A test scripts the model's replies (`model.script(plan(step(...)), say(...))`), logs the Customer in with the Bank as the Channel does (`log_in`), hands the Agent the session token (`start_conversation`), sends messages (`send`), answers prompts such as a Plan card (`act`), reads and types SMS codes on the simulated phone with the phone token (`phone_messages`, `latest_code`, `type_code`) and checks the events, the Bank's state and the Bank's audit trail (`bank_audit`, read with the admin key the Agent never has). A Staff Member takes over with `take_over`, replies with `staff_reply` and hands back with `hand_back`; what the Channel polls between turns is `updates`. What the fake model was shown is in `model.requests`. Never import Agent internals in a test, and never call a real model. Name each test after something the Customer would see.
