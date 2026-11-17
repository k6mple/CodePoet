export type DslFunction = 'circle' | 'polygon' | 'polyline' | 'set_param'

export type SandboxStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'timed_out' | 'cancelled'

export interface AuditFinding {
  ruleId: string
  line: number
  column: number
  message: string
}

export interface SandboxMetrics {
  wallTimeMs: number
  cpuTimeMs: number
  drawCalls: number
  steps: number
  peakMemoryMiB: number | null
}

export interface SandboxPolicySnapshot {
  revision: number
  dslWallTimeMs: number
  dslCpuTimeSeconds: number
  dslMemoryMiB: number
  dslMaxDrawCalls: number
  dslMaxAstNodes: number
  enabledDslFunctions: DslFunction[]
}

export interface SandboxRun {
  id: string
  createdAt: string
  ownerId: string
  baseGenerationId: string
  status: SandboxStatus
  policyRevision: number
  sourceHash: string
  startedAt: string | null
  finishedAt: string | null
  generationId: string | null
  metrics: SandboxMetrics | null
  error: { code: string; message: string; details?: Record<string, unknown> | null } | null
  policySnapshot: SandboxPolicySnapshot | null
  auditFindings: AuditFinding[]
}

export interface PolicyValues {
  maxIRNodes: number
  maxVertices: number
  maxRequestBytes: number
  maxIRBytes: number
  maxParameters: number
  dslWallTimeMs: number
  dslCpuTimeSeconds: number
  dslMemoryMiB: number
  dslMaxDrawCalls: number
  dslMaxAstNodes: number
  dslMaxRangeIterations: number
  renderWallTimeMs: number
  turtleMemoryMiB: number
  browserMemoryMiB: number
  enabledDslFunctions: DslFunction[]
}

export interface AuditRule {
  ruleId: string
  description: string
  enabled: boolean
}

export interface Policies {
  revision: number
  updatedAt: string
  values: PolicyValues
  hardLimits: PolicyValues
  auditRules: AuditRule[]
}
