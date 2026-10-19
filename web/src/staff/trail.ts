// One session's full audit trail: the Agent's history (messages and every event sent,
// decision trace included) merged with the Bank's audit trail. The Bank clock can stand
// still, so the two can't be merged by time. Instead the Agent's entries keep their
// order, and each Bank entry is placed right after the Agent's `bank_request` trace that
// caused it; Bank entries no Agent call caused (code attempts from the simulated phone,
// Safety Locks, lifts) keep their place among the Bank's entries.
// See docs/conversation-events.md, "The staff interface".
import type { HistoryEntry } from '../events'
import type { AuditEntry } from './api'

export type TrailItem =
  | { source: 'agent'; entry: HistoryEntry }
  | { source: 'bank'; entry: AuditEntry }

type BankEvent = { event: string; operation?: string }

/** The Bank audit entry an Agent call to the Bank leaves, or null if it leaves none. A
 * ticket that adds a new kind of Bank call adds it here. */
function bankEventFor({ method, path }: { method: string; path: string }): BankEvent | null {
  if (method !== 'POST') return null
  if (path === '/tier-preview') return { event: 'tier_preview' }
  if (path === '/confirmations') return { event: 'confirmation_issued' }
  if (path === '/verifications') return { event: 'verification_challenge' }
  const operation = /^\/operations\/([^/]+)$/.exec(path)?.[1]
  if (operation) return { event: 'operation', operation }
  return null
}

function bankRequest(entry: HistoryEntry) {
  return entry.type === 'trace' && entry.stage === 'bank_request' ? entry.data : null
}

export function mergeTrail(agentEntries: HistoryEntry[], bankEntries: AuditEntry[]): TrailItem[] {
  const merged: TrailItem[] = []
  let next = 0 // the first Bank entry not yet placed
  for (const entry of agentEntries) {
    merged.push({ source: 'agent', entry })
    const request = bankRequest(entry)
    const expected = request && bankEventFor(request)
    if (!expected) continue
    const caused = bankEntries.findIndex(
      (b, i) =>
        i >= next &&
        b.event === expected.event &&
        (expected.operation === undefined || b.operation === expected.operation),
    )
    if (caused === -1) continue
    // Bank entries before the one this call caused happened before the call.
    const before = bankEntries.slice(next, caused).map((b) => ({ source: 'bank' as const, entry: b }))
    merged.splice(merged.length - 1, 0, ...before)
    merged.push({ source: 'bank', entry: bankEntries[caused] })
    next = caused + 1
  }
  for (const after of bankEntries.slice(next)) merged.push({ source: 'bank', entry: after })
  return merged
}
