import { expect, mock, test } from 'claude-code/testing'

import type { On } from 'claude-code'

import { parseQuery } from './register'

const PLUGIN = 'recall'
const pane = { title: '', isFocused: true, bodyColumns: 62, placement: 'dock', scroll: { offset: 0, bodyRows: 30 }, view: {} } as const

const sessions = [
  {
    id: 'aaaaaaaa-1111-4111-8111-111111111111',
    title: 'Webhook retries',
    cwd: '/work/my-app',
    project: '/work/my-app',
    branch: 'main',
    lastActive: '2026-10-01T10:00:07+00:00',
    age: '2h ago',
    lastPrompt: 'wire up the webhook retries',
    prs: ['https://github.com/x/y/pull/7'],
  },
  {
    id: 'bbbbbbbb-2222-4222-8222-222222222222',
    title: 'Flaky test',
    cwd: '/work/other',
    project: '/work/other',
    branch: null,
    lastActive: '2026-10-01T09:00:00+00:00',
    age: '3h ago',
    lastPrompt: null,
    prs: [],
  },
]

// The engine calls the pane makes, answered here as a session would.
const stubEngine = (on: On) => {
  on('ui.open', () => ({ value: { isPlaced: true } }) as never)
  on('session.id', () => ({ value: 'current-session' }) as never)
  on('ui.toast', () => ({ value: undefined }) as never)
  on('ui.focus', () => ({ value: {} }) as never)
}

const ok = (stdout: string) => ({ value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }) as never

test('parseQuery splits a project filter from the search text', () => {
  expect(parseQuery('p:harness webhook  retries')).toEqual({ project: 'harness', text: 'webhook retries' })
  expect(parseQuery('')).toEqual({ project: undefined, text: '' })
})

for (const surface of ['terminal', 'desktop'] as const) {
  test(`${surface}: /recall lists sessions, picking one loads its digest into the conversation`, async ($, on) => {
    stubEngine(on)
    const session = mock.session(on)
    const argvs: string[][] = []
    on('process.run', (_$, e) => {
      argvs.push([...e.argv])
      return ok(e.argv.includes('digest') ? '# Session digest: Webhook retries\n- id: aaaaaaaa' : JSON.stringify(sessions))
    })

    const ran = await $.command.run({ command: 'recall', args: 'p:my-app' } as never)
    expect(ran.text).toBe('Recall pane opened.')
    const listArgs = argvs[0] ?? []
    expect(listArgs.slice(2, 5)).toEqual(['list', '--json', '--limit'])
    expect(listArgs).toContain('--project')
    expect(listArgs).toContain('my-app')
    expect(listArgs).toContain('current-session')

    const ui = await $.ui.mount({ plugin: PLUGIN, surface, component: 'Pane', requestId: 'recall', props: pane })
    expect((await ui.find({ key: `s-${sessions[0]!.id}` }))?.props.label).toBe('Webhook retries')
    expect(await ui.find({ text: /2 recent sessions in "my-app"/ })).toBeDefined()

    await ui.press({ key: `s-${sessions[0]!.id}` })
    expect(await ui.find({ text: /wire up the webhook retries/ })).toBeDefined()

    await ui.press({ key: 'load' })
    const digestArgs = argvs.find(a => a.includes('digest')) ?? []
    expect(digestArgs).toContain(sessions[0]!.id)
    const rows = session.appended()
    expect(rows.map(r => r.message.type)).toEqual(['user', 'system'])
    const recalled = JSON.stringify(rows[0])
    expect(recalled).toContain('# Session digest: Webhook retries')
    expect(recalled).toContain('not instructions')
    expect(await ui.find({ text: /Loaded "Webhook retries"/ })).toBeDefined()
    await ui.unmount()
  })

  test(`${surface}: a focus move that fails never fails the lookup`, async ($, on) => {
    on('ui.open', () => ({ value: { isPlaced: true } }) as never)
    on('session.id', () => ({ value: 'current-session' }) as never)
    on('ui.focus', () => {
      throw new Error('no surface')
    })
    on('process.run', () => ok(JSON.stringify(sessions)))
    await $.command.run({ command: 'recall', args: '' } as never)
    const ui = await $.ui.mount({ plugin: PLUGIN, surface, component: 'Pane', requestId: 'recall', props: pane })
    expect(await ui.find({ text: /2 recent sessions/ })).toBeDefined()
    await ui.unmount()
  })

  test(`${surface}: a failing script shows the error instead of a list`, async ($, on) => {
    stubEngine(on)
    on('process.run', () => ({ value: { exitCode: 1, stdout: '', stderr: 'boom', isStdoutTruncated: false, isStderrTruncated: false } }) as never)
    await $.command.run({ command: 'recall', args: 'retries' } as never)
    const ui = await $.ui.mount({ plugin: PLUGIN, surface, component: 'Pane', requestId: 'recall', props: pane })
    expect(await ui.find({ text: /Failed: boom/ })).toBeDefined()
    await ui.unmount()
  })
}
