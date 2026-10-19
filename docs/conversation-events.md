# Conversation events

The contract between the Agent's conversation service and everything that talks to it: the web Channel, the staff console, the tests and the evaluation runner. Vocabulary: [CONTEXT.md](../CONTEXT.md). Python models: `agent/src/agent/events.py`; TypeScript types: `web/src/events.ts`. Change all three together.

## The conversation interface

| Call | Body | Returns |
|------|------|---------|
| `POST /conversations` | `{session_token}` | `{conversation_id}` |
| `POST /conversations/{conversation_id}/messages` | `{text}` | the turn's events |
| `POST /conversations/{conversation_id}/actions` | `{action, ...}` | the turn's events (see below) |
| `GET /conversations/{conversation_id}/audit-trail` | none | one `trace` event (`stage: "audit_trail"`): the session's audit trail, for the decision trace panel |

- **Login stays with the Channel.** The Channel logs the Customer in with the Bank (`POST /sessions`), keeps the `phone_token` for its simulated phone and hands the Agent the `session_token` only. `POST /conversations` refuses any other field (422), so the Agent never sees a password or the phone token. The Agent acts only through that session and holds no Bank admin key ([ADR-0001](adr/0001-bank-enforces-permission-tiers.md)).
- **A turn is one streamed response.** It is newline-delimited JSON (`application/x-ndjson`): one event object per line, each with a `type`. The turn is over when the stream ends.
- **The Customer's language.** Replies (`answer`, `refusal`, and later the other events' `text`) are in Chinese, or in English when the Customer's latest message with any words in it is in English (has Latin letters and no Chinese characters).
- **An unknown conversation** is `404 {"error": "unknown_conversation"}`.
- **A session the Bank doesn't know** (unknown or ended token): `POST /conversations` is `401 {"error": "not_authenticated"}`. The Agent asks the Bank which session and Customer the token is (`GET /sessions/current`) when the conversation starts.
- **The Bank can't be reached** outside a turn (starting a conversation, the staff views): `503 {"error": "bank_unavailable"}`.
- **A turn with a Plan card** ends after `confirmation_requested`: nothing on the card runs until the Customer answers it with `confirm` or `reject`. A Plan card is answered once. An action for a Plan card that isn't waiting for an answer (already answered, or replaced because the Customer sent a new message first) is `409 {"error": "plan_not_pending"}`, before any event.

### Customer actions

The Customer answers a Plan card and the other prompts with `POST /conversations/{id}/actions`. `confirm` and `reject` are built (#31); the rest are proposed, and the ticket that builds one may refine it and must update this table. An action the Agent doesn't know yet is `422`.

| `action` | Fields | Answers |
|----------|--------|---------|
| `confirm` (built, #31) | `plan_id` (`removed_step_ids` planned, #38) | `confirmation_requested`: one tap confirms every Yellow step on the card. Only now does the Agent ask the Bank for Confirmation tokens, one per Yellow step, in one request; then the steps run (`step_started`, then `step_succeeded` or `step_failed`) and the turn ends with `answer`. |
| `reject` (built, #31) | `plan_id` | `plan_proposed`: nothing runs; the turn is one `answer` acknowledging it. (Answering `reversal_offered` is planned, #42.) |
| `verified` | `step_id`, `verification_token` | `verification_requested`. The Customer types the code into the simulated phone, which sends it straight to the Bank with the phone token. Only the resulting verification token reaches the Agent, never the code. |
| `choose` | `value` | `clarification_question` |
| `reverse` | `plan_id`, `step_ids` | `reversal_offered`. Nothing is reversed without this. |
| `stop` | none | any running Plan: pause it |
| `ask_for_human` | none | any time |

## The staff interface

For the staff console. Every call needs `X-Staff-Key`: the Agent's own staff key (`AGENT_STAFF_KEY`), never the Bank's admin key, which the Agent doesn't have. Without it, or with a Customer's session token, the answer is `401 {"error": "staff_key_required"}`.

| Call | Returns |
|------|---------|
| `GET /staff/conversations` | `{conversations: [{conversation_id, session_id, customer: {customer_id, name}}]}`: the conversations whose Bank session is still active (a Safety Lock doesn't end a session), in the order they started |
| `GET /staff/conversations/{conversation_id}` | `{conversation_id, session_id, customer, entries}`: the conversation's history, readable after the session ended too |

`entries` is everything in order: each Customer message or action, then every event the Customer was sent, exactly as sent, `trace` events included (so the Plan with its tiers is the `plan` trace, and every Bank call of the turn is a `bank_request` + `bank_response` pair).

```jsonc
{"type": "customer_message", "text": "我卡里还有多少钱？"}
// ...then the turn's events, as streamed: trace intent, tier-preview pair, trace plan, step_started, ..., answer
{"type": "customer_action", "action": "confirm", "plan_id": "p1"}
// ...then that action's turn: the /confirmations pair, step_started, ..., answer
```

A `customer_action` entry names the action and its `plan_id` only, never the action's other fields, so a token the Customer sends with an action never reaches the history.

The Bank's side of the trail (tier previews, Confirmations, verification attempts, Safety Lock and lift, Operation results) and lifting a Safety Lock are the Bank's admin calls (`GET /admin/sessions/{session_id}/audit`, `GET`/`DELETE /admin/sessions/{session_id}/safety-lock`), which the console makes with the Bank's admin key, never through the Agent. The Bank clock can stand still, so the console merges the two trails by cause, not time: the Agent's entries keep their order, and each Bank audit entry is placed right after the Agent's `bank_request` trace that caused it (`/tier-preview` → `tier_preview`, `/operations/{name}` → that Operation's `operation`, `/confirmations` → its `confirmation_issued` entries, one per step it confirms, `/verifications` → `verification_challenge`). Bank entries no Agent call caused (code attempts from the simulated phone, Safety Locks, lifts) stay where the Bank put them. A ticket that adds a new kind of Bank call adds its pair to `bankEventFor` in `web/src/staff/trail.ts`.

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

Every amount, date, card and Payee it mentions appears in a Bank result from this turn or earlier in the conversation (#32). A draft that fails that check is regenerated once; if the new one fails too, `text` is the raw Bank result. When the turn's queries all failed, or the Bank couldn't be reached at all, `text` says the information couldn't be retrieved and states no figures.

```json
{"type": "answer", "text": "您的活期账户余额为 1,180.00 元。"}
```

### `plan_proposed` (emitted, #31)

The Plan needs a Plan card: at least one step that can run is Yellow or Red. A Green-only Plan never gets one; it runs straight away (a step the tier preview refused then fails with `step_failed`).

**Exact details first.** When a Yellow or Red step's input waits on a Green look-up ("把余额的一半转给老妈", send Mum half my balance), the look-ups run first in the same turn (`step_started`, `step_succeeded`), the Planner completes the Plan from the Bank's results, and only then is the card proposed. The card's `steps` are the steps still to run, so a step's `depends_on` may name a look-up that already ran. A refused step has `"tier": null` and an entry in `refusals`; it is not on `confirmation_requested`, and if the Customer confirms it fails with `step_failed` when its turn comes.

```jsonc
{"type": "plan_proposed", "plan_id": "p1",
 "steps": [/* PlanStep, in the order they will run */],
 "refusals": [{"step_id": "s2", "reason": "insufficient_funds", "message": "…", "detail": {}}]}  // steps the Bank's preview says can never run
```

### `confirmation_requested` (emitted, #31)

Ask the Customer to confirm every Yellow step of the Plan card with one tap. Follows `plan_proposed` and ends the turn. The Customer answers with `confirm` or `reject` and the `plan_id`.

```json
{"type": "confirmation_requested", "plan_id": "p1", "step_ids": ["s1", "s2"]}
```

### `verification_requested` (reserved, #37)

A Red step needs Strong Verification. The Bank has texted a code to the simulated phone.

```json
{"type": "verification_requested", "plan_id": "p1", "step_id": "s3", "challenge_id": "…", "sent_to": "138****0001", "expires_at": "2026-10-20T09:05:00+08:00"}
```

### `step_failed` (emitted, #31, #32)

The step didn't run. `reason`, `message` and `detail` are the Bank's refusal, unchanged, or `reason: "bank_unavailable"` when the Agent couldn't reach the Bank. The turn ends with `answer`.

- A step the Bank's tier preview refused is never sent to the Bank, so it gets `step_failed` with no `step_started` before it; the steps after it still run (the Plan card showed it as refused).
- A step that fails once sent (it follows its `step_started`) stops the Plan: the steps after it don't run.
- On `confirm`, if the Bank refuses a step's Confirmation (things changed since the card was shown, e.g. the Daily Transfer Total grew and the step is now Red), no token is issued and nothing runs: the turn is that step's `step_failed`, then `answer`.

Re-planning and reversal offers after a failure are #42.

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

### `refusal` (emitted for `out_of_scope`, #33; reserved for `manipulation` #43, `secret` #44)

The Agent won't do this: out of scope, a scam or manipulation attempt, or a request for a secret. `text` explains and lists what the Agent can do. Ends the turn; no Operation runs.

```json
{"type": "refusal", "reason": "out_of_scope",
 "text": "抱歉，这个我暂时帮不了您。我可以帮您：\n- 从当前客户的活期账户向一位收款人转账。\n- 查询当前客户名下所有账户（或其中指定一个账户）的余额。"}
```

`reason`: `out_of_scope` | `manipulation` | `secret`.

- **`out_of_scope`**: Wealth Operations (product recommendations, risk assessment, subscribe / redeem) and non-banking topics. The Planner declines instead of planning, naming up to 5 catalogue Operations to offer; `text` lists them one per line (`- …`) in the Bank's own words (`description_zh` / `description_en` from the Operation catalogue), so the list grows with the catalogue. Names the Bank doesn't know are dropped; if none are left, the catalogue's first 5 are listed.

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

### `trace` (emitted, #34)

Decision-trace data for the Channel's toggleable panel, hidden by default: what the Agent understood, planned and was told by the Bank. The other events of a turn don't depend on it; a client that shows no trace skips every `trace` event.

```jsonc
{"type": "trace", "stage": "intent", "data": {"intent": "查询账户余额"}}  // the Planner's reading of the request
{"type": "trace", "stage": "intent", "data": {"intent": "", "declined": {"reason": "out_of_scope", "can_do": ["query_balance"]}}}  // or why it declined
{"type": "trace", "stage": "plan", "data": {"steps": [/* PlanStep, in run order, each with the Bank's tier */]}}
{"type": "trace", "stage": "bank_request", "data": {"method": "POST", "path": "/operations/query_balance", "body": {"input": {}}}}
{"type": "trace", "stage": "bank_response", "data": {"method": "POST", "path": "/operations/query_balance", "status": 200,
  "body": {"operation": "query_balance", "tier": "green", "output": {}}}}   // the Bank's raw body, refusals included
{"type": "trace", "stage": "audit_trail", "data": {"session_id": "…", "entries": [/* read_audit_trail output */]}}
```

- **Order in a turn.** `intent` once the Plan is made (a declined request has only `intent`, then its `refusal`), `plan` once the Bank's tier preview has tiered it, and every Bank call as a `bank_request` + `bank_response` pair just before the first event that follows it. A Green turn is: `intent`, tier-preview pair, `plan`, then per step `step_started`, its pair, `step_succeeded`; then `answer`. A Plan-card turn ends with `plan`, `plan_proposed`, `confirmation_requested`; when Green look-ups run first, it starts with their own `plan` trace (`"look_ups": true`) and steps, then the completed Plan's tier-preview pair and `plan`. A `plan` trace lists the steps the preview refused under `refusals`. A `confirm` turn starts with the `/confirmations` pair (its tokens redacted), then the steps as in a Green turn. The Operation catalogue fetch is not traced.
- **The audit trail** is not part of a turn: the panel fetches it with `GET /conversations/{id}/audit-trail`, which runs the Green `read_audit_trail` Operation through the session (so it works under a Safety Lock). The read is itself audited and shows up in the next read.
- **No secrets.** A `trace` never carries a token (session, phone, Confirmation, verification), a password or a verification code: the session token travels in a header, which is never traced, and any field whose name contains `token` or `password`, or is `code`, is replaced by `"[redacted]"` wherever it appears.
