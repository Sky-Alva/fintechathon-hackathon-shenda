import { type FormEvent, useEffect, useState } from 'react'
import type { VerificationRequested } from './events'

/* The Customer's simulated phone: the SMS the Bank texts verification codes to.
 *
 * It talks to the Bank only, with the phone token from login. The Customer types the code
 * here and the phone sends it straight to the Bank (POST /verifications/{challenge_id});
 * the Agent is handed only the resulting verification token, never the code or the phone
 * token, so neither the Agent nor its model can approve a Red step on their own. */

const POLL_MS = 2000

type Sms = { message_id: number; sent_to: string; at: string; text: string }

type Refusal = { reason: string; message: string; detail: Record<string, unknown> }

async function readMessages(phoneToken: string): Promise<Sms[]> {
  const response = await fetch('/bank/phone/messages', {
    headers: { Authorization: `Bearer ${phoneToken}` },
  })
  if (!response.ok) throw new Error(`phone: ${response.status}`)
  return (await response.json()).messages
}

/** The code, straight to the Bank: a verification token, or the Bank's refusal. */
async function submitCode(
  phoneToken: string,
  challengeId: string,
  code: string,
): Promise<{ token: string } | { refusal: Refusal }> {
  const response = await fetch(`/bank/verifications/${encodeURIComponent(challengeId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${phoneToken}` },
    body: JSON.stringify({ code }),
  })
  const body = await response.json()
  if (response.ok) return { token: body.verification_token }
  if (body.refusal) return { refusal: body.refusal }
  throw new Error(`verification: ${response.status}`)
}

type Props = {
  phoneToken: string
  /** The Red step waiting for a code, if any. */
  verification: VerificationRequested | null
  disabled: boolean
  onVerified: (verification: VerificationRequested, token: string) => void
  onWrongCode: (verification: VerificationRequested, failedAttempts: number) => void
}

export default function SimulatedPhone({
  phoneToken,
  verification,
  disabled,
  onVerified,
  onWrongCode,
}: Props) {
  const [messages, setMessages] = useState<Sms[]>([])
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)

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

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!verification || !code.trim() || sending) return
    setSending(true)
    setError(null)
    try {
      const result = await submitCode(phoneToken, verification.challenge_id, code.trim())
      setCode('')
      if ('token' in result) {
        onVerified(verification, result.token)
      } else if (
        result.refusal.reason === 'token_invalid' &&
        typeof result.refusal.detail.failed_attempts === 'number'
      ) {
        setError('验证码不正确')
        onWrongCode(verification, result.refusal.detail.failed_attempts)
      } else {
        setError(result.refusal.message)
      }
    } catch {
      setError('无法连接银行，请重试')
    } finally {
      setSending(false)
    }
  }

  return (
    <aside className="sms-phone" aria-label="模拟手机">
      <div className="sms-badge">模拟手机 · 短信</div>
      <ol className="sms-list">
        {messages.length === 0 && <li className="sms-empty">暂无短信</li>}
        {messages.map((sms) => (
          <li key={sms.message_id}>
            <div className="sms-meta">
              {sms.sent_to} · {sms.at.slice(11, 16)}
            </div>
            {sms.text}
          </li>
        ))}
      </ol>
      {verification && (
        <form className="sms-code" onSubmit={submit}>
          <label htmlFor="sms-code">输入短信验证码</label>
          <div>
            <input
              id="sms-code"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
              disabled={disabled || sending}
            />
            <button
              type="submit"
              className="primary"
              disabled={disabled || sending || code.length === 0}
            >
              验证
            </button>
          </div>
          {error && <div className="sms-error">{error}</div>}
        </form>
      )}
    </aside>
  )
}
