#!/usr/bin/env bun
// squad for Codex: a live panel of a Codex session's subagents, run in a terminal split
// beside Codex (Codex has no pane API). Reads only what Codex writes anyway:
//   ~/.codex/state_N.sqlite   which threads a session spawned, their names, model and effort
//   rollout-*.jsonl           each subagent's tool calls, tokens and completion, appended live
// Usage: squad-codex [--session <thread id>] [--cwd <dir>] [--blocks]
import { Database } from 'bun:sqlite'
import { closeSync, existsSync, fstatSync, openSync, readSync, readdirSync } from 'node:fs'
import { basename, join } from 'node:path'

import type { SquadAgent } from '../types'
import { HIRES_COLUMNS, HIRES_ROWS, hiresPixels } from '../hooks/hires'
import { modelColor } from '../hooks/sprite'
import { render, vehicleOf } from './render'
import { type ThreadRow, type Track, applyLines, newTrack } from './track'

const args = process.argv.slice(2)
const flag = (name: string): string | undefined => {
  const i = args.indexOf(name)
  return i >= 0 ? args[i + 1] : undefined
}
if (args.includes('-h') || args.includes('--help')) {
  console.log('squad-codex [--session <thread id>] [--cwd <dir>] [--blocks]\n' +
    'Follows the newest Codex session started in --cwd (default: here); q quits.')
  process.exit(0)
}
const cwd = flag('--cwd') ?? process.cwd()
const pinned = flag('--session')
const hasPixels = !args.includes('--blocks') && /ghostty|kitty/i.test(`${process.env.TERM_PROGRAM ?? ''} ${process.env.TERM ?? ''}`)
const codexHome = process.env.CODEX_HOME ?? join(process.env.HOME ?? '', '.codex')

const STALL_MS = 120_000
const FINISHED_ROW_TTL_MS = 60 * 60 * 1000

// The newest state database (state_5.sqlite today; the number moves with Codex migrations).
const findDb = (): string | undefined => {
  if (!existsSync(codexHome)) return undefined
  const found = readdirSync(codexHome)
    .map(name => ({ name, n: Number(/^state_(\d+)\.sqlite$/.exec(name)?.[1]) }))
    .filter(f => Number.isFinite(f.n))
    .sort((a, b) => b.n - a.n)[0]
  return found ? join(codexHome, found.name) : undefined
}

let db: Database | undefined
const query = <T>(sql: string, ...params: string[]): T[] => {
  try {
    if (!db) {
      const path = findDb()
      if (!path) return []
      db = new Database(path, { readonly: true })
    }
    return db.query(sql).all(...params) as T[]
  } catch {
    db = undefined // reopen next time: Codex may have migrated or been mid-write
    return []
  }
}

const rootSession = (): string | undefined =>
  pinned ??
  query<{ id: string }>(
    `select id from threads where coalesce(thread_source, '') != 'subagent' and archived = 0 and cwd = ?
     order by coalesce(created_at_ms, created_at * 1000) desc limit 1`,
    cwd,
  )[0]?.id

const descendants = (root: string): ThreadRow[] =>
  query<ThreadRow>(
    `with recursive kids(id) as (
       select child_thread_id from thread_spawn_edges where parent_thread_id = ?
       union select e.child_thread_id from thread_spawn_edges e join kids k on e.parent_thread_id = k.id)
     select t.id, t.agent_nickname as nickname, t.agent_path as agentPath, t.agent_role as role, t.model,
            t.reasoning_effort as effort, coalesce(t.created_at_ms, t.created_at * 1000) as createdAtMs,
            t.rollout_path as rolloutPath
     from threads t join kids k on k.id = t.id order by createdAtMs`,
    root,
  )

// Whatever the file gained since the last read, up to its last whole line.
const readNew = (track: Track, path: string): Track => {
  let fd: number | undefined
  try {
    fd = openSync(path, 'r')
    const size = fstatSync(fd).size
    if (size <= track.offset) return track
    const buf = Buffer.alloc(size - track.offset)
    readSync(fd, buf, 0, buf.length, track.offset)
    const end = buf.lastIndexOf(0x0a)
    if (end < 0) return track
    return applyLines(track, buf.subarray(0, end + 1).toString('utf8'), end + 1)
  } catch {
    return track
  } finally {
    if (fd !== undefined) closeSync(fd)
  }
}

let session: string | undefined
let tracks = new Map<string, { track: Track; path: string }>()

const refreshThreads = () => {
  const root = rootSession()
  if (root !== session) {
    session = root
    tracks = new Map()
  }
  if (!session) return
  for (const row of descendants(session)) {
    if (!tracks.has(row.id)) tracks.set(row.id, { track: newTrack(row), path: row.rolloutPath })
  }
}

const readTracks = () => {
  for (const [id, t] of tracks) tracks.set(id, { ...t, track: readNew(t.track, t.path) })
}

// Kitty graphics (Ghostty, kitty): each vehicle picture is sent once and placed by id.
const imageIds = new Map<string, number>()
const apc = (body: string) => `\x1b_G${body}\x1b\\`
const imageFor = (a: SquadAgent, frame: number): string => {
  const look = { activity: vehicleOf(a), paint: modelColor(a.model), isFailed: a.status === 'failed', frame }
  const key = `${look.activity ?? 'idle'}|${look.paint}|${look.isFailed}|${frame % 2}`
  let id = imageIds.get(key)
  let out = ''
  if (id === undefined) {
    id = imageIds.size + 1
    imageIds.set(key, id)
    const pic = hiresPixels(look)
    const chunks = pic.rgba.match(/.{1,4096}/g) ?? []
    chunks.forEach((chunk, i) => {
      const more = i < chunks.length - 1 ? 1 : 0
      out += apc(i === 0 ? `a=t,f=32,s=${pic.width},v=${pic.height},i=${id},q=2,m=${more};${chunk}` : `m=${more};${chunk}`)
    })
  }
  return out + apc(`a=p,i=${id},c=${HIRES_COLUMNS},r=${HIRES_ROWS},C=1,q=2`)
}

let frame = 0
let step = 0
const draw = () => {
  const now = Date.now()
  const agents = [...tracks.values()]
    .map(t => t.track.agent)
    .filter(a => a.status === 'running' || a.endedAt === undefined || now - a.endedAt <= FINISHED_ROW_TTL_MS)
  const header = session
    ? `squad · codex · ${basename(cwd)} · session ${session.slice(-6)}`
    : `squad · codex · waiting for a Codex session in ${cwd}`
  const { lines, placements } = render({
    agents, now, frame, step, header, hasPixels,
    columns: process.stdout.columns || 80,
    rows: process.stdout.rows || 24,
    stallMs: STALL_MS,
  })
  let out = '\x1b[?2026h\x1b[H' + lines.map(l => `${l}\x1b[K`).join('\r\n') + '\x1b[J'
  if (hasPixels) {
    out += apc('a=d,d=a,q=2')
    for (const p of placements) {
      out += `\x1b[${p.line + 1};${p.column + 1}H` + imageFor(p.agent, p.agent.status === 'running' ? frame : 0)
    }
  }
  process.stdout.write(out + '\x1b[?2026l')
}

const quit = () => {
  process.stdout.write((hasPixels ? apc('a=d,d=A,q=2') : '') + '\x1b[0m\x1b[?25h\x1b[?1049l')
  process.exit(0)
}

process.stdout.write('\x1b[?1049h\x1b[?25l')
process.on('SIGINT', quit)
process.on('SIGTERM', quit)
if (process.stdin.isTTY) {
  process.stdin.setRawMode(true)
  process.stdin.on('data', d => {
    const key = d.toString()
    if (key === 'q' || key === '\x03') quit()
  })
}

refreshThreads()
readTracks()
draw()
setInterval(refreshThreads, 1000)
setInterval(readTracks, 250)
setInterval(() => (frame += 1), 1000)
// Ten frames a second while an agent works (the bar's sweep); once a second otherwise (clocks).
let drawnFrame = -1
setInterval(() => {
  const isBusy = [...tracks.values()].some(t => t.track.agent.status === 'running')
  if (isBusy) step += 1
  if (isBusy || frame !== drawnFrame) draw()
  drawnFrame = frame
}, 100)
process.stdout.on('resize', draw)
