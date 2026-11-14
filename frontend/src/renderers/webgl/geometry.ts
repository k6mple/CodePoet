// Pure 2D geometry for the WebGL renderer.
//
// The scene is already in world pixels after sampleScene(). This module only
// turns fills into triangles and strokes into a non-overlapping ribbon so that
// per-node opacity is applied once per draw call (no darker joins caused by
// overlapping translucent stroke triangles).
export type Point = [number, number]

const EPSILON = 1e-9

function cross(ax: number, ay: number, bx: number, by: number, cx: number, cy: number): number {
  return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
}

function pointInTriangle(p: Point, a: Point, b: Point, c: Point): boolean {
  const d1 = cross(a[0], a[1], b[0], b[1], p[0], p[1])
  const d2 = cross(b[0], b[1], c[0], c[1], p[0], p[1])
  const d3 = cross(c[0], c[1], a[0], a[1], p[0], p[1])
  const hasNegative = d1 < -EPSILON || d2 < -EPSILON || d3 < -EPSILON
  const hasPositive = d1 > EPSILON || d2 > EPSILON || d3 > EPSILON
  return !(hasNegative && hasPositive)
}

function signedArea(points: Point[]): number {
  let area = 0
  for (let index = 0; index < points.length; index += 1) {
    const current = points[index]
    const next = points[(index + 1) % points.length]
    area += current[0] * next[1] - next[0] * current[1]
  }
  return area / 2
}

function distance(a: Point, b: Point): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1])
}

function add(a: Point, b: Point): Point {
  return [a[0] + b[0], a[1] + b[1]]
}

function scale(point: Point, factor: number): Point {
  return [point[0] * factor, point[1] * factor]
}

function normalize(point: Point): Point {
  const length = Math.hypot(point[0], point[1])
  return length <= EPSILON ? [0, 0] : [point[0] / length, point[1] / length]
}

function normal(direction: Point): Point {
  return [-direction[1], direction[0]]
}

function cleanPoints(points: Point[], closed: boolean): Point[] {
  const cleaned: Point[] = []
  for (const point of points) {
    const last = cleaned[cleaned.length - 1]
    if (!last || distance(last, point) > EPSILON) cleaned.push([point[0], point[1]])
  }
  if (closed && cleaned.length > 1 && distance(cleaned[0], cleaned[cleaned.length - 1]) <= EPSILON) {
    cleaned.pop()
  }
  return cleaned
}

// Collinear vertices are harmless for a stroke but can make ear clipping stall.
function removeCollinear(points: Point[]): Point[] {
  if (points.length <= 3) return points
  const result: Point[] = []
  for (let index = 0; index < points.length; index += 1) {
    const previous = points[(index - 1 + points.length) % points.length]
    const current = points[index]
    const next = points[(index + 1) % points.length]
    if (Math.abs(cross(previous[0], previous[1], current[0], current[1], next[0], next[1])) > EPSILON) {
      result.push(current)
    }
  }
  return result.length >= 3 ? result : points
}

// Ear clipping with a fan fallback for pathological/self-intersecting input.
// The renderer only promises simple polygons; the fallback keeps the frame
// drawable instead of silently dropping an entire node.
export function triangulate(points: Point[]): Point[] {
  const cleaned = cleanPoints(points, true)
  if (cleaned.length < 3) return []
  const area = signedArea(cleaned)
  if (Math.abs(area) <= EPSILON) return []
  const polygon = removeCollinear(area < 0 ? [...cleaned].reverse() : cleaned)
  if (polygon.length < 3) return []

  const indices = polygon.map((_, index) => index)
  const triangles: Point[] = []
  while (indices.length > 3) {
    let ear = -1
    for (let index = 0; index < indices.length; index += 1) {
      const previous = indices[(index - 1 + indices.length) % indices.length]
      const current = indices[index]
      const next = indices[(index + 1) % indices.length]
      const a = polygon[previous]
      const b = polygon[current]
      const c = polygon[next]
      if (cross(a[0], a[1], b[0], b[1], c[0], c[1]) <= EPSILON) continue
      let contains = false
      for (const candidate of indices) {
        if (candidate === previous || candidate === current || candidate === next) continue
        if (pointInTriangle(polygon[candidate], a, b, c)) {
          contains = true
          break
        }
      }
      if (!contains) {
        ear = index
        break
      }
    }
    if (ear < 0) break
    const previous = indices[(ear - 1 + indices.length) % indices.length]
    const current = indices[ear]
    const next = indices[(ear + 1) % indices.length]
    triangles.push(polygon[previous], polygon[current], polygon[next])
    indices.splice(ear, 1)
  }

  if (indices.length === 3) {
    triangles.push(polygon[indices[0]], polygon[indices[1]], polygon[indices[2]])
  } else if (indices.length > 3) {
    // A real ear-clipping failure: keep the shape visible with a fan.
    for (let index = 1; index < indices.length - 1; index += 1) {
      triangles.push(polygon[indices[0]], polygon[indices[index]], polygon[indices[index + 1]])
    }
  }
  return triangles
}

// A closed stroke ribbon with butt caps and miter joins (bevel when the miter
// limit is exceeded). Rings are emitted in path order; quads between adjacent
// rings share edges, so translucent strokes are not blended twice at joins.
export function strokeTriangles(points: Point[], closed: boolean, width: number, miterLimit = 10): Point[] {
  const cleaned = cleanPoints(points, closed)
  const count = cleaned.length
  if (count < 2 || width <= 0) return []
  const half = width / 2
  const rings: [Point, Point][] = []

  const pushRing = (center: Point, normalVector: Point) => {
    rings.push([
      add(center, scale(normalVector, half)),
      add(center, scale(normalVector, -half)),
    ])
  }

  for (let index = 0; index < count; index += 1) {
    const isOpenStart = !closed && index === 0
    const isOpenEnd = !closed && index === count - 1
    const center = cleaned[index]
    const previous = cleaned[(index - 1 + count) % count]
    const next = cleaned[(index + 1) % count]
    const previousDirection = isOpenStart ? null : normalize([center[0] - previous[0], center[1] - previous[1]])
    const nextDirection = isOpenEnd ? null : normalize([next[0] - center[0], next[1] - center[1]])

    if (!previousDirection && nextDirection) {
      pushRing(center, normal(nextDirection))
      continue
    }
    if (previousDirection && !nextDirection) {
      pushRing(center, normal(previousDirection))
      continue
    }
    if (!previousDirection || !nextDirection) {
      // A two-point path with identical directions or a degenerate segment.
      pushRing(center, normal(nextDirection ?? previousDirection ?? [1, 0]))
      continue
    }

    const previousNormal = normal(previousDirection)
    const nextNormal = normal(nextDirection)
    const miterCandidate: Point = [previousNormal[0] + nextNormal[0], previousNormal[1] + nextNormal[1]]
    const miterLength = Math.hypot(miterCandidate[0], miterCandidate[1])
    if (miterLength <= EPSILON) {
      // 180-degree reversal: bevel both sides.
      pushRing(center, previousNormal)
      pushRing(center, nextNormal)
      continue
    }
    const miter = normalize(miterCandidate)
    const denominator = miter[0] * previousNormal[0] + miter[1] * previousNormal[1]
    // Canvas miter limit is miterLength / lineWidth; here denominator is
    // sin(half angle), so 1 / denominator is that ratio.
    if (denominator <= EPSILON || 1 / denominator > miterLimit) {
      pushRing(center, previousNormal)
      pushRing(center, nextNormal)
    } else {
      const offset = scale(miter, half / denominator)
      rings.push([add(center, offset), add(center, scale(offset, -1))])
    }
  }

  const triangles: Point[] = []
  const segmentCount = closed ? rings.length : rings.length - 1
  for (let index = 0; index < segmentCount; index += 1) {
    const left = rings[index]
    const right = rings[(index + 1) % rings.length]
    triangles.push(left[0], right[0], right[1], left[0], right[1], left[1])
  }
  return triangles
}

export function rgb(hex: string): [number, number, number] {
  const raw = hex.startsWith('#') ? hex.slice(1) : hex
  if (!/^[0-9a-fA-F]{6}$/.test(raw)) return [0, 0, 0]
  return [
    Number.parseInt(raw.slice(0, 2), 16) / 255,
    Number.parseInt(raw.slice(2, 4), 16) / 255,
    Number.parseInt(raw.slice(4, 6), 16) / 255,
  ]
}
