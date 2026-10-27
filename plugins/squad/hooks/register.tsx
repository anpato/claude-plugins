import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { SquadActivity, SquadAgent, SquadUsage } from '../types'
import { formatDuration, formatTokens, modelLabel, totalTokens } from './format'
import { HIRES_COLUMNS, HIRES_ROWS, hiresPixels } from './hires'
import { ACTIVITY_LABEL, SPRITE_COLUMNS, SPRITE_ROWS, hex, modelColor, spriteCells } from './sprite'

const PANE = 'squad'
const STEP_TOOL = 'mcp__squad__step'
const SPINNER = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
const DONE_COLOR = '#5fb87a'
const FAILED_COLOR = '#e06c75'
const BAR_TRACK = '#3a3f4b'
const BAR_TRACK_RGB = 0x3a3f4b
const VIEWED_COLOR = '#61afef'
// Cells kept clear at the pane's left and right edges.
const PANE_PADDING = 2
// The bar shows activity, not progress: a pulse sweeps left to right, fading
// behind it, while the agent works; finished it is solid.
const SWEEP_MS = 100
const SWEEP_TAIL = 10
// Rows a full agent row takes (four lines and a gap) and a compact one (title and bar).
const FULL_ROW_LINES = 5
const COMPACT_ROW_LINES = 2

const STEP_NOTE =
  '\n\n(Progress reporting: if `mcp__squad__step` is among your tools, call it once you have ' +
  'a plan, with `total` steps and `done: 0`, then again with `done` and a few-word `note` as ' +
  'each step finishes. Skip it if the tool is absent.)'

const agents = atom({ plugin: 'squad', key: 'agents' } as const, [])
const tick = atom({ plugin: 'squad', key: 'tick' } as const, 0)
const sweep = atom({ plugin: 'squad', key: 'sweep' } as const, 0)
const openable = atom({ plugin: 'squad', key: 'openable' } as const, [])
const contextWindow = atom({ plugin: 'squad', key: 'contextWindow' } as const, 0)
const isFinishedCollapsed = atom({ plugin: 'squad', key: 'isFinishedCollapsed' } as const, false)
const hasPixels = atom({ plugin: 'squad', key: 'hasPixels' } as const, false)
// Offload runs read from disk, shown as rows beside the subagents.
const offloadRuns = atom({ plugin: 'squad', key: 'offloadRuns' } as const, [])

const DEFAULT_STALL_AFTER_SEC = 120
const STALL_COLOR = '#e5c07b'
const OFFLOAD_POLL_MS = 5000
const OFFLOAD_RUNS_DIR = '.cache/offload/runs'

// A finished agent's row is dropped this long after it ended.
const FINISHED_ROW_TTL_MS = 60 * 60 * 1000

const SEARCH_TOOLS = new Set(['Read', 'Grep', 'Glob', 'LSP', 'ToolSearch', 'NotebookRead'])
const EDIT_TOOLS = new Set(['Edit', 'MultiEdit', 'Write', 'NotebookEdit'])
const RUN_TOOLS = new Set(['Bash', 'PowerShell', 'Monitor', 'TaskStop'])
const WEB_TOOLS = new Set(['WebFetch', 'WebSearch'])

// What a tool call says the agent is doing; undefined for calls that say nothing.
const activityOfTool = (tool: string): SquadActivity | undefined => {
  if (tool === STEP_TOOL) return undefined
  if (SEARCH_TOOLS.has(tool)) return 'search'
  if (EDIT_TOOLS.has(tool)) return 'edit'
  if (RUN_TOOLS.has(tool)) return 'run'
  if (WEB_TOOLS.has(tool) || tool.startsWith('mcp__')) return 'web'
  return undefined
}

const withActivity = (a: SquadAgent, activity: SquadActivity): SquadAgent => ({
  ...a,
  activity,
  activityCounts: { ...a.activityCounts, [activity]: (a.activityCounts?.[activity] ?? 0) + 1 },
})

// The vehicle to draw: what a running agent is doing now, what a finished one
// did most; an Explore agent that has not called a tool yet is searching.
const vehicleOf = (a: SquadAgent): SquadActivity | undefined => {
  if (a.status !== 'running' && a.activityCounts !== undefined) {
    const counted = Object.entries(a.activityCounts) as [SquadActivity, number][]
    const top = counted.filter(([kind]) => kind !== 'answer').sort((x, y) => y[1] - x[1])[0]
    if (top !== undefined) return top[0]
  }
  return a.activity ?? (a.type === 'Explore' ? 'search' : undefined)
}

const ZERO: SquadUsage = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }

const addUsage = (a: SquadUsage, b: SquadUsage): SquadUsage => ({
  input: a.input + b.input,
  output: a.output + b.output,
  cacheRead: a.cacheRead + b.cacheRead,
  cacheWrite: a.cacheWrite + b.cacheWrite,
})

const patchAgent = ($: EngineInterface, id: string, patch: (a: SquadAgent) => SquadAgent) =>
  update($, agents, list => list.map(a => (a.id === id ? patch(a) : a)))

const openPane = ($: EngineInterface) => $.ui.open({ id: PANE, title: 'Squad', columns: 52 })

const isPaneOpen = async ($: EngineInterface) =>
  (await $.ui.panes().catch(() => [])).some(p => p.id === PANE)

// The agents whose transcripts can still be opened, read once a second and on
// each spawn rather than on every redraw.
const refreshOpenable = async ($: EngineInterface) => {
  const listed = await $.agent.list().catch(() => [])
  const ids = listed.map(a => a.id).sort()
  const current = await read($, openable)
  if (ids.join() !== [...current].sort().join()) await update($, openable, () => ids)
}

// A whole, non-negative count from whatever the model sent; undefined for
// anything that is not a finite number (or numeric string).
const toCount = (value: unknown): number | undefined => {
  const n = typeof value === 'string' && value.trim() !== '' ? Number(value) : value
  return typeof n === 'number' && Number.isFinite(n) ? Math.max(0, Math.round(n)) : undefined
}

// A color between two, `t` from 0 (the first) to 1 (the second).
const mix = (from: number, to: number, t: number): number => {
  const ch = (shift: number) =>
    Math.round(((from >> shift) & 0xff) + (((to >> shift) & 0xff) - ((from >> shift) & 0xff)) * t)
  return (ch(16) << 16) | (ch(8) << 8) | ch(0)
}

// The lit stretch of a working bar: a pulse at `head` with a tail fading out
// over the SWEEP_TAIL - 1 cells behind it. Every frame lights at least one
// cell (the cycle is width + SWEEP_TAIL - 1 long, so it never goes dark).
const sweepPulse = (width: number, step: number): { from: number; levels: number[] } => {
  const head = step % (width + SWEEP_TAIL - 1)
  const from = Math.max(0, head - SWEEP_TAIL + 1)
  const to = Math.min(width - 1, head)
  const levels: number[] = []
  for (let i = from; i <= to; i++) levels.push(1 - (head - i) / SWEEP_TAIL)
  return { from, levels }
}

// A positive number of seconds from a setting, else the default.
const stallAfterMs = (value: unknown): number =>
  (typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : DEFAULT_STALL_AFTER_SEC) * 1000

// A running subagent that has made no tool call or model request for too long.
// Offload runs report no activity, so they are never flagged.
const isIdle = (a: SquadAgent, now: number, limitMs: number): boolean =>
  a.status === 'running' && a.type !== 'offload' && now - (a.lastActivityAt ?? a.startedAt) > limitMs

const idleLabel = (a: SquadAgent, now: number): string => {
  const sec = Math.floor((now - (a.lastActivityAt ?? a.startedAt)) / 1000)
  return `idle ${sec >= 60 ? `${Math.floor(sec / 60)}m` : `${sec}s`}`
}

const callsLabel = (a: SquadAgent): string =>
  a.toolCalls === undefined ? '' : `${a.toolCalls} ${a.toolCalls === 1 ? 'call' : 'calls'}`

const asText = (v: unknown): string | undefined => (typeof v === 'string' && v !== '' ? v : undefined)
const asSeconds = (v: unknown): number | undefined => (typeof v === 'number' && Number.isFinite(v) ? v : undefined)

// One offload run.json as a row, or undefined when it is malformed or too old.
// Missing fields are unknown, never an error.
const offloadRow = (raw: unknown, now: number): SquadAgent | undefined => {
  if (typeof raw !== 'object' || raw === null) return undefined
  const r = raw as Record<string, unknown>
  const runId = asText(r.run_id)
  const state = asText(r.status)
  if (runId === undefined) return undefined
  const created = asSeconds(r.created)
  const isLive = state === 'running' || state === 'prepared'
  const base = {
    id: `offload:${runId}`,
    title: asText(r.slug) ?? runId,
    type: 'offload',
    model: asText(r.model) ?? '',
    toolCalls: typeof r.tool_calls === 'number' ? r.tool_calls : undefined,
    usage: ZERO,
    contextTokens: 0,
  }
  if (isLive) return { ...base, status: 'running', startedAt: created !== undefined ? created * 1000 : now }
  if (state !== 'ok' && state !== 'failed' && state !== 'timeout') return undefined
  const ended = asSeconds(r.ended_at)
  const duration = asSeconds(r.duration_s)
  const endedAt =
    ended !== undefined ? ended * 1000 : created !== undefined && duration !== undefined ? (created + duration) * 1000 : undefined
  // Without a start or an end there is no elapsed time to show or age to judge.
  if (created === undefined || endedAt === undefined || now - endedAt > FINISHED_ROW_TTL_MS) return undefined
  return { ...base, status: state === 'ok' ? 'done' : 'failed', startedAt: created * 1000, endedAt }
}

// Reads ~/.cache/offload/runs/*/run.json. Any failure yields no rows.
const readOffloadRuns = async ($: EngineInterface, now: number): Promise<SquadAgent[]> => {
  try {
    const home = await $.env.get('HOME')
    if (!home) return []
    const dir = `${home}/${OFFLOAD_RUNS_DIR}`
    if (!(await $.fs.exists(dir))) return []
    const entries = await $.fs.list(dir)
    const rows: SquadAgent[] = []
    for (const entry of entries) {
      if (entry.kind !== 'dir') continue
      try {
        const text = await $.fs.read(`${dir}/${entry.name}/run.json`)
        const row = offloadRow(JSON.parse(typeof text === 'string' ? text : ''), now)
        if (row !== undefined) rows.push(row)
      } catch {
        // a missing or malformed run.json skips that run only
      }
    }
    return rows.sort((a, b) => a.startedAt - b.startedAt)
  } catch {
    return []
  }
}

const stepsLabel = (a: SquadAgent): string => {
  if (a.stepsTotal !== undefined) {
    const done = a.status === 'done' ? a.stepsTotal : Math.min(a.stepsTotal, a.stepsDone ?? 0)
    return `${done}/${a.stepsTotal}`
  }
  return a.status === 'running' && !a.toolCalls ? '…' : ''
}

export const register: Register = (on, options) => {
  // Whether the pane is open, refreshed once a second: the sweep only runs then.
  let isOpen = false
  let offloadPolledAt = -Infinity

  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'squad',
      description: 'Show the subagents panel (open, close or reset)',
      argumentHint: '[open|close|reset]',
    })
    // Only when asked: the description alone makes agents call it, about a
    // third of a short agent's tool calls, and activity already shows progress.
    if (options.askForSteps === true) await $.tool.register({
      name: 'step',
      description:
        'Report progress on your own task to the squad panel. After reading your task, call it ' +
        'with `total` (your plan in 3-8 steps) and `done: 0`; call it again as each step ' +
        'finishes. Silent and cheap: it only draws a progress bar.',
      inputSchema: {
        type: 'object',
        properties: {
          done: { type: 'integer', minimum: 0, description: 'Steps finished so far.' },
          total: { type: 'integer', minimum: 1, description: 'Steps planned; may change.' },
          note: { type: 'string', description: 'The step in progress, a few words.' },
        },
        required: ['done'],
      },
      isDeferred: false,
    })

    // Ghostty and kitty draw the Image element as real pixels; elsewhere it
    // would fall back to text, so those terminals keep the block vehicles.
    const program = (await $.env.get('TERM_PROGRAM')) ?? ''
    const term = (await $.env.get('TERM')) ?? ''
    await update($, hasPixels, () => /ghostty|kitty/i.test(`${program} ${term}`))

    const { context } = await $.session.usage()
    await update($, contextWindow, () => context.window)

    // Redraw clocks and walking mascots once a second while anything runs, and
    // drop finished rows an hour after they ended.
    $.clock.every(1000, async () => {
      isOpen = await isPaneOpen($)
      if (isOpen) await refreshOpenable($)
      const now = await $.clock.now()
      if (now - offloadPolledAt >= OFFLOAD_POLL_MS) {
        offloadPolledAt = now
        const runs = await readOffloadRuns($, now)
        if (JSON.stringify(runs) !== JSON.stringify(await read($, offloadRuns))) {
          await update($, offloadRuns, () => runs)
        }
      }
      const list = [...(await read($, agents)), ...(await read($, offloadRuns))]
      if (list.some(a => a.status === 'running')) await update($, tick, n => n + 1)

      const isExpired = (a: SquadAgent) =>
        a.status !== 'running' && a.endedAt !== undefined && now - a.endedAt > FINISHED_ROW_TTL_MS
      if (list.some(isExpired)) await update($, agents, all => all.filter(a => !isExpired(a)))
    })
    // Only while the pane is open: nothing else shows the sweep.
    $.clock.every(SWEEP_MS, async () => {
      if (!isOpen) return
      const list = await read($, agents)
      if (list.some(a => a.status === 'running')) await update($, sweep, n => n + 1)
    })

    return next(e)
  })

  on('command.run', { command: 'squad' }, async ($, e) => {
    const arg = e.args.trim()
    if (arg === 'close') {
      await $.ui.close({ id: PANE })
      return { text: 'Squad panel closed.' }
    }
    if (arg === 'reset') {
      await update($, agents, () => [])
      return { text: 'Squad cleared.' }
    }
    await openPane($)
    return { text: 'Squad panel opened.' }
  })

  on('agent.spawn', async ($, e, next) => {
    const canRewrite = e.workflow === undefined && e.resume === undefined
    const shouldAsk = options.askForSteps === true && canRewrite && !e.prompt.includes(STEP_TOOL)
    const started = await next(shouldAsk ? { ...e, prompt: e.prompt + STEP_NOTE } : e)
    if (started.deny !== undefined || started.agentId === undefined) return started

    const agent: SquadAgent = {
      id: started.agentId,
      title: e.description || e.subagentType,
      type: e.subagentType,
      model: started.model,
      status: 'running',
      startedAt: await $.clock.now(),
      usage: ZERO,
      contextTokens: 0,
      toolCalls: 0,
      lastActivityAt: await $.clock.now(),
    }
    await update($, agents, list => [...list.filter(a => a.id !== agent.id), agent].slice(-100))
    await refreshOpenable($)

    if (options.autoOpen !== false && !(await isPaneOpen($))) {
      void openPane($).catch(() => undefined)
    }
    return started
  }).catch(($, e, next) => next(e)) // never block a spawn over the panel's bookkeeping

  // A teammate wakes for another turn: it is running again.
  on('turn.start', async ($, e, next) => {
    const { agentId } = e
    if (agentId !== undefined) {
      await patchAgent($, agentId, a =>
        a.status === 'running' ? a : { ...a, status: 'running', endedAt: undefined },
      )
    }
    return next(e)
  })

  // Each model request of a subagent: its tokens, effort and live context.
  on('turn.step', async function* ($, e, next) {
    const result = yield* next(e)
    const { agentId } = e
    const reported = result.usage
    if (agentId === undefined) return result
    const at = await $.clock.now()
    await patchAgent($, agentId, a => ({ ...a, lastActivityAt: at }))
    if (result.toolUses.length === 0 && result.answer !== '') {
      await patchAgent($, agentId, a => withActivity(a, 'answer'))
    }
    if (reported === null) return result

    const usage: SquadUsage = {
      input: reported.input_tokens,
      output: reported.output_tokens,
      cacheRead: reported.cache_read_input_tokens,
      cacheWrite: reported.cache_creation_input_tokens,
    }
    await patchAgent($, agentId, a => ({
      ...a,
      model: reported.model,
      effort: e.effort === undefined ? a.effort : String(e.effort),
      usage: addUsage(a.usage, usage),
      contextTokens: usage.input + usage.cacheRead + usage.cacheWrite,
    }))
    return result
  })

  on('turn.complete', async ($, e, next) => {
    const { agentId } = e
    if (agentId !== undefined) {
      const endedAt = await $.clock.now()
      const status = e.reason === 'answer' ? 'done' : 'failed'
      await patchAgent($, agentId, a => ({ ...a, status, endedAt }))
    }
    return next(e)
  })

  // Every tool a subagent calls says what it is doing; the step tool is
  // answered here too (one hook: two on tool.call are too deep for the checker).
  on('tool.call', async ($, e, next) => {
    // Read as a plain shape: the input's full union is too deep for the checker.
    const input = e as { agentId?: string; tool: string; done?: unknown; total?: unknown; note?: unknown }
    const { agentId, tool } = input

    if (tool === STEP_TOOL) {
      if (agentId === undefined) return { result: 'ignored: only subagents report steps' }
      const at = await $.clock.now()
      // A missing, invalid or zero total keeps the plan already reported.
      const total = toCount(input.total)
      const done = toCount(input.done)
      const note = typeof input.note === 'string' ? input.note.trim() : ''
      await patchAgent($, agentId, a => ({
        ...a,
        lastActivityAt: at,
        stepsTotal: total !== undefined && total > 0 ? total : a.stepsTotal,
        stepsDone: done ?? a.stepsDone,
        note: note || undefined,
      }))
      return { result: 'ok' }
    }

    if (agentId !== undefined) {
      const activity = activityOfTool(tool)
      const at = await $.clock.now()
      await patchAgent($, agentId, a => {
        const counted = { ...a, toolCalls: (a.toolCalls ?? 0) + 1, lastActivityAt: at }
        return activity === undefined ? counted : withActivity(counted, activity)
      })
    }
    return next(e)
  }).catch(($, e, next) => next(e)) // never block a tool over the panel's bookkeeping

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const ui = $.ui.resolve(e)
    const { Box, Text, Button } = ui
    const Raster = e.surface === 'terminal' && 'Raster' in ui ? ui.Raster : undefined
    const Image = e.surface === 'terminal' && 'Image' in ui && (await read($, hasPixels)) ? ui.Image : undefined

    const list = [...(await read($, agents)), ...(await read($, offloadRuns))]
    const stallMs = stallAfterMs(options.stallAfterSec)
    const frame = await read($, tick)
    const step = await read($, sweep)
    const window = await read($, contextWindow)
    const isCollapsed = await read($, isFinishedCollapsed)
    const now = await $.clock.now()
    const viewed = e.props.view.agentId
    // Claude Code keeps a finished agent's transcript only for a while, and none
    // from an earlier process: only the agents it still lists can be opened.
    const canOpen = new Set(await read($, openable))

    const running = list.filter(a => a.status === 'running')
    const finished = list.filter(a => a.status !== 'running')

    const barWidth = Math.max(4, e.props.bodyColumns - PANE_PADDING * 2 - (Image || Raster ? SPRITE_COLUMNS + 2 : 0))

    // More agents than the pane can show in full: each becomes its title and bar.
    const chrome = 2 + (viewed !== undefined ? 1 : 0) + (running.length > 0 ? 2 : 0) + (finished.length > 0 ? 2 : 0)
    const shown = running.length + (isCollapsed ? 0 : finished.length)
    const isCompact = chrome + shown * FULL_ROW_LINES > e.props.scroll.bodyRows
    const compactBarWidth = Math.max(4, e.props.bodyColumns - PANE_PADDING * 2)

    // Opens the agent's transcript in the main view, as the tasks list does;
    // pressing the one already shown goes back to the main conversation.
    const show = async (agentId: string) => {
      const shown = await $.ui.view(viewed === agentId ? {} : { agentId })
      if (shown.deny !== undefined) $.ui.toast(`squad: can't open that transcript (${shown.deny})`)
    }

    const row = (a: SquadAgent) => {
      const color = hex(modelColor(a.model))
      const vehicle = vehicleOf(a)
      const isViewed = viewed === a.id
      const mark =
        a.status === 'running' ? (
          <Text color={color}>{SPINNER[frame % SPINNER.length]}</Text>
        ) : a.status === 'done' ? (
          <Text color={DONE_COLOR}>✓</Text>
        ) : (
          <Text color={FAILED_COLOR}>✗</Text>
        )
      const ctx = window > 0 ? `ctx ${Math.round((a.contextTokens / window) * 100)}% · ` : ''
      const meta = `${ctx}${formatTokens(totalTokens(a.usage))} · ${formatDuration((a.endedAt ?? now) - a.startedAt)}`

      const bar = (width: number) => {
        if (a.status !== 'running') {
          return <Text color={a.status === 'failed' ? FAILED_COLOR : color}>{'━'.repeat(width)}</Text>
        }
        // Working: a dim track with a bright pulse moving left to right, drawn
        // as the track before it, its lit cells, and the track after it.
        const paint = modelColor(a.model)
        const { from, levels } = sweepPulse(width, step)
        return (
          <Text>
            <Text color={BAR_TRACK}>{'─'.repeat(from)}</Text>
            {levels.map(level => (
              <Text color={hex(mix(BAR_TRACK_RGB, paint, level))}>━</Text>
            ))}
            <Text color={BAR_TRACK}>{'─'.repeat(Math.max(0, width - from - levels.length))}</Text>
          </Text>
        )
      }
      const title = canOpen.has(a.id) ? (
        <Button key={`open-${a.id}`} plain onPress={() => show(a.id)}>
          <Text bold color={isViewed ? VIEWED_COLOR : undefined}>
            {isViewed ? '▶ ' : ''}
            {a.title}
          </Text>
        </Button>
      ) : (
        <Text bold dimColor wrap="truncate-end">
          {a.title}
        </Text>
      )

      if (isCompact) {
        return (
          <Box key={a.id} flexDirection="column">
            <Box flexDirection="row" justifyContent="space-between" columnGap={1}>
              {title}
              <Text>
                {isIdle(a, now, stallMs) && <Text dimColor color={STALL_COLOR}>{idleLabel(a, now)} </Text>}
                <Text dimColor>{[modelLabel(a.model), stepsLabel(a), callsLabel(a)].filter(Boolean).join(' · ')} </Text>
                {mark}
              </Text>
            </Box>
            {bar(compactBarWidth)}
          </Box>
        )
      }

      return (
        <Box key={a.id} flexDirection="row" columnGap={2} marginBottom={1}>
          {Image ? (
            <Image
              key={`sprite-${a.id}`}
              columns={HIRES_COLUMNS}
              rows={HIRES_ROWS}
              alt=" "
              source={hiresPixels({
                activity: vehicle,
                paint: modelColor(a.model),
                isFailed: a.status === 'failed',
                frame: a.status === 'running' ? frame : 0,
              })}
            />
          ) : (
            Raster && (
              <Raster
                key={`sprite-${a.id}`}
                columns={SPRITE_COLUMNS}
                rows={SPRITE_ROWS}
                cells={spriteCells({
                  activity: vehicle,
                  model: a.model,
                  isFailed: a.status === 'failed',
                  frame: a.status === 'running' ? frame : 0,
                })}
              />
            )
          )}
          <Box flexDirection="column" flexGrow={1}>
            <Box flexDirection="row" justifyContent="space-between">
              {title}
              {mark}
            </Box>
            <Text wrap="truncate-end">
              <Text color={color}>{a.type}</Text> {modelLabel(a.model)}
              {a.effort !== undefined ? ` · ${a.effort}` : ''}
              {vehicle !== undefined ? <Text dimColor> · {ACTIVITY_LABEL[vehicle]}</Text> : ''}
            </Text>
            <Box flexDirection="row" justifyContent="space-between" columnGap={1}>
              <Text wrap="truncate-end">
                {[stepsLabel(a), callsLabel(a)].filter(Boolean).join(' · ')}
                {isIdle(a, now, stallMs) && <Text dimColor color={STALL_COLOR}> {idleLabel(a, now)}</Text>}
                {a.note !== undefined && a.status === 'running' ? <Text dimColor> {a.note}</Text> : ''}
              </Text>
              <Text dimColor>{meta}</Text>
            </Box>
            {bar(barWidth)}
          </Box>
        </Box>
      )
    }

    return (
      <Box flexDirection="column" paddingLeft={PANE_PADDING} paddingRight={PANE_PADDING}>
        {viewed !== undefined && (
          <Button key="back" plain onPress={() => $.ui.view({})}>
            <Text color={VIEWED_COLOR}>← back to the main conversation</Text>
          </Button>
        )}
        {list.length === 0 && <Text dimColor>No subagents yet this session.</Text>}
        {list.length > 0 && (
          <Text dimColor>Press a title to open its transcript (dim ones are closed).</Text>
        )}
        {running.length > 0 && (
          <Box flexDirection="column" marginTop={1}>
            <Text bold>Running · {running.length}</Text>
            {running.map(row)}
          </Box>
        )}
        {finished.length > 0 && (
          <Box flexDirection="column" marginTop={1}>
            <Button
              key="toggle-finished"
              plain
              onPress={() => update($, isFinishedCollapsed, v => !v)}
            >
              {isCollapsed ? '▸' : '▾'} Finished · {finished.length}
            </Button>
            {!isCollapsed && finished.slice().reverse().map(row)}
          </Box>
        )}
      </Box>
    )
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const list = [...(await read($, agents)), ...(await read($, offloadRuns))]
    const running = list.filter(a => a.status === 'running')
    if (e.props.hasSurvey || running.length === 0) return next(e)

    const { Box, Text, Button } = $.ui.resolve(e)
    const frame = await read($, tick)
    const now = await $.clock.now()
    const done = list.length - running.length
    const idle = running.filter(a => isIdle(a, now, stallAfterMs(options.stallAfterSec))).length
    const span = now - Math.min(...running.map(a => a.startedAt))

    return (
      <Box flexDirection="row" columnGap={1}>
        <Text color={hex(modelColor(running[0]?.model ?? ''))}>{SPINNER[frame % SPINNER.length]}</Text>
        <Text>
          squad · {running.length} running{done > 0 ? ` · ${done} done` : ''}
        </Text>
        {idle > 0 && <Text dimColor color={STALL_COLOR}>{idle} idle</Text>}
        <Text dimColor>{formatDuration(span)}</Text>
        <Button key="open" label="open" onPress={() => openPane($)} />
      </Box>
    )
  })
}
