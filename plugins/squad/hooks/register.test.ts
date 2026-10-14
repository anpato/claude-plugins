import { expect, mock, test } from 'claude-code/testing'

const PLUGIN = 'squad'
const STEP = 'mcp__squad__step'
const pane = { title: '', isFocused: false, bodyColumns: 52, placement: 'dock', scroll: { offset: 0, bodyRows: 30 }, view: {} } as const
const band = { hasSurvey: false, isWorking: true, maxRows: 4, bodyColumns: 100, scroll: { offset: 0, bodyRows: 4 }, view: {} } as const

// The bar's sweep ticks every 100ms, so long waits are taken a few minutes at a
// time: the mock clock refuses one advance that crosses too many timers.
const advanceMinutes = async (clock: { advance: (ms: number) => Promise<void> }, minutes: number) => {
  for (let left = minutes; left > 0; left -= 5) await clock.advance(Math.min(5, left) * 60 * 1000)
}

const spawnInput = {
  tool_use_id: 't1',
  prompt: 'Find where X is defined.',
  description: 'Find the bug',
  subagentType: 'general-purpose',
  provider: { plugin: 'engine', tier: 'core' },
  parentModel: 'claude-opus-5-5',
  background: false,
  fork: false,
}

for (const surface of ['terminal', 'desktop'] as const) {
  test(`${surface}: a subagent is tracked from spawn to finish`, async ($, on) => {
    const clock = mock.clock(on, { now: 1_000 })
    let promptSeen = ''
    on('agent.spawn', (_$, e) => {
      promptSeen = e.prompt
      return { model: 'claude-sonnet-5-5', agentId: 'a1' }
    })
    on('turn.step', async function* (_$: unknown, e: { turnId: string; index: number }) {
      return {
        turnId: e.turnId,
        index: e.index,
        answer: '',
        toolUses: [],
        stopReason: 'end_turn',
        usage: {
          model: 'claude-sonnet-5-5',
          input_tokens: 1000,
          output_tokens: 500,
          cache_read_input_tokens: 9000,
          cache_creation_input_tokens: 0,
        },
      }
    } as never)
    on('turn.complete', () => ({ text: '' }))
    on('tool.call', { tool: 'Read' }, () => ({ result: 'file text' }) as never)
    on('agent.list', () => ({ value: [{ id: 'a1', description: 'Find the bug', type: 'general-purpose', status: 'running' }] }))
    const views: unknown[] = []
    on('ui.view', (_$, e) => {
      views.push(e)
      return { value: {} }
    })

    const spawned = await $.agent.spawn(spawnInput as never)
    expect(spawned).toMatchObject({ agentId: 'a1' })
    // The subagent was asked to report its steps.
    expect(promptSeen).toContain(STEP)

    // While it runs, the band above the prompt counts it.
    const bar = await $.ui.mount({ plugin: PLUGIN, surface, component: 'AbovePrompt', props: band })
    expect(await bar.find({ text: /1 running/ })).toBeDefined()

    expect(await $.tool.call({ tool: STEP, agentId: 'a1', done: 1, total: 3, note: 'reading' } as never)).toMatchObject({ result: 'ok' })

    const step = $.turn.step({ turnId: 'u1', index: 0, model: 'claude-sonnet-5-5', effort: 'medium', messageCount: 1, agentId: 'a1' } as never)
    for await (const _ of step) void _
    await step.result

    const ui = await $.ui.mount({ plugin: PLUGIN, surface, component: 'Pane', requestId: 'squad', props: pane })
    expect(await ui.find({ text: /Running · 1/ })).toBeDefined()
    expect(await ui.find({ text: /Find the bug/ })).toBeDefined()
    expect(await ui.find({ text: /1\/3/ })).toBeDefined()
    // A Read call makes it a searcher.
    await $.tool.call({ tool: 'Read', agentId: 'a1', file_path: '/tmp/x' } as never)
    expect(await ui.find({ text: /searching/ })).toBeDefined()
    // The mascot is a Raster on the terminal, and simply absent elsewhere.
    expect((await ui.findAll({ type: 'Raster' })).length).toBe(surface === 'terminal' ? 1 : 0)
    // 1000 input + 500 output + 9000 cache reads = 10,500 tokens.
    expect(await ui.find({ text: /11k/ })).toBeDefined()

    // Pressing the title opens the agent's transcript in the main view.
    await ui.press({ key: 'open-a1' })
    expect(views).toEqual([expect.objectContaining({ agentId: 'a1' })])

    await clock.advance(30_000)
    await $.turn.complete({ turnId: 'u1', reason: 'answer', answer: '', agentId: 'a1' } as never)
    // The mounted pane redraws from state.
    expect(await ui.find({ text: /Finished · 1/ })).toBeDefined()
    expect(await ui.find({ text: /0:30/ })).toBeDefined()
    // A finished agent reads as complete even though it never reported 3/3.
    expect(await ui.find({ text: /3\/3/ })).toBeDefined()
    expect(await ui.find({ text: /Running/ })).toBeUndefined()
    await ui.unmount()
    await bar.unmount()
  })
}

test('a step report from the main loop is ignored', async $ => {
  expect(await $.tool.call({ tool: STEP, done: 1 } as never)).toMatchObject({ result: 'ignored: only subagents report steps' })
})

test('bad step input keeps the last good values', async ($, on) => {
  mock.clock(on)
  on('agent.spawn', () => ({ model: 'claude-haiku-5-5', agentId: 'a2' }))
  // a2 is not listed: its title draws, but not as something to press.
  on('agent.list', () => ({ value: [] }))
  await $.agent.spawn({ ...spawnInput, tool_use_id: 't2' } as never)

  await $.tool.call({ tool: STEP, agentId: 'a2', done: 2, total: 5 } as never)
  // Not numbers, a zero total and a non-text note: none of it may break the row.
  expect(await $.tool.call({ tool: STEP, agentId: 'a2', done: 'abc', total: 0, note: 5 } as never)).toMatchObject({ result: 'ok' })

  const ui = await $.ui.mount({ plugin: PLUGIN, surface: 'terminal', component: 'Pane', requestId: 'squad', props: pane })
  expect(await ui.find({ text: /2\/5/ })).toBeDefined()
  expect(await ui.find({ text: /NaN/ })).toBeUndefined()
  expect(await ui.find({ key: 'open-a2' })).toBeUndefined()
  await ui.unmount()
})

test('a finished row is dropped an hour after it ended', async ($, on) => {
  const clock = mock.clock(on)
  mock.env(on, {})
  on('session.usage', () => ({ value: { startedAt: 0, context: { window: 1_000_000 }, rateLimits: {}, cost: {} } }) as never)
  on('agent.spawn', (_$, e) => ({ model: 'claude-sonnet-5-5', agentId: e.tool_use_id === 't3' ? 'a3' : 'a4' }))
  on('turn.complete', () => ({ text: '' }))
  on('agent.list', () => ({ value: [] }))
  on('command.register', (_$, e) => ({ value: { command: e.name } }))
  on('tool.register', (_$, e) => ({ value: { tool: `mcp__squad__${e.name}` } }))
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  await $.session.start({ source: 'startup', cwd: '/', surface: null, isInteractive: true } as never)

  await $.agent.spawn({ ...spawnInput, tool_use_id: 't3', description: 'Old one' } as never)
  await $.turn.complete({ turnId: 'u3', reason: 'answer', answer: '', agentId: 'a3' } as never)
  await advanceMinutes(clock, 30)
  await $.agent.spawn({ ...spawnInput, tool_use_id: 't4', description: 'Newer one' } as never)
  await $.turn.complete({ turnId: 'u4', reason: 'answer', answer: '', agentId: 'a4' } as never)

  // 61 minutes after the first ended, 31 after the second.
  await advanceMinutes(clock, 31)
  const ui = await $.ui.mount({ plugin: PLUGIN, surface: 'terminal', component: 'Pane', requestId: 'squad', props: pane })
  expect(await ui.find({ text: /Old one/ })).toBeUndefined()
  expect(await ui.find({ text: /Newer one/ })).toBeDefined()
  await ui.unmount()
})

for (const [name, env, element] of [
  ['Ghostty', { TERM_PROGRAM: 'ghostty', TERM: 'xterm-ghostty' }, 'Image'],
  ['iTerm', { TERM_PROGRAM: 'iTerm.app', TERM: 'xterm-256color' }, 'Raster'],
] as const) {
  test(`${name} draws the vehicles as ${element === 'Image' ? 'images' : 'blocks'}`, async ($, on) => {
    mock.clock(on)
    mock.env(on, env)
    on('session.usage', () => ({ value: { startedAt: 0, context: { window: 1_000_000 }, rateLimits: {}, cost: {} } }) as never)
    on('agent.spawn', () => ({ model: 'claude-opus-5-5', agentId: 'a5' }))
    on('agent.list', () => ({ value: [] }))
    on('command.register', (_$, e) => ({ value: { command: e.name } }))
    on('tool.register', (_$, e) => ({ value: { tool: `mcp__squad__${e.name}` } }))
    on('session.start', (_$, e) => ({ cwd: e.cwd }))
    await $.session.start({ source: 'startup', cwd: '/', surface: null, isInteractive: true } as never)
    await $.agent.spawn({ ...spawnInput, tool_use_id: 't5' } as never)

    const ui = await $.ui.mount({ plugin: PLUGIN, surface: 'terminal', component: 'Pane', requestId: 'squad', props: pane })
    expect((await ui.findAll({ type: 'Image' })).length).toBe(element === 'Image' ? 1 : 0)
    expect((await ui.findAll({ type: 'Raster' })).length).toBe(element === 'Raster' ? 1 : 0)
    await ui.unmount()
  })
}

test('more agents than the pane holds collapse to title and bar', async ($, on) => {
  mock.clock(on)
  let n = 0
  on('agent.spawn', () => ({ model: 'claude-sonnet-5-5', agentId: `c${++n}` }))
  on('agent.list', () => ({ value: [] }))
  for (let i = 0; i < 6; i++) await $.agent.spawn({ ...spawnInput, tool_use_id: `tc${i}`, description: `Task ${i}` } as never)

  // Six full rows need 32 lines; a 12-line pane gets the compact layout, which
  // drops the model line, while a 60-line pane keeps every row in full.
  const small = await $.ui.mount({ plugin: PLUGIN, surface: 'terminal', component: 'Pane', requestId: 'squad', props: { ...pane, scroll: { offset: 0, bodyRows: 12 } } })
  expect(await small.find({ text: /Task 5/ })).toBeDefined()
  expect(await small.find({ text: /Sonnet 5\.5/ })).toBeUndefined()
  expect((await small.findAll({ type: 'Raster' })).length).toBe(0)
  await small.unmount()

  const big = await $.ui.mount({ plugin: PLUGIN, surface: 'terminal', component: 'Pane', requestId: 'squad', props: { ...pane, scroll: { offset: 0, bodyRows: 60 } } })
  expect(await big.find({ text: /Sonnet 5\.5/ })).toBeDefined()
  await big.unmount()
})
