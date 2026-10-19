import type { Declined } from './events'

// A declined request, for the decision trace and the staff console.
export function describeDeclined(declined: Declined): string {
  if (declined.reason === 'manipulation') {
    return `已拒绝并报告银行（疑似${declined.kind === 'injection' ? '注入' : '冒充诈骗'}）：“${declined.evidence}”`
  }
  return `已拒绝（${declined.reason}），可改为：${declined.can_do.join('、')}`
}
