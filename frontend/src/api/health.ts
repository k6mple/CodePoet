import { request } from './client'

export interface Health {
  status: 'ok'
  apiVersion: '1.1.0'
}

export async function getHealth(signal?: AbortSignal): Promise<Health> {
  const data = await request<Health>('/health', { signal })
  if (data.status !== 'ok' || data.apiVersion !== '1.1.0') {
    throw new Error('后端返回了不符合当前契约的健康状态')
  }
  return data
}
