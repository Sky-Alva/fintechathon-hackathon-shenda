// One session's full audit trail: the Agent's history (messages, Plans, events sent)
// merged with the Bank's audit trail. The Bank clock can stand still, so the two can't be
// merged by time. Instead the Agent's entries keep their order, and each Bank entry is
// placed at the Agent entry that caused it; Bank entries nothing on the Agent's side
// caused (code attempts from the simulated phone, Safety Locks, lifts) keep their place
// among the Bank's entries. See docs/conversation-events.md, "The staff interface".
import type { HistoryEntry } from '../events'
import type { AuditEntry } from './api'

export type TrailItem =
  | { source: 'agent'; entry: HistoryEntry }
  | { source: 'bank'; entry: AuditEntry }

/** The Bank audit event each Agent entry causes. A ticket that adds a Bank call to a
 * turn adds its pair here. */
const BANK_EVENT_FOR: Partial<Record<HistoryEntry['type'], string>> = {
  plan: 'tier_preview',
  step_started: 'operation',
  confirmation_requested: 'confirmation_issued',
  verification_requested: 'verification_challenge',
}

function causes(agentEntry: HistoryEntry, bankEntry: AuditEntry): boolean {
  if (BANK_EVENT_FOR[agentEntry.type] !== bankEntry.event) return false
  if (agentEntry.type === 'step_started') return agentEntry.step.operation === bankEntry.operation
  return true
}

export function mergeTrail(agentEntries: HistoryEntry[], bankEntries: AuditEntry[]): TrailItem[] {
  const merged: TrailItem[] = []
  let next = 0 // the first Bank entry not yet placed
  for (const entry of agentEntries) {
    const caused = bankEntries.findIndex((b, i) => i >= next && causes(entry, b))
    if (caused === -1) {
      merged.push({ source: 'agent', entry })
      continue
    }
    // Bank entries before the one this caused happened before it.
    for (const before of bankEntries.slice(next, caused)) {
      merged.push({ source: 'bank', entry: before })
    }
    merged.push({ source: 'agent', entry }, { source: 'bank', entry: bankEntries[caused] })
    next = caused + 1
  }
  for (const after of bankEntries.slice(next)) merged.push({ source: 'bank', entry: after })
  return merged
}
