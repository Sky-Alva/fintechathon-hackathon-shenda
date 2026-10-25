// The staff console (/staff): the active sessions with their Safety Lock status and
// requests for a human, a session's full audit trail, lifting a Safety Lock, and taking a
// conversation over from the Agent, replying and handing it back. It only displays what the
// Agent and the Bank return, so it is checked by hand.
import { type FormEvent, useCallback, useEffect, useState } from 'react'
import { describeDeclined } from '../declined'
import type { HistoryEntry, Trace } from '../events'
import {
  type AuditEntry,
  type SafetyLock,
  type StaffConversation,
  type StaffKeys,
  activeConversations,
  auditTrail,
  conversationHistory,
  handBack,
  liftSafetyLock,
  safetyLock,
  staffReply,
  takeOver,
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
  const [staffName, setStaffName] = useState('')

  function submit(event: FormEvent) {
    event.preventDefault()
    onSubmit({ agentStaffKey, bankAdminKey, staffName: staffName.trim() })
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
      <label>
        您的名字（接管对话时客户看到的称呼）
        <input value={staffName} onChange={(e) => setStaffName(e.target.value)} />
      </label>
      <button
        type="submit"
        className="primary"
        disabled={!agentStaffKey || !bankAdminKey || !staffName.trim()}
      >
        进入
      </button>
    </form>
  )
}

function TraceEntry({ entry }: { entry: Trace }) {
  switch (entry.stage) {
    case 'intent':
      return entry.data.declined ? (
        <>
          <b>理解意图</b> {describeDeclined(entry.data.declined)}
        </>
      ) : (
        <>
          <b>理解意图</b> {entry.data.intent}
        </>
      )
    case 'plan':
      return (
        <>
          <b>Plan</b>
          <ol className="plan">
            {entry.data.steps.map((step) => (
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
    case 'bank_request':
      return (
        <>
          <b>请求银行</b> <code>
            {entry.data.method} {entry.data.path}
          </code>
          <details>
            <summary>请求内容</summary>
            <pre>{json(entry.data.body)}</pre>
          </details>
        </>
      )
    case 'bank_response':
      return (
        <>
          <b>银行响应</b> <code>
            {entry.data.status} {entry.data.path}
          </code>
          <details>
            <summary>原始响应</summary>
            <pre>{json(entry.data.body)}</pre>
          </details>
        </>
      )
    default:
      return (
        <>
          <b>trace · {entry.stage}</b>
          <details>
            <summary>内容</summary>
            <pre>{json(entry.data)}</pre>
          </details>
        </>
      )
  }
}

const ACTION_LABELS: Record<string, string> = {
  confirm: '确认',
  reject: '拒绝',
  stop: '停止',
  resume: '继续',
  cancel: '取消剩余',
  reverse: '撤销已完成步骤：',
}

function AgentEntry({ entry }: { entry: HistoryEntry }) {
  switch (entry.type) {
    case 'trace':
      return <TraceEntry entry={entry} />
    case 'customer_message':
      return (
        <>
          <b>客户</b> {entry.text}
        </>
      )
    case 'customer_action':
      if (entry.action === 'ask_for_human')
        return (
          <>
            <b>客户操作</b> 请求人工客服
          </>
        )
      if (entry.action === 'verified' || entry.action === 'wrong_code')
        return (
          <>
            <b>客户操作</b> {entry.action === 'verified' ? '短信验证通过' : '验证码错误'}，计划{' '}
            <code>{entry.plan_id}</code>
          </>
        )
      return (
        <>
          <b>客户操作</b> {ACTION_LABELS[entry.action] ?? entry.action}计划{' '}
          <code>{entry.plan_id}</code>
        </>
      )
    case 'plan_paused':
      return (
        <>
          <b>计划已暂停</b> 已执行 {entry.ran.join('、') || '无'}；未执行 {entry.not_run.join('、')}
        </>
      )
    case 'answer':
      return (
        <>
          <b>Agent 回答</b> {entry.text}
        </>
      )
    case 'step_failed':
      return (
        <>
          <b>步骤失败</b> <code>{entry.step_id}</code> {entry.operation}{' '}
          <span className="refused">
            {entry.reason}：{entry.message}
          </span>{' '}
          {entry.text}
        </>
      )
    case 'reversal_offered':
      return (
        <>
          <b>提议撤销</b> 计划 <code>{entry.plan_id}</code>：
          {entry.steps.map((step) => `${step.step_id} → ${step.operation}`).join('，')}
          <details>
            <summary>内容</summary>
            <pre>{json(entry.steps)}</pre>
          </details>
        </>
      )
    case 'refusal':
      return (
        <>
          <b>Agent 拒绝（{entry.reason}）</b> {entry.text}
        </>
      )
    case 'verification_requested':
      return (
        <>
          <b>短信验证</b> <code>{entry.step_id}</code> 验证码已发送至 {entry.sent_to}（
          <code>{entry.challenge_id}</code>）
        </>
      )
    case 'injection_warning':
      return (
        <>
          <b className="locked">银行数据含可疑指令</b> <code>{entry.step_id}</code> {entry.operation}
          （{entry.reported ? '已报告银行' : '未能报告'}）：{entry.text}
        </>
      )
    case 'session_locked':
      return (
        <>
          <b className="locked">安全锁定（{entry.trigger}）</b> 至 {entry.locked_until.slice(11, 16)}：
          {entry.text}
        </>
      )
    case 'human_requested':
      return (
        <>
          <b className="locked">客户请求人工</b> {entry.text}
        </>
      )
    case 'handed_over':
      return (
        <>
          <b>人工接管</b> {entry.staff} 接管了对话，Agent 暂停
        </>
      )
    case 'staff_message':
      return (
        <>
          <b>人工客服 {entry.staff}</b> {entry.text}
        </>
      )
    case 'handed_back':
      return (
        <>
          <b>交还 Agent</b> Agent 恢复服务
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
        <li
          key={i}
          className={item.source === 'agent' && item.entry.type === 'trace' ? 'trace' : item.source}
        >
          <span className="source">{item.source === 'agent' ? 'Agent' : '银行审计'}</span>
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
  onChanged,
}: {
  keys: StaffKeys
  conversation: StaffConversation
  lock: SafetyLock | undefined
  /** The session changed (lock lifted, taken over, handed back): refresh the list. */
  onChanged: () => void
}) {
  const [items, setItems] = useState<TrailItem[]>([])
  const [error, setError] = useState<string | null>(null)
  const [lifting, setLifting] = useState(false)
  const [handing, setHanding] = useState(false)
  const [reply, setReply] = useState('')

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
      onChanged()
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : '解除失败')
    } finally {
      setLifting(false)
    }
  }

  /** A takeover call, then fresh history and session list. */
  async function hand(call: () => Promise<unknown>) {
    setHanding(true)
    try {
      await call()
      onChanged()
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败')
    } finally {
      setHanding(false)
    }
  }

  async function sendReply(event: FormEvent) {
    event.preventDefault()
    const text = reply.trim()
    if (!text) return
    await hand(() => staffReply(keys, conversation.conversation_id, text))
    setReply('')
  }

  const handedOver = conversation.handed_over_to !== null

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
        {handedOver ? (
          <button
            type="button"
            className="secondary"
            onClick={() => hand(() => handBack(keys, conversation.conversation_id))}
            disabled={handing}
          >
            交还 Agent
          </button>
        ) : (
          <button
            type="button"
            className="primary"
            onClick={() => hand(() => takeOver(keys, conversation.conversation_id))}
            disabled={handing}
          >
            接管对话
          </button>
        )}
      </header>
      {handedOver && (
        <p className="muted">
          {conversation.handed_over_to} 正在接管：Agent 已暂停，客户的消息由您回复。
        </p>
      )}
      {error && <p className="error">{error}</p>}
      <Trail items={items} />
      {handedOver && (
        <form className="staff-reply" onSubmit={sendReply}>
          <input
            value={reply}
            onChange={(e) => setReply(e.target.value)}
            placeholder={`以 ${conversation.handed_over_to} 的身份回复客户`}
          />
          <button type="submit" className="primary" disabled={handing || !reply.trim()}>
            发送
          </button>
        </form>
      )}
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
                {c.human_requested && <span className="locked">请求人工</span>}
                {c.handed_over_to && (
                  <span className="muted">人工接管中 · {c.handed_over_to}</span>
                )}
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
            onChanged={() => void refresh()}
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
