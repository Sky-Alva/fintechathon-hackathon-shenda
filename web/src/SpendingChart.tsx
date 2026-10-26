import type { CategoryTotal, Chart } from './events'
import { type Language, useLanguage } from './language'

/** The Bank's spending categories, as the Channel names them. */
const CATEGORY_NAME: Record<Language, Record<string, string>> = {
  zh: {
    income: '收入',
    housing: '住房',
    food_delivery: '外卖',
    transport: '交通',
    subscriptions: '订阅',
    shopping: '购物',
    gifts: '礼物',
    utilities: '水电燃气',
    dining: '餐饮',
    entertainment: '娱乐',
    healthcare: '医疗',
  },
  en: {
    income: 'Income',
    housing: 'Housing',
    food_delivery: 'Food delivery',
    transport: 'Transport',
    subscriptions: 'Subscriptions',
    shopping: 'Shopping',
    gifts: 'Gifts',
    utilities: 'Utilities',
    dining: 'Dining',
    entertainment: 'Entertainment',
    healthcare: 'Healthcare',
  },
}

const WORDS = {
  zh: {
    none: '这段时间没有支出',
    count: (n: number) => `（${n} 笔）`,
    colon: '：',
    income: '收入',
    spending: '支出',
    net: '结余',
    totalSpending: '总支出',
    yearIncome: '全年收入',
    yearSpending: '全年支出',
    monthly: '每月收入与支出',
    byMonth: '按月明细',
    month: '月份',
  },
  en: {
    none: 'No spending in this period',
    count: (n: number) => ` (${n} transaction${n === 1 ? '' : 's'})`,
    colon: ': ',
    income: 'Income',
    spending: 'Spending',
    net: 'Net',
    totalSpending: 'Total spending',
    yearIncome: 'Income this year',
    yearSpending: 'Spending this year',
    monthly: 'Monthly income and spending',
    byMonth: 'By month',
    month: 'Month',
  },
}

/** Bar lengths are scaled from the Bank's amounts; every number shown is the Bank's own
 * string, never one the Channel computed or the model wrote. */
const amount = (value: string) => Number(value)

/** Categories by amount, largest first, as horizontal bars: the Bank's figure on each. */
function CategoryBars({ categories }: { categories: CategoryTotal[] }) {
  const sorted = [...categories].sort((a, b) => amount(b.amount) - amount(a.amount))
  const largest = Math.max(...sorted.map((c) => amount(c.amount)), 0)
  const language = useLanguage()
  const w = WORDS[language]
  const categoryName = (category: string) => CATEGORY_NAME[language][category] ?? category
  if (sorted.length === 0) return <p className="chart-empty">{w.none}</p>
  return (
    <ul className="chart-bars">
      {sorted.map((c) => (
        <li key={c.category} title={`${categoryName(c.category)}${w.colon}¥${c.amount}${w.count(c.transaction_count)}`}>
          <span className="chart-label">{categoryName(c.category)}</span>
          <span className="chart-track">
            <span
              className="chart-bar"
              style={{ width: largest > 0 ? `${(amount(c.amount) / largest) * 100}%` : '0%' }}
            />
          </span>
          <span className="chart-value">¥{c.amount}</span>
        </li>
      ))}
    </ul>
  )
}

const WIDTH = 320
const HEIGHT = 140
const BAR = 9

/** Monthly income and spending side by side, on one axis, from the Bank's annual report. */
function MonthlyBars({
  labels,
  income,
  spending,
}: {
  labels: string[]
  income: string[]
  spending: string[]
}) {
  const largest = Math.max(...income.map(amount), ...spending.map(amount), 0)
  const w = WORDS[useLanguage()]
  const slot = WIDTH / labels.length
  const height = (value: string) => (largest > 0 ? (amount(value) / largest) * HEIGHT : 0)
  const bar = (value: string, x: number, series: 'income' | 'spending', label: string) => {
    const h = height(value)
    return (
      <rect
        className={`chart-${series}`}
        x={x}
        y={HEIGHT - h}
        width={BAR}
        height={h}
        rx={h > 4 ? 2 : 0}
      >
        <title>{`${label} ${series === 'income' ? w.income : w.spending}${w.colon}¥${value}`}</title>
      </rect>
    )
  }
  return (
    <>
      <div className="chart-legend">
        <span>
          <i className="chart-income" /> {w.income}
        </span>
        <span>
          <i className="chart-spending" /> {w.spending}
        </span>
      </div>
      <svg
        className="chart-svg"
        viewBox={`0 0 ${WIDTH} ${HEIGHT + 16}`}
        role="img"
        aria-label={w.monthly}
      >
        <line className="chart-axis" x1={0} x2={WIDTH} y1={HEIGHT} y2={HEIGHT} />
        {labels.map((label, i) => {
          const x = i * slot + (slot - 2 * BAR - 2) / 2
          return (
            <g key={label}>
              {bar(income[i], x, 'income', label)}
              {bar(spending[i], x + BAR + 2, 'spending', label)}
              <text className="chart-tick" x={i * slot + slot / 2} y={HEIGHT + 12}>
                {Number(label.slice(5))}
              </text>
            </g>
          )
        })}
      </svg>
    </>
  )
}

function Totals({ rows }: { rows: [string, string][] }) {
  return (
    <dl className="chart-totals">
      {rows.map(([name, value]) => (
        <div key={name}>
          <dt>{name}</dt>
          <dd>¥{value}</dd>
        </div>
      ))}
    </dl>
  )
}

/** A chart of what the Bank returned for one step (#40): category summary, monthly or
 * annual report. Drawn from the event's `data` only, which is the Bank's output unchanged. */
export default function SpendingChart({ chart }: { chart: Chart }) {
  const w = WORDS[useLanguage()]
  return (
    <figure className="chart">
      <figcaption>{chart.title}</figcaption>
      {chart.operation === 'spending_by_category' && (
        <>
          <Totals rows={[[w.totalSpending, chart.data.total_spending]]} />
          <CategoryBars categories={chart.data.categories} />
        </>
      )}
      {chart.operation === 'monthly_report' && (
        <>
          <Totals
            rows={[
              [w.income, chart.data.total_income],
              [w.spending, chart.data.total_spending],
              [w.net, chart.data.net],
            ]}
          />
          <CategoryBars categories={chart.data.categories} />
        </>
      )}
      {chart.operation === 'annual_report' && (
        <>
          <Totals
            rows={[
              [w.yearIncome, chart.data.total_income],
              [w.yearSpending, chart.data.total_spending],
              [w.net, chart.data.net],
            ]}
          />
          <MonthlyBars
            labels={chart.data.labels}
            income={chart.data.monthly_income}
            spending={chart.data.monthly_spending}
          />
          <details className="chart-table">
            <summary>{w.byMonth}</summary>
            <table>
              <thead>
                <tr>
                  <th>{w.month}</th>
                  <th>{w.income}</th>
                  <th>{w.spending}</th>
                  <th>{w.net}</th>
                </tr>
              </thead>
              <tbody>
                {chart.data.labels.map((label, i) => (
                  <tr key={label}>
                    <td>{label}</td>
                    <td>{chart.data.monthly_income[i]}</td>
                    <td>{chart.data.monthly_spending[i]}</td>
                    <td>{chart.data.monthly_net[i]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </>
      )}
    </figure>
  )
}
