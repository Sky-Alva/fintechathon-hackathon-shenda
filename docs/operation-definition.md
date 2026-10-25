# Operation definition

**Frozen 19 Oct 2026** (#4). Scenario owners build against this; changes go through the Lead.

An Operation is declared once, in a `BankModule` (`bank/src/bank/registry.py`), and the Bank does the rest: the catalogue entry, input validation, tier resolution, Confirmation / Strong Verification, limiting the session to its own Customer, and the audit log. Handlers contain business logic only.

## Declaring an Operation

```python
Operation(
    name="transfer",                       # unique, snake_case verb_noun
    description_zh="从当前客户的活期账户向一位收款人转账。",
    description_en="Transfer money from the current Customer's current account to a Payee.",
    input_model=TransferInput,             # pydantic model, ConfigDict(extra="forbid")
    output_model=TransferOutput,           # pydantic model
    tier=TierRule(description_zh=..., description_en=..., resolve=transfer_tier),
    reversibility=Irreversible(),
    handler=transfer,                      # (OperationContext, TransferInput) -> TransferOutput
    daily_transfer_amount=lambda input: input.amount,   # only for money leaving the Customer
)
```

| Field | Rule |
|-------|------|
| `name` | Unique across the Bank. Use [CONTEXT.md](../CONTEXT.md) terms. |
| `description_zh` / `description_en` | What the Agent reads to plan. One sentence each, describing the effect for the Customer. |
| `input_model` | Pydantic model with `extra="forbid"`, so a stray field is `invalid_input` instead of being silently ignored. Money fields use `bank.money.Amount`. Never take a customer ID: the Customer always comes from the session. Type every ID of something a Customer owns with an `OwnedBy` type (see below), and mark secrets with `Secret()`. |
| `output_model` | Pydantic model. Money is `Decimal`, serialised as a string with two decimals. |
| `tier` | Either a fixed `Tier.GREEN / YELLOW / RED`, or a `TierRule` whose `resolve(input, ctx)` returns a tier from the input and current Bank state. It must not write anything. Follow the tier table in [plan.md §3](plan.md#3-permission-tiers): anything that makes the account less safe is Red. Money leaving the Customer is tiered with `limits.tier_for_outgoing(amount, ctx)`. |
| `reversibility` | `ReadOnly()` for queries; `Irreversible()`; or `ReversedBy(operation, build_input)` naming the Operation that undoes it and building its input from `(original_input, original_output)`. The Agent uses this to *offer* rollback. |
| `handler` | Business logic only. Read and write through `ctx.conn`. Scope every read or change of the Customer's own data by `ctx.customer_id` (paying in to a Payee's account and Bank-wide ID counters are the exceptions). Use `ctx.now`, never the system clock. |
| `daily_transfer_amount` | Only for Operations that send the Customer's money out (transfers, Merchant payments from Reserved Funds, a Scheduled Transfer when it runs): the amount, from the input. The gate adds it to the Daily Transfer Total after the handler succeeds. Modules never keep their own total. |

### IDs of things a Customer owns

Any input field holding the ID of an account, a card or anything else a Customer owns is typed with an `OwnedBy(table, key)` marker. The table needs a `customer_id` column:

```python
AccountId = Annotated[str, OwnedBy("accounts", "account_id")]     # bank.customers
CardId = Annotated[str, OwnedBy("cards", "card_id")]              # your module

class LockCardInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card_id: CardId
```

The gate checks every marked ID before the tier rule runs, on every call, tier preview, Confirmation and verification request. If the ID belongs to another Customer, the call is refused with `forbidden_other_customer` (`detail: {field}`). If it belongs to nobody, the call is refused with `not_found` (`detail: {field, id}`). Both are audited. The check also works inside `list[...]`, `... | None` and nested input models. Handlers can therefore trust a marked ID, though they still scope their queries by `ctx.customer_id`. Payee IDs aren't marked: a Payee who isn't the Customer's own is simply `not_found`. Paying *to* a Payee (including the spouse) is unaffected.

### Secrets

Mark an input field the audit log must never store, such as a new password, as `Annotated[str, ..., Secret()]`. The audit log records it as `**********`. It is still part of the approval fingerprint, so a token covers only that exact value. Only top-level fields can be marked.

### The Daily Transfer Total

The policy gate owns it. `ctx.daily_transfer_total` is the Customer's total for the Bank-clock day *before* this Operation. When the Bank tiers several steps of a Plan together (a tier preview or a Confirmation request), it also includes the earlier steps, in order, without writing anything. So a tier rule only ever needs `(input, ctx)`.

The one exception is money leaving on another day. A Scheduled Transfer is tiered against its run day's *projected* total (sent that day, plus the Customer's other Scheduled Transfers waiting to run on it), read by its own tier rule and passed to `limits.tier_for_day_total`. When it runs, at 09:00 on its run date, it goes through the gate's `policy.run_approved` as the `transfer` the Customer approved: the gate checks the session's Safety Lock and the tier again, adds it to that day's total and audits it in the session that scheduled it.

### Refusing

Raise `Refusal(RefusalReason.X, message, detail)` from a handler or tier rule. The gate rolls back everything the call wrote, audits the refusal and returns it. Reasons:

| Reason | HTTP | When |
|--------|------|------|
| `needs_confirmation` | 403 | Yellow, no Confirmation token (gate only) |
| `needs_verification` | 403 | Red, no Strong Verification (gate only) |
| `token_invalid` | 403 | Token or challenge doesn't cover this exact call, is used, expired or from another session; or a wrong verification code (gate only) |
| `session_locked` | 423 | Safety Lock in force and the call isn't Green; `detail: {trigger, locked_until}` (gate only) |
| `forbidden_other_customer` | 403 | Input names another Customer's account, card, etc. (gate, via `OwnedBy`) |
| `insufficient_funds` | 409 | Not enough money |
| `ambiguous_payee` | 409 | More than one Payee matches; `detail.candidates` lists them |
| `not_found` | 404 | No such Operation, Payee, card, etc. (for this Customer) |
| `invalid_input` | 422 | Input fails validation or a business rule on its shape; or a Confirmation was requested for a Green Operation |
| `not_authenticated` | 401 | No valid session or admin key (gate only) |

A tier rule may refuse an input that can never run (an unknown Payee, insufficient funds), so the Customer is never asked to confirm or verify something that will fail. The handler must still re-check, because state can change between Confirmation and execution.

## Calling an Operation

```http
POST /operations/transfer
Authorization: Bearer <session_token>

{"input": {"payee_id": "PAY-LM-002", "amount": "200.00"}, "confirmation_token": "<token>"}
```

| Tier when the call runs | Needs |
|-------------------------|-------|
| Green | nothing |
| Yellow | `confirmation_token`, or a `verification_token` for the same call (e.g. one obtained while it was Red) |
| Red | `verification_token`; a `confirmation_token` alone is refused with `needs_verification` |

Success: `200 {"operation", "tier", "output"}`. Refusal: `{"refusal": {"reason", "message", "detail"}}`.

### Tier preview

Before showing a Plan card, the Agent asks the Bank what each step of the proposed Plan will need:

```http
POST /tier-preview
Authorization: Bearer <session_token>

{"operations": [{"operation": "transfer", "input": {"payee_id": "PAY-HM-001", "amount": "600.00"}},
                {"operation": "transfer", "input": {"payee_id": "PAY-HM-001", "amount": "600.00"}}]}
```

```json
{"steps": [{"index": 0, "operation": "transfer", "tier": "yellow"},
           {"index": 1, "operation": "transfer", "tier": "red"}]}
```

- Steps are tiered in submitted order (at most 20). Each one counts the transfers already made today and the earlier steps toward the Daily Transfer Total, as a Confirmation request does.
- A step that can never run comes back with `refusal: {reason, message, detail}` and no `tier`, and adds nothing to the total. The other steps are still tiered. Only a malformed request as a whole (no list, an empty list, a stray field) is refused with `invalid_input`.
- The preview is Green and changes nothing: no balances, totals or tokens. It is audited as one `tier_preview` entry listing every step's result.
- It simulates the Daily Transfer Total only, not balances. A step that is funded now but wouldn't be after earlier steps is refused with `insufficient_funds` when it runs.

### Confirmation (Yellow)

```http
POST /confirmations
Authorization: Bearer <session_token>

{"operations": [{"operation": "transfer", "input": {"payee_id": "PAY-LM-002", "amount": "200.00"}}, ...]}
```

Returns one `{operation, tier, confirmation_id, confirmation_token, expires_at}` per Operation, in order. The Agent asks for these only after the Customer confirms the Plan card.

- Each token covers one Operation call with exactly that input (amounts are compared as money, so `"200"` equals `"200.00"`), for this Customer and this session.
- It works once, and expires 5 Bank-clock minutes after issue.
- Operations are tiered in order, each counting the earlier ones toward the Daily Transfer Total: two ¥600 transfers on one card make the second Red.
- All or none: if any Operation is Green, Red, unknown or invalid, no tokens are issued and the refusal's `detail.index` names the first failing Operation. Use the tier preview first so Red steps go to Strong Verification instead.
- The tier is re-checked when the Operation runs. If it has become Red in the meantime (the Daily Transfer Total moved), the Yellow token isn't enough and the call is refused with `needs_verification`.
- A call that is refused does not use up its token, because nothing it confirmed has happened. A token can therefore be tried against other inputs until it expires; every such try is refused with `token_invalid` and audited against its `confirmation_id`.

### Strong Verification (Red)

Red Operations get their own verification, one at a time, after the Customer has seen the Plan card.

```http
POST /verifications
Authorization: Bearer <session_token>

{"operation": "transfer", "input": {"payee_id": "PAY-HM-001", "amount": "1200.00"}}
```

Returns `{challenge_id, operation, tier, sent_to, expires_at}` and texts a 6-digit code to the Customer's simulated phone (`sent_to` is the masked number, e.g. `138****0002`). Only a Red call gets a challenge: a Yellow or Green one is refused with `invalid_input`, and one that can never run (unknown Payee, insufficient funds) is refused with that reason, so no code is sent for it. A Red step that is only Red because of earlier steps of the same Plan becomes Red once they have run, so request its challenge when the Executor reaches it.

```http
POST /verifications/{challenge_id}
Authorization: Bearer <phone_token>

{"code": "123456"}
```

The code is typed into the simulated phone, which sends it with the same session's `phone_token` (the session token is refused with `not_authenticated`). The right code returns `{operation, verification_id, verification_token, expires_at}`, which the Channel passes on for the call.

- The challenge is bound like a Confirmation token (Customer, session, Operation name, exact input), lasts 5 Bank-clock minutes and can be answered once, from its own session. Otherwise the answer is refused with `token_invalid`.
- A wrong code is refused with `token_invalid` and `detail: {challenge_id, failed_attempts}`, where `failed_attempts` counts every wrong code in the session. The third wrong code since the session's last `wrong_codes` Safety Lock locks the session, and its refusal also carries `locked_until`. Three wrong codes close the challenge; after that, request a new one.
- The verification token covers that one call, works once and expires 5 Bank-clock minutes after issue. Pass it as `verification_token`.

#### The simulated phone

The Bank's SMS go to the Customer's simulated phone, readable with `GET /phone/messages` and the `phone_token` from login, or by the staff console with `GET /admin/customers/{customer_id}/phone/messages`. Each login has its own phone token, and it shows every SMS the Customer has received. The session token can neither read the phone nor answer a challenge: the Channel keeps the phone token for its phone view and never hands it, or any code, to the Agent. Codes go from the Customer to the Bank, never through the model.

### Safety Lock

A session is locked for 30 Bank-clock minutes when any of these happens in it:

| Trigger (`detail.trigger`) | When |
|----------------------------|------|
| `wrong_codes` | the 3rd wrong verification code |
| `reported_attempts` | the 2nd `report_suspected_attempt` call |
| `red_operations` | a 6th Red Operation runs within 10 Bank-clock minutes (the 6th itself runs; the lock stops the 7th) |

While locked, only Green Operations run. Every other call, Confirmation request, verification request and challenge answer is refused with `session_locked` and `detail: {trigger, locked_until}` (a Confirmation request also names `index`); a tier preview still answers, with a `session_locked` refusal on each non-Green step. Tokens issued before the lock are not used up, and work again once it ends, if they haven't expired.

The lock holds that session only, not the Customer's other sessions. It ends at `locked_until`, or earlier when a Staff Member lifts it (`DELETE /admin/sessions/{session_id}/safety-lock`; `GET` on the same path reads it for the staff console). Nothing done while a lock holds counts toward the next one, and a lock clears the signals of its own trigger only: one reported attempt before a `wrong_codes` lock still counts afterwards. `failed_attempts` in a wrong-code refusal counts the whole session, so it can exceed 3 after a lock has ended.

The Agent reports a suspected prompt injection or impersonation attempt with the Green Operation `report_suspected_attempt` `{kind: "injection" | "impersonation", evidence}`, quoting the text that raised it. Its output `session_locked` says whether the session is now locked.

### Audit

Every Operation call, tier preview, Confirmation request, verification challenge and code attempt is recorded in order against its session, with the Bank-clock time, event (`operation` / `tier_preview` / `confirmation_issued` / `verification_challenge` / `verification_attempt`), Operation name, raw input, tier, outcome, refusal reason and detail (e.g. which `confirmation_id`, `challenge_id` or `verification_id` was issued or used, and `failed_attempts` for a wrong code). A Safety Lock is recorded as a `safety_lock` entry right after the call that tripped it (`detail: {lock_id, trigger, locked_until}`), and a lift as `safety_lock_lifted` (`detail: {lock_id, lifted_by}`). Tokens, codes and `Secret` fields are never logged.

A session reads its own trail with the Green Operation `read_audit_trail` (input `{}`), for the Agent's decision trace panel. The output is `{session_id, entries}`, and each entry has `entry_id`, `at`, `event`, `operation`, `input`, `tier`, `outcome`, `reason` and `detail`. It never reaches another session's trail, not even one of the same Customer's. A refused entry's `detail` is exactly the refusal's `detail` the caller got back (e.g. `failed_attempts` for a wrong code), never the audit-only detail Staff Members see (e.g. which token a refused one turned out to be). The read is itself audited, so it shows up in the next read. A Staff Member reads any session's full trail, ended sessions included, with `GET /admin/sessions/{session_id}/audit`.
