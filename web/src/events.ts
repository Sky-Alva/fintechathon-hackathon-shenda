// Conversation events, as the Agent streams them. The contract is
// docs/conversation-events.md; keep this file, the doc and agent/src/agent/events.py in step.

export type Tier = 'green' | 'yellow' | 'red'

export type PlanStep = {
  id: string
  operation: string
  input: Record<string, unknown>
  depends_on: string[]
  tier: Tier | null
}

export type BankRefusal = {
  reason: string
  message: string
  detail: Record<string, unknown>
}

// Emitted since #29.
export type StepStarted = { type: 'step_started'; step: PlanStep }
export type StepSucceeded = {
  type: 'step_succeeded'
  step_id: string
  operation: string
  tier: Tier
  output: Record<string, unknown>
}
export type Answer = { type: 'answer'; text: string }
// Emitted for 'out_of_scope' since #33; 'manipulation' (#43) and 'secret' (#44) reserved.
export type Refusal = {
  type: 'refusal'
  reason: 'out_of_scope' | 'manipulation' | 'secret'
  text: string
}

// Emitted since #32. `reason` is the Bank's refusal reason, or 'bank_unavailable' when
// the Agent couldn't reach the Bank.
export type StepFailed = BankRefusal & { type: 'step_failed'; step_id: string; operation: string }

// Reserved: proposed payloads, refined by the ticket that first emits each one.
export type PlanProposed = {
  type: 'plan_proposed'
  plan_id: string
  steps: PlanStep[]
  refusals: (BankRefusal & { step_id: string })[]
}
export type ConfirmationRequested = {
  type: 'confirmation_requested'
  plan_id: string
  step_ids: string[]
}
export type VerificationRequested = {
  type: 'verification_requested'
  plan_id: string
  step_id: string
  challenge_id: string
  sent_to: string
  expires_at: string
}
export type ReversalOffered = {
  type: 'reversal_offered'
  plan_id: string
  steps: { step_id: string; operation: string; input: Record<string, unknown> }[]
}
export type ClarificationQuestion = {
  type: 'clarification_question'
  text: string
  options: { label: string; value: string }[]
}
export type SessionLocked = {
  type: 'session_locked'
  trigger: string
  locked_until: string
  text: string
}
export type HandedOver = { type: 'handed_over'; staff: string }
export type HandedBack = { type: 'handed_back' }
// Emitted since #34. Never carries a token, password or verification code.
export type AuditEntry = {
  entry_id: number
  at: string
  event: string
  operation: string | null
  input: unknown
  tier: Tier | null
  outcome: 'ok' | 'refused'
  reason: string | null
  detail: Record<string, unknown>
}
export type Trace = { type: 'trace' } & (
  | {
      stage: 'intent'
      // `declined`: the Planner declined the request instead of planning it.
      data: { intent: string; declined?: { reason: Refusal['reason']; can_do: string[] } }
    }
  | { stage: 'plan'; data: { steps: PlanStep[] } }
  | { stage: 'bank_request'; data: { method: string; path: string; body: unknown } }
  | { stage: 'bank_response'; data: { method: string; path: string; status: number; body: unknown } }
  | { stage: 'audit_trail'; data: { session_id: string; entries: AuditEntry[] } }
)

export type ConversationEvent =
  | StepStarted
  | StepSucceeded
  | Answer
  | PlanProposed
  | ConfirmationRequested
  | VerificationRequested
  | StepFailed
  | ReversalOffered
  | ClarificationQuestion
  | Refusal
  | SessionLocked
  | HandedOver
  | HandedBack
  | Trace

// A conversation's history, as Staff Members read it (GET /staff/conversations/{id}):
// each Customer message, then every event sent, the decision trace included.
export type CustomerMessageEntry = { type: 'customer_message'; text: string }
export type HistoryEntry = CustomerMessageEntry | ConversationEvent
