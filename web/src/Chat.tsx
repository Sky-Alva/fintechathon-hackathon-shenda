import { type FormEvent, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { fetchUpdates, sendAction, sendMessage } from './api'
import type {
  BankRefusal,
  ConversationEvent,
  PlanStep,
  PlanWarning,
  VerificationRequested,
} from './events'
import PlanCard, { type PlanCardStatus } from './PlanCard'
import SimulatedPhone from './SimulatedPhone'
import TracePanel, { type TraceTurn } from './TracePanel'

/** One step's progress line: running, then done or failed (with the Bank's message). */
type StepItem = {
  kind: 'step'
  stepId: string
  operation: string
  status: StepStatus
  failure?: string
}

type Item =
  | { kind: 'customer'; text: string }
  | { kind: 'agent'; text: string }
  | { kind: 'staff'; staff: string; text: string }
  | { kind: 'notice'; text: string }
  // The Safety Lock notice: only queries work until it is lifted or expires.
  | { kind: 'locked'; text: string }
  | StepItem
  | {
      kind: 'plan'
      planId: string
      steps: PlanStep[]
      refusals: (BankRefusal & { step_id: string })[]
      warnings: PlanWarning[]
      confirmIds: string[]
      status: PlanCardStatus
    }
  | { kind: 'error'; text: string }

type StepStatus = 'running' | 'done' | 'failed'

const STEP_LABEL: Record<StepStatus, string> = {
  running: '正在办理',
  done: '已完成',
  failed: '未能完成',
}

type Props = {
  customerName: string
  conversationId: string
  /** For the simulated phone only: it goes to the Bank, never to the Agent. */
  phoneToken: string
  /** Where the simulated phone is shown, beside the Customer's banking app. */
  smsSlot: HTMLElement | null
  onLogOut: () => void
}

/** How often the Channel asks for what a Staff Member sent between the Customer's turns. */
const UPDATES_MS = 2000

/** Six digits on their own: what the Agent takes for a verification code in the chat. */
const CODE_IN_CHAT = /(?<![A-Za-z0-9_.,¥￥-])\d{6}(?![A-Za-z0-9_-])/g

/** Settle the latest progress line for this step that is still running. */
function settle(items: Item[], stepId: string, change: Partial<StepItem>): Item[] {
  const index = items.findLastIndex(
    (item) => item.kind === 'step' && item.stepId === stepId && item.status === 'running',
  )
  if (index < 0) return items
  return items.map((item, i) => (i === index && item.kind === 'step' ? { ...item, ...change } : item))
}

/** Folds one streamed event into the chat. Events this slice doesn't display yet
 * (trace…) are left to the tickets that emit them. */
function apply(items: Item[], event: ConversationEvent): Item[] {
  switch (event.type) {
    case 'step_started':
      return [
        ...items,
        { kind: 'step', stepId: event.step.id, operation: event.step.operation, status: 'running' },
      ]
    case 'step_succeeded':
      return settle(items, event.step_id, { status: 'done' })
    case 'step_failed': {
      const settled = settle(items, event.step_id, { status: 'failed', failure: event.message })
      // Refused before it started (by the tier preview, or its Confirmation): show it anyway.
      return settled !== items
        ? settled
        : [
            ...items,
            {
              kind: 'step',
              stepId: event.step_id,
              operation: event.operation,
              status: 'failed',
              failure: event.message,
            },
          ]
    }
    case 'plan_proposed':
      return [
        ...items,
        {
          kind: 'plan',
          planId: event.plan_id,
          steps: event.steps,
          refusals: event.refusals,
          warnings: event.warnings ?? [],
          confirmIds: [],
          status: 'proposed',
        },
      ]
    case 'confirmation_requested':
      return items.map((item) =>
        item.kind === 'plan' && item.planId === event.plan_id
          ? { ...item, confirmIds: event.step_ids, status: 'awaiting' }
          : item,
      )
    case 'answer':
    case 'refusal':
    case 'human_requested':
      return [...items, { kind: 'agent', text: event.text }]
    case 'verification_requested':
      return [
        ...items,
        { kind: 'notice', text: `验证码已发送至 ${event.sent_to}，请在模拟手机上输入以继续` },
      ]
    case 'session_locked':
      return [...items, { kind: 'locked', text: event.text }]
    case 'handed_over':
      return [...items, { kind: 'notice', text: `人工客服 ${event.staff} 已接入对话` }]
    case 'staff_message':
      return [...items, { kind: 'staff', staff: event.staff, text: event.text }]
    case 'handed_back':
      return [...items, { kind: 'notice', text: '人工客服已离开，智能助手继续为您服务' }]
    default:
      return items
  }
}

/** A Plan card still waiting for an answer is replaced by the Customer's next message. */
function replaceWaitingPlans(items: Item[]): Item[] {
  return items.map((item) =>
    item.kind === 'plan' && item.status === 'awaiting' ? { ...item, status: 'replaced' } : item,
  )
}

export default function Chat({
  customerName,
  conversationId,
  phoneToken,
  smsSlot,
  onLogOut,
}: Props) {
  const [items, setItems] = useState<Item[]>([
    { kind: 'agent', text: `${customerName}，您好！我是您的智能银行助手，有什么可以帮您？` },
  ])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [showTrace, setShowTrace] = useState(false)
  const [traceTurns, setTraceTurns] = useState<TraceTurn[]>([])
  const [finishedTurns, setFinishedTurns] = useState(0)
  // The Staff Member who has the conversation: the Agent is paused meanwhile.
  const [staffMember, setStaffMember] = useState<string | null>(null)
  // The Red step waiting for the code typed into the simulated phone.
  const [verification, setVerification] = useState<VerificationRequested | null>(null)
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => bottom.current?.scrollIntoView({ behavior: 'smooth' }), [items])

  // A Staff Member's takeover, replies and hand-back arrive between the Customer's turns.
  useEffect(() => {
    let seen = 0
    let polling = false
    async function poll() {
      if (polling) return
      polling = true
      try {
        const { events, next } = await fetchUpdates(conversationId, seen)
        seen = next
        for (const event of events) {
          if (event.type === 'handed_over') setStaffMember(event.staff)
          if (event.type === 'handed_back') setStaffMember(null)
          setItems((current) => apply(current, event))
        }
      } catch {
        // Try again on the next tick.
      } finally {
        polling = false
      }
    }
    const timer = setInterval(() => void poll(), UPDATES_MS)
    return () => clearInterval(timer)
  }, [conversationId])

  /** One turn: `label` names it in the decision trace (the message, or the tap). */
  async function runTurn(
    label: string,
    turn: (onEvent: (e: ConversationEvent) => void) => Promise<void>,
  ) {
    setBusy(true)
    setTraceTurns((turns) => [...turns, { text: label, traces: [] }])
    try {
      await turn((e) => {
        // Trace events feed the decision trace panel only, never the chat.
        if (e.type === 'trace')
          setTraceTurns((turns) => [
            ...turns.slice(0, -1),
            { ...turns[turns.length - 1], traces: [...turns[turns.length - 1].traces, e] },
          ])
        else {
          if (e.type === 'verification_requested') setVerification(e)
          setItems((current) => apply(current, e))
        }
      })
    } catch {
      setItems((current) => [...current, { kind: 'error', text: '出错了，请稍后再试。' }])
    } finally {
      setBusy(false)
      setFinishedTurns((n) => n + 1)
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    const text = draft.trim()
    if (!text || busy) return
    setDraft('')
    // A code typed into the chat while a verification is pending isn't shown back: the
    // Agent masks it too, and points the Customer to the simulated phone.
    const shown = verification ? text.replace(CODE_IN_CHAT, '******') : text
    setVerification(null) // a new request drops a Plan paused for verification
    setItems((current) => [...replaceWaitingPlans(current), { kind: 'customer', text: shown }])
    await runTurn(shown, (onEvent) => sendMessage(conversationId, text, onEvent))
  }

  async function answerPlan(planId: string, action: 'confirm' | 'reject') {
    if (busy) return
    const status: PlanCardStatus = action === 'confirm' ? 'confirmed' : 'rejected'
    setItems((current) =>
      current.map((item) =>
        item.kind === 'plan' && item.planId === planId ? { ...item, status } : item,
      ),
    )
    const label = action === 'confirm' ? '（确认计划）' : '（取消计划）'
    await runTurn(label, (onEvent) =>
      sendAction(conversationId, { action, plan_id: planId }, onEvent),
    )
  }

  /** The simulated phone got a verification token from the Bank for the right code. */
  async function verified(waiting: VerificationRequested, token: string) {
    if (busy) return
    setVerification(null)
    await runTurn('（短信验证通过）', (onEvent) =>
      sendAction(
        conversationId,
        {
          action: 'verified',
          plan_id: waiting.plan_id,
          step_id: waiting.step_id,
          verification_token: token,
        },
        onEvent,
      ),
    )
  }

  /** The Bank refused the code typed into the simulated phone. */
  async function wrongCode(waiting: VerificationRequested, failedAttempts: number) {
    if (busy) return
    setVerification(null)
    await runTurn('（验证码错误）', (onEvent) =>
      sendAction(
        conversationId,
        {
          action: 'wrong_code',
          plan_id: waiting.plan_id,
          step_id: waiting.step_id,
          failed_attempts: failedAttempts,
        },
        onEvent,
      ),
    )
  }

  async function askForHuman() {
    if (busy) return
    setItems((current) => [...current, { kind: 'customer', text: '转人工' }])
    await runTurn('（转人工）', (onEvent) =>
      sendAction(conversationId, { action: 'ask_for_human' }, onEvent),
    )
  }

  return (
    <div className="chat">
      <header>
        <span>{staffMember ? `人工客服 · ${staffMember}` : '智能银行助手'}</span>
        <span>
          {!staffMember && (
            <button type="button" className="link" onClick={askForHuman} disabled={busy}>
              转人工
            </button>
          )}
          <button
            type="button"
            className="link"
            aria-pressed={showTrace}
            onClick={() => setShowTrace((shown) => !shown)}
          >
            {showTrace ? '隐藏追踪' : '决策追踪'}
          </button>
          <button type="button" className="link" onClick={onLogOut}>
            {customerName} · 退出
          </button>
        </span>
      </header>
      {showTrace && (
        <TracePanel
          conversationId={conversationId}
          turns={traceTurns}
          finishedTurns={finishedTurns}
          onClose={() => setShowTrace(false)}
        />
      )}
      <main>
        {items.map((item, i) => {
          switch (item.kind) {
            case 'step':
              return (
                <div key={i} className={`step ${item.status}`}>
                  {STEP_LABEL[item.status]}
                  ：{item.operation}
                  {item.failure && `（银行：${item.failure}）`}
                </div>
              )
            case 'plan':
              return (
                <PlanCard
                  key={i}
                  steps={item.steps}
                  refusals={item.refusals}
                  warnings={item.warnings}
                  confirmIds={item.confirmIds}
                  status={item.status}
                  disabled={busy || staffMember !== null}
                  onConfirm={() => answerPlan(item.planId, 'confirm')}
                  onReject={() => answerPlan(item.planId, 'reject')}
                />
              )
            case 'staff':
              return (
                <div key={i} className="bubble staff">
                  <span className="from">人工客服 · {item.staff}</span>
                  {item.text}
                </div>
              )
            case 'locked':
              return (
                <div key={i} className="locked-notice" role="alert">
                  <strong>会话已安全锁定</strong>
                  {item.text}
                </div>
              )
            case 'notice':
              return (
                <div key={i} className="notice">
                  {item.text}
                </div>
              )
            default:
              return (
                <div key={i} className={`bubble ${item.kind}`}>
                  {item.text}
                </div>
              )
          }
        })}
        {busy && !staffMember && <div className="typing">助手正在处理…</div>}
        <div ref={bottom} />
      </main>
      {smsSlot &&
        createPortal(
          <SimulatedPhone
            phoneToken={phoneToken}
            verification={verification}
            disabled={busy || staffMember !== null}
            onVerified={verified}
            onWrongCode={wrongCode}
          />,
          smsSlot,
        )}
      <form className="composer" onSubmit={submit}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="例如：我卡里还有多少钱？"
          disabled={busy}
        />
        <button type="submit" className="primary" disabled={busy || !draft.trim()}>
          发送
        </button>
      </form>
    </div>
  )
}
