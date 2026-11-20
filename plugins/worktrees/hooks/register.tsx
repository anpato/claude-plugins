import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'
import type { Worktree, WorktreeDetail, WorktreeView } from '../types'

const PANE = 'worktrees'
const PAGE_SIZE = 9
const initial: WorktreeView = {
  cwd: '', query: '', worktrees: [], pickedPath: null, detail: null,
  isBusy: false, error: '', page: 0, request: 0,
}
const view = atom({ plugin: 'worktrees', key: 'view' } as const, initial)
const patch = ($: EngineInterface, fn: (v: WorktreeView) => Partial<WorktreeView>) =>
  update($, view, v => ({ ...(v ?? initial), ...fn(v ?? initial) }))

// Git paths, commit subjects and diffs are data, including any terminal controls.
export const safeText = (text: string) => text.replace(/[\x00-\x1f\x7f-\x9f]/g,
  ch => `\\x${ch.charCodeAt(0).toString(16).padStart(2, '0')}`)
const safeDiff = (text: string) => text.replace(/[\x00-\x08\x0b-\x1f\x7f-\x9f]/g, '�')
const fit = (text: string, width: number) => text.length > width ? `${text.slice(0, Math.max(0, width - 1))}…` : text
export const openCommand = (path: string) => `cd '${path.replace(/'/g, "'\\''")}' && claude`
const branchLabel = (row: Worktree) => row.bare ? 'Bare repository' : row.branch ?? `Detached HEAD · ${row.head.slice(0, 7)}`
export const prLabel = (row: Worktree) => {
  const pr = row.pullRequest
  const labels = { OPEN: '↗ Open', DRAFT: '↗ Draft', MERGED: '⤵ Merged', CLOSED: '↗ Closed', NONE: 'No PR', UNAVAILABLE: 'PR status unavailable', 'N/A': 'No branch PR' }
  return `${labels[pr.state]}${pr.number ? ` · #${pr.number}` : ''}${pr.state === 'MERGED' && !pr.headMatches ? ' · branch changed since merge' : ''}`
}
const prColor = (row: Worktree) => ({ OPEN: '#3fb950', MERGED: '#a371f7', CLOSED: '#f85149' }[row.pullRequest.state] ?? undefined)
export const badgeFits = (width: number, title: string, badge: string) => width >= title.length + badge.length + 5
export const storageLabel = (row: Worktree) => {
  if (row.storageBytes === null) return 'Storage unavailable'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let value = row.storageBytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++ }
  return `${row.storagePartial ? '≥ ' : ''}${value < 10 && unit > 0 ? value.toFixed(1) : Math.round(value)} ${units[unit]}${row.storagePartial ? ' (partial)' : ''}`
}
export const changesLabel = (row: Worktree) => row.bare ? 'No working files' : row.error ? 'Status unavailable' :
  row.tracked === 0 && row.untracked === 0 ? 'Clean' : `${row.tracked ?? '?'} tracked · ${row.untracked ?? '?'} untracked`
const metaLabel = (row: Worktree) => [
  changesLabel(row), row.upstream ? (row.ahead === null ? row.upstream : `↑${row.ahead} ↓${row.behind}`) : 'no upstream',
  row.conflicts ? `${row.conflicts} conflicts` : '', row.locked ? 'locked' : '', row.prunable ? 'prunable' : '',
].filter(Boolean).join(' · ')
export const matching = (rows: Worktree[], query: string) => {
  const needle = query.trim().toLocaleLowerCase()
  return rows.filter(row => `${branchLabel(row)} ${row.path}`.toLocaleLowerCase().includes(needle))
}

const focusOn = async ($: EngineInterface, key: string) => {
  try { await $.ui.focus({ requestId: PANE, key }) } catch { /* Best effort on headless surfaces. */ }
}
const run = async ($: EngineInterface, args: string[]) => {
  const result = await $.process.run(['python3', `${$.plugin.root}/scripts/worktrees.py`, ...args], { timeoutMs: 120_000 })
  if (result.exitCode !== 0) throw new Error(result.stderr.trim() || 'Could not read worktrees.')
  if (result.isStdoutTruncated) throw new Error('Worktree output was truncated. Try a smaller repository.')
  return JSON.parse(result.stdout)
}

const refresh = async ($: EngineInterface, query?: string) => {
  let request = 0
  await patch($, v => {
    request = v.request + 1
    return { request, isBusy: true, error: '', pickedPath: null, detail: null, page: 0, query: query ?? v.query }
  })
  try {
    const cwd = await $.session.cwd()
    const result = await run($, ['list', '--cwd', cwd]) as { worktrees: Worktree[] }
    await patch($, v => v.request === request ? { cwd, worktrees: result.worktrees, isBusy: false } : {})
  } catch (err) {
    await patch($, v => v.request === request ? { worktrees: [], isBusy: false, error: String(err instanceof Error ? err.message : err) } : {})
  }
}

const inspect = async ($: EngineInterface, row: Worktree) => {
  let request = 0
  await patch($, v => {
    request = v.request + 1
    return { request, pickedPath: row.path, detail: null, isBusy: true, error: '' }
  })
  try {
    const cwd = (await read($, view)).cwd
    const detail = await run($, ['inspect', '--cwd', cwd, '--path', row.path]) as WorktreeDetail
    await patch($, v => v.request === request ? { detail, isBusy: false } : {})
  } catch (err) {
    await patch($, v => v.request === request ? { isBusy: false, error: String(err instanceof Error ? err.message : err) } : {})
  }
  await focusOn($, 'back')
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: PANE, description: 'Browse Git worktrees, summaries and local changes', argumentHint: '[branch or path | close]', immediate: true })
    return next(e)
  })

  on('command.run', { command: 'worktrees' }, async ($, e) => {
    if (e.args.trim() === 'close') {
      await $.ui.close({ id: PANE })
      return {}
    }
    await $.ui.open({ id: PANE, title: 'Worktrees', columns: 56, focus: true, closeOnEscape: true })
    await refresh($, e.args.trim())
    return {}
  })

  // design-studio: claude-worktrees / Worktrees {}
  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button, Input, Code, Link } = $.ui.resolve(e)
    const v = (await read($, view)) ?? initial
    const width = Math.max(16, e.props.bodyColumns - 4)
    const picked = v.worktrees.find(row => row.path === v.pickedPath)
    const rows = matching(v.worktrees, v.query)
    const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE))
    const page = Math.min(v.page, pages - 1)
    const displayed = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)
    return (
      <Box flexDirection="column" paddingX={1}>
        <Text bold>Worktrees</Text>
        <Text dimColor>Read-only · refresh on demand</Text>
        {picked ? (
          <Box flexDirection="column" marginTop={1}>
            <Box key="detail-header" flexDirection={badgeFits(width, branchLabel(picked) + (picked.isCurrent ? ' · current' : ''), prLabel(picked)) ? 'row' : 'column'} justifyContent="space-between" columnGap={1}>
              <Text bold wrap="wrap">{safeText(branchLabel(picked))}{picked.isCurrent ? ' · current' : ''}</Text>
              <Text color={prColor(picked)} wrap="wrap">{prLabel(picked)}</Text>
            </Box>
            <Text wrap="wrap">{safeText(picked.path)}</Text>
            <Text dimColor>HEAD {picked.head.slice(0, 12) || 'unborn'}</Text>
            {picked.pullRequest.url && <Link href={picked.pullRequest.url} label={`PR #${picked.pullRequest.number}`} />}
            <Box flexDirection="column" marginTop={1}>
              <Text bold>Summary</Text>
              <Text wrap="wrap">{safeText(picked.summary)}</Text>
              <Text dimColor wrap="wrap">{safeText(metaLabel(picked))}</Text>
              <Text wrap="wrap">Storage: {storageLabel(picked)}</Text>
              <Text dimColor wrap="wrap">Includes ignored files. Shared Git store excluded; symlinks are not followed.</Text>
              {picked.upstream && <Text dimColor wrap="wrap">Upstream: {safeText(picked.upstream)}</Text>}
            </Box>
            {picked.locked && <Text wrap="wrap">Locked: {safeText(picked.locked)}</Text>}
            {picked.prunable && <Text wrap="wrap">Prunable: {safeText(picked.prunable)}</Text>}
            <Box marginTop={1} gap={1} flexWrap="wrap">
              <Button key="back" plain hotkey="b" label="back" autoFocus onPress={async () => {
                await patch($, state => ({ pickedPath: null, detail: null, error: '', isBusy: false, request: state.request + 1 }))
                await focusOn($, `row-${picked.path}`)
              }} />
              {!picked.bare && !picked.prunable && !picked.error && <Button key="copy" plain hotkey="c" label="copy open cmd" onPress={async press => {
                try {
                  const cmd = openCommand(picked.path)
                  const copied = await $.ui.copy({ text: cmd, surface: press.surface })
                  $.ui.toast(copied.isCopied ? 'Open command copied. Run it in another terminal.' : safeText(cmd))
                } catch { $.ui.toast('Could not copy the open command.') }
              }} />}
            </Box>
            {v.detail && <Box flexDirection="column" marginTop={1}>
              <Text bold>Changes · {v.detail.totalFiles} files</Text>
              {v.detail.files.map((file, i) => <Text key={`file-${i}`} wrap="truncate-end">
                {fit(`${file.status} ${safeText(file.path)}`, width)}
              </Text>)}
              {v.detail.totalFiles > v.detail.files.length && <Text dimColor>Showing the first {v.detail.files.length} files.</Text>}
              <Box marginTop={1} flexDirection="column">
                <Text bold>Tracked diff · staged + unstaged</Text>
                {v.detail.diff ? <Code source={safeDiff(v.detail.diff)} language="diff" wrap="wrap" /> : <Text dimColor>No tracked diff.</Text>}
                {v.detail.truncated && <Text>Diff preview truncated.</Text>}
                <Text dimColor wrap="wrap">{v.detail.note}</Text>
              </Box>
            </Box>}
          </Box>
        ) : !v.isBusy && !v.error ? (
          <Box flexDirection="column" marginTop={1}>
            <Input key="query" placeholder="Filter branch or path · Enter" value={v.query} onSubmit={async value => {
              await patch($, () => ({ query: value, page: 0 }))
              await focusOn($, 'search')
            }} />
            <Text dimColor>{rows.length} of {v.worktrees.length} worktrees · 1–9 inspect</Text>
            {displayed.map((row, i) => <Box key={`box-${row.path}`} flexDirection="column" marginTop={1}>
              <Box key={`header-${row.path}`} flexDirection={badgeFits(width, branchLabel(row) + (row.isCurrent ? ' · current' : ''), prLabel(row)) ? 'row' : 'column'} justifyContent="space-between" columnGap={1}>
                <Button key={`row-${row.path}`} plain hotkey={String(i + 1)} autoFocus={i === 0 ? true : undefined}
                  label={fit(safeText(`${branchLabel(row)}${row.isCurrent ? ' · current' : ''}`), width - 3)}
                  onPress={() => inspect($, row)} />
                <Text key={`pr-${row.path}`} color={prColor(row)} wrap="truncate-end">{fit(prLabel(row), width)}</Text>
              </Box>
              <Text wrap="truncate-end">{fit(safeText(row.summary), width)}</Text>
              <Text dimColor wrap="truncate-end">{fit(safeText(metaLabel(row)), width)}</Text>
              <Text dimColor>Storage: {storageLabel(row)}</Text>
              {row.error && <Text wrap="truncate-end">{fit(safeText(row.error), width)}</Text>}
            </Box>)}
            {rows.length === 0 && <Text wrap="wrap">{v.query ? 'No matching worktrees. Try another branch name or path.' : 'No registered worktrees.'}</Text>}
            {v.worktrees.length === 1 && rows.length === 1 && <Text dimColor wrap="wrap">Only this worktree. Additional worktrees will appear after refresh.</Text>}
            <Box marginTop={1} gap={1} flexWrap="wrap">
              <Button key="search" plain hotkey="s" label="search" onPress={() => focusOn($, 'query')} />
              {v.query !== '' && <Button key="clear" plain hotkey="a" label="clear filter" onPress={() => patch($, () => ({ query: '', page: 0 }))} />}
              {page > 0 && <Button key="prev" plain hotkey="p" label="prev" onPress={() => patch($, () => ({ page: page - 1 }))} />}
              {page < pages - 1 && <Button key="next" plain hotkey="n" label="next" onPress={() => patch($, () => ({ page: page + 1 }))} />}
              {pages > 1 && <Text dimColor>Page {page + 1}/{pages}</Text>}
            </Box>
          </Box>
        ) : null}
        {v.isBusy && <Text>Loading {picked ? 'changes' : 'worktrees'}…</Text>}
        {v.error !== '' && <Box flexDirection="column" marginTop={1}>
          <Text bold>Couldn’t load {picked ? 'changes' : 'worktrees'}</Text>
          <Text wrap="wrap">{safeText(v.error)}</Text>
          {!picked && <Text dimColor wrap="wrap">Open Claude in a Git repository, then refresh.</Text>}
        </Box>}
        <Box marginTop={1} gap={1} flexWrap="wrap">
          <Button key="refresh" plain hotkey="r" label="refresh" onPress={() => refresh($)} />
          <Button key="close" plain hotkey="q" label="close" onPress={() => $.ui.close({ id: PANE })} />
        </Box>
        <Text dimColor wrap="wrap">Tab / ↑↓ navigate · Enter inspect · Esc close</Text>
      </Box>
    )
  })
}
