# Conversation events

The contract between the Agent's conversation service and everything that talks to it: the web Channel, the staff console, the tests and the evaluation runner. Vocabulary: [CONTEXT.md](../CONTEXT.md). Python models: `agent/src/agent/events.py`; TypeScript types: `web/src/events.ts`. Change all three together.

## The conversation interface

| Call | Body | Returns |
|------|------|---------|
| `POST /conversations` | `{session_token}` | `{conversation_id}` |
| `POST /conversations/{conversation_id}/messages` | `{text}` | the turn's events |
| `POST /conversations/{conversation_id}/actions` | `{action, ...}` | the turn's events (not built yet, see below) |

- **Login stays with the Channel.** The Channel logs the Customer in with the Bank (`POST /sessions`), keeps the `phone_token` for its simulated phone and hands the Agent the `session_token` only. `POST /conversations` refuses any other field (422), so the Agent never sees a password or the phone token. The Agent acts only through that session and holds no Bank admin key ([ADR-0001](adr/0001-bank-enforces-permission-tiers.md)).
- **A turn is one streamed response.** It is newline-delimited JSON (`application/x-ndjson`): one event object per line, each with a `type`. The turn is over when the stream ends.
- **An unknown conversation** is `404 {"error": "unknown_conversation"}`.

### Customer actions (planned)

Later slices answer a Plan card and the other prompts with `POST /conversations/{id}/actions`. Here is the proposed shape; the ticket that builds an action may refine it and must update this table.

| `action` | Fields | Answers |
|----------|--------|---------|
| `confirm` | `plan_id`, `removed_step_ids?` | `confirmation_requested`: one tap confirms every Yellow step still in the Plan |
| `reject` | `plan_id` | `plan_proposed` or `reversal_offered` |
| `verified` | `step_id`, `verification_token` | `verification_requested`. The Customer types the code into the simulated phone, which sends it straight to the Bank with the phone token. Only the resulting verification token reaches the Agent, never the code. |
| `choose` | `value` | `clarification_question` |
| `reverse` | `plan_id`, `step_ids` | `reversal_offered`. Nothing is reversed without this. |
| `stop` | none | any running Plan: pause it |
| `ask_for_human` | none | any time |

## Events

Status: **emitted** means a slice emits it and tests pin it. **Reserved** payloads are proposed: the ticket that first emits one may refine it, and must update this doc and both type files.

### Shared shapes

```jsonc
// PlanStep: one Operation of a Plan
{"id": "s1", "operation": "query_balance", "input": {}, "depends_on": [],
 "tier": "green"}            // "green" | "yellow" | "red", from the Bank's tier preview; null before it
```

### `step_started` (emitted, #29)

A step of the Plan is about to be sent to the Bank.

```json
{"type": "step_started", "step": {"id": "s1", "operation": "query_balance", "input": {}, "depends_on": [], "tier": "green"}}
```

### `step_succeeded` (emitted, #29)

The Bank ran the step. `tier` is the tier the Bank ran it at; `output` is the Bank's output, unchanged.

```json
{"type": "step_succeeded", "step_id": "s1", "operation": "query_balance", "tier": "green",
 "output": {"accounts": [{"account_id": "ACC-LM-001", "kind": "current", "name": "活期账户 Current account", "currency": "CNY", "balance": "1180.00"}]}}
```

### `answer` (emitted, #29)

The Agent's reply to the Customer, written only from the Bank's results this turn and the conversation. Ends a normal turn.

```json
{"type": "answer", "text": "您的活期账户余额为 1,180.00 元。"}
```

### `plan_proposed` (reserved, #31)

The Plan needs a Plan card: at least one step is Yellow or Red. A Green-only Plan never gets one; it runs straight away.

```jsonc
{"type": "plan_proposed", "plan_id": "p1",
 "steps": [/* PlanStep, in the order they will run */],
 "refusals": [{"step_id": "s2", "reason": "insufficient_funds", "message": "…", "detail": {}}]}  // steps the Bank's preview says can never run
```

### `confirmation_requested` (reserved, #31)

Ask the Customer to confirm every Yellow step of the Plan card with one tap.

```json
{"type": "confirmation_requested", "plan_id": "p1", "step_ids": ["s1", "s2"]}
```

### `verification_requested` (reserved, #37)

A Red step needs Strong Verification. The Bank has texted a code to the simulated phone.

```json
{"type": "verification_requested", "plan_id": "p1", "step_id": "s3", "challenge_id": "…", "sent_to": "138****0001", "expires_at": "2026-10-20T09:05:00+08:00"}
```

### `step_failed` (reserved, #42)

The Bank refused or failed the step. `reason`, `message` and `detail` are the Bank's refusal, unchanged.

```json
{"type": "step_failed", "step_id": "s2", "operation": "transfer", "reason": "insufficient_funds", "message": "…", "detail": {}}
```

### `reversal_offered` (reserved, #42)

After a failure, offer to undo completed reversible steps, in reverse order. Nothing is reversed unless the Customer answers with `reverse`.

```json
{"type": "reversal_offered", "plan_id": "p1", "steps": [{"step_id": "s1", "operation": "release_reserved_funds", "input": {}}]}
```

### `clarification_question` (reserved, #39)

One short question when the request is genuinely ambiguous (e.g. which 张伟). Options never carry full account details.

```json
{"type": "clarification_question", "text": "您要转给哪位张伟？", "options": [{"label": "张伟（同事）", "value": "PAY-LM-003"}]}
```

### `refusal` (reserved, #33, #43, #44)

The Agent won't do this: out of scope, a scam or manipulation attempt, or a request for a secret. `text` explains and lists what the Agent can do.

```json
{"type": "refusal", "reason": "out_of_scope", "text": "…"}
```

`reason`: `out_of_scope` | `manipulation` | `secret`.

### `session_locked` (reserved, #37)

The Bank has put the session under a Safety Lock: only Green Operations run until `locked_until` or a Staff Member lifts it.

```json
{"type": "session_locked", "trigger": "wrong_codes", "locked_until": "2026-10-20T09:30:00+08:00", "text": "…"}
```

### `handed_over` / `handed_back` (reserved, #46)

A Staff Member took over the conversation (the Agent stays silent) / handed it back to the Agent.

```json
{"type": "handed_over", "staff": "staff-1"}
{"type": "handed_back"}
```

### `trace` (reserved, #34)

Decision-trace data for the toggleable panel, hidden by default. One event per stage, in order.

```jsonc
{"type": "trace", "stage": "intent", "data": {}}   // stage: "intent" | "plan" | "bank_request" | "bank_response"
```
