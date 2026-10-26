import type { Declined, Trace } from './events'

// What a decision-trace record says, in words: for the decision trace panel and the staff
// console alike, which lay it out differently.

/** A declined request: what the Agent offered instead, or the attempt it reported. */
export function describeDeclined(declined: Declined): string {
  if (declined.reason === 'manipulation') {
    return `已拒绝并报告银行（疑似${declined.kind === 'injection' ? '注入' : '冒充诈骗'}）：“${declined.evidence}”`
  }
  return `已拒绝（${declined.reason}），可改为：${declined.can_do.join('、')}`
}

type IntentData = Extract<Trace, { stage: 'intent' }>['data']

/** What the Agent understood the Customer wants, or what it did instead of planning. */
export function describeIntent(data: IntentData): string {
  if (data.declined) return describeDeclined(data.declined)
  if (data.clarification) return `追问 Asked: ${data.clarification}`
  if (data.ask_for_human) return '转人工 Asked for a human'
  return data.intent || '（未说明）'
}

/** A Bank request or response body, as it was sent. */
export function json(value: unknown): string {
  return JSON.stringify(value, null, 2)
}
