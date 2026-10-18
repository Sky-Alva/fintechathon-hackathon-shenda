import { type FormEvent, useEffect, useRef, useState } from 'react'
import { sendMessage } from './api'
import type { ConversationEvent } from './events'

type Item =
  | { kind: 'customer'; text: string }
  | { kind: 'agent'; text: string }
  | { kind: 'step'; stepId: string; operation: string; done: boolean }
  | { kind: 'error'; text: string }

type Props = { customerName: string; conversationId: string; onLogOut: () => void }

/** Folds one streamed event into the chat. Events this slice doesn't display yet
 * (Plan cards, verification, trace…) are left to the tickets that emit them. */
function apply(items: Item[], event: ConversationEvent): Item[] {
  switch (event.type) {
    case 'step_started':
      return [
        ...items,
        { kind: 'step', stepId: event.step.id, operation: event.step.operation, done: false },
      ]
    case 'step_succeeded':
      return items.map((item) =>
        item.kind === 'step' && item.stepId === event.step_id && !item.done
          ? { ...item, done: true }
          : item,
      )
    case 'answer':
    case 'refusal':
      return [...items, { kind: 'agent', text: event.text }]
    default:
      return items
  }
}

export default function Chat({ customerName, conversationId, onLogOut }: Props) {
  const [items, setItems] = useState<Item[]>([
    { kind: 'agent', text: `${customerName}，您好！我是您的智能银行助手，有什么可以帮您？` },
  ])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => bottom.current?.scrollIntoView({ behavior: 'smooth' }), [items])

  async function submit(event: FormEvent) {
    event.preventDefault()
    const text = draft.trim()
    if (!text || busy) return
    setDraft('')
    setBusy(true)
    setItems((current) => [...current, { kind: 'customer', text }])
    try {
      await sendMessage(conversationId, text, (e) => setItems((current) => apply(current, e)))
    } catch {
      setItems((current) => [...current, { kind: 'error', text: '出错了，请稍后再试。' }])
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="chat">
      <header>
        <span>智能银行助手</span>
        <button type="button" className="link" onClick={onLogOut}>
          {customerName} · 退出
        </button>
      </header>
      <main>
        {items.map((item, i) =>
          item.kind === 'step' ? (
            <div key={i} className={`step ${item.done ? 'done' : ''}`}>
              {item.done ? '已完成' : '正在办理'}：{item.operation}
            </div>
          ) : (
            <div key={i} className={`bubble ${item.kind}`}>
              {item.text}
            </div>
          ),
        )}
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
