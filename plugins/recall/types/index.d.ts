// A past session as recall.py --json describes it.
export type RecallSession = {
  id: string
  title: string
  cwd: string | null
  project: string
  branch: string | null
  lastActive: string
  age: string
  lastPrompt: string | null
  prs: string[]
  hits?: number
  snippets?: string[]
}

// What the pane shows: a list from `list`/`search`, or one session picked from it.
export type RecallView = {
  query: string
  status: string
  isBusy: boolean
  sessions: RecallSession[]
  pickedId: string | null
  loadedIds: string[]
}

declare module 'claude-code' {
  interface PluginState {
    recall: { view: RecallView }
  }
}
