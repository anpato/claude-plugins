import type { SquadUsage } from '../types'

export const totalTokens = (usage: SquadUsage): number =>
  usage.input + usage.output + usage.cacheRead + usage.cacheWrite

// 'claude-opus-5-5' -> 'Opus 5.5'; anything else is shown as given.
export const modelLabel = (model: string): string => {
  const match = /(fable|mythos|opus|sonnet|haiku)-(\d+)(?:-(\d+))?/i.exec(model)
  if (!match) return model
  const [, family = '', major, minor] = match
  const name = family.charAt(0).toUpperCase() + family.slice(1).toLowerCase()
  return minor === undefined ? `${name} ${major}` : `${name} ${major}.${minor}`
}

export const formatTokens = (n: number): string => {
  if (n < 1000) return String(n)
  if (n < 10_000) return `${(n / 1000).toFixed(1)}k`
  if (n < 1_000_000) return `${Math.round(n / 1000)}k`
  return `${(n / 1_000_000).toFixed(1)}M`
}

export const formatDuration = (ms: number): string => {
  const total = Math.max(0, Math.floor(ms / 1000))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = String(total % 60).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`
}
