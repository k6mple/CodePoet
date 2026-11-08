export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string,
    public fieldErrors: { path: string; message: string }[] = [], public requestId = '') {
    super(message)
  }
}

let csrfToken = ''
let csrfPending: Promise<void> | undefined
export function setCsrf(token: string) { csrfToken = token }
async function ensureCsrf() {
  if (csrfToken) return
  csrfPending ??= request<{ csrfToken: string }>('/auth/csrf')
    .then((data) => { csrfToken = data.csrfToken }).finally(() => { csrfPending = undefined })
  await csrfPending
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = options.method ?? 'GET'
  const headers = new Headers(options.headers)
  if (!['GET', 'HEAD'].includes(method)) {
    await ensureCsrf()
    headers.set('X-CSRF-Token', csrfToken)
    if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  }
  let response: Response
  try {
    response = await fetch(`/api/v1${path}`, { ...options, headers, credentials: 'include', cache: 'no-store',
      signal: options.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(30000)]) : AbortSignal.timeout(30000) })
  } catch (cause) {
    if (options.signal?.aborted) throw cause
    throw new Error('网络连接失败或请求超时。编辑内容已保留；请检查连接后重试。')
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const error = body?.error
    const failure = new ApiError(response.status, error?.code ?? 'HTTP_ERROR', error?.message ?? `请求失败 HTTP ${response.status}`,
      error?.fieldErrors ?? [], error?.requestId ?? response.headers.get('X-Request-Id') ?? '')
    if (response.status === 401 || failure.code === 'CSRF_INVALID') {
      csrfToken = ''
      if (typeof window !== 'undefined') window.dispatchEvent(new Event('codepoet:auth-required'))
    }
    throw failure
  }
  if (response.status === 204) return undefined as T
  if (headers.get('Accept') === 'image/png') {
    if (!response.headers.get('Content-Type')?.startsWith('image/png')) throw new Error('产物响应不是 PNG')
    return await response.blob() as T
  }
  return response.json() as Promise<T>
}

export function errorMessage(cause: unknown) {
  if (!(cause instanceof ApiError)) return cause instanceof Error ? cause.message : '操作失败，请重试'
  const advice: Record<number, string> = {
    401: '请重新登录，当前编辑内容已保留。', 403: '没有权限，或认证令牌失效，请重新登录。',
    404: '资源不存在或不可访问；请确认后端已实现对应接口。',
    409: '数据已被修改，请刷新后重试。',
    422: '请修正以下参数。', 429: '未完成上传任务过多，请重试原任务或等待过期。',
  }
  return [`HTTP ${cause.status} · ${cause.code}：${cause.message}`, advice[cause.status],
    ...cause.fieldErrors.map((item) => `${item.path}：${item.message}`),
    cause.requestId && `requestId=${cause.requestId}`].filter(Boolean).join('\n')
}
