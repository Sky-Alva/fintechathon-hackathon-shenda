import type { BankRefusal, PlanStep, PlanWarning, Tier } from './events'

/** proposed → awaiting (confirmation_requested) → confirmed | rejected, or replaced when the
 * Customer sends a new message instead of answering. */
export type PlanCardStatus = 'proposed' | 'awaiting' | 'confirmed' | 'rejected' | 'replaced'

type Props = {
  steps: PlanStep[]
  refusals: (BankRefusal & { step_id: string })[]
  /** Why each Red step needs Strong Verification, e.g. it crosses the ¥1,000 daily line. */
  warnings: PlanWarning[]
  confirmIds: string[]
  status: PlanCardStatus
  disabled: boolean
  onConfirm: () => void
  onReject: () => void
}

const OPERATION_LABELS: Record<string, string> = {
  transfer: '转账',
  add_card_lock: '添加卡锁',
  lower_card_limit: '调低卡限额',
  apply_virtual_card: '申请虚拟卡',
}

const TIER_LABELS: Record<Tier, string> = {
  green: '绿色 · 自动执行',
  yellow: '黄色 · 需确认',
  red: '红色 · 需短信验证',
}

const INPUT_LABELS: Record<string, string> = {
  payee_id: '收款人',
  amount: '金额',
  note: '附言',
  card_id: '卡',
  scope: '范围',
  new_limit: '新限额',
  name: '名称',
}

/** Every input of the step, exactly as it will be sent to the Bank. */
function details(step: PlanStep): [string, string][] {
  return Object.entries(step.input)
    .filter(([, value]) => value !== '' && value !== null && value !== undefined)
    .map(([key, value]) => [
      INPUT_LABELS[key] ?? key,
      key === 'amount' || key === 'new_limit' ? `¥${value}` : String(value),
    ])
}

const STATUS_TEXT: Partial<Record<PlanCardStatus, string>> = {
  confirmed: '已确认',
  rejected: '已取消',
  replaced: '已作废',
}

/** One Plan card: every step that will run, with its exact details and the Bank's tier.
 * One tap confirms every Yellow step on it; each Red step then asks for its own SMS code
 * when it is reached. */
export default function PlanCard({
  steps,
  refusals,
  warnings,
  confirmIds,
  status,
  disabled,
  onConfirm,
  onReject,
}: Props) {
  const refusalOf = (id: string) => refusals.find((refusal) => refusal.step_id === id)
  return (
    <div className="plan-card">
      <div className="plan-title">请确认以下计划</div>
      <ol>
        {steps.map((step) => {
          const refusal = refusalOf(step.id)
          const warning = warnings.find((w) => w.step_id === step.id)
          return (
            <li key={step.id} className={confirmIds.includes(step.id) ? 'to-confirm' : ''}>
              <div className="plan-step-head">
                <span>{OPERATION_LABELS[step.operation] ?? step.operation}</span>
                {step.tier && <span className={`tier ${step.tier}`}>{TIER_LABELS[step.tier]}</span>}
              </div>
              <dl>
                {details(step).map(([label, value]) => (
                  <div key={label}>
                    <dt>{label}</dt>
                    <dd>{value}</dd>
                  </div>
                ))}
              </dl>
              {warning && (
                <div className="plan-warning" role="note">
                  {warning.text}
                </div>
              )}
              {refusal && <div className="plan-refusal">银行提示无法执行：{refusal.message}</div>}
            </li>
          )
        })}
      </ol>
      {status === 'awaiting' ? (
        <div className="plan-actions">
          <button type="button" className="secondary" disabled={disabled} onClick={onReject}>
            取消
          </button>
          <button type="button" className="primary" disabled={disabled} onClick={onConfirm}>
            一键确认{confirmIds.length > 1 ? `（${confirmIds.length} 项）` : ''}
          </button>
        </div>
      ) : (
        STATUS_TEXT[status] && <div className="plan-status">{STATUS_TEXT[status]}</div>
      )}
    </div>
  )
}
