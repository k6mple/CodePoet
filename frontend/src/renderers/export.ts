import type { DrawingIR } from '../types/core'
import { registry } from './registry'

export async function exportBackendFrame(backend: string, ir: DrawingIR, timeMs: number): Promise<Blob> {
  const host = document.createElement(backend === 'canvas2d' ? 'canvas' : 'div')
  const renderer = registry.create(backend, host)
  try {
    await renderer.load(ir)
    await renderer.seek(timeMs)
    return await renderer.toPNG()
  } finally { renderer.dispose() }
}
