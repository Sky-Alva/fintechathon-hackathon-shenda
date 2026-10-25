import { useEffect, useState } from 'react'
import { fetchAuditTrail } from './api'
import { describeDeclined } from './declined'
import type { AuditEntry, PlanStep, Trace } from './events'

/** One Customer turn and the `trace` events it streamed, in order. */
export type TraceTurn = { text: string; traces: Trace[] }

type Props = {
  conversationId: string
  /** The Customer's Bank session token: the Agent shows the audit trail to it only. */
  sessionToken: string
  turns: TraceTurn[]
  /** How many turns have finished: the audit trail is re-read after each. */
  finishedTurns: number
  onClose: () => void
}

/** The decision trace: what the Agent understood, planned and was told by the Bank, per
 * turn, and the session's audit trail as the Bank recorded it. For judges; hidden by
 * default. Everything shown comes from `trace` events, which never carry a token or code. */
export default function TracePanel({
  conversationId,
  sessionToken,
  turns,
  finishedTurns,
  onClose,
}: Props) {
  const [audit, setAudit] = useState<AuditEntry[] | null>(null)
  const [auditError, setAuditError] = useState(false)

  // Re-read the audit trail when the panel opens and after each turn.
  useEffect(() => {
    let current = true
    fetchAuditTrail(conversationId, sessionToken)
      .then((entries) => {
        if (current) {
          setAudit(entries)
          setAuditError(false)
        }
      })
      .catch(() => current && setAuditError(true))
    return () => {
      current = false
    }
  }, [conversationId, sessionToken, finishedTurns])

  return (
    <aside className="trace-panel" aria-label="决策追踪 Decision trace">
      <header>
        <span>决策追踪 Decision trace</span>
        <button type="button" className="link" onClick={onClose}>
          关闭
        </button>
      </header>
      <div className="trace-body">
        {turns.length === 0 && <p className="muted">发送一条消息后，这里会显示每一轮的决策过程。</p>}
        {turns.map((turn, i) => (
          <section key={i} className="trace-turn">
            <h3>
              第 {i + 1} 轮：{turn.text}
            </h3>
            {turn.traces.map((trace, j) => (
              <TraceItem key={j} trace={trace} />
            ))}
          </section>
        ))}
        <section className="trace-turn">
          <h3>银行审计记录 Bank audit trail</h3>
          {auditError && <p className="muted">无法读取审计记录。</p>}
          {audit && <AuditTable entries={audit} />}
        </section>
      </div>
    </aside>
  )
}

function TraceItem({ trace }: { trace: Trace }) {
  switch (trace.stage) {
    case 'intent':
      return (
        <div className="trace-item">
          <span className="trace-label">意图 Intent</span>
          <span>
            {trace.data.declined
              ? describeDeclined(trace.data.declined)
              : trace.data.clarification
                ? `追问 Asked: ${trace.data.clarification}`
                : trace.data.ask_for_human
                  ? '转人工 Asked for a human'
                  : trace.data.intent || '（未说明）'}
          </span>
        </div>
      )
    case 'plan':
      return (
        <div className="trace-item">
          <span className="trace-label">计划 Plan</span>
          <PlanGraph steps={trace.data.steps} />
        </div>
      )
    case 'bank_request':
      return (
        <details className="trace-item">
          <summary>
            <span className="trace-label">→ 银行请求</span> {trace.data.method} {trace.data.path}
          </summary>
          <pre>{json(trace.data.body)}</pre>
        </details>
      )
    case 'bank_response':
      return (
        <details className="trace-item">
          <summary>
            <span className="trace-label">← 银行响应</span> {trace.data.status} {trace.data.path}
            {isRefusal(trace.data.body) && <span className="refused">拒绝</span>}
          </summary>
          <pre>{json(trace.data.body)}</pre>
        </details>
      )
    case 'audit_trail':
      return <AuditTable entries={trace.data.entries} />
  }
}

function PlanGraph({ steps }: { steps: PlanStep[] }) {
  return (
    <ol className="plan-graph">
      {steps.map((step) => (
        <li key={step.id}>
          <span className={`tier ${step.tier ?? 'none'}`}>{step.tier ?? '—'}</span>
          <code>{step.id}</code> {step.operation}
          {step.depends_on.length > 0 && (
            <span className="muted"> ← {step.depends_on.join(', ')}</span>
          )}
          {Object.keys(step.input).length > 0 && <pre>{json(step.input)}</pre>}
        </li>
      ))}
    </ol>
  )
}

function AuditTable({ entries }: { entries: AuditEntry[] }) {
  if (entries.length === 0) return <p className="muted">暂无记录。</p>
  return (
    <table className="audit">
      <thead>
        <tr>
          <th>#</th>
          <th>事件</th>
          <th>操作</th>
          <th>等级</th>
          <th>结果</th>
        </tr>
      </thead>
      <tbody>
        {entries.map((entry) => (
          <tr key={entry.entry_id} title={json({ input: entry.input, detail: entry.detail })}>
            <td>{entry.entry_id}</td>
            <td>{entry.event}</td>
            <td>{entry.operation ?? '—'}</td>
            <td>
              {entry.tier && <span className={`tier ${entry.tier}`}>{entry.tier}</span>}
            </td>
            <td className={entry.outcome === 'refused' ? 'refused' : ''}>
              {entry.outcome === 'refused' ? entry.reason : 'ok'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function isRefusal(body: unknown): boolean {
  return typeof body === 'object' && body !== null && 'refusal' in body
}

function json(value: unknown): string {
  return JSON.stringify(value, null, 2)
}
