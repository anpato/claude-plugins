import type { SquadActivity, SquadAgent, SquadUsage } from '../types'

// One Codex subagent, rebuilt from its rollout file
// (~/.codex/sessions/YYYY/MM/DD/rollout-*-<thread id>.jsonl), which Codex appends to as
// the agent works. A spawned agent's file starts with a copy of its parent's history;
// its own part begins at the `thread_settings_applied` event that names its thread.

export type Track = {
  agent: SquadAgent
  // Bytes of the file already read, always up to the end of a whole line.
  offset: number
  isOwn: boolean
}

export type ThreadRow = {
  id: string
  nickname: string | null
  agentPath: string | null
  role: string | null
  model: string | null
  effort: string | null
  createdAtMs: number
  rolloutPath: string
}

const ZERO: SquadUsage = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }

// '/root/count_notes' -> 'count_notes'; nested agents keep their parent: 'a/count'.
const titleOf = (row: ThreadRow): string =>
  (row.agentPath ?? '').replace(/^\/root\/?/, '') || row.nickname || row.id.slice(-6)

export const newTrack = (row: ThreadRow): Track => ({
  agent: {
    id: row.id,
    title: titleOf(row),
    // Codex names each agent (Halley, Sagan); a custom agent's role says more.
    type: row.role ?? row.nickname ?? 'agent',
    model: row.model ?? '',
    effort: row.effort ?? undefined,
    status: 'running',
    startedAt: row.createdAtMs,
    usage: ZERO,
    contextTokens: 0,
    toolCalls: 0,
    lastActivityAt: row.createdAtMs,
  },
  offset: 0,
  isOwn: false,
})

const SEARCH_COMMANDS = new Set([
  'cat', 'head', 'tail', 'sed', 'rg', 'grep', 'ls', 'find', 'fd', 'wc', 'tree', 'nl', 'awk', 'jq', 'stat', 'file', 'pwd',
])

// What one inner tool call (inside Codex's `exec` script tool) says the agent is doing.
export const activityOfCall = (tool: string, command: string): SquadActivity | undefined => {
  if (tool === 'apply_patch') return 'edit'
  if (tool === 'exec_command' || tool === 'write_stdin') {
    const first = command.trim().split(/[\s;&|()]+/)[0]?.split('/').pop() ?? ''
    return SEARCH_COMMANDS.has(first) ? 'search' : 'run'
  }
  if (tool.startsWith('mcp__') || tool.startsWith('web') || tool === 'view_image') return 'web'
  return undefined
}

// The inner calls of an `exec` script: `tools.exec_command({cmd:"wc -l x"})`, `tools.apply_patch("...")`.
export const callsInScript = (script: string): { tool: string; command: string }[] =>
  [...script.matchAll(/tools\.([A-Za-z_][\w]*)\(\s*(\{[^}]*?\bcmd\s*:\s*"((?:[^"\\]|\\.)*)")?/g)].map(m => ({
    tool: m[1] ?? '',
    command: (m[3] ?? '').replace(/\\"/g, '"'),
  }))

const withActivity = (a: SquadAgent, activity: SquadActivity): SquadAgent => ({
  ...a,
  activity,
  activityCounts: { ...a.activityCounts, [activity]: (a.activityCounts?.[activity] ?? 0) + 1 },
})

const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0)
const obj = (v: unknown): Record<string, unknown> =>
  typeof v === 'object' && v !== null ? (v as Record<string, unknown>) : {}

// One rollout record applied to the track. Records before the agent's own part are skipped.
export const applyRecord = (track: Track, record: unknown): Track => {
  const r = obj(record)
  const p = obj(r.payload)
  const at = typeof r.timestamp === 'string' ? Date.parse(r.timestamp) || 0 : 0
  const a = track.agent

  if (!track.isOwn) {
    // A file with no copied history starts with the agent's own session_meta.
    const isFresh = r.type === 'session_meta' && p.id === a.id && p.forked_from_id === undefined
    const isStart = r.type === 'event_msg' && p.type === 'thread_settings_applied' && p.thread_id === a.id
    if (!isFresh && !isStart) return track
    const model = obj(p.thread_settings).model
    return { ...track, isOwn: true, agent: typeof model === 'string' ? { ...a, model } : a }
  }

  const touch = (next: SquadAgent): Track => ({ ...track, agent: at ? { ...next, lastActivityAt: at } : next })

  if (r.type === 'turn_context') {
    return {
      ...track,
      agent: {
        ...a,
        model: typeof p.model === 'string' ? p.model : a.model,
        effort: typeof p.effort === 'string' ? p.effort : a.effort,
      },
    }
  }

  if (r.type === 'token_usage_record') {
    const u = obj(p.usage)
    const cached = num(u.cached_input_tokens)
    const usage: SquadUsage = {
      input: a.usage.input + Math.max(0, num(u.input_tokens) - cached),
      output: a.usage.output + num(u.output_tokens),
      cacheRead: a.usage.cacheRead + cached,
      cacheWrite: a.usage.cacheWrite + num(u.cache_write_input_tokens),
    }
    return touch({ ...a, usage })
  }

  if (r.type === 'event_msg') {
    if (p.type === 'token_count') {
      const info = obj(p.info)
      const last = obj(info.last_token_usage)
      const window = num(info.model_context_window)
      return {
        ...track,
        agent: { ...a, contextTokens: num(last.input_tokens), contextWindow: window || a.contextWindow },
      }
    }
    if (p.type === 'task_started') return touch({ ...a, status: 'running', endedAt: undefined })
    if (p.type === 'task_complete') {
      const ended = num(p.completed_at) * 1000 || at
      return { ...track, agent: { ...a, status: 'done', endedAt: ended } }
    }
    if (p.type === 'turn_aborted') return { ...track, agent: { ...a, status: 'failed', endedAt: at || undefined } }
    return track
  }

  if (r.type === 'response_item') {
    if (p.type === 'custom_tool_call' && p.name === 'exec') {
      const calls = callsInScript(typeof p.input === 'string' ? p.input : '')
      let next: SquadAgent = { ...a, toolCalls: (a.toolCalls ?? 0) + Math.max(1, calls.length) }
      for (const call of calls) {
        const activity = activityOfCall(call.tool, call.command)
        if (activity !== undefined) next = withActivity(next, activity)
      }
      return touch(next)
    }
    if (p.type === 'function_call') return touch({ ...a, toolCalls: (a.toolCalls ?? 0) + 1 })
    if (p.type === 'custom_tool_call_output' || p.type === 'function_call_output') return touch(a)
    if (p.type === 'message' && p.role === 'assistant' && p.phase === 'final_answer') {
      return touch(withActivity(a, 'answer'))
    }
  }
  return track
}

// Whole lines read from the file (`bytes` of it) applied in order.
export const applyLines = (track: Track, text: string, bytes: number): Track => {
  let next: Track = { ...track, offset: track.offset + bytes }
  for (const line of text.split('\n')) {
    if (line.trim() === '') continue
    try {
      next = applyRecord(next, JSON.parse(line))
    } catch {
      // a malformed line skips that record only
    }
  }
  return next
}
