import type { GradientFill, PaperStyle, TextGeometry } from '../types/core'
import type { SampledNode } from './scene'

export interface ResolvedStop {
  offset: number
  r: number
  g: number
  b: number
  opacity: number
  cssColor: string
  hex: string
}

export interface NodeBounds {
  minX: number
  minY: number
  maxX: number
  maxY: number
  width: number
  height: number
}

// 确定性伪随机数生成器（保证每次复现宣纸肌理与水墨颗粒完全一致）
export function createPrng(seed: number): () => number {
  let a = (seed >>> 0) || 1
  return () => {
    a |= 0
    a = (a + 0x6D2B79F5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export function hashString(input: string): number {
  let h = 2166136261
  for (let i = 0; i < input.length; i += 1) {
    h ^= input.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

export function parseHex(hex: string): [number, number, number] {
  const clean = hex.replace('#', '')
  const value = Number.parseInt(clean, 16)
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255]
}

export function toHex(r: number, g: number, b: number): string {
  const c = (v: number) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')
  return `#${c(r)}${c(g)}${c(b)}`
}

export function mixRgb(a: [number, number, number], b: [number, number, number], t: number): [number, number, number] {
  return [
    a[0] + (b[0] - a[0]) * t,
    a[1] + (b[1] - a[1]) * t,
    a[2] + (b[2] - a[2]) * t,
  ]
}

// 解析渐变色标：若开启 tintFromFill 且节点存在 style.fill，则围绕主色自动派生
// 亮部高光 -> 主色 -> 深部晕染的工笔多色标渐变，确保前端调色滑块/取色器实时生效
export function resolveGradientStops(gradient: GradientFill, fill?: string | null): ResolvedStop[] {
  const stops = gradient.stops
  const tint = Boolean(gradient.tintFromFill && fill)
  const baseRgb = tint && fill ? parseHex(fill) : null
  const warmLight: [number, number, number] = [252, 247, 238]
  const deepInk: [number, number, number] = [38, 28, 30]

  return stops.map((stop, idx) => {
    let rgb: [number, number, number]
    if (baseRgb) {
      const frac = stops.length <= 1 ? 0.5 : idx / (stops.length - 1)
      if (frac < 0.45) {
        // 亮部向暖宣白过渡（工笔分染高光）
        const k = (0.45 - frac) / 0.45
        rgb = mixRgb(baseRgb, warmLight, 0.44 * k)
      } else if (frac > 0.55) {
        // 暗部向沉稳墨韵收深（工笔分染积色）
        const k = (frac - 0.55) / 0.45
        rgb = mixRgb(baseRgb, deepInk, 0.30 * k)
      } else {
        rgb = baseRgb
      }
    } else {
      rgb = parseHex(stop.color)
    }
    const opacity = stop.opacity ?? 1
    const r = Math.round(rgb[0])
    const g = Math.round(rgb[1])
    const b = Math.round(rgb[2])
    return {
      offset: stop.offset,
      r,
      g,
      b,
      opacity,
      hex: toHex(r, g, b),
      cssColor: opacity < 1 ? `rgba(${r},${g},${b},${opacity})` : `rgb(${r},${g},${b})`,
    }
  })
}

export function nodeBounds(points: [number, number][], vx: number, vy: number): NodeBounds {
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const [x, y] of points) {
    const px = x - vx
    const py = y - vy
    if (px < minX) minX = px
    if (py < minY) minY = py
    if (px > maxX) maxX = px
    if (py > maxY) maxY = py
  }
  const width = Math.max(1, maxX - minX)
  const height = Math.max(1, maxY - minY)
  return { minX, minY, maxX, maxY, width, height }
}

export function linearGradientEndpoints(bounds: NodeBounds, angleDeg = 90): [number, number, number, number] {
  const rad = (angleDeg * Math.PI) / 180
  const dx = Math.cos(rad)
  const dy = Math.sin(rad)
  const cx = bounds.minX + bounds.width / 2
  const cy = bounds.minY + bounds.height / 2
  const halfX = (bounds.width / 2) * Math.abs(dx) + (bounds.height / 2) * Math.abs(dy)
  return [
    cx - dx * halfX,
    cy - dy * halfX,
    cx + dx * halfX,
    cy + dy * halfX,
  ]
}

export function calligraphyFont(family: TextGeometry['fontFamily'] = 'kai', size = 20): string {
  if (family === 'seal') {
    return `700 ${size}px SimSun, STSong, "Songti SC", "Noto Serif SC", serif`
  }
  if (family === 'song') {
    return `500 ${size}px "Songti SC", SimSun, STSong, "Noto Serif SC", Georgia, serif`
  }
  return `400 ${size}px KaiTi, STKaiti, "Kaiti SC", FangSong, "Noto Serif SC", Georgia, serif`
}

// 在 2D Canvas 上绘制确定性宣纸 / 宋绢 / 古宣底纹（细微温和，不喧宾夺主）
export function paintPaperBackground(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  paper: PaperStyle | null | undefined,
  background: string | null,
  seed: number,
): void {
  if (!paper || paper === 'none') return
  const rand = createPrng((seed ^ 0x5A17C3) >>> 0)
  const bg = background ? parseHex(background) : [245, 240, 230]

  ctx.save()
  // 1. 柔和暖暗角与宣纸自然光韵（四角略沉、中心温润）
  const vignette = ctx.createRadialGradient(
    width * 0.48,
    height * 0.44,
    Math.min(width, height) * 0.18,
    width * 0.5,
    height * 0.5,
    Math.hypot(width, height) * 0.58,
  )
  const edgeStrength = paper === 'aged' ? 0.11 : paper === 'silk' ? 0.08 : 0.075
  const centerGlow = paper === 'aged' ? 0.05 : 0.04
  vignette.addColorStop(0, `rgba(255,252,244,${centerGlow})`)
  vignette.addColorStop(0.68, 'rgba(210,196,170,0)')
  vignette.addColorStop(1, `rgba(145,126,96,${edgeStrength})`)
  ctx.fillStyle = vignette
  ctx.fillRect(0, 0, width, height)

  // 2. 宋绢经纬细丝或生宣短纤维纹理
  if (paper === 'silk') {
    ctx.strokeStyle = `rgba(${Math.max(0, bg[0] - 28)},${Math.max(0, bg[1] - 28)},${Math.max(0, bg[2] - 32)},0.055)`
    ctx.lineWidth = 0.7
    for (let y = 2; y < height; y += 5) {
      ctx.beginPath()
      ctx.moveTo(0, y)
      ctx.lineTo(width, y)
      ctx.stroke()
    }
    ctx.strokeStyle = `rgba(${Math.max(0, bg[0] - 22)},${Math.max(0, bg[1] - 24)},${Math.max(0, bg[2] - 28)},0.04)`
    for (let x = 2; x < width; x += 6) {
      ctx.beginPath()
      ctx.moveTo(x, 0)
      ctx.lineTo(x, height)
      ctx.stroke()
    }
  } else {
    // 宣纸 / 古宣：随机确定性短纸纤维丝 + 极淡云斑
    const patchCount = paper === 'aged' ? 14 : 9
    for (let i = 0; i < patchCount; i += 1) {
      const px = rand() * width
      const py = rand() * height
      const pr = 70 + rand() * 140
      const g = ctx.createRadialGradient(px, py, 2, px, py, pr)
      const alpha = (paper === 'aged' ? 0.048 : 0.03) * (0.6 + rand() * 0.6)
      g.addColorStop(0, `rgba(176,158,128,${alpha})`)
      g.addColorStop(1, 'rgba(176,158,128,0)')
      ctx.fillStyle = g
      ctx.fillRect(px - pr, py - pr, pr * 2, pr * 2)
    }

    const fiberCount = paper === 'aged' ? 240 : 180
    ctx.lineWidth = 0.65
    for (let i = 0; i < fiberCount; i += 1) {
      const x = rand() * width
      const y = rand() * height
      const len = 5 + rand() * 14
      const ang = rand() * Math.PI * 2
      const dark = rand() > 0.4
      ctx.strokeStyle = dark
        ? `rgba(${Math.max(0, bg[0] - 42)},${Math.max(0, bg[1] - 44)},${Math.max(0, bg[2] - 48)},${0.04 + rand() * 0.045})`
        : `rgba(255,253,247,${0.05 + rand() * 0.05})`
      ctx.beginPath()
      ctx.moveTo(x, y)
      ctx.lineTo(x + Math.cos(ang) * len, y + Math.sin(ang) * len)
      ctx.stroke()
    }
  }
  ctx.restore()
}

// 在已 clip 到当前路径的 Canvas 2D 上绘制确定性水墨颗粒 / 工笔丝绢微光
export function paintMaterialTexture(
  ctx: CanvasRenderingContext2D,
  node: SampledNode,
  bounds: NodeBounds,
  seed: number,
): void {
  if (node.shaderRef === 'flat') return
  const rand = createPrng((seed ^ hashString(node.id)) >>> 0)
  ctx.save()
  if (node.shaderRef === 'ink_wash') {
    const area = bounds.width * bounds.height
    const blotches = Math.min(28, Math.max(4, Math.floor(area / 1200)))
    for (let i = 0; i < blotches; i += 1) {
      const bx = bounds.minX + rand() * bounds.width
      const by = bounds.minY + rand() * bounds.height
      const br = 8 + rand() * Math.min(bounds.width, bounds.height) * 0.28
      const g = ctx.createRadialGradient(bx, by, 1, bx, by, br)
      const light = rand() > 0.45
      g.addColorStop(0, light ? 'rgba(252,248,240,0.11)' : 'rgba(28,24,22,0.085)')
      g.addColorStop(1, 'rgba(0,0,0,0)')
      ctx.fillStyle = g
      ctx.fillRect(bx - br, by - br, br * 2, br * 2)
    }
  } else if (node.shaderRef === 'gongbi') {
    // 工笔分染：左上微暖柔光，凸显花瓣/荷叶丝绒立体感
    const sheen = ctx.createLinearGradient(bounds.minX, bounds.minY, bounds.maxX, bounds.maxY)
    sheen.addColorStop(0, 'rgba(255,252,246,0.16)')
    sheen.addColorStop(0.55, 'rgba(255,252,246,0)')
    sheen.addColorStop(1, 'rgba(42,26,28,0.10)')
    ctx.fillStyle = sheen
    ctx.fillRect(bounds.minX, bounds.minY, bounds.width, bounds.height)
  }
  ctx.restore()
}

// 在 Canvas 2D 上绘制书法题跋与篆印文字节点
export function drawTextNode2D(
  ctx: CanvasRenderingContext2D,
  node: SampledNode,
  vx: number,
  vy: number,
): void {
  const geom = node.text
  if (!geom) return
  const fontSize = geom.fontSize ?? 20
  const letterSpacing = geom.letterSpacing ?? 4
  const writingMode = geom.writingMode ?? 'vertical'
  const chars = Array.from(geom.text)
  const [a, b, c, d, e, f] = node.matrix

  ctx.save()
  ctx.transform(a, b, c, d, e - vx, f - vy)
  ctx.globalAlpha = node.opacity
  ctx.font = calligraphyFont(geom.fontFamily, fontSize)
  ctx.textBaseline = 'top'
  ctx.textAlign = writingMode === 'vertical' ? 'center' : 'left'
  const fill = node.style.fill
  const stroke = node.style.stroke
  const strokeWidth = node.style.strokeWidth ?? 0

  chars.forEach((ch, idx) => {
    const cx = writingMode === 'vertical' ? (geom.x ?? 0) : (geom.x ?? 0) + idx * (fontSize + letterSpacing)
    const cy = writingMode === 'vertical' ? (geom.y ?? 0) + idx * (fontSize + letterSpacing) : (geom.y ?? 0)
    if (stroke && strokeWidth > 0) {
      ctx.strokeStyle = stroke
      ctx.lineWidth = strokeWidth
      ctx.lineJoin = 'round'
      ctx.strokeText(ch, cx, cy)
    }
    if (fill) {
      ctx.fillStyle = fill
      ctx.fillText(ch, cx, cy)
    }
  })
  ctx.restore()
}
