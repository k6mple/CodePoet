import type { DrawingIR } from '../../types/core'
import { registry } from '../registry'

// Compatibility entry points for current previews, uploads and fixture tools.
export { renderScene, RENDERER_VERSION } from './drawScene'
export { sampleTimes } from '../scene'

export async function exportFrame(ir: DrawingIR, timeMs: number): Promise<Blob> {
  // A private instance keeps export seeks away from the preview's clock and canvas.
  const renderer = registry.create('canvas2d', document.createElement('canvas'))
  try {
    await renderer.load(ir)
    await renderer.seek(timeMs)
    return await renderer.toPNG()
  } finally { renderer.dispose() }
}
