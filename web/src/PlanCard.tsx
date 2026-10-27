import { useState } from 'react'
import type { BankRefusal, CardPayee, PlanStep, PlanWarning, StepCard, Tier } from './events'
import { type Language, useLanguage } from './language'

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
  /** Which card each card step acts on, as the Bank listed it, so the Customer can check it. */
  cards: StepCard[]
  confirmIds: string[]
  status: PlanCardStatus
  disabled: boolean
  onConfirm: () => void
  /** The steps the Customer took off the card: the Bank re-tiers the rest, as a new card. */
  onRemove: (removedIds: string[]) => void
  onReject: () => void
}

const OPERATION_LABELS: Record<Language, Record<string, string>> = {
  zh: {
    transfer: '转账',
    add_card_lock: '添加卡锁',
    lower_card_limit: '调低卡限额',
    raise_card_limit: '调高卡限额',
    lift_card_lock: '解除卡锁',
    file_loss_report: '挂失',
    withdraw_loss_report: '撤销挂失',
    apply_virtual_card: '申请虚拟卡',
    reserve_funds: '预留资金',
    order_from_merchant: '向商户下单（送达日从预留资金付款）',
    cancel_subscription: '取消订阅',
  },
  en: {
    transfer: 'Transfer',
    add_card_lock: 'Add a Card Lock',
    lower_card_limit: 'Lower the Card Limit',
    raise_card_limit: 'Raise the Card Limit',
    lift_card_lock: 'Lift a Card Lock',
    file_loss_report: 'File a Loss Report',
    withdraw_loss_report: 'Withdraw the Loss Report',
    apply_virtual_card: 'Apply for a virtual card',
    reserve_funds: 'Reserve funds',
    order_from_merchant: 'Order from a Merchant (paid from Reserved Funds on delivery)',
    cancel_subscription: 'Cancel a Subscription',
  },
}

const TIER_LABELS: Record<Language, Record<Tier, string>> = {
  zh: { green: '绿色 · 自动执行', yellow: '黄色 · 需确认', red: '红色 · 需短信验证' },
  en: { green: 'Green · runs by itself', yellow: 'Yellow · needs your Confirmation', red: 'Red · needs an SMS code' },
}

const INPUT_LABELS: Record<Language, Record<string, string>> = {
  zh: {
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
    subscription_id: '订阅',
  },
  en: {
    payee_id: 'Payee',
    amount: 'Amount',
    note: 'Note',
    card_id: 'Card',
    scope: 'Scope',
    new_limit: 'New limit',
    name: 'Name',
    purpose: 'Purpose',
    merchant_categories: 'Can pay',
    merchant_id: 'Merchant',
    product_id: 'Product',
    quantity: 'Quantity',
    delivery_date: 'Delivery date',
    reservation_id: 'Reserved Funds',
    subscription_id: 'Subscription',
  },
}

const CATEGORY_LABELS: Record<Language, Record<string, string>> = {
  zh: { flowers: '鲜花店', cake: '蛋糕店' },
  en: { flowers: 'flower shops', cake: 'cake shops' },
}

const WORDS = {
  zh: {
    title: '请确认以下计划',
    restore: '恢复',
    remove: '移除',
    newPayee: (name: string) => `新收款人：您从未向 ${name} 转过账，请核对收款人信息`,
    refused: (message: string) => `银行提示无法执行：${message}`,
    reTier: '银行将按新的顺序重新评估剩余步骤，您再确认一次',
    reject: '取消',
    update: '更新计划',
    confirm: (n: number) => `一键确认${n > 1 ? `（${n} 项）` : ''}`,
    separator: '、',
    payee: (name: string, extra: string[]) => (extra.length ? `${name}（${extra.join('，')}）` : name),
    card: (name: string, last4: string) => `${name}，尾号 ${last4}`,
  },
  en: {
    title: 'Please confirm this Plan',
    restore: 'Restore',
    remove: 'Remove',
    newPayee: (name: string) => `New Payee: you've never sent money to ${name}. Please check.`,
    refused: (message: string) => `The Bank says this can't run: ${message}`,
    reTier: 'The Bank re-tiers the rest in their new order; you confirm once more',
    reject: 'Cancel',
    update: 'Update the Plan',
    confirm: (n: number) => `Confirm${n > 1 ? ` (${n})` : ''}`,
    separator: ', ',
    payee: (name: string, extra: string[]) => (extra.length ? `${name} (${extra.join(', ')})` : name),
    card: (name: string, last4: string) => `${name}, ending ${last4}`,
  },
}

const STATUS_TEXT: Record<Language, Partial<Record<PlanCardStatus, string>>> = {
  zh: { confirmed: '已确认', rejected: '已取消', edited: '已修改，请确认新的计划', replaced: '已作废' },
  en: {
    confirmed: 'Confirmed',
    rejected: 'Cancelled',
    edited: 'Changed: please confirm the new Plan',
    replaced: 'Replaced',
  },
}

function shownValue(key: string, value: unknown, language: Language): string {
  if (key === 'amount' || key === 'new_limit') return `¥${value}`
  if (key === 'merchant_categories' && Array.isArray(value))
    return value
      .map((category) => CATEGORY_LABELS[language][category] ?? String(category))
      .join(WORDS[language].separator)
  return String(value)
}

/** Every input of the step, exactly as it will be sent to the Bank; a Payee the lookup
 * found is named, with their nickname and masked phone, and a card the Bank listed by its
 * name and last four digits. */
function details(
  step: PlanStep,
  language: Language,
  payee?: CardPayee,
  card?: StepCard,
): [string, string][] {
  const labels = INPUT_LABELS[language]
  return Object.entries(step.input)
    .filter(([, value]) => value !== '' && value !== null && value !== undefined)
    .map(([key, value]) => {
      if (key === 'payee_id' && payee) {
        const extra = [payee.nickname, payee.phone].filter((part): part is string => !!part)
        return [labels[key], WORDS[language].payee(payee.name, extra)]
      }
      if (key === 'card_id' && card) return [labels[key], WORDS[language].card(card.name, card.last4)]
      return [labels[key] ?? key, shownValue(key, value, language)]
    })
}

/** One Plan card: every step that will run, with its exact details and the Bank's tier.
 * The Customer can take steps off it first; one tap confirms every Yellow step left, and
 * each Red step then asks for its own SMS code when it is reached. */
export default function PlanCard({
  steps,
  refusals,
  warnings,
  payees,
  cards,
  confirmIds,
  status,
  disabled,
  onConfirm,
  onRemove,
  onReject,
}: Props) {
  const [removed, setRemoved] = useState<string[]>([])
  const language = useLanguage()
  const w = WORDS[language]
  const refusalOf = (id: string) => refusals.find((refusal) => refusal.step_id === id)
  const awaiting = status === 'awaiting'
  const toggle = (id: string) =>
    setRemoved((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]))
  const left = steps.filter((step) => !removed.includes(step.id))
  const confirming = confirmIds.filter((id) => !removed.includes(id)).length
  const statusText = STATUS_TEXT[language][status]
  return (
    <div className="plan-card">
      <div className="plan-title">{w.title}</div>
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
          const card = cards.find((c) => c.step_id === step.id)
          return (
            <li key={step.id} className={classes.trim()}>
              <div className="plan-step-head">
                <span>
                  {index + 1}. {OPERATION_LABELS[language][step.operation] ?? step.operation}
                </span>
                <span>
                  {/* Tiers depend on order: after a removal the Bank re-tiers the rest. */}
                  {step.tier && !isRemoved && (
                    <span className={`tier ${step.tier}`}>
                      {TIER_LABELS[language][step.tier]}
                    </span>
                  )}
                  {awaiting && steps.length > 1 && (
                    <button
                      type="button"
                      className="link remove-step"
                      disabled={disabled}
                      onClick={() => toggle(step.id)}
                    >
                      {isRemoved ? w.restore : w.remove}
                    </button>
                  )}
                </span>
              </div>
              <dl>
                {details(step, language, payee, card).map(([label, value]) => (
                  <div key={label}>
                    <dt>{label}</dt>
                    <dd>{value}</dd>
                  </div>
                ))}
              </dl>
              {payee?.is_new && (
                <div className="plan-warning" role="note">
                  {w.newPayee(payee.name)}
                </div>
              )}
              {warning && (
                <div className="plan-warning" role="note">
                  {warning.text}
                </div>
              )}
              {refusal && <div className="plan-refusal">{w.refused(refusal.message)}</div>}
            </li>
          )
        })}
      </ol>
      {awaiting ? (
        <div className="plan-actions">
          {removed.length > 0 && left.length > 0 && <span className="plan-note">{w.reTier}</span>}
          <button type="button" className="secondary" disabled={disabled} onClick={onReject}>
            {w.reject}
          </button>
          <button
            type="button"
            className="primary"
            disabled={disabled || left.length === 0}
            onClick={() => (removed.length ? onRemove(removed) : onConfirm())}
          >
            {removed.length ? w.update : w.confirm(confirming)}
          </button>
        </div>
      ) : (
        statusText && <div className="plan-status">{statusText}</div>
      )}
    </div>
  )
}
