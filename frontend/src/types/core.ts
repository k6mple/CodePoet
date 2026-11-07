// P0 view models, field names follow docs/openapi-core.json.
export type Value = string | number | boolean
export type Params = Record<string, Value>
export interface DrawingIR {
  schemaVersion: string
  seed: number
  canvas: { width: number; height: number; background: string | null }
  viewport: { x: number; y: number; width: number; height: number }
  durationMs: number
  nodes: IRNode[]
  animations: { nodeId: string; property: 'x' | 'y' | 'rotation' | 'opacity'; from: number; to: number; startMs: number; endMs: number; easing: 'linear' }[]
  narrative: { timeMs: number; entityIds: string[]; caption: string }[]
}
export interface IRNode {
  id: string
  parentId: string | null
  type: 'group' | 'circle' | 'polygon' | 'polyline'
  transform?: Partial<{ x: number; y: number; rotation: number; scaleX: number; scaleY: number }>
  style?: { fill?: string | null; stroke?: string | null; strokeWidth?: number; opacity?: number }
  geometry?: { cx?: number; cy?: number; r?: number; points?: number[][] }
  material?: { shaderRef: string }
}
export interface User { id: string; username: string; nickname: string; role: string; permissions: string[] }
export interface AuthSession { user: User; csrfToken: string; expiresAt: string }
type BaseParameter = { name: string; label: string; description: string }
export type Parameter = BaseParameter & (
  { type: 'number' | 'integer'; default: number; min: number; max: number; step: number } |
  { type: 'enum'; default: string; options: string[] } |
  { type: 'boolean'; default: boolean } |
  { type: 'color'; default: string }
)
export interface Template { id: string; metadata: { name: string; description: string }; publishedVersionId: string | null; supportedBackends: string[] }
export interface TemplateVersion { id: string; status: string; metadata: Template['metadata']; parameterSchema: Parameter[]; irTemplate: DrawingIR }
export interface Generation { id: string; ownerId: string; ir: DrawingIR; irHash: string; params: Params; seed: number; unpublishedSource: boolean; requestId: string }
export interface Frame { artifactId: string; sha256: string; width: number; height: number; timeMs: number }
export interface RenderJob {
  id: string; generationId: string; status: string; phase: string; timesMs: number[]
  error: { code: string; message: string } | null
  manifest: { irHash: string; frames: Frame[]; timesMs: number[] } | null
}
export interface Work { id: string; title: string; description: string; displayVersionId: string }
export interface WorkVersion { id: string; workId: string; title: string; status: string }
export interface WorkDetail { workVersion: WorkVersion; generation: Generation; renderJob: RenderJob }
export interface Page<T> { items: T[]; total: number; page: number; pageSize: number }
export interface Capabilities { profile: string; backends: { backend: string; available: boolean; executionModes: string[]; unavailableReason: string | null }[] }
