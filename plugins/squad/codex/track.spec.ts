import { expect, test } from 'bun:test'

import { render } from './render'
import { activityOfCall, applyLines, applyRecord, callsInScript, newTrack, type ThreadRow, type Track } from './track'

// Records shaped like a real Codex 0.167 subagent rollout: a copy of the parent's history,
// then the agent's own part from `thread_settings_applied` on.
const ID = 'child-1'
const row: ThreadRow = {
  id: ID, nickname: 'Halley', agentPath: '/root/count_notes', role: null,
  model: 'gpt-6-astra', effort: 'low', createdAtMs: 1_000_000, rolloutPath: '/dev/null',
}
const ts = (s: number) => new Date(1_000_000 + s * 1000).toISOString()
const exec = (s: number, script: string) => ({
  timestamp: ts(s), type: 'response_item', payload: { type: 'custom_tool_call', name: 'exec', input: script },
})
const forkedPrefix = [
  { timestamp: ts(0), type: 'session_meta', payload: { id: ID, forked_from_id: 'parent' } },
  { timestamp: ts(0), type: 'session_meta', payload: { id: 'parent' } },
  { timestamp: ts(0), type: 'response_item', payload: { type: 'function_call', name: 'spawn_agent' } },
  exec(0, 'text(await tools.exec_command({cmd:"cat parent.txt"}))'),
  { timestamp: ts(0), type: 'event_msg', payload: { type: 'turn_aborted' } },
]
const start = { timestamp: ts(0), type: 'event_msg', payload: { type: 'thread_settings_applied', thread_id: ID, thread_settings: { model: 'gpt-6-astra' } } }

const play = (records: unknown[]): Track => records.reduce<Track>((t, r) => applyRecord(t, r), newTrack(row))

test('the copied parent history is skipped: no calls, not failed', () => {
  const a = play(forkedPrefix).agent
  expect(a.toolCalls).toBe(0)
  expect(a.status).toBe('running')
  expect(a.title).toBe('count_notes')
  expect(a.type).toBe('Halley')
})

test('inner tool calls set the vehicle and count one call each', () => {
  const a = play([
    ...forkedPrefix, start,
    exec(1, 'text(await tools.exec_command({cmd:"wc -l notes.txt",yield_time_ms:1000}));'),
    exec(2, 'text(await tools.exec_command({cmd:"sleep 25"}));\ntext(await tools.exec_command({cmd:"rg foo"}));'),
    exec(3, 'text(await tools.apply_patch("*** Begin Patch\\n*** End Patch"));'),
  ]).agent
  expect(a.toolCalls).toBe(4)
  expect(a.activity).toBe('edit')
  expect(a.activityCounts).toEqual({ search: 2, run: 1, edit: 1 })
  expect(a.lastActivityAt).toBe(1_000_000 + 3000)
})

test('tokens add up per response; context and window come from token_count', () => {
  const a = play([
    ...forkedPrefix, start,
    { timestamp: ts(1), type: 'token_usage_record', payload: { usage: { input_tokens: 21044, cached_input_tokens: 20096, output_tokens: 54 } } },
    { timestamp: ts(2), type: 'token_usage_record', payload: { usage: { input_tokens: 100, cached_input_tokens: 0, output_tokens: 10 } } },
    { timestamp: ts(2), type: 'event_msg', payload: { type: 'token_count', info: { last_token_usage: { input_tokens: 26566 }, model_context_window: 258400 } } },
  ]).agent
  expect(a.usage).toEqual({ input: 948 + 100, output: 64, cacheRead: 20096, cacheWrite: 0 })
  expect(a.contextTokens).toBe(26566)
  expect(a.contextWindow).toBe(258400)
})

test('task_complete finishes the agent; turn_context updates model and effort', () => {
  const a = play([
    ...forkedPrefix, start,
    { timestamp: ts(1), type: 'turn_context', payload: { model: 'gpt-6.1-sol', effort: 'high' } },
    { timestamp: ts(1), type: 'response_item', payload: { type: 'message', role: 'assistant', phase: 'final_answer' } },
    { timestamp: ts(9), type: 'event_msg', payload: { type: 'task_complete', completed_at: 1009 } },
  ]).agent
  expect(a.status).toBe('done')
  expect(a.endedAt).toBe(1_009_000)
  expect(a.model).toBe('gpt-6.1-sol')
  expect(a.effort).toBe('high')
  expect(a.activityCounts).toEqual({ answer: 1 })
})

test('an aborted turn of its own fails the agent', () => {
  const a = play([...forkedPrefix, start, { timestamp: ts(4), type: 'event_msg', payload: { type: 'turn_aborted' } }]).agent
  expect(a.status).toBe('failed')
})

test('a file without copied history counts from its own session_meta', () => {
  const a = play([
    { timestamp: ts(0), type: 'session_meta', payload: { id: ID } },
    exec(1, 'text(await tools.exec_command({cmd:"ls"}))'),
  ]).agent
  expect(a.toolCalls).toBe(1)
})

test('applyLines advances the offset and skips malformed lines', () => {
  const text = [...forkedPrefix, start].map(r => JSON.stringify(r)).join('\n') + '\n{not json\n' +
    JSON.stringify(exec(1, 'text(await tools.exec_command({cmd:"ls"}))')) + '\n'
  const t = applyLines(newTrack(row), text, 1234)
  expect(t.offset).toBe(1234)
  expect(t.agent.toolCalls).toBe(1)
})

test('script parsing reads escaped commands and classifies tools', () => {
  expect(callsInScript('tools.exec_command({workdir:"/x", cmd:"echo \\"hi\\""}); tools.view_image({path:"a"})')).toEqual([
    { tool: 'exec_command', command: 'echo "hi"' },
    { tool: 'view_image', command: '' },
  ])
  expect(activityOfCall('exec_command', '/usr/bin/grep x')).toBe('search')
  expect(activityOfCall('exec_command', 'pwd; rg --files')).toBe('search')
  expect(activityOfCall('exec_command', 'go test ./...')).toBe('run')
  expect(activityOfCall('mcp__design_studio__screenshot', '')).toBe('web')
  expect(activityOfCall('web__run', '')).toBe('web')
  expect(activityOfCall('TodoItem', '')).toBeUndefined()
})

const strip = (s: string) => s.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '')
const view = { now: 1_030_000, frame: 0, step: 0, columns: 70, rows: 40, header: 'squad · codex', stallMs: 120_000, hasPixels: false }

test('a full row shows title, model, calls, tokens and time', () => {
  const a = play([...forkedPrefix, start, exec(1, 'text(await tools.exec_command({cmd:"sleep 1"}))')]).agent
  const text = render({ ...view, agents: [a] }).lines.map(strip).join('\n')
  expect(text).toContain('Running · 1')
  expect(text).toContain('count_notes')
  expect(text).toContain('Halley gpt-6-astra · low · running commands')
  expect(text).toContain('1 call')
  expect(text).toContain('0:30')
})

test('rows go compact when they do not fit, and finished ones show a check', () => {
  const done = play([...forkedPrefix, start, { timestamp: ts(5), type: 'event_msg', payload: { type: 'task_complete', completed_at: 1005 } }]).agent
  const agents = Array.from({ length: 6 }, (_, i) => ({ ...done, id: `d${i}` }))
  const lines = render({ ...view, rows: 20, agents }).lines.map(strip)
  expect(lines.some(l => l.includes('Finished · 6'))).toBe(true)
  expect(lines.filter(l => l.includes('✓')).length).toBeGreaterThan(1)
  expect(lines.length).toBeLessThanOrEqual(20)
})

test('pixel mode leaves the vehicle cells blank and reports where to place it', () => {
  const a = play([...forkedPrefix, start]).agent
  const { lines, placements } = render({ ...view, hasPixels: true, agents: [a] })
  expect(placements).toHaveLength(1)
  expect(strip(lines[placements[0]!.line]!).startsWith(' '.repeat(2 + 7 + 2) + 'count_notes')).toBe(true)
})

test('an agent quiet past the limit is flagged idle', () => {
  const a = play([...forkedPrefix, start]).agent
  const text = render({ ...view, now: 1_000_000 + 200_000, agents: [a] }).lines.map(strip).join('\n')
  expect(text).toContain('idle 3m')
})
