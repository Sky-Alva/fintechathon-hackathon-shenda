import { type FormEvent, useEffect, useRef, useState } from 'react'
import { type CustomerAction, sendAction, sendMessage } from './api'
import type { BankRefusal, ConversationEvent, PlanStep } from './events'
import PlanCard, { type PlanCardStatus } from './PlanCard'
import TracePanel, { type TraceTurn } from './TracePanel'

/** One step's progress line: running, then done or failed (with the Bank's message). */
type StepItem = {
  kind: 'step'
  stepId: string
  operation: string
  status: 'running' | 'done' | 'failed'
  failure?: string
}

type Item =
  | { kind: 'customer'; text: string }
  | { kind: 'agent'; text: string }
  | StepItem
  | {
      kind: 'plan'
      planId: string
      steps: PlanStep[]
      refusals: (BankRefusal & { step_id: string })[]
      confirmIds: string[]
      status: PlanCardStatus
    }
  | { kind: 'error'; text: string }

type Props = { customerName: string; conversationId: string; onLogOut: () => void }

/** Settle the latest progress line for this step that is still running. */
function settle(items: Item[], stepId: string, change: Partial<StepItem>): Item[] {
  const index = items.findLastIndex(
    (item) => item.kind === 'step' && item.stepId === stepId && item.status === 'running',
  )
  if (index < 0) return items
  return items.map((item, i) => (i === index && item.kind === 'step' ? { ...item, ...change } : item))
}

/** Folds one streamed event into the chat. Events this slice doesn't display yet
 * (verification, trace…) are left to the tickets that emit them. */
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
      // Refused before it started (its Confirmation was refused): show it anyway.
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
      return [...items, { kind: 'agent', text: event.text }]
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

export default function Chat({ customerName, conversationId, onLogOut }: Props) {
  const [items, setItems] = useState<Item[]>([
    { kind: 'agent', text: `${customerName}，您好！我是您的智能银行助手，有什么可以帮您？` },
  ])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [showTrace, setShowTrace] = useState(false)
  const [traceTurns, setTraceTurns] = useState<TraceTurn[]>([])
  const [finishedTurns, setFinishedTurns] = useState(0)
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => bottom.current?.scrollIntoView({ behavior: 'smooth' }), [items])

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
        else setItems((current) => apply(current, e))
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
    setItems((current) => [...replaceWaitingPlans(current), { kind: 'customer', text }])
    await runTurn(text, (onEvent) => sendMessage(conversationId, text, onEvent))
  }

  async function answerPlan(planId: string, action: CustomerAction['action']) {
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

  return (
    <div className="chat">
      <header>
        <span>智能银行助手</span>
        <span>
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
                  {item.status === 'done' ? '已完成' : item.status === 'failed' ? '未完成' : '正在办理'}
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
                  confirmIds={item.confirmIds}
                  status={item.status}
                  disabled={busy}
                  onConfirm={() => answerPlan(item.planId, 'confirm')}
                  onReject={() => answerPlan(item.planId, 'reject')}
                />
              )
            default:
              return (
                <div key={i} className={`bubble ${item.kind}`}>
                  {item.text}
                </div>
              )
          }
        })}
        {busy && <div className="typing">助手正在处理…</div>}
        <div ref={bottom} />
      </main>
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
