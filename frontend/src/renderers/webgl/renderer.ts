import type { DrawingIR, PaperStyle } from '../../types/core'
import {
  drawTextNode2D,
  hashString,
  linearGradientEndpoints,
  nodeBounds,
  resolveGradientStops,
} from '../artStyle'
import { sampleScene, type SampledNode } from '../scene'
import type { Renderer } from '../types'
import { polylineSelfIntersects, rgb, strokeTriangles, triangulate, type Point } from './geometry'

export const WEBGL_RENDERER_VERSION = 'codepoet-webgl-1.0.0'

// 主图形着色器：支持纯色(flat)、GPU 线性/径向多色标渐变、GPU 水墨 FBM 颗粒(ink_wash) 与工笔微光(gongbi)
const VERTEX_SHADER = `
attribute vec2 a_position;
attribute vec2 a_pixel;
varying vec2 v_pixel;
void main() {
  v_pixel = a_pixel;
  gl_Position = vec4(a_position, 0.0, 1.0);
}
`

const FRAGMENT_SHADER = `
precision mediump float;
varying vec2 v_pixel;
uniform vec4 u_color;
uniform int u_fillMode;        // 0=solid, 1=linear, 2=radial
uniform int u_shaderType;      // 0=flat, 1=ink_wash, 2=gongbi
uniform vec4 u_gradParams;     // linear:(x0,y0,x1,y1)  radial:(cx,cy,r,0)
uniform vec4 u_bounds;         // (minX, minY, width, height)
uniform float u_nodeSeed;
uniform int u_stopCount;
uniform vec4 u_stopColors[6];
uniform float u_stopOffsets[6];

float hash21(vec2 p) {
  p = fract(p * vec2(123.34, 456.21) + u_nodeSeed * 0.013);
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

float vnoise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  float a = hash21(i);
  float b = hash21(i + vec2(1.0, 0.0));
  float c = hash21(i + vec2(0.0, 1.0));
  float d = hash21(i + vec2(1.0, 1.0));
  return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}

vec4 evalStops(float t) {
  t = clamp(t, 0.0, 1.0);
  vec4 col = u_stopColors[0];
  for (int i = 1; i < 6; i++) {
    if (i >= u_stopCount) break;
    float o0 = u_stopOffsets[i - 1];
    float o1 = u_stopOffsets[i];
    if (t >= o0 && t <= o1) {
      float frac = (o1 - o0) > 1e-5 ? (t - o0) / (o1 - o0) : 1.0;
      return mix(u_stopColors[i - 1], u_stopColors[i], frac);
    }
    if (t > o1) col = u_stopColors[i];
  }
  return col;
}

void main() {
  vec4 baseColor = u_color;
  if (u_fillMode == 1) {
    vec2 p0 = u_gradParams.xy;
    vec2 d = u_gradParams.zw - p0;
    float denom = max(dot(d, d), 1e-4);
    float t = dot(v_pixel - p0, d) / denom;
    vec4 g = evalStops(t);
    baseColor = vec4(g.rgb, g.a * u_color.a);
  } else if (u_fillMode == 2) {
    float dist = length(v_pixel - u_gradParams.xy);
    float t = dist / max(u_gradParams.z, 1.0);
    vec4 g = evalStops(t);
    baseColor = vec4(g.rgb, g.a * u_color.a);
  }

  if (u_shaderType == 1) {
    // GPU 过程式水墨 FBM 渗化颗粒
    float n = vnoise(v_pixel * 0.045) * 0.65 + vnoise(v_pixel * 0.13) * 0.35;
    float grain = (n - 0.5) * 0.14;
    baseColor.rgb = clamp(baseColor.rgb + vec3(grain), 0.0, 1.0);
  } else if (u_shaderType == 2) {
    // GPU 工笔丝绢左上柔和受光
    vec2 uv = clamp((v_pixel - u_bounds.xy) / max(u_bounds.zw, vec2(1.0)), 0.0, 1.0);
    float sheen = (1.0 - (uv.x * 0.5 + uv.y * 0.5)) * 0.11 - 0.04;
    baseColor.rgb = clamp(baseColor.rgb + vec3(sheen), 0.0, 1.0);
  }

  // Premultiplied output matching WebGL drawing buffer.
  gl_FragColor = vec4(baseColor.rgb * baseColor.a, baseColor.a);
}
`

// GPU 宣纸 / 宋绢 / 古宣底纹着色器（确定性种子，零外部纹理依赖）
const PAPER_VERTEX_SHADER = `
attribute vec2 a_pos;
varying vec2 v_uv;
void main() {
  v_uv = a_pos * 0.5 + 0.5;
  gl_Position = vec4(a_pos, 0.0, 1.0);
}
`

const PAPER_FRAGMENT_SHADER = `
precision mediump float;
varying vec2 v_uv;
uniform vec3 u_bg;
uniform vec2 u_res;
uniform int u_paperType;   // 1=xuan, 2=silk, 3=aged
uniform float u_seed;

float hash(vec2 p) {
  p = fract(p * vec2(127.1, 311.7) + u_seed * 0.017);
  p += dot(p, p + 34.5);
  return fract(p.x * p.y);
}

float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x),
    mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x),
    f.y
  );
}

void main() {
  vec2 px = vec2(v_uv.x, 1.0 - v_uv.y) * u_res;
  vec3 col = u_bg;

  // 柔和宣纸暖暗角
  float dist = length((v_uv - vec2(0.48, 0.56)) * vec2(1.15, 1.0));
  float vig = smoothstep(0.22, 0.88, dist);
  float vigStrength = u_paperType == 3 ? 0.068 : (u_paperType == 2 ? 0.052 : 0.048);
  col = mix(col + vec3(0.012, 0.010, 0.006), col * vec3(0.92, 0.90, 0.86), vig * vigStrength * 1.4);

  if (u_paperType == 2) {
    // 宋绢细经纬纹
    float weft = sin(px.y * 1.25) * 0.5 + 0.5;
    float warp = sin(px.x * 1.05) * 0.5 + 0.5;
    col -= vec3((weft * 0.55 + warp * 0.45) * 0.018);
  } else {
    // 生宣/古宣纤维与微云斑
    float cloud = noise(px * 0.012);
    float fiber = noise(vec2(px.x * 0.28, px.y * 0.06)) * 0.5 + noise(vec2(px.x * 0.06, px.y * 0.28)) * 0.5;
    float scale = u_paperType == 3 ? 0.042 : 0.028;
    col += vec3((cloud - 0.5) * scale + (fiber - 0.5) * (scale * 0.9));
  }

  col = clamp(col, 0.0, 1.0);
  gl_FragColor = vec4(col, 1.0);
}
`

// 纹理合成着色器（用于把书法文字层与柔化光晕层精准叠合进 WebGL 帧缓冲）
const TEX_VERTEX_SHADER = `
attribute vec2 a_pos;
varying vec2 v_uv;
void main() {
  v_uv = vec2(a_pos.x * 0.5 + 0.5, 0.5 - a_pos.y * 0.5);
  gl_Position = vec4(a_pos, 0.0, 1.0);
}
`

const TEX_FRAGMENT_SHADER = `
precision mediump float;
varying vec2 v_uv;
uniform sampler2D u_tex;
void main() {
  vec4 c = texture2D(u_tex, v_uv);
  gl_FragColor = c;
}
`

type Viewport = { x: number; y: number; width: number; height: number }

export class WebglRenderer implements Renderer {
  readonly backend = 'webgl' as const
  readonly rendererVersion = WEBGL_RENDERER_VERSION
  private canvas: HTMLCanvasElement | null
  private gl: WebGLRenderingContext | null = null
  private program: WebGLProgram | null = null
  private paperProgram: WebGLProgram | null = null
  private texProgram: WebGLProgram | null = null
  private buffer: WebGLBuffer | null = null
  private quadBuffer: WebGLBuffer | null = null
  private overlayTexture: WebGLTexture | null = null
  private positionLocation = -1
  private pixelLocation = -1
  private colorLocation: WebGLUniformLocation | null = null
  private fillModeLocation: WebGLUniformLocation | null = null
  private shaderTypeLocation: WebGLUniformLocation | null = null
  private gradParamsLocation: WebGLUniformLocation | null = null
  private boundsLocation: WebGLUniformLocation | null = null
  private nodeSeedLocation: WebGLUniformLocation | null = null
  private stopCountLocation: WebGLUniformLocation | null = null
  private stopColorsLocation: WebGLUniformLocation | null = null
  private stopOffsetsLocation: WebGLUniformLocation | null = null
  private stencilAvailable = false
  private selfIntersectCache = new Map<string, boolean>()
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
      this.paperProgram = null
      this.texProgram = null
      this.buffer = null
      this.quadBuffer = null
      this.overlayTexture = null
      this.colorLocation = null
      this.positionLocation = -1
      this.pixelLocation = -1
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
      this.restoreError = null
      this.selfIntersectCache.clear()
      this.initContext()
      this.ir = ir
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

  dispose(releaseContext = true): void {
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
      if (this.paperProgram) this.gl.deleteProgram(this.paperProgram)
      if (this.texProgram) this.gl.deleteProgram(this.texProgram)
      if (this.buffer) this.gl.deleteBuffer(this.buffer)
      if (this.quadBuffer) this.gl.deleteBuffer(this.quadBuffer)
      if (this.overlayTexture) this.gl.deleteTexture(this.overlayTexture)
      if (releaseContext) this.gl.getExtension('WEBGL_lose_context')?.loseContext()
    }
    this.gl = null
    this.program = null
    this.paperProgram = null
    this.texProgram = null
    this.buffer = null
    this.quadBuffer = null
    this.overlayTexture = null
    this.colorLocation = null
    this.positionLocation = -1
    this.pixelLocation = -1
    if (canvas) {
      canvas.width = 0
      canvas.height = 0
    }
  }

  private linkProgram(gl: WebGLRenderingContext, vsSource: string, fsSource: string): WebGLProgram {
    const vertexShader = this.compile(gl, gl.VERTEX_SHADER, vsSource)
    const fragmentShader = this.compile(gl, gl.FRAGMENT_SHADER, fsSource)
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
    return program
  }

  private initContext(): void {
    const canvas = this.canvas
    if (!canvas) throw new Error('Renderer 已释放')
    if (this.gl) {
      if (this.program) this.gl.deleteProgram(this.program)
      if (this.paperProgram) this.gl.deleteProgram(this.paperProgram)
      if (this.texProgram) this.gl.deleteProgram(this.texProgram)
      if (this.buffer) this.gl.deleteBuffer(this.buffer)
      if (this.quadBuffer) this.gl.deleteBuffer(this.quadBuffer)
      if (this.overlayTexture) this.gl.deleteTexture(this.overlayTexture)
    }
    const gl = canvas.getContext('webgl', {
      alpha: true,
      antialias: true,
      depth: false,
      stencil: true,
      premultipliedAlpha: true,
      preserveDrawingBuffer: true,
    })
    if (!gl) throw new Error('当前浏览器不支持 WebGL')

    const program = this.linkProgram(gl, VERTEX_SHADER, FRAGMENT_SHADER)
    const paperProgram = this.linkProgram(gl, PAPER_VERTEX_SHADER, PAPER_FRAGMENT_SHADER)
    const texProgram = this.linkProgram(gl, TEX_VERTEX_SHADER, TEX_FRAGMENT_SHADER)

    const positionLocation = gl.getAttribLocation(program, 'a_position')
    const pixelLocation = gl.getAttribLocation(program, 'a_pixel')
    const colorLocation = gl.getUniformLocation(program, 'u_color')
    const buffer = gl.createBuffer()
    const quadBuffer = gl.createBuffer()
    const overlayTexture = gl.createTexture()
    if (positionLocation < 0 || colorLocation === null || !buffer || !quadBuffer || !overlayTexture) {
      gl.deleteProgram(program)
      throw new Error('WebGL 着色器接口不完整')
    }

    gl.bindBuffer(gl.ARRAY_BUFFER, quadBuffer)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, 1, 1, -1, -1, 1, 1, -1, 1]), gl.STATIC_DRAW)

    gl.bindTexture(gl.TEXTURE_2D, overlayTexture)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)

    gl.enable(gl.BLEND)
    gl.blendFuncSeparate(gl.ONE, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA)
    gl.disable(gl.DEPTH_TEST)
    gl.disable(gl.CULL_FACE)
    gl.disable(gl.STENCIL_TEST)
    gl.clearStencil(0)
    gl.stencilMask(0xFF)
    this.stencilAvailable = gl.getParameter(gl.STENCIL_BITS) > 0

    this.gl = gl
    this.program = program
    this.paperProgram = paperProgram
    this.texProgram = texProgram
    this.buffer = buffer
    this.quadBuffer = quadBuffer
    this.overlayTexture = overlayTexture
    this.positionLocation = positionLocation
    this.pixelLocation = pixelLocation
    this.colorLocation = colorLocation
    this.fillModeLocation = gl.getUniformLocation(program, 'u_fillMode')
    this.shaderTypeLocation = gl.getUniformLocation(program, 'u_shaderType')
    this.gradParamsLocation = gl.getUniformLocation(program, 'u_gradParams')
    this.boundsLocation = gl.getUniformLocation(program, 'u_bounds')
    this.nodeSeedLocation = gl.getUniformLocation(program, 'u_nodeSeed')
    this.stopCountLocation = gl.getUniformLocation(program, 'u_stopCount')
    this.stopColorsLocation = gl.getUniformLocation(program, 'u_stopColors[0]')
    this.stopOffsetsLocation = gl.getUniformLocation(program, 'u_stopOffsets[0]')
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

  private drawPaperPass(bgColor: [number, number, number], paper: PaperStyle, viewport: Viewport, seed: number): void {
    const gl = this.gl
    const prog = this.paperProgram
    const quad = this.quadBuffer
    if (!gl || !prog || !quad) return
    const typeCode = paper === 'aged' ? 3 : paper === 'silk' ? 2 : 1
    gl.useProgram(prog)
    gl.bindBuffer(gl.ARRAY_BUFFER, quad)
    const loc = gl.getAttribLocation(prog, 'a_pos')
    gl.enableVertexAttribArray(loc)
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0)
    gl.uniform3f(gl.getUniformLocation(prog, 'u_bg'), bgColor[0], bgColor[1], bgColor[2])
    gl.uniform2f(gl.getUniformLocation(prog, 'u_res'), viewport.width, viewport.height)
    gl.uniform1i(gl.getUniformLocation(prog, 'u_paperType'), typeCode)
    gl.uniform1f(gl.getUniformLocation(prog, 'u_seed'), (seed % 9973) + 1)
    gl.drawArrays(gl.TRIANGLES, 0, 6)
    gl.disableVertexAttribArray(loc)
  }

  private drawTextOverlay(textNodes: SampledNode[], viewport: Viewport): void {
    const gl = this.gl
    const prog = this.texProgram
    const quad = this.quadBuffer
    const tex = this.overlayTexture
    if (!gl || !prog || !quad || !tex || textNodes.length === 0) return

    const off = document.createElement('canvas')
    off.width = viewport.width
    off.height = viewport.height
    const ctx = off.getContext('2d')
    if (!ctx) return
    for (const node of textNodes) {
      drawTextNode2D(ctx, node, viewport.x, viewport.y)
    }

    gl.useProgram(prog)
    gl.activeTexture(gl.TEXTURE0)
    gl.bindTexture(gl.TEXTURE_2D, tex)
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, 1)
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, off)
    gl.uniform1i(gl.getUniformLocation(prog, 'u_tex'), 0)
    gl.bindBuffer(gl.ARRAY_BUFFER, quad)
    const loc = gl.getAttribLocation(prog, 'a_pos')
    gl.enableVertexAttribArray(loc)
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0)
    gl.drawArrays(gl.TRIANGLES, 0, 6)
    gl.disableVertexAttribArray(loc)
    off.width = 0
    off.height = 0
  }

  private render(timeMs: number): void {
    if (this.contextLost) throw new Error('WebGL 上下文已丢失，正在恢复')
    if (this.restoreError) throw new Error(this.restoreError)
    const gl = this.gl
    const canvas = this.canvas
    const ir = this.ir
    if (!gl || !canvas || !ir) throw new Error('请先加载 IR')
    if (gl.isContextLost()) throw new Error('WebGL 上下文已丢失，正在恢复')

    const scene = sampleScene(ir, timeMs)
    this.lastTime = scene.timeMs
    gl.viewport(0, 0, canvas.width, canvas.height)
    const background = scene.background ? rgb(scene.background) : null
    gl.clearColor(background?.[0] ?? 0, background?.[1] ?? 0, background?.[2] ?? 0, background ? 1 : 0)
    gl.clear(gl.COLOR_BUFFER_BIT)

    if (scene.paper && scene.paper !== 'none' && background) {
      this.drawPaperPass(background, scene.paper, scene.viewport, scene.seed)
    }

    const pendingTextNodes: SampledNode[] = []

    for (const node of scene.nodes) {
      if (node.opacity <= 0) continue
      if (node.type === 'text') {
        pendingTextNodes.push(node)
        continue
      }
      if (pendingTextNodes.length > 0) {
        this.drawTextOverlay(pendingTextNodes.splice(0), scene.viewport)
      }

      if (node.style.gradient || node.style.fill != null) {
        if (node.type === 'polyline') {
          let selfIntersecting = this.selfIntersectCache.get(node.id)
          if (selfIntersecting === undefined) {
            selfIntersecting = polylineSelfIntersects(node.points)
            this.selfIntersectCache.set(node.id, selfIntersecting)
          }
          if (selfIntersecting) throw new Error('WebGL 渲染器暂不支持自交折线填充')
        }
        this.drawFilledNode(triangulate(node.points), node, scene.viewport, scene.seed)
      }
      const strokeWidth = node.style.strokeWidth ?? 1
      if (node.style.stroke != null && strokeWidth > 0) {
        this.drawStroke(
          strokeTriangles(node.points, node.type !== 'polyline', strokeWidth),
          node.style.stroke,
          node.opacity,
          scene.viewport,
        )
      }
    }

    if (pendingTextNodes.length > 0) {
      this.drawTextOverlay(pendingTextNodes, scene.viewport)
    }
  }

  private bindShapeProgram(): void {
    const gl = this.gl
    const program = this.program
    const buffer = this.buffer
    if (!gl || !program || !buffer) return
    gl.useProgram(program)
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
    gl.enableVertexAttribArray(this.positionLocation)
    gl.vertexAttribPointer(this.positionLocation, 2, gl.FLOAT, false, 16, 0)
    if (this.pixelLocation >= 0) {
      gl.enableVertexAttribArray(this.pixelLocation)
      gl.vertexAttribPointer(this.pixelLocation, 2, gl.FLOAT, false, 16, 8)
    }
  }

  private drawFilledNode(triangles: Point[], node: SampledNode, viewport: Viewport, seed: number): void {
    const gl = this.gl
    if (!gl || triangles.length === 0) return
    this.bindShapeProgram()

    const shaderCode = node.shaderRef === 'ink_wash' ? 1 : node.shaderRef === 'gongbi' ? 2 : 0
    const bounds = nodeBounds(node.points, viewport.x, viewport.y)
    gl.uniform1i(this.shaderTypeLocation, shaderCode)
    gl.uniform4f(this.boundsLocation, bounds.minX, bounds.minY, bounds.width, bounds.height)
    gl.uniform1f(this.nodeSeedLocation, ((seed ^ hashString(node.id)) % 9973) + 1)

    if (node.style.gradient) {
      const g = node.style.gradient
      const stops = resolveGradientStops(g, node.style.fill)
      const count = Math.min(6, stops.length)
      const colors = new Float32Array(24)
      const offsets = new Float32Array(6)
      for (let i = 0; i < count; i += 1) {
        colors[i * 4] = stops[i].r / 255
        colors[i * 4 + 1] = stops[i].g / 255
        colors[i * 4 + 2] = stops[i].b / 255
        colors[i * 4 + 3] = stops[i].opacity
        offsets[i] = stops[i].offset
      }
      gl.uniform1i(this.fillModeLocation, g.type === 'radial' ? 2 : 1)
      gl.uniform1i(this.stopCountLocation, count)
      gl.uniform4fv(this.stopColorsLocation, colors)
      gl.uniform1fv(this.stopOffsetsLocation, offsets)
      if (g.type === 'radial') {
        const cx = bounds.minX + bounds.width * (g.cx ?? 0.5)
        const cy = bounds.minY + bounds.height * (g.cy ?? 0.5)
        const r = Math.max(bounds.width, bounds.height) * (g.r ?? 0.6)
        gl.uniform4f(this.gradParamsLocation, cx, cy, Math.max(1, r), 0)
      } else {
        const [x0, y0, x1, y1] = linearGradientEndpoints(bounds, g.angle ?? 90)
        gl.uniform4f(this.gradParamsLocation, x0, y0, x1, y1)
      }
      gl.uniform4f(this.colorLocation, 1, 1, 1, Math.max(0, Math.min(1, node.opacity)))
    } else {
      const [red, green, blue] = rgb(node.style.fill ?? '#000000')
      gl.uniform1i(this.fillModeLocation, 0)
      gl.uniform4f(this.colorLocation, red, green, blue, Math.max(0, Math.min(1, node.opacity)))
    }

    this.uploadAndDraw(triangles, viewport)
  }

  private drawStroke(triangles: Point[], color: string, opacity: number, viewport: Viewport): void {
    const gl = this.gl
    if (!gl || !this.stencilAvailable) {
      this.drawSolid(triangles, color, opacity, viewport)
      return
    }
    if (triangles.length === 0) return
    gl.clearStencil(0)
    gl.stencilMask(0xFF)
    gl.clear(gl.STENCIL_BUFFER_BIT)
    gl.enable(gl.STENCIL_TEST)
    gl.stencilFunc(gl.ALWAYS, 1, 0xFF)
    gl.stencilOp(gl.KEEP, gl.KEEP, gl.REPLACE)
    gl.colorMask(false, false, false, false)
    try {
      this.drawSolid(triangles, color, opacity, viewport)
    } finally {
      gl.colorMask(true, true, true, true)
    }
    gl.stencilFunc(gl.EQUAL, 1, 0xFF)
    gl.stencilOp(gl.KEEP, gl.KEEP, gl.KEEP)

    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
    for (const point of triangles) {
      minX = Math.min(minX, point[0])
      minY = Math.min(minY, point[1])
      maxX = Math.max(maxX, point[0])
      maxY = Math.max(maxY, point[1])
    }
    const quad: Point[] = [
      [minX, minY], [maxX, minY], [maxX, maxY],
      [minX, minY], [maxX, maxY], [minX, maxY],
    ]
    this.drawSolid(quad, color, opacity, viewport)
    gl.disable(gl.STENCIL_TEST)
  }

  private drawSolid(triangles: Point[], color: string, opacity: number, viewport: Viewport): void {
    const gl = this.gl
    if (!gl || triangles.length === 0) return
    this.bindShapeProgram()
    const [red, green, blue] = rgb(color)
    gl.uniform1i(this.fillModeLocation, 0)
    gl.uniform1i(this.shaderTypeLocation, 0)
    gl.uniform4f(this.colorLocation, red, green, blue, Math.max(0, Math.min(1, opacity)))
    this.uploadAndDraw(triangles, viewport)
  }

  private uploadAndDraw(triangles: Point[], viewport: Viewport): void {
    const gl = this.gl
    const buffer = this.buffer
    if (!gl || !buffer || triangles.length === 0) return

    const data = new Float32Array(triangles.length * 4)
    for (let index = 0; index < triangles.length; index += 1) {
      const px = triangles[index][0] - viewport.x
      const py = triangles[index][1] - viewport.y
      data[index * 4] = (px / viewport.width) * 2 - 1
      data[index * 4 + 1] = 1 - (py / viewport.height) * 2
      data[index * 4 + 2] = px
      data[index * 4 + 3] = py
    }
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.STREAM_DRAW)
    gl.drawArrays(gl.TRIANGLES, 0, triangles.length)
  }
}
