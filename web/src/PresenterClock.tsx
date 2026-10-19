import { type FormEvent, useEffect, useState } from 'react'

/* The presenter's demo control for the Bank clock, kept outside the Customer's phone.
 *
 * Reading the clock is public (GET /clock). Moving it uses the Bank's admin calls, so
 * the presenter types the Bank's admin key here: it stays in this tab's memory and goes
 * only to the Bank. The Agent never holds it (ADR-0001), and the built Channel doesn't
 * contain it either. */

const POLL_MS = 5000

type ClockResponse = { now: string }

async function readClock(): Promise<string> {
  const response = await fetch('/bank/clock')
  if (!response.ok) throw new Error(`Bank clock: ${response.status}`)
  return ((await response.json()) as ClockResponse).now
}

async function adminClock(
  adminKey: string,
  method: 'POST' | 'PUT',
  path: string,
  body: object,
): Promise<string> {
  const response = await fetch(`/bank/admin${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-Admin-Key': adminKey },
    body: JSON.stringify(body),
  })
  if (response.status === 401) throw new Error('管理密钥不正确')
  if (!response.ok) throw new Error('调整 Bank 时钟失败')
  return ((await response.json()) as ClockResponse).now
}

/** "2026-10-20T09:00:00+08:00" → "2026-10-20 09:00". The Bank returns its own time
 * zone (Beijing time), so the wall-clock part is shown as is. */
function wallClock(iso: string): string {
  return iso.slice(0, 16).replace('T', ' ')
}

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']

function weekday(iso: string): string {
  // The date part alone, read as UTC, gives the Bank's calendar day.
  return `星期${WEEKDAYS[new Date(`${iso.slice(0, 10)}T00:00:00Z`).getUTCDay()]}`
}

const STEPS = [
  { label: '+30 分钟', minutes: 30 },
  { label: '+1 小时', minutes: 60 },
  { label: '+1 天', minutes: 24 * 60 },
]

export default function PresenterClock() {
  const [now, setNow] = useState<string | null>(null)
  const [adminKey, setAdminKey] = useState('')
  const [target, setTarget] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [unreachable, setUnreachable] = useState(false)
  const [busy, setBusy] = useState(false)

  // The Bank clock only moves when someone moves it (here, a test run, a reset), so a
  // slow poll is enough to keep the display honest.
  useEffect(() => {
    let mounted = true
    function poll() {
      readClock().then(
        (value) => {
          if (!mounted) return
          setNow(value)
          setUnreachable(false)
        },
        () => mounted && setUnreachable(true),
      )
    }
    poll()
    const timer = setInterval(poll, POLL_MS)
    return () => {
      mounted = false
      clearInterval(timer)
    }
  }, [])

  async function run(move: () => Promise<string>) {
    setBusy(true)
    setError(null)
    try {
      setNow(await move())
    } catch (e) {
      setError(e instanceof Error ? e.message : '调整 Bank 时钟失败')
    } finally {
      setBusy(false)
    }
  }

  function advance(minutes: number) {
    void run(() => adminClock(adminKey, 'POST', '/clock/advance', { minutes }))
  }

  function jump(event: FormEvent) {
    event.preventDefault()
    if (!now || !target) return
    // The chosen wall-clock time is Bank time (Beijing, +08:00).
    const chosen = `${target}:00+08:00`
    if (new Date(chosen) <= new Date(now)) {
      setError('只能把时钟往后拨')
      return
    }
    void run(() => adminClock(adminKey, 'PUT', '/clock', { now: chosen }))
  }

  const locked = busy || !adminKey

  return (
    <aside className="presenter" aria-label="演示控制：Bank 时钟">
      <div className="presenter-badge">演示控制 · 仅供演示者使用</div>
      <p className="presenter-note">不属于客户界面。拨动的是整个 Bank 的时钟。</p>

      <div className="presenter-clock">
        <span className="presenter-label">Bank 时钟（北京时间）</span>
        <strong>{now ? wallClock(now) : '—'}</strong>
        {now && <span className="presenter-label">{weekday(now)}</span>}
      </div>

      <label className="presenter-field">
        Bank 管理密钥
        <input
          type="password"
          value={adminKey}
          onChange={(e) => setAdminKey(e.target.value)}
          placeholder="BANK_ADMIN_KEY"
          autoComplete="off"
        />
      </label>

      <div className="presenter-steps">
        {STEPS.map((s) => (
          <button key={s.minutes} type="button" disabled={locked} onClick={() => advance(s.minutes)}>
            {s.label}
          </button>
        ))}
      </div>

      <form className="presenter-jump" onSubmit={jump}>
        <input
          type="datetime-local"
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          aria-label="拨到指定时间"
        />
        <button type="submit" disabled={locked || !target}>
          拨到
        </button>
      </form>

      {unreachable && <p className="presenter-error">读取 Bank 时钟失败</p>}
      {error && <p className="presenter-error">{error}</p>}
    </aside>
  )
}
