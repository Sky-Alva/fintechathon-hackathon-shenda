import type { CategoryTotal, Chart } from './events'

/** The Bank's spending categories, as the Channel names them. */
const CATEGORY_NAME: Record<string, string> = {
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
}

const categoryName = (category: string) => CATEGORY_NAME[category] ?? category

/** Bar lengths are scaled from the Bank's amounts; every number shown is the Bank's own
 * string, never one the Channel computed or the model wrote. */
const amount = (value: string) => Number(value)

/** Categories by amount, largest first, as horizontal bars: the Bank's figure on each. */
function CategoryBars({ categories }: { categories: CategoryTotal[] }) {
  const sorted = [...categories].sort((a, b) => amount(b.amount) - amount(a.amount))
  const largest = Math.max(...sorted.map((c) => amount(c.amount)), 0)
  if (sorted.length === 0) return <p className="chart-empty">这段时间没有支出</p>
  return (
    <ul className="chart-bars">
      {sorted.map((c) => (
        <li key={c.category} title={`${categoryName(c.category)}：¥${c.amount}（${c.transaction_count} 笔）`}>
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
        <title>{`${label} ${series === 'income' ? '收入' : '支出'}：¥${value}`}</title>
      </rect>
    )
  }
  return (
    <>
      <div className="chart-legend">
        <span>
          <i className="chart-income" /> 收入
        </span>
        <span>
          <i className="chart-spending" /> 支出
        </span>
      </div>
      <svg
        className="chart-svg"
        viewBox={`0 0 ${WIDTH} ${HEIGHT + 16}`}
        role="img"
        aria-label="每月收入与支出"
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
  return (
    <figure className="chart">
      <figcaption>{chart.title}</figcaption>
      {chart.operation === 'spending_by_category' && (
        <>
          <Totals rows={[['总支出', chart.data.total_spending]]} />
          <CategoryBars categories={chart.data.categories} />
        </>
      )}
      {chart.operation === 'monthly_report' && (
        <>
          <Totals
            rows={[
              ['收入', chart.data.total_income],
              ['支出', chart.data.total_spending],
              ['结余', chart.data.net],
            ]}
          />
          <CategoryBars categories={chart.data.categories} />
        </>
      )}
      {chart.operation === 'annual_report' && (
        <>
          <Totals
            rows={[
              ['全年收入', chart.data.total_income],
              ['全年支出', chart.data.total_spending],
              ['结余', chart.data.net],
            ]}
          />
          <MonthlyBars
            labels={chart.data.labels}
            income={chart.data.monthly_income}
            spending={chart.data.monthly_spending}
          />
          <details className="chart-table">
            <summary>按月明细</summary>
            <table>
              <thead>
                <tr>
                  <th>月份</th>
                  <th>收入</th>
                  <th>支出</th>
                  <th>结余</th>
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
