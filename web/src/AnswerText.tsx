import type { ReactNode } from 'react'

/** A table row in Markdown: `| a | b |`. */
const ROW = /^\s*\|.*\|\s*$/
/** The row under a Markdown table's header: `|---|:--:|`. */
const RULE = /^\s*\|(\s*:?-{3,}:?\s*\|)+\s*$/
const HEADING = /^\s*#{1,6}\s+/

function cells(row: string): string[] {
  return row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim())
}

/** `**bold**` as bold; any other text as it is. */
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') && part.length > 4 ? (
      <strong key={i}>{part.slice(2, -2)}</strong>
    ) : (
      part
    ),
  )
}

/** The Agent's answer as the bubble shows it. The Agent is told to write plain sentences;
 * if Markdown still comes through, a table is drawn as a table that fits the bubble and
 * bold and headings as bold, never as raw `|` and `**`. Nothing is added or computed. */
export default function AnswerText({ text }: { text: string }) {
  const lines = text.split('\n')
  const blocks: ReactNode[] = []
  let prose: string[] = []
  const flushProse = () => {
    // A table is a block of its own: the blank lines around it would only add a gap.
    while (prose.length > 0 && prose[0].trim() === '') prose.shift()
    while (prose.length > 0 && prose[prose.length - 1].trim() === '') prose.pop()
    if (prose.length === 0) return
    blocks.push(
      <div key={blocks.length}>
        {prose.map((line, i) => (
          <span key={i}>
            {i > 0 && '\n'}
            {HEADING.test(line) ? <strong>{inline(line.replace(HEADING, ''))}</strong> : inline(line)}
          </span>
        ))}
      </div>,
    )
    prose = []
  }
  for (let i = 0; i < lines.length; ) {
    if (ROW.test(lines[i]) && i + 1 < lines.length && RULE.test(lines[i + 1])) {
      flushProse()
      const head = cells(lines[i])
      const body: string[][] = []
      i += 2
      while (i < lines.length && ROW.test(lines[i])) body.push(cells(lines[i++]))
      blocks.push(
        <table key={blocks.length}>
          <thead>
            <tr>
              {head.map((cell, c) => (
                <th key={c}>{inline(cell)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((row, r) => (
              <tr key={r}>
                {row.map((cell, c) => (
                  <td key={c}>{inline(cell)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>,
      )
      continue
    }
    prose.push(lines[i++])
  }
  flushProse()
  return <>{blocks}</>
}
