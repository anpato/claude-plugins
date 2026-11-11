import type { SquadActivity } from '../types'

// Tiny vehicles drawn as a Raster of quadrant blocks: each cell holds 2x2
// pixels in two colors, so 7 columns by 3 rows carry a 14x6 picture. The
// vehicle says what the agent is doing, its paint says the model, and a
// running agent's vehicle moves (two frames).

export const SPRITE_COLUMNS = 7
export const SPRITE_ROWS = 3
const WIDTH = SPRITE_COLUMNS * 2
const HEIGHT = SPRITE_ROWS * 2

const DEFAULT = 0x01000000 // the terminal's own color: a transparent pixel
const SPACE = 0x20
// The glyph for each set of lit quadrants: top-left 1, top-right 2,
// bottom-left 4, bottom-right 8.
const QUADRANTS = [
  SPACE, 0x2598, 0x259d, 0x2580, 0x2596, 0x258c, 0x259e, 0x259b,
  0x2597, 0x259a, 0x2590, 0x259c, 0x2584, 0x2599, 0x259f, 0x2588,
]

const CLAY = 0xd97757
const FAILED_PAINT = 0x6b6b6b

// Fixed colors; `B` is the model's paint and `D` a darker shade of it.
const COLORS: Record<string, number> = {
  W: 0xa9d6f5, // glass
  w: 0xf4f3ee, // paper
  K: 0x4a4a48, // tyres, keys (dark grey: black vanishes on a dark terminal)
  G: 0x8e929a, // steel
  Y: 0xf5c542, // flame core, stars
  R: 0xff7b3a, // flame
  d: 0x8a5a33, // dirt
  o: 0xbfe3fb, // bubbles
}

type Frames = [string[], string[]]

// Each picture is 6 rows of 14 pixels; a pixel is half a cell wide and half a
// cell tall, so two across look square. Every vehicle faces right. A cell draws
// two colors, so each 2x2 block (even column, even row) holds at most two.
const IDLE: Frames = [
  ['..............', '....BBBBBB....', '...BWWBBWWB...', '.BBBBBBBBBBBB.', '.BBBBBBBBBBBB.', '..KK......KK..'],
  ['..............', '....BBBBBB....', '...BWWBBWWB...', '.BBBBBBBBBBBB.', '.BBBBBBBBBBBB.', '..KK......KK..'],
]

const VEHICLES: Record<SquadActivity, Frames> = {
  // Submarine with a periscope; the propeller turns and bubbles rise.
  search: [
    ['.......GGG....', '.......G....o.', '...BBBBBBBBB..', 'G.BBWBBWBBWBB.', '.GBBBBBBBBBBB.', '...DDDDDDDDD..'],
    ['.......GGG..o.', '.......G......', '...BBBBBBBBB..', '.GBBWBBWBBWBB.', 'G.BBBBBBBBBBB.', '...DDDDDDDDD..'],
  ],
  // Bulldozer; the treads roll and the blade pushes dirt.
  edit: [
    ['..BBBB........', '..BWWB........', '.BBBBBBBBBB.GG', '.BBBBBBBBBBGGG', 'KKKKKKKKKKK.GG', 'KGKGKGKGKGK.dd'],
    ['..BBBB........', '..BWWB........', '.BBBBBBBBBB.GG', '.BBBBBBBBBBGGG', 'KKKKKKKKKKK.GG', 'GKGKGKGKGKG.d.'],
  ],
  // Race car with a spoiler; exhaust puffs out behind it.
  run: [
    ['..............', 'D.....WWW.....', 'DBBBBBBBBBB...', 'BBBBBBBBBBBBBB', '..BBBBBBBBBB..', 'GGKK......KK..'],
    ['..............', 'D.....WWW.....', 'DBBBBBBBBBB...', 'BBBBBBBBBBBBBB', '..BBBBBBBBBB..', '.GKK......KK..'],
  ],
  // Rocket; the flame flickers and a star blinks.
  web: [
    ['..DD.........Y', '..BBBBBBBBB...', 'RYBBBBWWBBBBB.', 'YRBBBBWWBBBBB.', '..BBBBBBBBB...', '..DD..........'],
    ['..DD..........', '..BBBBBBBBB...', 'YRBBBBWWBBBBB.', 'RYBBBBWWBBBBB.', '..BBBBBBBBB...', '..DD..........'],
  ],
  // Typewriter; the keys go down and a letter lands on the page.
  answer: [
    ['...wwwwww.....', '...wKwwww.....', '.DDDDDDDDDD...', '.BBBBBBBBBB.G.', '.BKBKBKBKBB...', '.BBBBBBBBBB...'],
    ['...wwwwww.....', '...wKwKww.....', '.DDDDDDDDDD...', '.BBBBBBBBBB.G.', '.BBKBKBKBKB...', '.BBBBBBBBBB...'],
  ],
}

export const ACTIVITY_LABEL: Record<SquadActivity, string> = {
  search: 'searching',
  edit: 'editing',
  run: 'running commands',
  web: 'on the web',
  answer: 'writing up',
}

export const hex = (color: number): string => `#${color.toString(16).padStart(6, '0')}`

const darker = (color: number): number => {
  const r = Math.floor(((color >> 16) & 0xff) * 0.7)
  const g = Math.floor(((color >> 8) & 0xff) * 0.7)
  const b = Math.floor((color & 0xff) * 0.7)
  return (r << 16) | (g << 8) | b
}

const distance = (a: number, b: number): number => {
  if (a === DEFAULT || b === DEFAULT) return a === b ? 0 : Number.POSITIVE_INFINITY
  const dr = ((a >> 16) & 0xff) - ((b >> 16) & 0xff)
  const dg = ((a >> 8) & 0xff) - ((b >> 8) & 0xff)
  const db = (a & 0xff) - (b & 0xff)
  return dr * dr + dg * dg + db * db
}

// One cell from its four pixels (top-left, top-right, bottom-left, bottom-right):
// a cell draws two colors, so a third is drawn as the nearer of the two kept.
const cellOf = (pixels: readonly number[]): [number, number, number] => {
  const counts = new Map<number, number>()
  for (const p of pixels) counts.set(p, (counts.get(p) ?? 0) + 1)
  const kept = [...counts.entries()].sort((x, y) => y[1] - x[1]).map(([color]) => color)
  const first = kept[0] ?? DEFAULT
  const second = kept[1] ?? DEFAULT
  // The lit quadrants take the foreground; a transparent color is always the background.
  const fg = first === DEFAULT ? second : first
  const bg = first === DEFAULT ? DEFAULT : second
  if (fg === DEFAULT) return [SPACE, DEFAULT, DEFAULT]

  let mask = 0
  pixels.forEach((p, i) => {
    const isFg = p === fg || (p !== bg && distance(p, fg) <= distance(p, bg))
    if (isFg) mask |= 1 << i
  })
  return [QUADRANTS[mask] ?? SPACE, fg, bg]
}

type Look = { activity?: SquadActivity; model: string; isFailed: boolean; frame: number }

// The Raster's `cells`: base64 of little-endian u32 triplets per cell.
export const spriteCells = ({ activity, model, isFailed, frame }: Look): string => {
  const frames = activity === undefined ? IDLE : VEHICLES[activity]
  const step = Math.abs(Math.trunc(frame)) % 2
  const picture = frames[step] ?? frames[0]
  const paint = isFailed ? FAILED_PAINT : modelColor(model)

  const pixel = (x: number, y: number): number => {
    const glyph = picture[y]?.[x]
    if (glyph === undefined || glyph === '.') return DEFAULT
    if (glyph === 'B') return paint
    if (glyph === 'D') return darker(paint)
    return COLORS[glyph] ?? DEFAULT
  }

  const view = new DataView(new ArrayBuffer(SPRITE_COLUMNS * SPRITE_ROWS * 12))
  for (let row = 0; row < SPRITE_ROWS; row++) {
    for (let col = 0; col < SPRITE_COLUMNS; col++) {
      const x = col * 2
      const y = row * 2
      const cell = cellOf([pixel(x, y), pixel(x + 1, y), pixel(x, y + 1), pixel(x + 1, y + 1)])
      const at = (row * SPRITE_COLUMNS + col) * 12
      cell.forEach((word, k) => view.setUint32(at + k * 4, word, true))
    }
  }

  let binary = ''
  for (const byte of new Uint8Array(view.buffer)) binary += String.fromCharCode(byte)
  return btoa(binary)
}

// The picture's pixels, row-major, for previews: 14x6 colors (DEFAULT = none).
export const spritePixels = (look: Look): number[] => {
  const cells = Uint8Array.from(atob(spriteCells(look)), c => c.charCodeAt(0))
  const view = new DataView(cells.buffer)
  const out = new Array<number>(WIDTH * HEIGHT).fill(DEFAULT)
  for (let row = 0; row < SPRITE_ROWS; row++) {
    for (let col = 0; col < SPRITE_COLUMNS; col++) {
      const at = (row * SPRITE_COLUMNS + col) * 12
      const glyph = view.getUint32(at, true)
      const fg = view.getUint32(at + 4, true)
      const bg = view.getUint32(at + 8, true)
      const mask = Math.max(0, QUADRANTS.indexOf(glyph))
      for (let i = 0; i < 4; i++) {
        const x = col * 2 + (i % 2)
        const y = row * 2 + Math.floor(i / 2)
        out[y * WIDTH + x] = mask & (1 << i) ? fg : bg
      }
    }
  }
  return out
}

// The model's color, for its name in the row's text.
const MODEL_COLORS: readonly (readonly [RegExp, number])[] = [
  [/opus/i, CLAY],
  [/sonnet/i, 0x6c9cf0],
  [/haiku/i, 0x63c58a],
  [/fable|mythos/i, 0xb48ead],
]

export const modelColor = (model: string): number =>
  MODEL_COLORS.find(([pattern]) => pattern.test(model))?.[1] ?? 0xb0b0b0
