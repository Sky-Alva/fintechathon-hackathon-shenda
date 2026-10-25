import { useState } from 'react'
import type { BankRefusal, CardPayee, PlanStep, PlanWarning, Tier } from './events'

/** proposed → awaiting (confirmation_requested) → confirmed | rejected | edited (steps taken
 * off: a new card follows), or replaced when the Customer sends a new message instead. */
export type PlanCardStatus =
  | 'proposed'
  | 'awaiting'
  | 'confirmed'
  | 'rejected'
  | 'edited'
  | 'replaced'

type Props = {
  steps: PlanStep[]
  refusals: (BankRefusal & { step_id: string })[]
  /** Why each Red step needs Strong Verification, e.g. it crosses the ¥1,000 daily line. */
  warnings: PlanWarning[]
  /** Who each transfer pays, as the Bank's lookup found them; `is_new`: never paid. */
  payees: CardPayee[]
  confirmIds: string[]
  status: PlanCardStatus
  disabled: boolean
  onConfirm: () => void
  /** The steps the Customer took off the card: the Bank re-tiers the rest, as a new card. */
  onRemove: (removedIds: string[]) => void
  onReject: () => void
}

const OPERATION_LABELS: Record<string, string> = {
  transfer: '转账',
  add_card_lock: '添加卡锁',
  lower_card_limit: '调低卡限额',
  apply_virtual_card: '申请虚拟卡',
  reserve_funds: '预留资金',
  order_from_merchant: '向商户下单（送达日从预留资金付款）',
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
  purpose: '用途',
  merchant_categories: '可付款商户',
  merchant_id: '商户',
  product_id: '商品',
  quantity: '数量',
  delivery_date: '送达日期',
  reservation_id: '预留资金',
}

const CATEGORY_LABELS: Record<string, string> = { flowers: '鲜花店', cake: '蛋糕店' }

function shownValue(key: string, value: unknown): string {
  if (key === 'amount' || key === 'new_limit') return `¥${value}`
  if (key === 'merchant_categories' && Array.isArray(value))
    return value.map((category) => CATEGORY_LABELS[category] ?? String(category)).join('、')
  return String(value)
}

/** Every input of the step, exactly as it will be sent to the Bank; a Payee the lookup
 * found is named, with their nickname and masked phone. */
function details(step: PlanStep, payee?: CardPayee): [string, string][] {
  return Object.entries(step.input)
    .filter(([, value]) => value !== '' && value !== null && value !== undefined)
    .map(([key, value]) => {
      if (key === 'payee_id' && payee) {
        const extra = [payee.nickname, payee.phone].filter(Boolean).join('，')
        return [INPUT_LABELS[key], extra ? `${payee.name}（${extra}）` : payee.name]
      }
      return [INPUT_LABELS[key] ?? key, shownValue(key, value)]
    })
}

const STATUS_TEXT: Partial<Record<PlanCardStatus, string>> = {
  confirmed: '已确认',
  rejected: '已取消',
  edited: '已修改，请确认新的计划',
  replaced: '已作废',
}

/** One Plan card: every step that will run, with its exact details and the Bank's tier.
 * The Customer can take steps off it first; one tap confirms every Yellow step left, and
 * each Red step then asks for its own SMS code when it is reached. */
export default function PlanCard({
  steps,
  refusals,
  warnings,
  payees,
  confirmIds,
  status,
  disabled,
  onConfirm,
  onRemove,
  onReject,
}: Props) {
  const [removed, setRemoved] = useState<string[]>([])
  const refusalOf = (id: string) => refusals.find((refusal) => refusal.step_id === id)
  const awaiting = status === 'awaiting'
  const toggle = (id: string) =>
    setRemoved((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]))
  const left = steps.filter((step) => !removed.includes(step.id))
  const confirming = confirmIds.filter((id) => !removed.includes(id)).length
  return (
    <div className="plan-card">
      <div className="plan-title">请确认以下计划</div>
      <ol>
        {steps.map((step, index) => {
          const refusal = refusalOf(step.id)
          const isRemoved = removed.includes(step.id)
          const classes = [
            confirmIds.includes(step.id) ? 'to-confirm' : '',
            isRemoved ? 'removed' : '',
          ].join(' ')
          // A removal re-tiers the rest, so a warning about the daily line may no longer hold.
          const warning = removed.length ? undefined : warnings.find((w) => w.step_id === step.id)
          const payee = payees.find((p) => p.step_id === step.id)
          return (
            <li key={step.id} className={classes.trim()}>
              <div className="plan-step-head">
                <span>
                  {index + 1}. {OPERATION_LABELS[step.operation] ?? step.operation}
                </span>
                <span>
                  {/* Tiers depend on order: after a removal the Bank re-tiers the rest. */}
                  {step.tier && !isRemoved && (
                    <span className={`tier ${step.tier}`}>{TIER_LABELS[step.tier]}</span>
                  )}
                  {awaiting && steps.length > 1 && (
                    <button
                      type="button"
                      className="link remove-step"
                      disabled={disabled}
                      onClick={() => toggle(step.id)}
                    >
                      {isRemoved ? '恢复' : '移除'}
                    </button>
                  )}
                </span>
              </div>
              <dl>
                {details(step, payee).map(([label, value]) => (
                  <div key={label}>
                    <dt>{label}</dt>
                    <dd>{value}</dd>
                  </div>
                ))}
              </dl>
              {payee?.is_new && (
                <div className="plan-warning" role="note">
                  新收款人：您从未向 {payee.name} 转过账，请核对收款人信息
                </div>
              )}
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
      {awaiting ? (
        <div className="plan-actions">
          {removed.length > 0 && left.length > 0 && (
            <span className="plan-note">银行将按新的顺序重新评估剩余步骤，您再确认一次</span>
          )}
          <button type="button" className="secondary" disabled={disabled} onClick={onReject}>
            取消
          </button>
          <button
            type="button"
            className="primary"
            disabled={disabled || left.length === 0}
            onClick={() => (removed.length ? onRemove(removed) : onConfirm())}
          >
            {removed.length
              ? '更新计划'
              : `一键确认${confirming > 1 ? `（${confirming} 项）` : ''}`}
          </button>
        </div>
      ) : (
        STATUS_TEXT[status] && <div className="plan-status">{STATUS_TEXT[status]}</div>
      )}
    </div>
  )
}
