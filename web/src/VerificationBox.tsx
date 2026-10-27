import { type FormEvent, useState } from 'react'
import type { VerificationRequested } from './events'
import { type Texts, useLanguage } from './language'

/* The SMS verification box: where the Customer types the code the Bank texted to their
 * simulated phone, in the chat under the Plan card that asked for it, as in a banking app.
 *
 * It sits in the chat but is not the chat: it sends the code straight to the Bank
 * (POST /verifications/{challenge_id}) with the phone token from login, and the Agent is
 * handed only the resulting verification token, never the code or the phone token, so
 * neither the Agent nor its model can approve a Red step on their own. */

type Refusal = { reason: string; message: string; detail: Record<string, unknown> }

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
  /** The Red step waiting for its code. */
  verification: VerificationRequested
  disabled: boolean
  onVerified: (verification: VerificationRequested, token: string) => void
  onWrongCode: (verification: VerificationRequested, failedAttempts: number) => void
}

export default function VerificationBox({
  phoneToken,
  verification,
  disabled,
  onVerified,
  onWrongCode,
}: Props) {
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const language = useLanguage()
  const say = (texts: Texts) => texts[language]

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!code.trim() || sending) return
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
        setError(say({ zh: '验证码不正确', en: "That code isn't right" }))
        onWrongCode(verification, result.refusal.detail.failed_attempts)
      } else {
        setError(result.refusal.message)
      }
    } catch {
      setError(say({ zh: '无法连接银行，请重试', en: "Couldn't reach the Bank. Please try again" }))
    } finally {
      setSending(false)
    }
  }

  return (
    <form
      className="verify-box"
      onSubmit={submit}
      aria-label={say({ zh: '短信验证', en: 'SMS verification' })}
    >
      <div className="verify-title">
        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
          <rect x="3" y="7" width="10" height="7" rx="1.5" fill="currentColor" />
          <path d="M5 7V5a3 3 0 0 1 6 0v2" fill="none" stroke="currentColor" strokeWidth="1.6" />
        </svg>
        {say({ zh: '短信验证', en: 'SMS verification' })}
      </div>
      <label htmlFor="verify-code">
        {say({
          zh: `输入发送到 ${verification.sent_to} 的验证码`,
          en: `Type the code sent to ${verification.sent_to}`,
        })}
      </label>
      <div className="verify-row">
        <input
          id="verify-code"
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
          {say({ zh: '验证', en: 'Verify' })}
        </button>
      </div>
      {error && <div className="verify-error">{error}</div>}
      <div className="verify-note">
        {say({
          zh: '验证码直接提交给银行，智能助手看不到。',
          en: 'The code goes straight to the Bank; the Agent never sees it.',
        })}
      </div>
    </form>
  )
}
