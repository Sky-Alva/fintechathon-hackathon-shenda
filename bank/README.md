# Bank

The simulated core-banking service. It owns all money and is the sole authority on whether an Operation may run ([ADR-0001](../docs/adr/0001-bank-enforces-permission-tiers.md)).

## Run

From the repo root:

```sh
docker compose up --build bank        # http://localhost:8000, seeded on first start
```

Demo logins: `liming` / `demo1234` and `hanmei` / `demo1234`. Admin calls need the header `X-Admin-Key` (default `demo-admin-key`, override with `BANK_ADMIN_KEY` in `.env`).

## HTTP API

| Call | Who | What |
|------|-----|------|
| `POST /sessions` `{username, password}` | anyone | Log in. Returns `session_id`, `session_token` and the Customer. |
| `GET /operations` | anyone | Operation catalogue: bilingual descriptions, input/output JSON Schemas, tier rule, reversibility. |
| `POST /operations/{name}` `{input}` | `Authorization: Bearer <session_token>` | Call an Operation through the policy gate. |
| `GET /clock` | anyone | Read the Bank clock. |
| `POST /admin/reset` | admin | Restore seed data (also ends every session and clears the audit log). |
| `PUT /admin/clock` `{now}` | admin | Set the Bank clock. |
| `POST /admin/clock/advance` `{minutes}` | admin | Move the Bank clock forward. |
| `GET /admin/sessions/{session_id}/audit` | admin | One session's audit trail, in order. |

A successful call returns `{operation, tier, output}`. Every refusal returns `{"refusal": {"reason", "message", "detail"}}`, where `reason` is one of the values of `RefusalReason` in `src/bank/registry.py`.

## Adding a Scenario module

A module is a `BankModule` (`src/bank/registry.py`): its SQL schema, its seed function and its `Operation`s. Add it to `MODULES` in `src/bank/app.py`. Each Operation declares its reversibility: `ReadOnly()` for queries, `Irreversible()`, or `ReversedBy(operation, build_input)`. `src/bank/customers.py` is the worked example. Handlers hold business logic only; tiers, approvals, session limits and audit are the policy gate's job (`src/bank/policy.py`). Money is stored as integer cents and returned as a `Decimal` (a string with two decimals in JSON). Use `ctx.now`, never the system clock.

## Tests

```sh
cd bank
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/mypy src tests
```

Tests call the HTTP API only (see `tests/conftest.py`): every test gets a freshly reset Bank with the clock fixed at the seed time, logs in with `login(...)`, calls Operations with `call(...)` and checks responses and state as the Agent would see them. Never import internal functions or read tables in a test. Name each test after a Customer-visible rule.

Scripted test tasks for the Agent's evaluation runner are a separate thing: see [docs/scripted-test-tasks.md](../docs/scripted-test-tasks.md).
