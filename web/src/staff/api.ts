// The staff console's calls. Conversations and their history come from the Agent with the
// Agent's staff key; audit trails and Safety Locks come from the Bank with the Bank's
// admin key, directly: the Agent never holds the Bank's admin key (ADR-0001).
import type { HistoryEntry } from '../events'

export type StaffKeys = { agentStaffKey: string; bankAdminKey: string }

export type Customer = { customer_id: string; name: string }

export type StaffConversation = {
  conversation_id: string
  session_id: string
  customer: Customer
}

export type ConversationHistory = StaffConversation & { entries: HistoryEntry[] }

export type SafetyLock =
  | { session_id: string; locked: false }
  | { session_id: string; locked: true; trigger: string; locked_at: string; locked_until: string }

/** One entry of the Bank's audit trail, as a Staff Member sees it. */
export type AuditEntry = {
  entry_id: number
  session_id: string
  at: string
  event: string
  operation: string | null
  input: unknown
  tier: 'green' | 'yellow' | 'red' | null
  outcome: 'ok' | 'refused'
  reason: string | null
  detail: Record<string, unknown>
}

async function read<T>(response: Response): Promise<T> {
  const body = await response.json()
  if (!response.ok) {
    throw new Error(body.refusal?.message ?? body.error ?? `HTTP ${response.status}`)
  }
  return body
}

function agent<T>(keys: StaffKeys, url: string): Promise<T> {
  return fetch(`/agent${url}`, { headers: { 'X-Staff-Key': keys.agentStaffKey } }).then(read<T>)
}

function bankAdmin<T>(keys: StaffKeys, url: string, method = 'GET'): Promise<T> {
  return fetch(`/bank/admin${url}`, {
    method,
    headers: { 'X-Admin-Key': keys.bankAdminKey },
  }).then(read<T>)
}

export async function activeConversations(keys: StaffKeys): Promise<StaffConversation[]> {
  return (await agent<{ conversations: StaffConversation[] }>(keys, '/staff/conversations'))
    .conversations
}

export function conversationHistory(
  keys: StaffKeys,
  conversationId: string,
): Promise<ConversationHistory> {
  return agent(keys, `/staff/conversations/${encodeURIComponent(conversationId)}`)
}

export async function auditTrail(keys: StaffKeys, sessionId: string): Promise<AuditEntry[]> {
  return (await bankAdmin<{ entries: AuditEntry[] }>(keys, `/sessions/${sessionId}/audit`))
    .entries
}

export function safetyLock(keys: StaffKeys, sessionId: string): Promise<SafetyLock> {
  return bankAdmin(keys, `/sessions/${sessionId}/safety-lock`)
}

export function liftSafetyLock(keys: StaffKeys, sessionId: string): Promise<unknown> {
  return bankAdmin(keys, `/sessions/${sessionId}/safety-lock`, 'DELETE')
}
