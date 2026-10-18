import { type FormEvent, useState } from 'react'

const DEMO_CUSTOMERS = [
  { username: 'liming', name: '李明' },
  { username: 'hanmei', name: '韩梅梅' },
]
const DEMO_PASSWORD = 'demo1234'

type Props = { onLogIn: (username: string, password: string) => Promise<void> }

export default function Login({ onLogIn }: Props) {
  const [username, setUsername] = useState(DEMO_CUSTOMERS[0].username)
  const [password, setPassword] = useState(DEMO_PASSWORD)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await onLogIn(username, password)
    } catch (e) {
      setError(e instanceof Error ? e.message : '登录失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="login" onSubmit={submit}>
      <h1>智能银行助手</h1>
      <p className="subtitle">登录手机银行</p>
      <div className="demo-customers">
        {DEMO_CUSTOMERS.map((c) => (
          <button
            key={c.username}
            type="button"
            className={c.username === username ? 'selected' : ''}
            onClick={() => {
              setUsername(c.username)
              setPassword(DEMO_PASSWORD)
            }}
          >
            {c.name}
          </button>
        ))}
      </div>
      <label>
        用户名
        <input value={username} onChange={(e) => setUsername(e.target.value)} />
      </label>
      <label>
        密码
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
      </label>
      {error && <p className="error">{error}</p>}
      <button type="submit" className="primary" disabled={busy}>
        {busy ? '登录中…' : '登录'}
      </button>
    </form>
  )
}
