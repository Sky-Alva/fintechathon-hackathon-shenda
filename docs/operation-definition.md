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
)
```

| Field | Rule |
|-------|------|
| `name` | Unique across the Bank. Use [CONTEXT.md](../CONTEXT.md) terms. |
| `description_zh` / `description_en` | What the Agent reads to plan. One sentence each, describing the effect for the Customer. |
| `input_model` | Pydantic model with `extra="forbid"`, so a stray field is `invalid_input` instead of being silently ignored. Money fields use `bank.money.Amount`. Never take a customer ID: the Customer always comes from the session. |
| `output_model` | Pydantic model. Money is `Decimal`, serialised as a string with two decimals. |
| `tier` | Either a fixed `Tier.GREEN / YELLOW / RED`, or a `TierRule` whose `resolve(input, ctx)` returns a tier from the input and current Bank state. Follow the tier table in [plan.md §3](plan.md#3-permission-tiers): anything that makes the account less safe is Red. |
| `reversibility` | `ReadOnly()` for queries; `Irreversible()`; or `ReversedBy(operation, build_input)` naming the Operation that undoes it and building its input from `(original_input, original_output)`. The Agent uses this to *offer* rollback. |
| `handler` | Business logic only. Read and write through `ctx.conn`; use `ctx.customer_id` for every query; use `ctx.now`, never the system clock. |

### Refusing

Raise `Refusal(RefusalReason.X, message, detail)` from a handler or tier rule. The gate rolls back everything the call wrote, audits the refusal and returns it. Reasons:

| Reason | HTTP | When |
|--------|------|------|
| `needs_confirmation` | 403 | Yellow, no Confirmation token (gate only) |
| `needs_verification` | 403 | Red, no Strong Verification (gate only) |
| `token_invalid` | 403 | Token doesn't cover this exact call, is used, expired or from another session (gate only) |
| `session_locked` | 423 | Safety Lock in force (gate only) |
| `forbidden_other_customer` | 403 | Input names another Customer's account, card, etc. |
| `insufficient_funds` | 409 | Not enough money |
| `ambiguous_payee` | 409 | More than one Payee matches; `detail.candidates` lists them |
| `not_found` | 404 | No such Operation, Payee, card, etc. (for this Customer) |
| `invalid_input` | 422 | Input fails validation or a business rule on its shape |
| `not_authenticated` | 401 | No valid session or admin key (gate only) |

A tier rule may refuse an input that can never run (an unknown Payee, insufficient funds), so the Customer is never asked to approve something that will fail. The handler must still re-check, because state can change between approval and execution.

## Calling an Operation

```http
POST /operations/transfer
Authorization: Bearer <session_token>

{"input": {"payee_id": "PAY-LM-002", "amount": "200.00"}, "confirmation_token": "<token>"}
```

Success: `200 {"operation", "tier", "output"}`. Refusal: `{"refusal": {"reason", "message", "detail"}}`.

### Confirmation (Yellow)

```http
POST /confirmations
Authorization: Bearer <session_token>

{"operations": [{"operation": "transfer", "input": {"payee_id": "PAY-LM-002", "amount": "200.00"}}, ...]}
```

Returns one `{operation, tier, confirmation_id, confirmation_token, expires_at}` per Operation, in order. The Agent asks for these only after the Customer approves the Plan card.

- Each token covers one Operation call with exactly that input (amounts are compared as money, so `"200"` equals `"200.00"`), for this Customer and this session.
- It works once, and expires 5 Bank-clock minutes after issue.
- All or none: if any Operation is Green, Red, unknown or invalid, no tokens are issued and the refusal's `detail.index` names the first failing Operation.
- The tier is re-checked when the Operation runs. If it has become Red in the meantime (the Daily Transfer Total moved), the Yellow token isn't enough and the call is refused with `needs_verification`.
- A call that is refused does not use up its token.

### Audit

Every Operation call and Confirmation request is recorded in order against its session, with the Bank-clock time, event (`operation` / `confirmation_issued`), Operation name, raw input, tier, outcome, refusal reason and detail (e.g. which `confirmation_id` was issued or used). Tokens themselves are never logged.
