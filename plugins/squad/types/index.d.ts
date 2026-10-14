export type SquadUsage = {
  input: number
  output: number
  cacheRead: number
  cacheWrite: number
}

// What an agent is doing, read from the tools it calls.
export type SquadActivity = 'search' | 'edit' | 'run' | 'web' | 'answer'

export type SquadAgent = {
  id: string
  title: string
  type: string
  model: string
  effort?: string
  status: 'running' | 'done' | 'failed'
  startedAt: number
  endedAt?: number
  usage: SquadUsage
  // Input side of the latest request: what the agent's context holds now.
  contextTokens: number
  stepsDone?: number
  stepsTotal?: number
  note?: string
  // What it is doing now; and, for a finished agent's costume, how often it did each.
  activity?: SquadActivity
  activityCounts?: Partial<Record<SquadActivity, number>>
}

declare module 'claude-code' {
  // The mod's own tool, so `tool.call`'s matcher and input know it.
  interface McpToolInputs {
    mcp__squad__step: { done: number; total?: number; note?: string }
  }

  interface PluginState {
    squad: {
      agents: SquadAgent[]
      tick: number
      // A faster counter that drives the working bars' sweep.
      sweep: number
      // Agents Claude Code still lists, so their transcripts can be opened.
      openable: string[]
      contextWindow: number
      isFinishedCollapsed: boolean
      // The terminal draws real pixels (Ghostty, kitty): vehicles are images, not blocks.
      hasPixels: boolean
    }
  }
}
