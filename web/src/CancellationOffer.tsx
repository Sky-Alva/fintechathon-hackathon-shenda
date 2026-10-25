import { useState } from 'react'
import type { Subscription } from './events'

/** An offer of Subscriptions to cancel: offered → picked | kept (none picked), or dropped
 * by the Customer's next message. */
export type OfferStatus = 'offered' | 'picked' | 'kept' | 'dropped'

const STATUS_TEXT: Record<Exclude<OfferStatus, 'offered'>, string> = {
  picked: '已选择，请查看计划卡片',
  kept: '都保留，没有取消',
  dropped: '未选择',
}

type Props = {
  text: string
  candidates: Subscription[]
  status: OfferStatus
  disabled: boolean
  /** The picked Subscription IDs; none picked keeps them all. */
  onPick: (subscriptionIds: string[]) => void
}

/** The Bank's candidates, as the Agent listed them, each with a box to tick. Nothing is
 * cancelled here: the pick comes back as a Plan card to confirm. */
export default function CancellationOffer({ text, candidates, status, disabled, onPick }: Props) {
  const [picked, setPicked] = useState<string[]>([])

  function toggle(id: string) {
    setPicked((current) =>
      current.includes(id) ? current.filter((other) => other !== id) : [...current, id],
    )
  }

  return (
    <div className="paused-card">
      <div className="offer-text">{text}</div>
      {status === 'offered' ? (
        <>
          <ul className="offer-candidates">
            {candidates.map((candidate, i) => (
              <li key={candidate.subscription_id}>
                <label>
                  <input
                    type="checkbox"
                    checked={picked.includes(candidate.subscription_id)}
                    disabled={disabled}
                    onChange={() => toggle(candidate.subscription_id)}
                  />
                  {i + 1}. {candidate.merchant}（¥{candidate.current_amount}）
                </label>
              </li>
            ))}
          </ul>
          <div className="plan-actions">
            <button
              type="button"
              className="secondary"
              disabled={disabled}
              onClick={() => onPick([])}
            >
              都保留
            </button>
            <button
              type="button"
              className="primary"
              disabled={disabled || picked.length === 0}
              onClick={() => onPick(picked)}
            >
              取消所选（{picked.length}）
            </button>
          </div>
        </>
      ) : (
        <div className="plan-status">{STATUS_TEXT[status]}</div>
      )}
    </div>
  )
}
