export type Worktree = {
  path: string
  head: string
  branch: string | null
  bare: boolean
  detached: boolean
  locked: string | null
  prunable: string | null
  isCurrent: boolean
  tracked: number | null
  untracked: number | null
  conflicts: number
  upstream: string | null
  ahead: number | null
  behind: number | null
  error: string | null
  summary: string
  storageBytes: number | null
  storagePartial: boolean
  pullRequest: {
    state: 'OPEN' | 'DRAFT' | 'MERGED' | 'CLOSED' | 'NONE' | 'UNAVAILABLE' | 'N/A'
    number: number | null
    url: string | null
    headMatches: boolean
  }
}

export type WorktreeDetail = {
  files: { status: string; path: string }[]
  totalFiles: number
  diff: string
  truncated: boolean
  note: string
}

export type WorktreeView = {
  cwd: string
  query: string
  worktrees: Worktree[]
  pickedPath: string | null
  detail: WorktreeDetail | null
  isBusy: boolean
  error: string
  page: number
  request: number
}

declare module 'claude-code' {
  interface PluginState {
    worktrees: { view: WorktreeView }
  }
}
