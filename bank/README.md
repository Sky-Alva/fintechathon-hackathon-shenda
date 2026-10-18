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
| `POST /sessions` `{username, password}` | anyone | Log in. Returns `session_id`, `session_token`, `phone_token` and the Customer. |
| `GET /operations` | anyone | Operation catalogue: bilingual descriptions, input/output JSON Schemas, tier rule, reversibility. |
| `POST /operations/{name}` `{input, confirmation_token?, verification_token?}` | `Authorization: Bearer <session_token>` | Call an Operation through the policy gate. |
| `POST /tier-preview` `{operations: [{operation, input}]}` | `Authorization: Bearer <session_token>` | Tier each step of a proposed Plan in order, counting the Daily Transfer Total; changes nothing. |
| `POST /confirmations` `{operations: [{operation, input}]}` | `Authorization: Bearer <session_token>` | Issue Confirmation tokens for the Yellow Operations on a Plan card. |
| `POST /verifications` `{operation, input}` | `Authorization: Bearer <session_token>` | Start Strong Verification for one Red Operation: texts a code to the Customer's simulated phone. |
| `POST /verifications/{challenge_id}` `{code}` | `Authorization: Bearer <phone_token>` | Answer the challenge from the simulated phone. The right code returns a verification token. |
| `GET /phone/messages` | `Authorization: Bearer <phone_token>` | The Customer's simulated phone (SMS inbox). The session token can neither read it nor answer a challenge. |
| `GET /clock` | anyone | Read the Bank clock. |
| `POST /admin/reset` | admin | Restore seed data (also ends every session and clears the audit log). |
| `PUT /admin/clock` `{now}` | admin | Set the Bank clock. |
| `POST /admin/clock/advance` `{minutes}` | admin | Move the Bank clock forward. |
| `GET /admin/sessions/{session_id}/audit` | admin | Any session's full audit trail, in order, for the staff console. A session reads its own with the Green Operation `read_audit_trail`. |
| `GET /admin/sessions/{session_id}/safety-lock` | admin | Whether the session is under a Safety Lock, why, and until when. |
| `DELETE /admin/sessions/{session_id}/safety-lock` | admin | A Staff Member lifts the session's Safety Lock (audited). |
| `GET /admin/customers/{customer_id}/phone/messages` | admin | A Customer's simulated phone, for the staff console. |

A successful call returns `{operation, tier, output}`. Every refusal returns `{"refusal": {"reason", "message", "detail"}}`, where `reason` is one of the values of `RefusalReason` in `src/bank/registry.py`.

The full contract for declaring and calling Operations, including Confirmation and Strong Verification, is [docs/operation-definition.md](../docs/operation-definition.md).

## Adding a Scenario module

A module is a `BankModule` (`src/bank/registry.py`): its SQL schema, its seed function and its `Operation`s. Add it to `MODULES` in `src/bank/app.py`. Each Operation declares its reversibility: `ReadOnly()` for queries, `Irreversible()`, or `ReversedBy(operation, build_input)`. `src/bank/customers.py` (Green, fixed tier) and `src/bank/transfers.py` (tier rule) are the worked examples. Type IDs of things a Customer owns with `OwnedBy` (like `customers.AccountId`) so the gate refuses another Customer's with `forbidden_other_customer`. Handlers hold business logic only; tiers, approvals, session limits, the Safety Lock and audit are the policy gate's job (`src/bank/policy.py`). Money is stored as integer cents and returned as a `Decimal` (a string with two decimals in JSON); use `bank.money` (`Amount`, `to_cents`, `from_cents`). Use `ctx.now`, never the system clock.

## Tests

```sh
cd bank
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/mypy src tests
```

Tests call the HTTP API only (see `tests/conftest.py`): every test gets a freshly reset Bank with the clock fixed at the seed time, logs in with `login(...)`, calls Operations with `call(...)` and checks responses and state as the Agent would see them. Never import internal functions or read tables in a test. Name each test after a Customer-visible rule.

Scripted test tasks for the Agent's evaluation runner are a separate thing: see [docs/scripted-test-tasks.md](../docs/scripted-test-tasks.md).
