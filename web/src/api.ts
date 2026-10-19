import type { AuditEntry, ConversationEvent } from './events'

/** What the Channel holds after logging the Customer in with the Bank. The phone token
 * stays here, for the simulated phone: it is never sent to the Agent. */
export type BankLogin = {
  session_id: string
  session_token: string
  phone_token: string
  customer: { customer_id: string; name: string }
}

export async function logIn(username: string, password: string): Promise<BankLogin> {
  const response = await fetch('/bank/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  const body = await response.json()
  if (!response.ok) throw new Error(body.refusal?.message ?? '登录失败')
  return body
}

/** Hand the Agent the session token, and only that. */
export async function startConversation(login: BankLogin): Promise<string> {
  const response = await fetch('/agent/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_token: login.session_token }),
  })
  if (!response.ok) throw new Error('无法连接智能助手')
  return (await response.json()).conversation_id
}

/** The session's audit trail as the Bank recorded it, for the decision trace panel. */
export async function fetchAuditTrail(conversationId: string): Promise<AuditEntry[]> {
  const response = await fetch(`/agent/conversations/${conversationId}/audit-trail`)
  if (!response.ok) throw new Error('无法读取审计记录')
  const event = await response.json()
  return event.data.entries
}

/** Send a Customer message; `onEvent` gets each event of the turn as it streams in. */
export async function sendMessage(
  conversationId: string,
  text: string,
  onEvent: (event: ConversationEvent) => void,
): Promise<void> {
  await streamTurn(`/agent/conversations/${conversationId}/messages`, { text }, onEvent)
}

/** The Customer's answer to a prompt, e.g. a tap on a Plan card. */
export type CustomerAction =
  // `removed_step_ids`: steps the Customer took off the card; the Bank re-tiers the rest.
  | { action: 'confirm'; plan_id: string; removed_step_ids?: string[] }
  | { action: 'reject'; plan_id: string }
  | { action: 'ask_for_human' }
  // The stop button, sent alongside the running Plan's turn: that turn ends with plan_paused.
  | { action: 'stop'; plan_id: string }
  | { action: 'resume'; plan_id: string }
  | { action: 'cancel'; plan_id: string }

/** Send a Customer action; `onEvent` gets each event of the turn as it streams in. */
export async function sendAction(
  conversationId: string,
  action: CustomerAction,
  onEvent: (event: ConversationEvent) => void,
): Promise<void> {
  await streamTurn(`/agent/conversations/${conversationId}/actions`, action, onEvent)
}

/** What the Customer was sent outside their own turns (a Staff Member taking over,
 * replying, handing back), from the `after`-th on; poll again with `next`. */
export async function fetchUpdates(
  conversationId: string,
  after: number,
): Promise<{ events: ConversationEvent[]; next: number }> {
  const response = await fetch(`/agent/conversations/${conversationId}/updates?after=${after}`)
  if (!response.ok) throw new Error('无法读取新消息')
  return response.json()
}

async function streamTurn(
  url: string,
  body: unknown,
  onEvent: (event: ConversationEvent) => void,
): Promise<void> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok || !response.body) throw new Error('消息发送失败')
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffered = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffered += value
    const lines = buffered.split('\n')
    buffered = lines.pop() ?? ''
    for (const line of lines) if (line.trim()) onEvent(JSON.parse(line))
  }
  if (buffered.trim()) onEvent(JSON.parse(buffered))
}
