import { request } from './client'
import type { AssistantProposal, AssistantTarget, Generation } from '@/types/core'

export interface CreateAssistantProposalInput {
  baseGenerationId: string
  prompt: string
  target: AssistantTarget
  rootNodeId?: string
}

export function createAssistantProposal(input: CreateAssistantProposalInput) {
  const body: Record<string, unknown> = {
    baseGenerationId: input.baseGenerationId,
    prompt: input.prompt,
    target: input.target,
  }
  if (input.target === 'ir_fragment' && input.rootNodeId) {
    body.rootNodeId = input.rootNodeId
  }
  return request<AssistantProposal>('/assistant/proposals', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function getAssistantProposal(id: string) {
  return request<AssistantProposal>(`/assistant/proposals/${encodeURIComponent(id)}`)
}

export function applyAssistantProposal(id: string) {
  return request<Generation>(`/assistant/proposals/${encodeURIComponent(id)}/apply`, {
    method: 'POST',
  })
}
