export interface Health {
  status: 'ok'
  apiVersion: '1.1.0'
}

export async function getHealth(signal?: AbortSignal): Promise<Health> {
  const response = await fetch('/api/v1/health', {
    credentials: 'include',
    cache: 'no-store',
    signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(5000)]) : AbortSignal.timeout(5000),
  })
  if (!response.ok) throw new Error(`健康检查失败（HTTP ${response.status}）`)
  const data = await response.json()
  if (data.status !== 'ok' || data.apiVersion !== '1.1.0') {
    throw new Error('后端返回了不符合当前契约的健康状态')
  }
  return data
}
