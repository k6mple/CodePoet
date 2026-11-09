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
  entityIds?: string[]
  tagIds?: string[]
}

export interface Template {
  id: string
  revision: number
  createdAt: string
  updatedAt: string
  ownerId: string
  metadata: TemplateMetadata
  workingMetadata: TemplateMetadata | null
  publishedVersionId: string | null
  latestVersionId: string | null
  supportedBackends: string[]
  thumbnailArtifactId: string | null
  permissions: string[]
}

export interface Review {
  id: string
  createdAt: string
  targetType: 'template_version' | 'work_version'
  targetId: string
  targetRevision: number
  submittedBy: string
  reviewerId: string
  decision: 'approve' | 'reject'
  reason: string
}

export interface TemplateVersion {
  id: string
  revision: number
  createdAt: string
  updatedAt: string
  templateId: string
  versionNumber: number
  createdBy: string
  submittedBy: string | null
  status: 'draft' | 'submitted' | 'published' | 'rejected' | 'archived'
  metadata: TemplateMetadata
  irTemplate: DrawingIR
  generatorRef: 'ir'
  parameterSchema: Parameter[]
  bindings: unknown[]
  constraints: unknown[]
  supportedBackends: string[]
  fragmentSource: null
  ontologyVersion: number
  changeNote: string
  submittedAt: string | null
  publishedAt: string | null
  lastReview: Review | null
}

export interface VersionCreateInput {
  metadata?: TemplateMetadata
  irTemplate?: DrawingIR
  parameterSchema?: Parameter[]
  bindings?: unknown[]
  constraints?: unknown[]
  supportedBackends?: string[]
  changeNote?: string
  fromVersionId?: string
}

export interface TemplateCreateInput {
  metadata: TemplateMetadata
}

export interface TemplateUpdateInput {
  expectedRevision: number
  metadata: TemplateMetadata
}

export interface ReviewQueueItem {
  targetType: 'template_version' | 'work_version'
  targetId: string
  targetRevision: number
  title: string
  submittedBy: string
  submittedAt: string
  status: 'submitted' | 'pending_review' | 'published' | 'rejected' | 'archived'
  canReview: boolean
  latestReview: Review | null
}

export interface ReviewCreateInput {
  targetType: 'template_version' | 'work_version'
  targetId: string
  expectedRevision: number
  decision: 'approve' | 'reject'
  reason: string
}

export interface EditorState {
  mode: 'template' | 'semantic' | 'ir'
  templateVersionId: string | null
  params: Params
  semanticInput: { text: string; entityIds: string[] }
  draftIR: DrawingIR | null
  fragmentSource: ''
  backend: string
  seed: number
  timelineTimeMs: number
  selectedNodeId: string | null
  generationId: string | null
}

export interface CreationSession {
  id: string
  revision: number
  createdAt: string
  updatedAt: string
  ownerId: string
  name: string
  state: EditorState
  latestSnapshotId: string | null
}

export interface Snapshot {
  id: string
  createdAt: string
  sessionId: string
  sessionRevision: number
  state: EditorState
  name: string
  note: string
  restoredFromSnapshotId: string | null
}

export interface SnapshotSaved {
  session: CreationSession
  snapshot: Snapshot
}

export interface FavoriteResult {
  workId: string
  isFavorite: boolean
}

export interface Issue {
  code: string
  message: string
  path: string | null
  nodeIds: string[]
}

export interface AuditLog {
  id: string
  createdAt: string
  actorId: string | null
  actorRole: string | null
  action: string
  targetType: string
  targetId: string | null
  versionId: string | null
  requestId: string
  parentRequestId: string | null
  generationId: string | null
  jobId: string | null
  workId: string | null
  inputSummary: Record<string, unknown>
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
  result: 'success' | 'failure'
  errorCode: string | null
}

export interface ExportRequest {
  types: string[]
  ids?: Record<string, unknown>
}

export interface ImportManifest {
  counts: Record<string, number>
  includesArtifacts: false
}

export interface BackupPackage {
  schemaVersion: '1.0'
  exportedAt: string
  manifest: ImportManifest
  data: Record<string, unknown[]>
}

export interface ImportValidation {
  validationId: string
  valid: boolean
  contentHash: string
  expectedDataRevision: number
  expiresAt: string
  counts: Record<string, number>
  errors: Issue[]
}

export interface ImportApply {
  validationId: string
  expectedDataRevision: number
}

export interface ImportApplied {
  importId: string
  dataRevision: number
  counts: Record<string, number>
  warnings: Issue[]
}

export interface SearchQuery {
  view: 'templates' | 'knowledge'
  q: string
  entityIds: string[]
  tagIds: string[]
  symbols: string[]
  backend: string
  timeTheme: string
  page: number
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
  provenance?: Provenance
  validationReport?: {
    parameterValid: boolean
    nodeCount: number
    outOfBoundsNodeCount: number
    issues: Issue[]
  }
  warnings?: Issue[]
}

export type SemanticInput = EditorState['semanticInput']

export interface Provenance {
  kind: string
  baseGenerationId: string | null
  matchedEntities: { entityId: string; revision: number; content: Record<string, unknown> }[]
  matchedRules: { ruleId: string; version: number; content: Record<string, unknown> }[]
  selectedSymbols: string[]
  paletteDecision: Decision | null
  compositionDecision: Decision | null
  timelineDecision: Decision | null
  templateSelection: { templateVersionId: string; reason: string } | null
  conflicts: Issue[]
  warnings: Issue[]
  engineVersion: string
  ontologyVersion: number
  policyRevision: number
}

interface Decision {
  value: unknown
  ruleIds: string[]
  reason: string
}

export interface MappingRuleInput {
  name: string
  enabled: boolean
  priority: number
  match: { keywords: string[]; entityIds: string[]; conceptIds: string[]; tagIds: string[]; operator: 'any' | 'all' }
  effects: {
    symbolIds: string[]
    palette: string[]
    composition: 'centered' | 'vertical' | 'horizontal' | 'radial'
    motion: 'none' | 'sway' | 'reveal'
    caption: string
  }
  explanation: string
}

export interface MappingRule extends MappingRuleInput {
  id: string
  revision: number
  version: number
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
  originalJobId?: string | null
  qualityReport?: {
    nonBackgroundPixelRatio: number
    emptyOutput: boolean
    issues: Issue[]
  } | null
}

interface PixelMetrics {
  exactMatch: boolean | null
  mae: number | null
  differentPixelRatio: number | null
}

export interface Comparison extends PixelMetrics {
  jobId: string
  originalJobId: string
  compatible: boolean
  reason: string | null
  frames: (PixelMetrics & { timeMs: number })[]
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
  isFavorite: boolean
  permissions?: string[]
}

export interface WorkVersion {
  id: string
  revision: number
  workId: string
  versionNumber: number
  status: 'private' | 'pending_review' | 'published' | 'rejected'
  title: string
  description: string
  generationId: string
  renderJobId: string
  createdBy: string
  submittedBy: string | null
  changeNote: string
  createdAt: string
  updatedAt: string
  submittedAt: string | null
  publishedAt: string | null
  lastReview: Review | null
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
  features?: { semanticGeneration?: boolean; subgraphEditing?: boolean }
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
