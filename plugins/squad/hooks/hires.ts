import type { SquadActivity } from '../types'

// High-resolution vehicles for terminals that draw real pixels (Ghostty, kitty):
// 28x24 pixel art, scaled up 4x with hard edges and sent as an `Image` over the
// same 7x3 cells the block version uses. Same vehicles, same model paint.

export const HIRES_COLUMNS = 7
export const HIRES_ROWS = 3
const W = 28
const H = 24
const SCALE = 4

type Look = { activity?: SquadActivity; paint: number; isFailed: boolean; frame: number }

const FAILED_PAINT = 0x6b6b6b
const GLASS = 0xa9d6f5
const GLINT = 0xeaf6ff
const TYRE = 0x3a3a3c
const STEEL = 0x9aa0a8
const STEEL_DARK = 0x6e737b
const WHITE = 0xf4f3ee
const INK = 0x2b2b2b
const YELLOW = 0xf5c542
const FLAME = 0xff7b3a
const RED = 0xe0565b
const DIRT = 0x8a5a33
const BUBBLE = 0xbfe3fb

const shade = (color: number, factor: number): number => {
  const ch = (shift: number) => Math.min(255, Math.max(0, Math.round(((color >> shift) & 0xff) * factor)))
  return (ch(16) << 16) | (ch(8) << 8) | ch(0)
}

// A 28x24 canvas: 0 is transparent, anything else is 0x1RRGGBB (bit 24 marks it set).
class Canvas {
  readonly px = new Uint32Array(W * H)

  rect(x: number, y: number, w: number, h: number, color: number): this {
    for (let j = Math.max(0, y); j < Math.min(H, y + h); j++) {
      for (let i = Math.max(0, x); i < Math.min(W, x + w); i++) this.px[j * W + i] = color | 0x1000000
    }
    return this
  }

  // A filled ellipse centred on pixel centres (cx, cy are in pixel units).
  oval(cx: number, cy: number, rx: number, ry: number, color: number): this {
    for (let j = 0; j < H; j++) {
      for (let i = 0; i < W; i++) {
        const dx = (i + 0.5 - cx) / rx
        const dy = (j + 0.5 - cy) / ry
        if (dx * dx + dy * dy <= 1) this.px[j * W + i] = color | 0x1000000
      }
    }
    return this
  }

  disc(cx: number, cy: number, r: number, color: number): this {
    return this.oval(cx, cy, r, r, color)
  }

  dot(x: number, y: number, color: number): this {
    return this.rect(x, y, 1, 1, color)
  }
}

const wheel = (c: Canvas, cx: number, cy: number, r: number, frame: number) => {
  c.disc(cx, cy, r, TYRE).disc(cx, cy, r * 0.45, STEEL)
  // The spoke flips between horizontal and vertical as the wheel turns.
  if (frame === 0) c.rect(Math.round(cx - r * 0.7), Math.round(cy - 0.5), Math.round(r * 1.4), 1, STEEL_DARK)
  else c.rect(Math.round(cx - 0.5), Math.round(cy - r * 0.7), 1, Math.round(r * 1.4), STEEL_DARK)
}

type Draw = (c: Canvas, paint: number, frame: number) => void

// A small parked car: nothing has started yet.
const idle: Draw = (c, paint) => {
  const dark = shade(paint, 0.7)
  const light = shade(paint, 1.25)
  c.rect(9, 7, 11, 1, light).rect(8, 8, 13, 5, paint)
  c.rect(10, 9, 4, 3, GLASS).rect(15, 9, 4, 3, GLASS).dot(10, 9, GLINT).dot(15, 9, GLINT)
  c.rect(3, 13, 22, 5, paint).rect(3, 13, 22, 1, light).rect(3, 17, 22, 1, dark)
  c.rect(23, 14, 2, 2, YELLOW).rect(3, 14, 1, 2, RED)
  wheel(c, 8.5, 19.5, 3, 0)
  wheel(c, 19.5, 19.5, 3, 0)
}

const VEHICLES: Record<SquadActivity, Draw> = {
  // Submarine with a periscope; the propeller turns and bubbles rise.
  search: (c, paint, frame) => {
    const dark = shade(paint, 0.7)
    const light = shade(paint, 1.25)
    c.rect(16, 2, 1, 6, STEEL).rect(16, 2, 4, 1, STEEL).rect(19, 2, 1, 2, GLASS)
    c.rect(11, 7, 7, 5, paint).rect(11, 7, 7, 1, light)
    c.oval(14, 15.5, 11.5, 5.5, paint)
    // The keel: the hull's lower rows in the darker shade.
    for (let j = 18; j < 22; j++) {
      for (let i = 0; i < W; i++) if (c.px[j * W + i]) c.px[j * W + i] = dark | 0x1000000
    }
    c.rect(8, 11, 12, 1, light)
    for (const x of [9, 14, 19]) c.disc(x, 14.5, 1.6, GLASS).dot(x - 1, 13, GLINT)
    c.rect(1, 15, 2, 1, STEEL_DARK)
    if (frame === 0) c.rect(0, 12, 2, 3, STEEL).rect(0, 16, 2, 3, STEEL)
    else c.rect(0, 14, 2, 3, STEEL)
    if (frame === 0) c.disc(24.5, 8.5, 1.2, BUBBLE).disc(26.5, 4.5, 1.6, BUBBLE)
    else c.disc(25.5, 6.5, 1.6, BUBBLE).disc(23.5, 2.5, 1.2, BUBBLE)
  },

  // Bulldozer; the treads roll and the blade pushes dirt.
  edit: (c, paint, frame) => {
    const dark = shade(paint, 0.7)
    const light = shade(paint, 1.25)
    c.rect(15, 5, 1, 6, STEEL_DARK).rect(14, 4, 3, 1, STEEL_DARK)
    c.rect(5, 4, 8, 7, paint).rect(5, 4, 8, 1, light).rect(7, 6, 4, 3, GLASS).dot(7, 6, GLINT)
    c.rect(3, 11, 16, 6, paint).rect(3, 11, 16, 1, light).rect(3, 16, 16, 1, dark)
    c.rect(19, 13, 4, 2, dark)
    c.oval(11, 20.5, 9.5, 3, TYRE)
    for (const x of [5, 9, 13, 17]) c.disc(x, 20.5, 1.6, STEEL)
    for (let x = 3 + frame; x < 20; x += 2) c.dot(x, 17, STEEL_DARK)
    c.rect(22, 9, 3, 13, STEEL).rect(24, 9, 1, 13, STEEL_DARK).rect(22, 21, 4, 1, STEEL_DARK)
    if (frame === 0) c.disc(26.5, 20.5, 1.8, DIRT).dot(27, 18, DIRT)
    else c.disc(26.5, 21, 1.4, DIRT).dot(26, 17, DIRT)
  },

  // Race car with a spoiler; the wheels spin and exhaust puffs out behind it.
  run: (c, paint, frame) => {
    const dark = shade(paint, 0.7)
    const light = shade(paint, 1.25)
    c.rect(1, 8, 6, 2, dark).rect(3, 10, 1, 4, dark)
    c.disc(13.5, 11, 2.6, WHITE).rect(13, 10, 3, 1, INK)
    c.rect(2, 13, 21, 5, paint).rect(2, 13, 21, 1, light)
    for (let i = 0; i < 4; i++) c.rect(23 + i, 14 + Math.floor(i / 2), 1, 4 - Math.floor(i / 2), paint)
    c.rect(2, 17, 24, 1, dark)
    c.disc(17, 15.5, 1.8, WHITE).dot(17, 15, INK)
    c.rect(9, 15, 4, 1, light)
    wheel(c, 6.5, 19, 3.4, frame)
    wheel(c, 21.5, 19, 3.4, frame)
    if (frame === 0) c.disc(1, 17.5, 1.3, STEEL).dot(0, 15, STEEL)
    else c.disc(0.5, 16, 1, STEEL)
  },

  // Rocket; the flame flickers and a star twinkles.
  web: (c, paint, frame) => {
    const dark = shade(paint, 0.7)
    const light = shade(paint, 1.25)
    c.rect(6, 4, 3, 1, dark).rect(6, 5, 4, 4, dark)
    c.rect(6, 16, 4, 4, dark).rect(6, 20, 3, 1, dark)
    c.rect(7, 9, 14, 7, paint).rect(7, 9, 14, 1, light).rect(7, 15, 14, 1, dark)
    for (let i = 0; i < 4; i++) c.rect(21 + i, 9 + i, 1, 7 - 2 * i, paint)
    c.dot(25, 12, light)
    c.disc(15, 12.5, 2.4, WHITE).disc(15, 12.5, 1.6, GLASS).dot(14, 11, GLINT)
    c.rect(5, 10, 2, 5, STEEL)
    if (frame === 0) c.rect(1, 11, 4, 3, FLAME).rect(3, 12, 2, 1, YELLOW).dot(0, 12, FLAME)
    else c.rect(2, 10, 3, 5, FLAME).rect(0, 12, 4, 1, YELLOW)
    const [sx, sy] = frame === 0 ? [25, 3] : [23, 20]
    c.dot(sx, sy, YELLOW).dot(sx - 1, sy, YELLOW).dot(sx + 1, sy, YELLOW).dot(sx, sy - 1, YELLOW).dot(sx, sy + 1, YELLOW)
  },

  // Typewriter; the keys go down and a line of text grows on the page.
  answer: (c, paint, frame) => {
    const dark = shade(paint, 0.7)
    const light = shade(paint, 1.25)
    c.rect(8, 1, 12, 9, WHITE).rect(10, 3, 8, 1, STEEL).rect(10, 5, frame === 0 ? 4 : 7, 1, STEEL)
    c.rect(5, 9, 18, 3, INK).rect(3, 9, 2, 3, STEEL).rect(23, 9, 2, 3, STEEL).rect(24, 7, 3, 1, STEEL)
    c.rect(4, 12, 20, 9, paint).rect(4, 12, 20, 1, light).rect(4, 20, 20, 1, dark)
    for (let row = 0; row < 2; row++) {
      for (let k = 0; k < 6; k++) {
        const isPressed = (frame + row + k) % 4 === 0
        c.rect(6 + k * 3, 14 + row * 3 + (isPressed ? 1 : 0), 2, 2, isPressed ? STEEL_DARK : WHITE)
      }
    }
  },
}

const base64 = (bytes: Uint8Array): string => {
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary)
}

// The picture as RGBA bytes, `SCALE` times larger with hard edges, so the
// terminal's own scaling into the 7x3 box does not blur it.
type Picture = { rgba: string; width: number; height: number }

// The panel redraws ten times a second while agents work (the bar's sweep), and
// each picture only changes with its look, so pictures are kept by look.
const cache = new Map<string, Picture>()

export const hiresPixels = (look: Look): Picture => {
  const key = `${look.activity ?? 'idle'}|${look.paint}|${look.isFailed}|${Math.abs(Math.trunc(look.frame)) % 2}`
  const kept = cache.get(key)
  if (kept !== undefined) return kept
  const picture = drawPicture(look)
  if (cache.size > 200) cache.clear()
  cache.set(key, picture)
  return picture
}

const drawPicture = ({ activity, paint, isFailed, frame }: Look): Picture => {
  const c = new Canvas()
  const draw = activity === undefined ? idle : VEHICLES[activity]
  draw(c, isFailed ? FAILED_PAINT : paint, Math.abs(Math.trunc(frame)) % 2)

  const width = W * SCALE
  const height = H * SCALE
  const out = new Uint8Array(width * height * 4)
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const p = c.px[Math.floor(y / SCALE) * W + Math.floor(x / SCALE)] ?? 0
      if (p === 0) continue
      const o = (y * width + x) * 4
      out[o] = (p >> 16) & 0xff
      out[o + 1] = (p >> 8) & 0xff
      out[o + 2] = p & 0xff
      out[o + 3] = 0xff
    }
  }
  return { rgba: base64(out), width, height }
}
