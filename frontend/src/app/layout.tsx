import type { Metadata } from 'next'
import type { ReactNode } from 'react'
import '../assets/main.css'
import SessionProvider from '@/components/SessionProvider'
import SiteFooter from '@/components/SiteFooter'

export const metadata: Metadata = {
  title: '代码诗 CodePoet · 生成艺术创作',
  description: '廉洁主题生成式可视化创作平台',
}

export default function RootLayout({ children }: { children: ReactNode }) {
  return <html lang="zh-CN">
    <body>
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <SessionProvider>{children}</SessionProvider>
      <SiteFooter />
    </body>
  </html>
}
