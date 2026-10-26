// The staff console's calls. Conversations and their history come from the Agent with the
// Agent's staff key; audit trails and Safety Locks come from the Bank with the Bank's
// admin key, directly: the Agent never holds the Bank's admin key (ADR-0001).
import type {
  AuditEntry as SessionAuditEntry,
  HandedBack,
  HandedOver,
  HistoryEntry,
  StaffMessage,
} from '../events'

/** What the Staff Member typed in: the two keys, and their name as Customers see it when
 * they take a conversation over. */
export type StaffKeys = { agentStaffKey: string; bankAdminKey: string; staffName: string }

export type Customer = { customer_id: string; name: string }

export type StaffConversation = {
  conversation_id: string
  session_id: string
  customer: Customer
  /** The Customer asked for a human and no Staff Member has taken over since. */
  human_requested: boolean
  /** The Staff Member who has the conversation (the Agent is paused), or null. */
  handed_over_to: string | null
}

export type ConversationHistory = StaffConversation & { entries: HistoryEntry[] }

export type SafetyLock =
  | { session_id: string; locked: false }
  | { session_id: string; locked: true; trigger: string; locked_at: string; locked_until: string }

/** One entry of the Bank's audit trail, as a Staff Member reads it with the admin key. */
export type AuditEntry = SessionAuditEntry & { session_id: string }

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

function agentPost<T>(keys: StaffKeys, url: string, body?: unknown): Promise<T> {
  return fetch(`/agent${url}`, {
    method: 'POST',
    headers: { 'X-Staff-Key': keys.agentStaffKey, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then(read<T>)
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


/** Take the conversation over from the Agent, which is paused until it is handed back. */
export function takeOver(keys: StaffKeys, conversationId: string): Promise<HandedOver> {
  return agentPost(keys, `/staff/conversations/${encodeURIComponent(conversationId)}/takeover`, {
    staff: keys.staffName,
  })
}

/** Reply to the Customer, as the Staff Member who has the conversation. */
export function staffReply(
  keys: StaffKeys,
  conversationId: string,
  text: string,
): Promise<StaffMessage> {
  return agentPost(keys, `/staff/conversations/${encodeURIComponent(conversationId)}/messages`, {
    text,
  })
}

/** Hand the conversation back to the Agent. */
export function handBack(keys: StaffKeys, conversationId: string): Promise<HandedBack> {
  return agentPost(keys, `/staff/conversations/${encodeURIComponent(conversationId)}/hand-back`)
}
