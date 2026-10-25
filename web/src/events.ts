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

// Emitted since #40, after the step_succeeded of a chartable step: `data` is the Bank's
// output for the step, unchanged (amounts are the Bank's decimal strings), and the Channel
// draws only those numbers. `title` is in the Customer's language.
export type CategoryTotal = { category: string; amount: string; transaction_count: number }
export type ReportSeries = { category: string; values: string[] }
export type SpendingByCategory = {
  start_date: string | null
  end_date: string | null
  total_spending: string
  categories: CategoryTotal[]
}
export type MonthlyReport = {
  year: number
  month: number
  labels: string[]
  categories: CategoryTotal[]
  total_income: string
  total_spending: string
  net: string
  series: ReportSeries[]
}
export type AnnualReport = {
  year: number
  labels: string[]
  monthly_income: string[]
  monthly_spending: string[]
  monthly_net: string[]
  series: ReportSeries[]
  total_income: string
  total_spending: string
  net: string
}
export type Chart = { type: 'chart'; step_id: string; title: string } & (
  | { operation: 'spending_by_category'; data: SpendingByCategory }
  | { operation: 'monthly_report'; data: MonthlyReport }
  | { operation: 'annual_report'; data: AnnualReport }
)
// Emitted for 'out_of_scope' since #33, 'manipulation' since #43 and 'secret' since #44.
export type Refusal = {
  type: 'refusal'
  reason: 'out_of_scope' | 'manipulation' | 'secret'
  text: string
}

// Emitted since #47: a step's Bank result holds instruction-like text (a Payee note giving
// orders). The Agent ignores it and carries on; `reported` says whether the Bank took the report.
export type InjectionWarning = {
  type: 'injection_warning'
  step_id: string
  operation: string
  reported: boolean
  text: string
}

// Emitted since #31: the Plan card and each step's outcome.
// Why a Red step needs Strong Verification, in the Customer's language (#37).
export type PlanWarning = {
  step_id: string
  reason: 'daily_transfer_total' | 'strong_verification'
  text: string
}
// Who a step pays, as the Bank's Payee lookup found them (#39); `is_new`: never paid before.
export type CardPayee = {
  step_id: string
  payee_id: string
  name: string
  nickname: string | null
  phone: string | null
  is_new: boolean
}
export type PlanProposed = {
  type: 'plan_proposed'
  plan_id: string
  steps: PlanStep[]
  refusals: (BankRefusal & { step_id: string })[]
  warnings: PlanWarning[]
  payees: CardPayee[]
}
export type ConfirmationRequested = {
  type: 'confirmation_requested'
  plan_id: string
  step_ids: string[]
}
// Emitted since #38: the Customer tapped stop. `ran` / `not_run` are card step IDs; `text`
// names them by their number on the card. Answered with resume or cancel, or a new message.
export type PlanPaused = {
  type: 'plan_paused'
  plan_id: string
  ran: string[]
  not_run: string[]
  text: string
}
// `reason` is the Bank's refusal reason, or 'bank_unavailable' when the Agent couldn't
// reach the Bank (#32); `text` explains it in plain words, from `reason` alone (#42).
export type StepFailed = BankRefusal & {
  type: 'step_failed'
  step_id: string
  operation: string
  text: string
}

// Emitted since #37: a Red step waits for the code typed into the simulated phone, and
// the Safety Lock notice (ends the turn).
export type VerificationRequested = {
  type: 'verification_requested'
  plan_id: string
  step_id: string
  challenge_id: string
  sent_to: string
  expires_at: string
}
export type SessionLocked = {
  type: 'session_locked'
  trigger: 'wrong_codes' | 'reported_attempts' | 'red_operations'
  locked_until: string
  text: string
}

// Emitted since #42: a step of the Plan failed; undo its completed steps (last-run first)?
// Answered with reverse (a new Plan card) or reject, or dropped by a new message.
export type ReversalOffered = {
  type: 'reversal_offered'
  plan_id: string
  steps: { step_id: string; operation: string; input: Record<string, unknown> }[]
  text: string
}

// Emitted since #39: one short question (which 张伟? how much?). Answered with `choose`
// and an option's value, or in words. Options never carry a full account or phone number.
export type ClarificationQuestion = {
  type: 'clarification_question'
  text: string
  options: { label: string; value: string }[]
}
// Emitted since #46. `human_requested` ends the Customer's turn; the takeover events
// arrive outside the Customer's turns, from GET /conversations/{id}/updates.
export type HumanRequested = { type: 'human_requested'; text: string }
export type HandedOver = { type: 'handed_over'; staff: string }
export type StaffMessage = { type: 'staff_message'; staff: string; text: string }
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
export type Declined =
  | { reason: 'out_of_scope' | 'secret'; can_do: string[] }
  | { reason: 'manipulation'; kind: 'impersonation' | 'injection'; evidence: string }
export type Trace = { type: 'trace' } & (
  | {
      stage: 'intent'
      // `declined`: the Planner (or the manipulation guard) declined the request instead
      // of planning it: what it offered instead, or the suspected attempt it reported.
      // `clarification`: the question the Planner asked instead of planning (#39).
      data: { intent: string; declined?: Declined; clarification?: string }
    }
  | { stage: 'plan'; data: { steps: PlanStep[] } }
  | { stage: 'bank_request'; data: { method: string; path: string; body: unknown } }
  | { stage: 'bank_response'; data: { method: string; path: string; status: number; body: unknown } }
  | { stage: 'audit_trail'; data: { session_id: string; entries: AuditEntry[] } }
)

export type ConversationEvent =
  | StepStarted
  | StepSucceeded
  | Chart
  | Answer
  | PlanProposed
  | ConfirmationRequested
  | PlanPaused
  | VerificationRequested
  | StepFailed
  | ReversalOffered
  | ClarificationQuestion
  | Refusal
  | InjectionWarning
  | SessionLocked
  | HumanRequested
  | HandedOver
  | StaffMessage
  | HandedBack
  | Trace

// A conversation's history, as Staff Members read it (GET /staff/conversations/{id}):
// each Customer message or action, then every event sent, the decision trace included.
export type CustomerMessageEntry = { type: 'customer_message'; text: string }
// `plan_id` is null for an action that answers no Plan card (`ask_for_human`).
export type CustomerActionEntry = {
  type: 'customer_action'
  action: string
  plan_id: string | null
}
export type HistoryEntry = CustomerMessageEntry | CustomerActionEntry | ConversationEvent
