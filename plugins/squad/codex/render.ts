import type { SquadActivity, SquadAgent } from '../types'
import { formatDuration, formatTokens, modelLabel, totalTokens } from '../hooks/format'
import { ACTIVITY_LABEL, SPRITE_COLUMNS, SPRITE_ROWS, hex, modelColor, spriteCells } from '../hooks/sprite'

// The squad panel drawn as plain terminal lines, for a viewer running beside Codex.
// Same rows as the Claude Code pane: vehicle, title, model, calls, tokens, time, bar.

const SPINNER = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
const DONE_COLOR = 0x5fb87a
const FAILED_COLOR = 0xe06c75
const STALL_COLOR = 0xe5c07b
const BAR_TRACK = 0x3a3f4b
const PAD = 2
const SWEEP_TAIL = 10
const FULL_ROW_LINES = 5
const SPRITE_GAP = 2
const DEFAULT = 0x01000000

type Style = { fg?: number; bold?: boolean; dim?: boolean }
type Seg = { text: string; style?: Style }

const sgr = (s: Style | undefined): string => {
  if (!s) return ''
  const codes: string[] = []
  if (s.bold) codes.push('1')
  if (s.dim) codes.push('2')
  if (s.fg !== undefined) codes.push(`38;2;${(s.fg >> 16) & 0xff};${(s.fg >> 8) & 0xff};${s.fg & 0xff}`)
  return codes.length ? `\x1b[${codes.join(';')}m` : ''
}

const visible = (segs: Seg[]): number => segs.reduce((n, s) => n + [...s.text].length, 0)

// Segments cut to `width` visible cells, the last kept one ending in '…' when cut.
const fit = (segs: Seg[], width: number): Seg[] => {
  if (visible(segs) <= width) return segs
  const out: Seg[] = []
  let left = Math.max(0, width - 1)
  for (const s of segs) {
    if (left <= 0) break
    const chars = [...s.text]
    out.push({ ...s, text: chars.slice(0, left).join('') })
    left -= Math.min(left, chars.length)
  }
  return [...out, { text: '…' }]
}

const paint = (segs: Seg[]): string => segs.map(s => (s.style ? `${sgr(s.style)}${s.text}\x1b[0m` : s.text)).join('')

// `left` cut to leave room for `right`, then `right` flush to the edge.
const spread = (left: Seg[], right: Seg[], width: number): string => {
  const r = fit(right, width)
  const l = fit(left, Math.max(0, width - visible(r) - 1))
  return paint(l) + ' '.repeat(Math.max(1, width - visible(l) - visible(r))) + paint(r)
}

const mix = (from: number, to: number, t: number): number => {
  const ch = (shift: number) =>
    Math.round(((from >> shift) & 0xff) + (((to >> shift) & 0xff) - ((from >> shift) & 0xff)) * t)
  return (ch(16) << 16) | (ch(8) << 8) | ch(0)
}

export const bar = (a: SquadAgent, width: number, step: number): string => {
  if (a.status !== 'running') {
    return paint([{ text: '━'.repeat(width), style: { fg: a.status === 'failed' ? FAILED_COLOR : modelColor(a.model) } }])
  }
  const head = step % (width + SWEEP_TAIL - 1)
  const from = Math.max(0, head - SWEEP_TAIL + 1)
  const to = Math.min(width - 1, head)
  const segs: Seg[] = [{ text: '─'.repeat(from), style: { fg: BAR_TRACK } }]
  for (let i = from; i <= to; i++) segs.push({ text: '━', style: { fg: mix(BAR_TRACK, modelColor(a.model), 1 - (head - i) / SWEEP_TAIL) } })
  segs.push({ text: '─'.repeat(Math.max(0, width - from - (to - from + 1))), style: { fg: BAR_TRACK } })
  return paint(segs)
}

// The vehicle: what a running agent is doing now, what a finished one did most.
export const vehicleOf = (a: SquadAgent): SquadActivity | undefined => {
  if (a.status !== 'running' && a.activityCounts !== undefined) {
    const counted = Object.entries(a.activityCounts) as [SquadActivity, number][]
    const top = counted.filter(([kind]) => kind !== 'answer').sort((x, y) => y[1] - x[1])[0]
    if (top !== undefined) return top[0]
  }
  return a.activity
}

// The block vehicle's three lines: quadrant glyphs in 24-bit color.
export const spriteLines = (a: SquadAgent, frame: number): string[] => {
  const bytes = Uint8Array.from(atob(spriteCells({ activity: vehicleOf(a), model: a.model, isFailed: a.status === 'failed', frame })), c => c.charCodeAt(0))
  const view = new DataView(bytes.buffer)
  const lines: string[] = []
  for (let row = 0; row < SPRITE_ROWS; row++) {
    let line = ''
    for (let col = 0; col < SPRITE_COLUMNS; col++) {
      const at = (row * SPRITE_COLUMNS + col) * 12
      const glyph = view.getUint32(at, true)
      const fg = view.getUint32(at + 4, true)
      const bg = view.getUint32(at + 8, true)
      const fgCode = fg === DEFAULT ? '39' : `38;2;${(fg >> 16) & 0xff};${(fg >> 8) & 0xff};${fg & 0xff}`
      const bgCode = bg === DEFAULT ? '49' : `48;2;${(bg >> 16) & 0xff};${(bg >> 8) & 0xff};${bg & 0xff}`
      line += `\x1b[${fgCode};${bgCode}m${String.fromCodePoint(glyph)}`
    }
    lines.push(`${line}\x1b[0m`)
  }
  return lines
}

export type View = {
  agents: SquadAgent[]
  now: number
  frame: number // once a second: spinner and vehicle animation
  step: number // ten times a second: the bar's sweep
  columns: number
  rows: number
  header: string
  stallMs: number
  // Pixel vehicles (Ghostty, kitty): leave the vehicle's cells blank and report where to place it.
  hasPixels: boolean
}

export type Placement = { agent: SquadAgent; line: number; column: number }

const isIdle = (a: SquadAgent, now: number, limitMs: number): boolean =>
  a.status === 'running' && now - (a.lastActivityAt ?? a.startedAt) > limitMs

const idleLabel = (a: SquadAgent, now: number): string => {
  const sec = Math.floor((now - (a.lastActivityAt ?? a.startedAt)) / 1000)
  return `idle ${sec >= 60 ? `${Math.floor(sec / 60)}m` : `${sec}s`}`
}

const callsLabel = (a: SquadAgent): string =>
  a.toolCalls === undefined ? '' : `${a.toolCalls} ${a.toolCalls === 1 ? 'call' : 'calls'}`

export const render = (v: View): { lines: string[]; placements: Placement[] } => {
  const width = Math.max(20, v.columns - PAD * 2)
  const margin = ' '.repeat(PAD)
  const lines: string[] = [margin + paint(fit([{ text: v.header, style: { dim: true } }], width))]
  const placements: Placement[] = []
  const running = v.agents.filter(a => a.status === 'running')
  const finished = v.agents.filter(a => a.status !== 'running').reverse()
  const chrome = 1 + (running.length > 0 ? 2 : 0) + (finished.length > 0 ? 2 : 0)
  const isCompact = chrome + (running.length + finished.length) * FULL_ROW_LINES > v.rows

  const mark = (a: SquadAgent): Seg =>
    a.status === 'running'
      ? { text: SPINNER[v.frame % SPINNER.length] ?? '', style: { fg: modelColor(a.model) } }
      : a.status === 'done'
        ? { text: '✓', style: { fg: DONE_COLOR } }
        : { text: '✗', style: { fg: FAILED_COLOR } }

  const row = (a: SquadAgent) => {
    const idle: Seg[] = isIdle(a, v.now, v.stallMs) ? [{ text: ` ${idleLabel(a, v.now)}`, style: { fg: STALL_COLOR } }] : []
    const ctx = a.contextWindow ? `ctx ${Math.round((a.contextTokens / a.contextWindow) * 100)}% · ` : ''
    const meta = `${ctx}${formatTokens(totalTokens(a.usage))} · ${formatDuration((a.endedAt ?? v.now) - a.startedAt)}`
    const title: Seg = { text: a.title, style: { bold: true } }

    if (isCompact) {
      const right: Seg[] = [...idle, { text: ` ${[modelLabel(a.model), callsLabel(a)].filter(Boolean).join(' · ')} `, style: { dim: true } }, mark(a)]
      lines.push(margin + spread([title], right, width))
      lines.push(margin + bar(a, width, v.step))
      return
    }

    const textWidth = width - SPRITE_COLUMNS - SPRITE_GAP
    const vehicle = vehicleOf(a)
    const sprite = v.hasPixels ? ['', '', ''].map(() => ' '.repeat(SPRITE_COLUMNS)) : spriteLines(a, a.status === 'running' ? v.frame : 0)
    if (v.hasPixels) placements.push({ agent: a, line: lines.length, column: PAD })
    const gap = ' '.repeat(SPRITE_GAP)
    const text = [
      spread([title], [mark(a)], textWidth),
      paint(fit([
        { text: a.type, style: { fg: modelColor(a.model) } },
        { text: ` ${modelLabel(a.model)}${a.effort ? ` · ${a.effort}` : ''}` },
        ...(vehicle ? [{ text: ` · ${ACTIVITY_LABEL[vehicle]}`, style: { dim: true } }] : []),
      ], textWidth)),
      spread([{ text: callsLabel(a) }, ...idle], [{ text: meta, style: { dim: true } }], textWidth),
      bar(a, textWidth, v.step),
    ]
    text.forEach((t, i) => lines.push(margin + (sprite[i] ?? ' '.repeat(SPRITE_COLUMNS)) + gap + t))
    lines.push('')
  }

  if (v.agents.length === 0) lines.push('', margin + paint([{ text: 'No subagents yet in this session.', style: { dim: true } }]))
  if (running.length > 0) {
    lines.push('', margin + paint([{ text: `Running · ${running.length}`, style: { bold: true } }]))
    running.forEach(row)
  }
  if (finished.length > 0) {
    lines.push('', margin + paint([{ text: `Finished · ${finished.length}`, style: { bold: true } }]))
    finished.forEach(row)
  }
  return { lines: lines.slice(0, v.rows), placements: placements.filter(p => p.line + SPRITE_ROWS <= v.rows) }
}
