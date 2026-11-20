import { expect, test } from 'claude-code/testing'
import type { On } from 'claude-code'
import type { Worktree } from '../types'
import { badgeFits, changesLabel, openCommand, prLabel, safeText, storageLabel } from './register'

const row = (overrides: Partial<Worktree> = {}): Worktree => ({
  path: '/work/app', branch: 'main', head: 'abcdef123456', bare: false, detached: false,
  locked: null, prunable: null, isCurrent: true, tracked: 0, untracked: 0, conflicts: 0,
  upstream: 'origin/main', ahead: 0, behind: 0, error: null,
  summary: 'Latest: Improve search', storageBytes: 1024 ** 3, storagePartial: false,
  pullRequest: { state: 'MERGED', number: 7, url: 'https://github.com/owner/app/pull/7', headMatches: true },
  ...overrides,
})
const rows = [row(), row({ path: "/work/app's feature", branch: 'feature', isCurrent: false, tracked: 2,
  summary: 'Latest: Add worktree pane', pullRequest: { state: 'OPEN', number: 8, url: null, headMatches: true } })]
const detail = { files: [{ status: '.M', path: 'src/pane.tsx' }], totalFiles: 1,
  diff: 'diff --git a/pane b/pane\n+new pane', truncated: false, note: 'Untracked files are not included.' }
const props = (width = 72) => ({ title: '', isFocused: true, bodyColumns: width, placement: 'dock',
  scroll: { offset: 0, bodyRows: 40 }, view: {} }) as const
const ok = (value: unknown) => ({ value: { exitCode: 0, stdout: JSON.stringify(value), stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }) as never
const fail = (message: string) => ({ value: { exitCode: 1, stdout: '', stderr: message, isStdoutTruncated: false, isStderrTruncated: false } }) as never
const stub = (on: On) => {
  on('ui.open', () => ({ value: { isPlaced: true } }) as never)
  on('ui.close', () => ({ value: undefined }) as never)
  on('ui.focus', () => ({ value: {} }) as never)
  on('ui.toast', () => ({ value: undefined }) as never)
  on('session.cwd', () => ({ value: '/work/app' }) as never)
}

test('safe display, shell quoting, storage and PR labels', () => {
  expect(openCommand("/work/a'b $(touch nope)")).toBe("cd '/work/a'\\''b $(touch nope)' && claude")
  expect(safeText('hello\x1b[31m\n')).toBe('hello\\x1b[31m\\x0a')
  expect(storageLabel(row())).toBe('1.0 GiB')
  expect(storageLabel(row({ storagePartial: true }))).toBe('≥ 1.0 GiB (partial)')
  expect(storageLabel(row({ storageBytes: null }))).toBe('Storage unavailable')
  expect(changesLabel(row())).toBe('Clean')
  expect(changesLabel(row({ error: 'missing' }))).toBe('Status unavailable')
  expect(prLabel(row())).toBe('⤵ Merged · #7')
  expect(prLabel(row({ pullRequest: { ...row().pullRequest, headMatches: false } }))).toBe('⤵ Merged · #7 · branch changed since merge')
  expect(badgeFits(70, 'feature', '↗ Open · #8')).toBe(true)
  expect(badgeFits(20, 'feature', '↗ Open · #8')).toBe(false)
})

for (const surface of ['terminal', 'desktop'] as const) {
  test(`${surface}: open, inspect summaries/storage/PR, copy a quoted command, back and refresh`, async ($, on) => {
    stub(on)
    const calls: string[][] = []
    let copied = ''
    on('ui.copy', (_$, e) => { copied = e.text; return { value: { isCopied: true } } as never })
    on('process.run', (_$, e) => { calls.push([...e.argv]); return ok(e.argv.includes('inspect') ? detail : { worktrees: rows }) })
    await $.command.run({ command: 'worktrees', args: '' } as never)
    expect(calls[0]?.slice(2)).toEqual(['list', '--cwd', '/work/app'])
    const ui = await $.ui.mount({ plugin: 'worktrees', surface, component: 'Pane', requestId: 'worktrees', props: props() })
    expect(await ui.find({ text: /Latest: Improve search/ })).toBeDefined()
    expect(await ui.find({ text: /Storage: 1.0 GiB/ })).toBeDefined()
    const merged = await ui.find({ type: 'Text', text: '⤵ Merged · #7' })
    expect(merged?.props.color).toBe('#a371f7')
    expect((await ui.find({ type: 'Text', text: '↗ Open · #8' }))?.props.color).toBe('#3fb950')
    // The repository name/path are only shown when inspecting a worktree.
    expect(await ui.find({ text: '/work/app' })).toBeUndefined()
    await ui.press({ key: `row-${rows[1]!.path}` })
    expect(await ui.find({ text: rows[1]!.path })).toBeDefined()
    expect(await ui.find({ text: /Changes · 1 files/ })).toBeDefined()
    expect(calls[1]?.slice(2)).toEqual(['inspect', '--cwd', '/work/app', '--path', rows[1]!.path])
    await ui.press({ key: 'copy' })
    expect(copied).toBe(openCommand(rows[1]!.path))
    await ui.press({ key: 'back' })
    await ui.press({ key: 'refresh' })
    expect(calls.filter(a => a.includes('list')).length).toBe(2)
    await ui.unmount()
  })

  test(`${surface}: filter, empty result, clear and pagination`, async ($, on) => {
    stub(on)
    const many = Array.from({ length: 12 }, (_, i) => row({ path: `/work/app-${i}`, branch: `feature-${i}`, isCurrent: false }))
    on('process.run', () => ok({ worktrees: many }))
    await $.command.run({ command: 'worktrees', args: 'no-match' } as never)
    const ui = await $.ui.mount({ plugin: 'worktrees', surface, component: 'Pane', requestId: 'worktrees', props: props() })
    expect(await ui.find({ text: /No matching worktrees/ })).toBeDefined()
    await ui.press({ key: 'clear' })
    expect(await ui.find({ key: 'row-/work/app-0' })).toBeDefined()
    expect(await ui.find({ key: 'row-/work/app-9' })).toBeUndefined()
    await ui.press({ key: 'next' })
    expect(await ui.find({ key: 'row-/work/app-9' })).toBeDefined()
    expect(await ui.find({ text: 'Page 2/2' })).toBeDefined()
    await ui.press({ key: 'prev' })
    expect(await ui.find({ key: 'row-/work/app-0' })).toBeDefined()
    await ui.unmount()
  })

  for (const width of [28, 90]) {
    test(`${surface}: PR badge responds to ${width} columns`, async ($, on) => {
      stub(on)
      on('process.run', () => ok({ worktrees: rows }))
      await $.command.run({ command: 'worktrees', args: '' } as never)
      const ui = await $.ui.mount({ plugin: 'worktrees', surface, component: 'Pane', requestId: 'worktrees', props: props(width) })
      expect((await ui.find({ key: 'header-/work/app' }))?.props.flexDirection).toBe(width === 28 ? 'column' : 'row')
      await ui.unmount()
    })
  }

  test(`${surface}: a Git error can be retried; an inspection error preserves Back`, async ($, on) => {
    stub(on)
    let fails = true
    on('process.run', (_$, e) => fails || e.argv.includes('inspect') ? fail('Git failed') : ok({ worktrees: rows }))
    await $.command.run({ command: 'worktrees', args: '' } as never)
    const ui = await $.ui.mount({ plugin: 'worktrees', surface, component: 'Pane', requestId: 'worktrees', props: props() })
    expect(await ui.find({ text: /Git failed/ })).toBeDefined()
    fails = false
    await ui.press({ key: 'refresh' })
    await ui.press({ key: 'row-/work/app' })
    expect(await ui.find({ text: /Couldn’t load changes/ })).toBeDefined()
    await ui.press({ key: 'back' })
    expect(await ui.find({ key: 'row-/work/app' })).toBeDefined()
    await ui.unmount()
  })

  test(`${surface}: missing worktrees remain visible; copy is unavailable`, async ($, on) => {
    stub(on)
    on('process.run', (_$, e) => e.argv.includes('inspect') ? fail('Worktree directory is missing.') :
      ok({ worktrees: [row({ prunable: 'missing', error: 'Worktree directory is missing.', storageBytes: null })] }))
    await $.command.run({ command: 'worktrees', args: '' } as never)
    const ui = await $.ui.mount({ plugin: 'worktrees', surface, component: 'Pane', requestId: 'worktrees', props: props() })
    expect(await ui.find({ text: /Storage unavailable/ })).toBeDefined()
    await ui.press({ key: 'row-/work/app' })
    expect(await ui.find({ key: 'copy' })).toBeUndefined()
    expect(await ui.find({ text: /Prunable: missing/ })).toBeDefined()
    await ui.unmount()
  })
}

test('a late response cannot overwrite a newer repository refresh', async ($, on) => {
  stub(on)
  let release: (() => void) | undefined
  let calls = 0
  on('process.run', async () => {
    calls++
    if (calls === 1) { await new Promise<void>(resolve => { release = resolve }); return ok({ worktrees: rows }) }
    return ok({ worktrees: [row({ summary: 'Latest: Newer result' })] })
  })
  const first = $.command.run({ command: 'worktrees', args: '' } as never)
  // Wait until the first process call is actually held before starting another.
  for (let i = 0; i < 100 && !release; i++) await Promise.resolve()
  await $.command.run({ command: 'worktrees', args: '' } as never)
  release?.()
  await first
  const ui = await $.ui.mount({ plugin: 'worktrees', surface: 'terminal', component: 'Pane', requestId: 'worktrees', props: props() })
  expect(await ui.find({ text: /Latest: Newer result/ })).toBeDefined()
  expect(await ui.find({ text: /Latest: Improve search/ })).toBeUndefined()
  await ui.unmount()
})
