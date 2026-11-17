import { request } from './client'
import type { Policies, PolicyValues, SandboxRun } from '@/types/sandbox'

export function submitSandboxRun(baseGenerationId: string, source: string) {
  return request<SandboxRun>('/sandbox-runs', {
    method: 'POST',
    body: JSON.stringify({ baseGenerationId, source }),
  })
}

export function getSandboxRun(id: string) {
  return request<SandboxRun>(`/sandbox-runs/${encodeURIComponent(id)}`)
}

export function getPolicies() {
  return request<Policies>('/admin/policies')
}

export function updatePolicies(expectedRevision: number, values: PolicyValues) {
  return request<Policies>('/admin/policies', {
    method: 'PATCH',
    body: JSON.stringify({ expectedRevision, values }),
  })
}
