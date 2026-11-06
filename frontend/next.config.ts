import type { NextConfig } from 'next'

const apiTarget = (process.env.API_PROXY_TARGET || 'http://127.0.0.1:8000').replace(/\/$/, '')

const config: NextConfig = {
  async rewrites() {
    return [{ source: '/api/:path*', destination: `${apiTarget}/api/:path*` }]
  },
}

export default config
