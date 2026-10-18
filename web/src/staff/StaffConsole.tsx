// The staff console (/staff): the active sessions with their Safety Lock status, a
// session's full audit trail, and lifting a Safety Lock. It only displays what the
// Agent and the Bank return, so it is checked by hand.
import { type FormEvent, useCallback, useEffect, useState } from 'react'
import type { HistoryEntry } from '../events'
import {
  type AuditEntry,
  type SafetyLock,
  type StaffConversation,
  type StaffKeys,
  activeConversations,
  auditTrail,
  conversationHistory,
  liftSafetyLock,
  safetyLock,
} from './api'
import { type TrailItem, mergeTrail } from './trail'
import './staff.css'

const REFRESH_MS = 3000

const TRIGGERS: Record<string, string> = {
  wrong_codes: '验证码多次错误',
  reported_attempts: 'Agent 多次上报可疑行为',
  red_operations: '10 分钟内 Red 操作过多',
}

/** Run `poll` now and every few seconds while mounted. */
function usePolling(poll: () => Promise<void>) {
  useEffect(() => {
    const first = setTimeout(() => void poll(), 0)
    const timer = setInterval(() => void poll(), REFRESH_MS)
    return () => {
      clearTimeout(first)
      clearInterval(timer)
    }
  }, [poll])
}

function time(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

function json(value: unknown): string {
  return JSON.stringify(value, null, 2)
}

function Tier({ tier }: { tier: string | null | undefined }) {
  return tier ? <span className={`tier ${tier}`}>{tier}</span> : null
}

function LockStatus({ lock }: { lock: SafetyLock | undefined }) {
  if (!lock) return <span className="muted">…</span>
  if (!lock.locked) return <span className="unlocked">未锁定</span>
  return (
    <span className="locked">
      安全锁 · {TRIGGERS[lock.trigger] ?? lock.trigger} · 至 {time(lock.locked_until)}
    </span>
  )
}

function KeysForm({ onSubmit }: { onSubmit: (keys: StaffKeys) => void }) {
  const [agentStaffKey, setAgentStaffKey] = useState('')
  const [bankAdminKey, setBankAdminKey] = useState('')

  function submit(event: FormEvent) {
    event.preventDefault()
    onSubmit({ agentStaffKey, bankAdminKey })
  }

  return (
    <form className="staff-keys" onSubmit={submit}>
      <h1>员工控制台</h1>
      <p className="muted">
        Agent 的员工密钥用于读取会话与对话记录；银行管理密钥用于读取审计记录和解除安全锁，
        只发给银行，Agent 从不持有。
      </p>
      <label>
        Agent 员工密钥（AGENT_STAFF_KEY）
        <input
          type="password"
          value={agentStaffKey}
          onChange={(e) => setAgentStaffKey(e.target.value)}
        />
      </label>
      <label>
        银行管理密钥（BANK_ADMIN_KEY）
        <input
          type="password"
          value={bankAdminKey}
          onChange={(e) => setBankAdminKey(e.target.value)}
        />
      </label>
      <button type="submit" className="primary" disabled={!agentStaffKey || !bankAdminKey}>
        进入
      </button>
    </form>
  )
}

function AgentEntry({ entry }: { entry: HistoryEntry }) {
  switch (entry.type) {
    case 'customer_message':
      return (
        <>
          <b>客户</b> {entry.text}
        </>
      )
    case 'answer':
      return (
        <>
          <b>Agent 回答</b> {entry.text}
        </>
      )
    case 'plan':
      return (
        <>
          <b>Plan</b>
          <ol className="plan">
            {entry.steps.map((step) => (
              <li key={step.id}>
                <code>{step.id}</code> {step.operation} <Tier tier={step.tier} />{' '}
                <code>{JSON.stringify(step.input)}</code>
                {step.depends_on.length > 0 && (
                  <span className="muted"> · 依赖 {step.depends_on.join(', ')}</span>
                )}
              </li>
            ))}
          </ol>
        </>
      )
    case 'step_started':
      return (
        <>
          <b>开始执行</b> <code>{entry.step.id}</code> {entry.step.operation}{' '}
          <Tier tier={entry.step.tier} />
        </>
      )
    case 'step_succeeded':
      return (
        <>
          <b>步骤成功</b> <code>{entry.step_id}</code> {entry.operation} <Tier tier={entry.tier} />
          <details>
            <summary>银行返回</summary>
            <pre>{json(entry.output)}</pre>
          </details>
        </>
      )
    default:
      return (
        <>
          <b>{entry.type}</b>
          <details>
            <summary>内容</summary>
            <pre>{json(entry)}</pre>
          </details>
        </>
      )
  }
}

function BankEntry({ entry }: { entry: AuditEntry }) {
  return (
    <>
      <b>{entry.event}</b> {entry.operation} <Tier tier={entry.tier} />{' '}
      <span className={entry.outcome === 'ok' ? 'ok' : 'refused'}>
        {entry.outcome === 'ok' ? 'ok' : `拒绝：${entry.reason}`}
      </span>{' '}
      <span className="muted">{time(entry.at)}（银行时钟）</span>
      <details>
        <summary>详情</summary>
        <pre>{json({ input: entry.input, detail: entry.detail })}</pre>
      </details>
    </>
  )
}

function Trail({ items }: { items: TrailItem[] }) {
  if (items.length === 0) return <p className="muted">还没有记录。</p>
  return (
    <ol className="trail">
      {items.map((item, i) => (
        <li key={i} className={item.source}>
          <span className="source">{item.source === 'agent' ? 'Agent' : '银行'}</span>
          <div>
            {item.source === 'agent' ? (
              <AgentEntry entry={item.entry} />
            ) : (
              <BankEntry entry={item.entry} />
            )}
          </div>
        </li>
      ))}
    </ol>
  )
}

function SessionView({
  keys,
  conversation,
  lock,
  onLifted,
}: {
  keys: StaffKeys
  conversation: StaffConversation
  lock: SafetyLock | undefined
  onLifted: () => void
}) {
  const [items, setItems] = useState<TrailItem[]>([])
  const [error, setError] = useState<string | null>(null)
  const [lifting, setLifting] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [history, audit] = await Promise.all([
        conversationHistory(keys, conversation.conversation_id),
        auditTrail(keys, conversation.session_id),
      ])
      setItems(mergeTrail(history.entries, audit))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : '读取失败')
    }
  }, [keys, conversation])

  usePolling(refresh)

  async function lift() {
    setLifting(true)
    try {
      await liftSafetyLock(keys, conversation.session_id)
      onLifted()
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : '解除失败')
    } finally {
      setLifting(false)
    }
  }

  return (
    <section className="session-view">
      <header>
        <h2>
          {conversation.customer.name} <span className="muted">{conversation.session_id}</span>
        </h2>
        <LockStatus lock={lock} />
        {lock?.locked && (
          <button type="button" className="primary" onClick={lift} disabled={lifting}>
            {lifting ? '解除中…' : '解除安全锁'}
          </button>
        )}
      </header>
      {error && <p className="error">{error}</p>}
      <Trail items={items} />
    </section>
  )
}

function Console({ keys, onLeave }: { keys: StaffKeys; onLeave: () => void }) {
  const [conversations, setConversations] = useState<StaffConversation[]>([])
  const [locks, setLocks] = useState<Record<string, SafetyLock>>({})
  const [selected, setSelected] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const active = await activeConversations(keys)
      const sessionIds = [...new Set(active.map((c) => c.session_id))]
      const read = await Promise.all(sessionIds.map((id) => safetyLock(keys, id)))
      setConversations(active)
      setLocks(Object.fromEntries(read.map((lock) => [lock.session_id, lock])))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : '读取失败')
    }
  }, [keys])

  usePolling(refresh)

  const current = conversations.find((c) => c.conversation_id === selected)

  return (
    <div className="staff-console">
      <aside>
        <header>
          <h1>员工控制台</h1>
          <button type="button" className="link" onClick={onLeave}>
            退出
          </button>
        </header>
        {error && <p className="error">{error}</p>}
        <h3>活跃会话</h3>
        {conversations.length === 0 && <p className="muted">暂无活跃会话。</p>}
        <ul className="sessions">
          {conversations.map((c) => (
            <li key={c.conversation_id}>
              <button
                type="button"
                className={c.conversation_id === selected ? 'selected' : ''}
                onClick={() => setSelected(c.conversation_id)}
              >
                <span className="customer">{c.customer.name}</span>
                <span className="muted">{c.session_id}</span>
                <LockStatus lock={locks[c.session_id]} />
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <main>
        {current ? (
          <SessionView
            key={current.conversation_id}
            keys={keys}
            conversation={current}
            lock={locks[current.session_id]}
            onLifted={() => void refresh()}
          />
        ) : (
          <p className="muted">选择一个会话查看完整审计记录。</p>
        )}
      </main>
    </div>
  )
}

export default function StaffConsole() {
  const [keys, setKeys] = useState<StaffKeys | null>(null)
  return (
    <div className="staff">
      {keys ? <Console keys={keys} onLeave={() => setKeys(null)} /> : <KeysForm onSubmit={setKeys} />}
    </div>
  )
}
