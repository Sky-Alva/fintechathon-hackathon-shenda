import { useEffect, useState } from 'react'
import type { VerificationRequested } from './events'
import { type Texts, useLanguage } from './language'

/* The Customer's simulated phone: the SMS inbox the Bank texts verification codes to.
 *
 * It reads the Bank only, with the phone token from login. The Customer reads the code
 * here and types it into the chat's SMS verification box (VerificationBox), as into a
 * banking app; the box sends it straight to the Bank, never to the Agent. */

const POLL_MS = 2000

type Sms = { message_id: number; sent_to: string; at: string; text: string }

async function readMessages(phoneToken: string): Promise<Sms[]> {
  const response = await fetch('/bank/phone/messages', {
    headers: { Authorization: `Bearer ${phoneToken}` },
  })
  if (!response.ok) throw new Error(`phone: ${response.status}`)
  return (await response.json()).messages
}

type Props = {
  phoneToken: string
  /** The Red step waiting for a code, if any: its SMS is fetched straight away. */
  verification: VerificationRequested | null
}

export default function SimulatedPhone({ phoneToken, verification }: Props) {
  const [messages, setMessages] = useState<Sms[]>([])

  useEffect(() => {
    let stopped = false
    async function poll() {
      try {
        const latest = await readMessages(phoneToken)
        if (!stopped) setMessages(latest)
      } catch {
        // Try again on the next tick.
      }
    }
    void poll()
    const timer = setInterval(() => void poll(), POLL_MS)
    return () => {
      stopped = true
      clearInterval(timer)
    }
  }, [phoneToken])

  // A new challenge (or the same one asked again after a wrong code): fetch its SMS now.
  useEffect(() => {
    if (verification) void readMessages(phoneToken).then(setMessages, () => {})
  }, [verification, phoneToken])

  const language = useLanguage()
  const say = (texts: Texts) => texts[language]

  return (
    <aside className="sms-phone" aria-label={say({ zh: '模拟手机', en: 'Simulated phone' })}>
      <div className="sms-badge">{say({ zh: '模拟手机 · 短信', en: 'Simulated phone · SMS' })}</div>
      <ol className="sms-list">
        {messages.length === 0 && <li className="sms-empty">{say({ zh: '暂无短信', en: 'No messages' })}</li>}
        {messages.map((sms) => (
          <li key={sms.message_id}>
            <div className="sms-meta">
              {sms.sent_to} · {sms.at.slice(11, 16)}
            </div>
            {sms.text}
          </li>
        ))}
      </ol>
    </aside>
  )
}
