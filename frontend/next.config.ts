import type { NextConfig } from 'next'
import path from 'node:path'

const apiTarget = (process.env.API_PROXY_TARGET || 'http://127.0.0.1:8000').replace(/\/$/, '')

const config: NextConfig = {
  // The fixture source lives in the sibling shared/demo directory.
  turbopack: { root: path.resolve(import.meta.dirname, '..') },
  outputFileTracingRoot: path.resolve(import.meta.dirname, '..'),
  async rewrites() {
    return [{ source: '/api/:path*', destination: `${apiTarget}/api/:path*` }]
  },
}

export default config
