import { useState } from 'react'
import { type BankLogin, logIn, startConversation } from './api'
import Chat from './Chat'
import Login from './Login'
import PresenterClock from './PresenterClock'

type Session = { login: BankLogin; conversationId: string }

export default function App() {
  const [session, setSession] = useState<Session | null>(null)

  async function onLogIn(username: string, password: string) {
    const login = await logIn(username, password)
    const conversationId = await startConversation(login)
    setSession({ login, conversationId })
  }

  // The presenter's demo control sits beside the phone, never inside the Customer's chat.
  return (
    <div className="stage">
      <div className="phone">
        {session ? (
          <Chat
            customerName={session.login.customer.name}
            conversationId={session.conversationId}
            onLogOut={() => setSession(null)}
          />
        ) : (
          <Login onLogIn={onLogIn} />
        )}
      </div>
      <PresenterClock />
    </div>
  )
}
