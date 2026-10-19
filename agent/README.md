# Agent

The Banking AI Agent: it turns what the Customer says into a Plan, has the Bank tier it, runs it through the Customer's own Bank session and answers only from what the Bank returned. It is an untrusted caller: it holds the session token the Channel hands it, never the password, the phone token or the Bank's admin key ([ADR-0001](../docs/adr/0001-bank-enforces-permission-tiers.md)).

## Run

From the repo root, with the Bank and the web Channel:

```sh
cp .env.example .env      # then fill in DASHSCOPE_API_KEY (or DEEPSEEK_API_KEY)
docker compose up --build # Channel: http://localhost:8080 · Agent: :8001 · Bank: :8000
```

`AGENT_MODEL_PROVIDER` picks the model: `qwen` (`qwen-plus` on DashScope, the default) or `deepseek` (`deepseek-chat`, the backup). Both go through one OpenAI-compatible client (`src/agent/model.py`).

## Conversation interface

`POST /conversations {session_token}`, then `POST /conversations/{id}/messages {text}`, which streams the turn's events as NDJSON. A Plan with a Yellow step stops at its Plan card; the Customer answers it with `POST /conversations/{id}/actions {action: confirm | reject, plan_id}`. Staff Members read the active sessions and each conversation's history at `/staff/...` with the Agent's own staff key (`AGENT_STAFF_KEY`, header `X-Staff-Key`), never the Bank's admin key. The event contract shared with the Channel, the staff console and the tests is [docs/conversation-events.md](../docs/conversation-events.md).

## Modules

| Module | Job |
|--------|-----|
| `app.py` | The HTTP conversation interface, and the staff views. |
| `conversation.py` | Conversation service: owns conversations and history (including what Staff Members read), runs a turn (plan → review → execute → answer). |
| `planner.py` | Conversation + the Bank's Operation catalogue → a Plan (a dependency graph of steps). Always plans, even for one step, unless it declines an out-of-scope request. |
| `refusals.py` | The `refusal` texts: an out-of-scope refusal lists what the Agent can do, from the catalogue. |
| `language.py` | The Customer's language: Chinese by default, English when they write in English. |
| `reviewer.py` | Asks the Bank's tier preview for the Plan, in run order. Green-only Plans run with no Plan card. On the Customer's confirm, and only then, collects one Confirmation token per Yellow step. |
| `executor.py` | Runs the steps against the Bank, as events; a step the Bank refuses or can't be reached for fails with `step_failed`, and once a step fails while running, the steps after it don't run. |
| `responder.py` | Writes the answer from the Bank's results only: a draft that fails the fact check is regenerated once, then replaced by the raw Bank result. |
| `fact_check.py` | Which amounts, dates, cards and Payees in an answer no Bank result backs. |
| `bank.py` | The Agent's view of the Bank: one Customer's session. |
| `model.py` | The model client interface and the OpenAI-compatible implementation. |
| `events.py` | The event models. |

## Tests

```sh
cd agent
python -m venv .venv && .venv/bin/pip install -e ../bank -e '.[dev]'
.venv/bin/pytest
.venv/bin/mypy src tests
```

Tests drive the conversation interface only (see `tests/conftest.py`): every test gets a real, freshly reset Bank in-process with the Bank clock fixed at the seed time, and an Agent whose model is a fake (`tests/fake_model.py`). A test scripts the model's replies (`model.script(plan(step(...)), say(...))`), logs the Customer in with the Bank as the Channel does (`log_in`), hands the Agent the session token (`start_conversation`), sends messages (`send`), answers prompts such as a Plan card (`act`) and checks the events, the Bank's state and the Bank's audit trail (`bank_audit`, read with the admin key the Agent never has). What the fake model was shown is in `model.requests`. Never import Agent internals in a test, and never call a real model. Name each test after something the Customer would see.
