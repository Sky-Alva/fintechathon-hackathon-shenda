import { type FormEvent, type KeyboardEvent, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import AnswerText from './AnswerText'
import { type CustomerAction, fetchUpdates, sendAction, sendMessage } from './api'
import type {
  BankRefusal,
  CardPayee,
  ClarificationQuestion,
  StepCard,
  Chart,
  ConversationEvent,
  PlanStep,
  PlanWarning,
  Subscription,
  VerificationRequested,
} from './events'
import CancellationOffer, { type OfferStatus } from './CancellationOffer'
import { type Language, LanguageContext, customerLanguage } from './language'
import PlanCard, { type PlanCardStatus } from './PlanCard'
import SimulatedPhone from './SimulatedPhone'
import SpendingChart from './SpendingChart'
import TracePanel, { type TraceTurn } from './TracePanel'
import VerificationBox from './VerificationBox'

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
  // Bank data held instruction-like text (#47): the Agent ignored it and says so.
  | { kind: 'suspicious'; text: string }
  | StepItem
  // A spending chart, drawn from the Bank's output only (#40).
  | { kind: 'chart'; chart: Chart }
  | {
      kind: 'plan'
      planId: string
      steps: PlanStep[]
      refusals: (BankRefusal & { step_id: string })[]
      warnings: PlanWarning[]
      payees: CardPayee[]
      cards: StepCard[]
      confirmIds: string[]
      status: PlanCardStatus
    }
  | {
      kind: 'question'
      text: string
      options: ClarificationQuestion['options']
      status: QuestionStatus
      chosen?: string
    }
  | { kind: 'paused'; planId: string; text: string; status: PausedStatus }
  | { kind: 'reversal'; planId: string; text: string; stepIds: string[]; status: ReversalStatus }
  | {
      kind: 'offer'
      offerId: string
      text: string
      candidates: Subscription[]
      status: OfferStatus
    }
  | { kind: 'error'; text: string }

/** A stopped Plan: waiting → resumed | cancelled, or changed by the Customer's next message. */
type PausedStatus = 'waiting' | 'resumed' | 'cancelled' | 'changed'

const PAUSED_STATUS_TEXT: Record<Language, Record<Exclude<PausedStatus, 'waiting'>, string>> = {
  zh: { resumed: '已继续', cancelled: '已取消剩余步骤', changed: '已修改，需重新确认' },
  en: {
    resumed: 'Resumed',
    cancelled: 'The rest is cancelled',
    changed: 'Changed: needs confirming again',
  },
}

/** An offer to undo a failed Plan's completed steps: offered → accepted | declined, or
 * dropped by the Customer's next message (the steps stay done). */
type ReversalStatus = 'offered' | 'accepted' | 'declined' | 'dropped'

const REVERSAL_STATUS_TEXT: Record<Language, Record<Exclude<ReversalStatus, 'offered'>, string>> = {
  zh: { accepted: '已选择撤销，请查看新的计划', declined: '不撤销，保持不变', dropped: '未撤销' },
  en: {
    accepted: 'Undo chosen: see the new Plan',
    declined: 'Not undone: the steps stay done',
    dropped: 'Not undone',
  },
}

/** A clarifying question: waiting → chosen (an option tapped), or answered in words by
 * the Customer's next message. */
type QuestionStatus = 'waiting' | 'chosen' | 'answered'

type StepStatus = 'running' | 'done' | 'failed'

const STEP_LABEL: Record<Language, Record<StepStatus, string>> = {
  zh: { running: '正在办理', done: '已完成', failed: '未能完成' },
  en: { running: 'Working on', done: 'Done', failed: "Couldn't do" },
}

/** The Channel's own words, in the conversation's language. Labels in parentheses name a
 * tap in the decision trace. */
const UI = {
  zh: {
    codeSent: (to: string) => `验证码已发送至 ${to}，请在下方输入`,
    staffJoined: (staff: string) => `人工客服 ${staff} 已接入对话`,
    staffLeft: '人工客服已离开，智能助手继续为您服务',
    error: '出错了，请稍后再试。',
    rejectTap: '（取消计划）',
    removeTap: (n: number) => `（移除 ${n} 步）`,
    confirmTap: '（确认计划）',
    resumeTap: '（继续计划）',
    cancelTap: '（取消剩余步骤）',
    undoTap: '（撤销已完成的步骤）',
    keepTap: '（不撤销）',
    pickTap: (n: number) => (n ? `（选择取消 ${n} 个订阅）` : '（都保留）'),
    verifiedTap: '（短信验证通过：银行核对了验证码，助手只收到验证凭证）',
    stopTap: '（停止计划）',
    wrongCodeTap: '（验证码错误）',
    chooseTap: (label: string) => `（选择：${label}）`,
    askForHuman: '转人工',
    askForHumanTap: '（转人工）',
    staffHeader: (staff: string) => `人工客服 · ${staff}`,
    agentHeader: '智能银行助手',
    hideTrace: '隐藏追踪',
    showTrace: '决策追踪',
    logOut: (name: string) => `${name} · 退出`,
    bankSaid: (failure: string) => `（银行：${failure}）`,
    changeInWords: '如需修改，直接告诉我',
    cancelRest: '取消剩余',
    resume: '继续',
    keep: '不撤销',
    undo: '撤销',
    staffFrom: (staff: string) => `人工客服 · ${staff}`,
    locked: '会话已安全锁定',
    suspicious: '发现可疑信息',
    typing: '助手正在处理…',
    stopping: '正在停止…',
    stop: '■ 停止',
    stopWaiting: '■ 停止（暂不验证）',
    placeholder: '例如：我卡里还有多少钱？',
    send: '发送',
  },
  en: {
    codeSent: (to: string) => `A code was sent to ${to}. Type it in below.`,
    staffJoined: (staff: string) => `Staff Member ${staff} has joined the conversation`,
    staffLeft: 'The Staff Member has left; the Agent is back with you',
    error: 'Something went wrong. Please try again later.',
    rejectTap: '(cancel the Plan)',
    removeTap: (n: number) => `(remove ${n} step${n > 1 ? 's' : ''})`,
    confirmTap: '(confirm the Plan)',
    resumeTap: '(resume the Plan)',
    cancelTap: '(cancel the rest)',
    undoTap: '(undo the completed steps)',
    keepTap: "(don't undo)",
    pickTap: (n: number) => (n ? `(cancel ${n} Subscription${n > 1 ? 's' : ''})` : '(keep all)'),
    verifiedTap: '(SMS code verified: the Bank checked the code, the Agent got only a token)',
    stopTap: '(stop the Plan)',
    wrongCodeTap: '(wrong code)',
    chooseTap: (label: string) => `(chose: ${label})`,
    askForHuman: 'Talk to a person',
    askForHumanTap: '(talk to a person)',
    staffHeader: (staff: string) => `Staff Member · ${staff}`,
    agentHeader: 'Banking Agent',
    hideTrace: 'Hide trace',
    showTrace: 'Decision trace',
    logOut: (name: string) => `${name} · Log out`,
    bankSaid: (failure: string) => ` (Bank: ${failure})`,
    changeInWords: 'To change it, just tell me',
    cancelRest: 'Cancel the rest',
    resume: 'Resume',
    keep: "Don't undo",
    undo: 'Undo',
    staffFrom: (staff: string) => `Staff Member · ${staff}`,
    locked: 'Session under a Safety Lock',
    suspicious: 'Suspicious text found',
    typing: 'The Agent is working…',
    stopping: 'Stopping…',
    stop: '■ Stop',
    stopWaiting: '■ Stop (not verifying now)',
    placeholder: 'e.g. How much is in my account?',
    send: 'Send',
  },
}

type Props = {
  customerName: string
  conversationId: string
  /** For the simulated phone and the SMS verification box only: they talk to the Bank,
   * never to the Agent. */
  phoneToken: string
  /** For the decision trace panel's audit trail, which the Agent shows this session only. */
  sessionToken: string
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
function apply(items: Item[], event: ConversationEvent, language: Language): Item[] {
  const t = UI[language]
  switch (event.type) {
    case 'step_started':
      return [
        ...items,
        { kind: 'step', stepId: event.step.id, operation: event.step.operation, status: 'running' },
      ]
    case 'step_succeeded':
      return settle(items, event.step_id, { status: 'done' })
    case 'chart':
      return [...items, { kind: 'chart', chart: event }]
    case 'step_failed': {
      // The Agent's plain words for the Bank's reason (#42), else the Bank's own message.
      const failure = event.text ?? event.message
      const settled = settle(items, event.step_id, { status: 'failed', failure })
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
              failure,
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
          payees: event.payees ?? [],
          cards: event.cards ?? [],
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
    case 'plan_paused':
      return [
        ...items,
        { kind: 'paused', planId: event.plan_id, text: event.text, status: 'waiting' },
      ]
    case 'reversal_offered':
      return [
        ...items,
        {
          kind: 'reversal',
          planId: event.plan_id,
          text: event.text,
          stepIds: event.steps.map((step) => step.step_id),
          status: 'offered',
        },
      ]
    case 'clarification_question':
      return [
        ...items,
        { kind: 'question', text: event.text, options: event.options, status: 'waiting' },
      ]
    case 'cancellation_offered':
      return [
        ...items,
        {
          kind: 'offer',
          offerId: event.offer_id,
          text: event.text,
          candidates: event.candidates,
          status: 'offered',
        },
      ]
    case 'answer':
    case 'refusal':
    case 'human_requested':
      return [...items, { kind: 'agent', text: event.text }]
    case 'verification_requested':
      return [
        ...items,
        { kind: 'notice', text: t.codeSent(event.sent_to) },
      ]
    case 'session_locked':
      return [...items, { kind: 'locked', text: event.text }]
    case 'injection_warning':
      return [...items, { kind: 'suspicious', text: event.text }]
    case 'handed_over':
      return [...items, { kind: 'notice', text: t.staffJoined(event.staff) }]
    case 'staff_message':
      return [...items, { kind: 'staff', staff: event.staff, text: event.text }]
    case 'handed_back':
      return [...items, { kind: 'notice', text: t.staffLeft }]
    default:
      return items
  }
}

/** A Plan card still waiting for an answer is replaced by the Customer's next message, and a
 * stopped Plan is changed by it (whatever the Agent plans then needs a new Plan card). */
function replaceWaitingPlans(items: Item[]): Item[] {
  return items.map((item) => {
    if (item.kind === 'plan' && item.status === 'awaiting') return { ...item, status: 'replaced' }
    if (item.kind === 'paused' && item.status === 'waiting') return { ...item, status: 'changed' }
    if (item.kind === 'reversal' && item.status === 'offered') return { ...item, status: 'dropped' }
    if (item.kind === 'question' && item.status === 'waiting') return { ...item, status: 'answered' }
    if (item.kind === 'offer' && item.status === 'offered') return { ...item, status: 'dropped' }
    return item
  })
}

export default function Chat({
  customerName,
  conversationId,
  phoneToken,
  sessionToken,
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
  // The confirmed Plan whose steps are running: the stop button pauses it.
  const [runningPlan, setRunningPlan] = useState<string | null>(null)
  const [stopping, setStopping] = useState(false)
  // The Red step waiting for its SMS code, typed into the verification box.
  const [verification, setVerification] = useState<VerificationRequested | null>(null)
  // The conversation's language, as the Agent decides it: the Channel's controls follow it.
  const [language, setLanguage] = useState<Language>('zh')
  const languageRef = useRef<Language>('zh')
  const t = UI[language]
  const bottom = useRef<HTMLDivElement>(null)
  const messageBox = useRef<HTMLTextAreaElement>(null)

  // Braces matter: Chromium's scrollIntoView now returns a Promise, which React would
  // take for the effect's cleanup and crash calling.
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' })
  }, [items])

  // A Staff Member's takeover, replies and hand-back arrive between the Customer's turns,
  // and so does the news that Merchant orders were paid when the Bank clock reached their
  // delivery date (#48).
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
          setItems((current) => apply(current, event, languageRef.current))
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
          setItems((current) => apply(current, e, languageRef.current))
        }
      })
    } catch {
      setItems((current) => [...current, { kind: 'error', text: UI[languageRef.current].error }])
    } finally {
      setBusy(false)
      setFinishedTurns((n) => n + 1)
    }
  }

  // The message box grows with the text (the CSS caps it at about 5 lines) and
  // shrinks back to one line once it's emptied after sending.
  useLayoutEffect(() => {
    const box = messageBox.current
    if (!box) return
    box.style.height = 'auto'
    box.style.height = `${box.scrollHeight + box.offsetHeight - box.clientHeight}px`
  }, [draft])

  // Enter sends and Shift+Enter adds a line; Enter that picks a pinyin candidate
  // (an IME composition in progress) doesn't send.
  function sendOnEnter(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter' || event.shiftKey) return
    if (event.nativeEvent.isComposing || event.keyCode === 229) return
    event.preventDefault()
    event.currentTarget.form?.requestSubmit()
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    const text = draft.trim()
    if (!text || busy) return
    setDraft('')
    languageRef.current = customerLanguage(text, languageRef.current)
    setLanguage(languageRef.current)
    // A code typed into the chat while a verification is pending isn't shown back: the
    // Agent masks it too, and points the Customer to the verification box.
    const shown = verification ? text.replace(CODE_IN_CHAT, '******') : text
    setVerification(null) // a new request drops a Plan paused for verification
    setItems((current) => [...replaceWaitingPlans(current), { kind: 'customer', text: shown }])
    await runTurn(shown, (onEvent) => sendMessage(conversationId, text, onEvent))
  }

  /** A turn that runs a confirmed Plan's steps: the stop button shows until it ends. */
  async function runPlan(planId: string, label: string, action: CustomerAction) {
    setRunningPlan(planId)
    try {
      await runTurn(label, (onEvent) => sendAction(conversationId, action, onEvent))
    } finally {
      setRunningPlan(null)
      setStopping(false)
    }
  }

  async function answerPlan(
    planId: string,
    action: 'confirm' | 'reject' | 'remove_steps',
    removed: string[] = [],
  ) {
    if (busy) return
    const status: PlanCardStatus =
      action === 'confirm' ? 'confirmed' : action === 'reject' ? 'rejected' : 'edited'
    setItems((current) =>
      current.map((item) =>
        item.kind === 'plan' && item.planId === planId ? { ...item, status } : item,
      ),
    )
    if (action === 'reject') {
      await runTurn(t.rejectTap, (onEvent) =>
        sendAction(conversationId, { action, plan_id: planId }, onEvent),
      )
      return
    }
    if (action === 'remove_steps') {
      // Nothing runs: the rest, re-tiered by the Bank, come back as a new Plan card.
      await runTurn(t.removeTap(removed.length), (onEvent) =>
        sendAction(conversationId, { action, plan_id: planId, step_ids: removed }, onEvent),
      )
      return
    }
    await runPlan(planId, t.confirmTap, { action, plan_id: planId })
  }

  /** The stop button: sent alongside the running turn, whose stream then ends with
   * plan_paused. */
  async function stopPlan() {
    if (!runningPlan || stopping) return
    setStopping(true)
    try {
      await sendAction(conversationId, { action: 'stop', plan_id: runningPlan }, () => {})
    } catch {
      // The Plan finished first: nothing left to stop.
    }
  }

  async function answerPaused(planId: string, action: 'resume' | 'cancel') {
    if (busy) return
    const status: PausedStatus = action === 'resume' ? 'resumed' : 'cancelled'
    setItems((current) =>
      current.map((item) =>
        item.kind === 'paused' && item.planId === planId ? { ...item, status } : item,
      ),
    )
    if (action === 'resume') await runPlan(planId, t.resumeTap, { action, plan_id: planId })
    else
      await runTurn(t.cancelTap, (onEvent) =>
        sendAction(conversationId, { action, plan_id: planId }, onEvent),
      )
  }

  /** Undo a failed Plan's completed steps (a new Plan card comes back), or leave them. */
  async function answerReversal(planId: string, stepIds: string[], accept: boolean) {
    if (busy) return
    const status: ReversalStatus = accept ? 'accepted' : 'declined'
    setItems((current) =>
      current.map((item) =>
        item.kind === 'reversal' && item.planId === planId ? { ...item, status } : item,
      ),
    )
    const action: CustomerAction = accept
      ? { action: 'reverse', plan_id: planId, step_ids: stepIds }
      : { action: 'reject', plan_id: planId }
    await runTurn(accept ? t.undoTap : t.keepTap, (onEvent) =>
      sendAction(conversationId, action, onEvent),
    )
  }

  /** The Customer's pick from the Subscriptions offered for cancelling: a Plan card comes
   * back to confirm; none picked keeps them all. */
  async function answerOffer(offerId: string, subscriptionIds: string[]) {
    if (busy) return
    const status: OfferStatus = subscriptionIds.length ? 'picked' : 'kept'
    setItems((current) =>
      current.map((item) =>
        item.kind === 'offer' && item.offerId === offerId ? { ...item, status } : item,
      ),
    )
    const action: CustomerAction = {
      action: 'cancel_subscriptions',
      offer_id: offerId,
      subscription_ids: subscriptionIds,
    }
    await runTurn(t.pickTap(subscriptionIds.length), (onEvent) => sendAction(conversationId, action, onEvent))
  }

  /** The verification box got a verification token from the Bank for the right code: the
   * Plan runs on from its Red step, and can be stopped. */
  async function verified(waiting: VerificationRequested, tokens: string[]) {
    if (busy) return
    setVerification(null)
    await runPlan(waiting.plan_id, t.verifiedTap, {
      action: 'verified',
      plan_id: waiting.plan_id,
      step_id: waiting.step_id,
      verification_tokens: Object.fromEntries(waiting.step_ids.map((id, i) => [id, tokens[i]])),
    })
  }

  /** Stop a Plan waiting at a Red step for its code: the stop's own events pause it. */
  async function stopWaiting(waiting: VerificationRequested) {
    if (busy) return
    setVerification(null)
    await runTurn(t.stopTap, (onEvent) =>
      sendAction(conversationId, { action: 'stop', plan_id: waiting.plan_id }, onEvent),
    )
  }

  /** The Bank refused the code typed into the verification box. */
  async function wrongCode(waiting: VerificationRequested, failedAttempts: number) {
    if (busy) return
    setVerification(null)
    await runTurn(t.wrongCodeTap, (onEvent) =>
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

  /** Tap an option of the clarifying question: the Agent plans again with it. */
  async function choose(index: number, option: ClarificationQuestion['options'][number]) {
    if (busy) return
    setItems((current) =>
      current.map((item, i) =>
        i === index && item.kind === 'question'
          ? { ...item, status: 'chosen', chosen: option.value }
          : item,
      ),
    )
    await runTurn(t.chooseTap(option.label), (onEvent) =>
      sendAction(conversationId, { action: 'choose', value: option.value }, onEvent),
    )
  }

  async function askForHuman() {
    if (busy) return
    setItems((current) => [...current, { kind: 'customer', text: t.askForHuman }])
    await runTurn(t.askForHumanTap, (onEvent) =>
      sendAction(conversationId, { action: 'ask_for_human' }, onEvent),
    )
  }

  return (
    <LanguageContext.Provider value={language}>
      <div className="chat">
        <header>
          <span>{staffMember ? t.staffHeader(staffMember) : t.agentHeader}</span>
          <span>
            {!staffMember && (
              <button type="button" className="link" onClick={askForHuman} disabled={busy}>
                {t.askForHuman}
              </button>
            )}
            <button
              type="button"
              className="link"
              aria-pressed={showTrace}
              onClick={() => setShowTrace((shown) => !shown)}
            >
              {showTrace ? t.hideTrace : t.showTrace}
            </button>
            <button type="button" className="link" onClick={onLogOut}>
              {t.logOut(customerName)}
            </button>
          </span>
        </header>
        {showTrace && (
          <TracePanel
            conversationId={conversationId}
            sessionToken={sessionToken}
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
                    {STEP_LABEL[language][item.status]}
                    ：{item.operation}
                    {item.failure && t.bankSaid(item.failure)}
                  </div>
                )
              case 'plan':
                return (
                  <PlanCard
                    key={i}
                    steps={item.steps}
                    refusals={item.refusals}
                    warnings={item.warnings}
                    payees={item.payees}
                    cards={item.cards}
                    confirmIds={item.confirmIds}
                    status={item.status}
                    disabled={busy || staffMember !== null}
                    onConfirm={() => answerPlan(item.planId, 'confirm')}
                    onRemove={(removed) => answerPlan(item.planId, 'remove_steps', removed)}
                    onReject={() => answerPlan(item.planId, 'reject')}
                  />
                )
              case 'paused':
                return (
                  <div key={i} className="paused-card">
                    <div>{item.text}</div>
                    {item.status === 'waiting' ? (
                      <div className="plan-actions">
                        <span className="plan-note">{t.changeInWords}</span>
                        <button
                          type="button"
                          className="secondary"
                          disabled={busy || staffMember !== null}
                          onClick={() => answerPaused(item.planId, 'cancel')}
                        >
                          {t.cancelRest}
                        </button>
                        <button
                          type="button"
                          className="primary"
                          disabled={busy || staffMember !== null}
                          onClick={() => answerPaused(item.planId, 'resume')}
                        >
                          {t.resume}
                        </button>
                      </div>
                    ) : (
                      <div className="plan-status">{PAUSED_STATUS_TEXT[language][item.status]}</div>
                    )}
                  </div>
                )
              case 'reversal':
                return (
                  <div key={i} className="paused-card">
                    <div>{item.text}</div>
                    {item.status === 'offered' ? (
                      <div className="plan-actions">
                        <button
                          type="button"
                          className="secondary"
                          disabled={busy || staffMember !== null}
                          onClick={() => answerReversal(item.planId, item.stepIds, false)}
                        >
                          {t.keep}
                        </button>
                        <button
                          type="button"
                          className="primary"
                          disabled={busy || staffMember !== null}
                          onClick={() => answerReversal(item.planId, item.stepIds, true)}
                        >
                          {t.undo}
                        </button>
                      </div>
                    ) : (
                      <div className="plan-status">{REVERSAL_STATUS_TEXT[language][item.status]}</div>
                    )}
                  </div>
                )
              case 'question':
                return (
                  <div key={i} className="paused-card">
                    <div>{item.text}</div>
                    {item.options.length > 0 && (
                      <div className="question-options">
                        {item.options.map((option) => (
                          <button
                            key={option.value}
                            type="button"
                            className={item.chosen === option.value ? 'primary' : 'secondary'}
                            disabled={busy || staffMember !== null || item.status !== 'waiting'}
                            onClick={() => choose(i, option)}
                          >
                            {option.label}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )
              case 'offer':
                return (
                  <CancellationOffer
                    key={i}
                    text={item.text}
                    candidates={item.candidates}
                    status={item.status}
                    disabled={busy || staffMember !== null}
                    onPick={(ids) => answerOffer(item.offerId, ids)}
                  />
                )
              case 'staff':
                return (
                  <div key={i} className="bubble staff">
                    <span className="from">{t.staffFrom(item.staff)}</span>
                    {item.text}
                  </div>
                )
              case 'chart':
                return <SpendingChart key={i} chart={item.chart} />
              case 'locked':
                return (
                  <div key={i} className="locked-notice" role="alert">
                    <strong>{t.locked}</strong>
                    {item.text}
                  </div>
                )
              case 'suspicious':
                return (
                  <div key={i} className="suspicious-notice" role="alert">
                    <strong>{t.suspicious}</strong>
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
                    {item.kind === 'agent' ? <AnswerText text={item.text} /> : item.text}
                  </div>
                )
            }
          })}
          {busy && !staffMember && <div className="typing">{t.typing}</div>}
          {runningPlan && (
            <button type="button" className="stop-plan" disabled={stopping} onClick={stopPlan}>
              {stopping ? t.stopping : t.stop}
            </button>
          )}
          {verification && (
            <VerificationBox
              phoneToken={phoneToken}
              verification={verification}
              disabled={busy || staffMember !== null}
              onVerified={verified}
              onWrongCode={wrongCode}
            />
          )}
          {verification && !busy && !staffMember && (
            <button type="button" className="stop-plan" onClick={() => stopWaiting(verification)}>
              {t.stopWaiting}
            </button>
          )}
          <div ref={bottom} />
        </main>
        {smsSlot &&
          createPortal(
            <SimulatedPhone phoneToken={phoneToken} verification={verification} />,
            smsSlot,
          )}
        <form className="composer" onSubmit={submit}>
          <textarea
            ref={messageBox}
            rows={1}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={sendOnEnter}
            placeholder={t.placeholder}
            disabled={busy}
          />
          <button type="submit" className="primary" disabled={busy || !draft.trim()}>
            {t.send}
          </button>
        </form>
      </div>
    </LanguageContext.Provider>
  )
}
