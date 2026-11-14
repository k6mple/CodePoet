import type { DrawingIR } from '../../types/core'
import { sampleScene } from '../scene'
import type { Renderer } from '../types'
import { rgb, strokeTriangles, triangulate, type Point } from './geometry'

export const WEBGL_RENDERER_VERSION = 'codepoet-webgl-1.0.0'

const VERTEX_SHADER = `
attribute vec2 a_position;
void main() {
  gl_Position = vec4(a_position, 0.0, 1.0);
}
`

const FRAGMENT_SHADER = `
precision mediump float;
uniform vec4 u_color;
void main() {
  // Premultiplied output: the drawing buffer is premultiplied, matching the
  // WebGL default and keeping transparent-background colors correct.
  gl_FragColor = vec4(u_color.rgb * u_color.a, u_color.a);
}
`

type Viewport = { x: number; y: number; width: number; height: number }

export class WebglRenderer implements Renderer {
  readonly backend = 'webgl' as const
  readonly rendererVersion = WEBGL_RENDERER_VERSION
  private canvas: HTMLCanvasElement | null
  private gl: WebGLRenderingContext | null = null
  private program: WebGLProgram | null = null
  private buffer: WebGLBuffer | null = null
  private positionLocation = -1
  private colorLocation: WebGLUniformLocation | null = null
  private ir: DrawingIR | null = null
  private lastTime = 0
  private pending: Promise<unknown> = Promise.resolve()
  private contextLost = false
  private restoreError: string | null = null
  private readonly onLost: (event: Event) => void
  private readonly onRestored: () => void

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas
    this.onLost = (event) => {
      event.preventDefault()
      this.contextLost = true
      this.restoreError = null
      this.gl = null
      this.program = null
      this.buffer = null
      this.colorLocation = null
      this.positionLocation = -1
    }
    this.onRestored = () => {
      this.contextLost = false
      this.restoreError = null
      try {
        if (!this.canvas) return
        this.initContext()
        if (this.ir) this.render(this.lastTime)
      } catch (cause) {
        this.restoreError = cause instanceof Error ? cause.message : 'WebGL 上下文恢复失败'
      }
    }
    canvas.addEventListener('webglcontextlost', this.onLost)
    canvas.addEventListener('webglcontextrestored', this.onRestored)
  }

  private enqueue<T>(operation: () => T | Promise<T>): Promise<T> {
    const next = this.pending.then(() => {
      if (!this.canvas) throw new Error('Renderer 已释放')
      return operation()
    })
    this.pending = next.catch(() => undefined)
    return next
  }

  load(ir: DrawingIR): Promise<void> {
    return this.enqueue(() => {
      const canvas = this.canvas
      if (!canvas) throw new Error('Renderer 已释放')
      canvas.width = ir.viewport.width
      canvas.height = ir.viewport.height
      this.ir = ir
      this.restoreError = null
      this.initContext()
      this.render(0)
    })
  }

  seek(timeMs: number): Promise<void> {
    return this.enqueue(() => {
      if (!this.ir) throw new Error('请先加载 IR')
      this.render(timeMs)
    })
  }

  toPNG(): Promise<Blob> {
    return this.enqueue(async () => {
      const canvas = this.canvas
      if (!canvas || !this.ir) throw new Error('请先加载 IR')
      this.render(this.lastTime)
      const output = document.createElement('canvas')
      output.width = canvas.width
      output.height = canvas.height
      const context = output.getContext('2d')
      if (!context) throw new Error('浏览器不支持 PNG 编码')
      context.drawImage(canvas, 0, 0)
      return await new Promise<Blob>((resolve, reject) => {
        output.toBlob((blob) => {
          output.width = 0
          output.height = 0
          if (!this.canvas) reject(new Error('Renderer 已释放'))
          else if (!blob) reject(new Error('PNG 导出失败，可保留现场后重试'))
          else if (blob.size > 5 * 1024 * 1024) reject(new Error('PNG 超过 5MiB'))
          else resolve(blob)
        }, 'image/png')
      })
    })
  }

  dispose(): void {
    const canvas = this.canvas
    this.canvas = null
    this.ir = null
    this.contextLost = false
    this.restoreError = null
    if (canvas) {
      canvas.removeEventListener('webglcontextlost', this.onLost)
      canvas.removeEventListener('webglcontextrestored', this.onRestored)
    }
    if (this.gl) {
      if (this.program) this.gl.deleteProgram(this.program)
      if (this.buffer) this.gl.deleteBuffer(this.buffer)
      this.gl.getExtension('WEBGL_lose_context')?.loseContext()
    }
    this.gl = null
    this.program = null
    this.buffer = null
    this.colorLocation = null
    this.positionLocation = -1
    if (canvas) {
      canvas.width = 0
      canvas.height = 0
    }
  }

  private initContext(): void {
    const canvas = this.canvas
    if (!canvas) throw new Error('Renderer 已释放')
    if (this.gl) {
      if (this.program) this.gl.deleteProgram(this.program)
      if (this.buffer) this.gl.deleteBuffer(this.buffer)
    }
    const gl = canvas.getContext('webgl', {
      alpha: true,
      antialias: true,
      depth: false,
      stencil: false,
      premultipliedAlpha: true,
      preserveDrawingBuffer: true,
    })
    if (!gl) throw new Error('当前浏览器不支持 WebGL')

    const vertexShader = this.compile(gl, gl.VERTEX_SHADER, VERTEX_SHADER)
    const fragmentShader = this.compile(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER)
    const program = gl.createProgram()
    if (!program) throw new Error('WebGL 程序创建失败')
    gl.attachShader(program, vertexShader)
    gl.attachShader(program, fragmentShader)
    gl.linkProgram(program)
    gl.deleteShader(vertexShader)
    gl.deleteShader(fragmentShader)
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      const log = gl.getProgramInfoLog(program) ?? ''
      gl.deleteProgram(program)
      throw new Error(`WebGL 程序链接失败：${log}`)
    }
    const positionLocation = gl.getAttribLocation(program, 'a_position')
    const colorLocation = gl.getUniformLocation(program, 'u_color')
    const buffer = gl.createBuffer()
    if (positionLocation < 0 || colorLocation === null || !buffer) {
      gl.deleteProgram(program)
      throw new Error('WebGL 着色器接口不完整')
    }

    gl.useProgram(program)
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
    gl.enableVertexAttribArray(positionLocation)
    gl.vertexAttribPointer(positionLocation, 2, gl.FLOAT, false, 0, 0)
    gl.enable(gl.BLEND)
    // Source-over with premultiplied source colors; alpha stays
    // source-over as well so a transparent canvas preserves exact coverage.
    gl.blendFuncSeparate(gl.ONE, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA)
    gl.disable(gl.DEPTH_TEST)
    gl.disable(gl.CULL_FACE)

    this.gl = gl
    this.program = program
    this.buffer = buffer
    this.positionLocation = positionLocation
    this.colorLocation = colorLocation
  }

  private compile(gl: WebGLRenderingContext, type: number, source: string): WebGLShader {
    const shader = gl.createShader(type)
    if (!shader) throw new Error('WebGL 着色器创建失败')
    gl.shaderSource(shader, source)
    gl.compileShader(shader)
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      const log = gl.getShaderInfoLog(shader) ?? ''
      gl.deleteShader(shader)
      throw new Error(`WebGL 着色器编译失败：${log}`)
    }
    return shader
  }

  private render(timeMs: number): void {
    const gl = this.gl
    const canvas = this.canvas
    const ir = this.ir
    if (!gl || !canvas || !ir) throw new Error('请先加载 IR')
    if (this.contextLost || gl.isContextLost()) throw new Error('WebGL 上下文已丢失，正在恢复')
    if (this.restoreError) throw new Error(this.restoreError)

    const scene = sampleScene(ir, timeMs)
    this.lastTime = scene.timeMs
    gl.viewport(0, 0, canvas.width, canvas.height)
    const background = scene.background ? rgb(scene.background) : null
    gl.clearColor(background?.[0] ?? 0, background?.[1] ?? 0, background?.[2] ?? 0, background ? 1 : 0)
    gl.clear(gl.COLOR_BUFFER_BIT)

    for (const node of scene.nodes) {
      if (node.opacity <= 0) continue
      if (node.style.fill != null) {
        this.draw(triangulate(node.points), node.style.fill, node.opacity, scene.viewport)
      }
      const strokeWidth = node.style.strokeWidth ?? 1
      if (node.style.stroke != null && strokeWidth > 0) {
        this.draw(
          strokeTriangles(node.points, node.type !== 'polyline', strokeWidth),
          node.style.stroke,
          node.opacity,
          scene.viewport,
        )
      }
    }
  }

  private draw(triangles: Point[], color: string, opacity: number, viewport: Viewport): void {
    const gl = this.gl
    const buffer = this.buffer
    const positionLocation = this.positionLocation
    const colorLocation = this.colorLocation
    if (!gl || !buffer || positionLocation < 0 || !colorLocation || triangles.length === 0) return

    const positions = new Float32Array(triangles.length * 2)
    for (let index = 0; index < triangles.length; index += 1) {
      const point = triangles[index]
      positions[index * 2] = ((point[0] - viewport.x) / viewport.width) * 2 - 1
      positions[index * 2 + 1] = 1 - ((point[1] - viewport.y) / viewport.height) * 2
    }

    const [red, green, blue] = rgb(color)
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
    gl.bufferData(gl.ARRAY_BUFFER, positions, gl.STREAM_DRAW)
    gl.vertexAttribPointer(positionLocation, 2, gl.FLOAT, false, 0, 0)
    gl.uniform4f(colorLocation, red, green, blue, Math.max(0, Math.min(1, opacity)))
    gl.drawArrays(gl.TRIANGLES, 0, positions.length / 2)
  }
}
