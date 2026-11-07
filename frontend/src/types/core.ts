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

export type Role = 'creator' | 'developer' | 'expert' | 'admin'
export type UserStatus = 'active' | 'disabled'

export interface RoleApplication {
  requestedRole: Role
  status: 'pending' | 'approved' | 'rejected'
  reason: string
  decidedAt: string | null
}

export interface User {
  id: string
  revision: number
  createdAt: string
  updatedAt: string
  username: string
  nickname: string
  bio: string
  role: Role
  status: UserStatus
  roleApplication: RoleApplication | null
  permissions: string[]
}

export interface AuthSession {
  user: User
  csrfToken: string
  expiresAt: string
}

type BaseParameter = { name: string; label: string; description: string }

export type Parameter = BaseParameter & (
  { type: 'number' | 'integer'; default: number; min: number; max: number; step: number } |
  { type: 'enum'; default: string; options: string[] } |
  { type: 'boolean'; default: boolean } |
  { type: 'color'; default: string }
)

export interface TemplateMetadata {
  name: string
  description: string
  symbols?: string[]
  timeTheme?: string | null
}

export interface Template {
  id: string
  metadata: TemplateMetadata
  publishedVersionId: string | null
  supportedBackends: string[]
}

export interface TemplateVersion {
  id: string
  status: string
  metadata: TemplateMetadata
  parameterSchema: Parameter[]
  irTemplate: DrawingIR
}

export interface Generation {
  id: string
  createdAt?: string
  ownerId: string
  mode?: string
  backend?: string
  ir: DrawingIR
  irHash: string
  params: Params
  seed: number
  templateVersionId?: string | null
  unpublishedSource: boolean
  requestId: string
}

export interface Frame {
  artifactId: string
  sha256: string
  width: number
  height: number
  timeMs: number
}

export interface RenderJob {
  id: string
  generationId: string
  status: string
  phase: string
  timesMs: number[]
  rendererVersion?: string | null
  error: { code: string; message: string } | null
  manifest: { irHash: string; frames: Frame[]; timesMs: number[] } | null
}

export interface Work {
  id: string
  revision: number
  createdAt: string
  updatedAt: string
  ownerId: string
  title: string
  description: string
  displayVersionId: string
  thumbnailArtifactId: string | null
}

export interface WorkVersion {
  id: string
  workId: string
  versionNumber: number
  status: 'private' | 'pending_review' | 'published' | 'rejected'
  title: string
  description: string
  generationId: string
  renderJobId: string
  createdAt: string
  updatedAt: string
}

export interface WorkDetail {
  workVersion: WorkVersion
  generation: Generation
  renderJob: RenderJob
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
  pageSize: number
}

export interface Capabilities {
  profile: string
  backends: { backend: string; available: boolean; executionModes: string[]; unavailableReason: string | null }[]
}

// P1-A knowledge, ontology and tag models. Field names follow docs/openapi-core.json.
export type KnowledgeType = 'person' | 'story' | 'concept' | 'symbol'
export type RelationPredicate = 'embodies' | 'associated_with' | 'symbolizes' | 'derived_from'
export type EntityStatus = 'active' | 'inactive'

export interface SourceReference {
  title: string
  url: string | null
  note: string
}

export interface KnowledgeRelation {
  predicate: RelationPredicate
  targetEntityId: string
}

export interface KnowledgeEntity {
  id: string
  revision: number
  createdAt: string
  updatedAt: string
  type: KnowledgeType
  name: string
  aliases: string[]
  summary: string
  content: string
  sourceReferences: SourceReference[]
  conceptIds: string[]
  tagIds: string[]
  relations: KnowledgeRelation[]
  timeTheme: string | null
  status: EntityStatus
  effectiveTagIds: string[]
  inheritedTagIds: string[]
}

export interface KnowledgeInput {
  type: KnowledgeType
  name: string
  aliases: string[]
  summary: string
  content: string
  sourceReferences: SourceReference[]
  conceptIds: string[]
  tagIds: string[]
  relations: KnowledgeRelation[]
  timeTheme: string | null
  status: EntityStatus
}

export interface Concept {
  id: string
  revision: number
  createdAt: string
  updatedAt: string
  name: string
  description: string
  parentId: string | null
  status: EntityStatus
}

export interface Tag extends Concept {
  conflictsWith: string[]
  inheritedTagIds: string[]
}

export interface ConceptInput {
  name: string
  description: string
  parentId: string | null
  status: EntityStatus
}

export interface TagInput extends ConceptInput {
  conflictsWith: string[]
}

export interface RoleDecisionInput {
  expectedRevision: number
  approved: boolean
  role: Role
  reason: string
}
