import type { DrawingIR } from '../types/core'

export type BrowserBackend = 'canvas2d' | 'css' | 'webgl'

// The caller owns the clock. Await each operation; toPNG encodes only the current frame.
export interface Renderer {
  readonly backend: BrowserBackend
  readonly rendererVersion: string
  load(ir: DrawingIR): Promise<void>
  seek(timeMs: number): Promise<void>
  toPNG(): Promise<Blob>
  dispose(): void
}
