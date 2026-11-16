import { useMemo } from 'react'
import type { DrawingIR } from '@/types/core'
import { sampleScene } from '@/renderers/scene'
import { descendantIds } from '@/utils/tree'

// A separate overlay never enters the renderer or its exported PNG.
export default function QualityFocus({ ir, timeMs, nodeIds }: { ir: DrawingIR; timeMs:number; nodeIds:string[] }) {
  const boxes=useMemo(()=>{
    try {
      const selected=new Set(nodeIds.flatMap(id=>[...descendantIds(ir.nodes,id)]))
      return sampleScene(ir,timeMs).nodes.filter(node=>selected.has(node.id)).map(node=>{
        const xs=node.points.map(point=>point[0]-ir.viewport.x),ys=node.points.map(point=>point[1]-ir.viewport.y)
        return {id:node.id,x:Math.min(...xs)-3,y:Math.min(...ys)-3,width:Math.max(...xs)-Math.min(...xs)+6,height:Math.max(...ys)-Math.min(...ys)+6}
      })
    } catch { return [] }
  },[ir,timeMs,nodeIds])
  return boxes.length>0 && <svg className="quality-focus-overlay" viewBox={`0 0 ${ir.viewport.width} ${ir.viewport.height}`} role="img" aria-label="质量问题定位">
    {boxes.map(box=><rect key={box.id} data-node-id={box.id} x={box.x} y={box.y} width={box.width} height={box.height} fill="none" stroke="var(--accent, #a7322d)" strokeWidth="2" strokeDasharray="6 4" vectorEffect="non-scaling-stroke" />)}
  </svg>
}
