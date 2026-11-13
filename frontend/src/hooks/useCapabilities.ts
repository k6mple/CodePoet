'use client'

import { useEffect, useState } from 'react'
import { request } from '@/api/client'
import type { Capabilities } from '@/types/core'

export function useCapabilities() {
  const [capabilities, setCapabilities] = useState<Capabilities>()
  useEffect(() => {
    const controller = new AbortController()
    void request<Capabilities>('/engine/capabilities', { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted) setCapabilities(result)
    }).catch(() => { /* Missing capability data never enables an unsupported control. */ })
    return () => controller.abort()
  }, [])
  return capabilities
}
