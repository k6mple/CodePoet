import type { Metadata } from 'next'
import type { ReactNode } from 'react'
import '../assets/main.css'
import SessionProvider from '@/components/SessionProvider'

export const metadata: Metadata = {
  title: '代码诗 · Canvas 创作',
  description: '廉洁主题生成式可视化创作平台',
}

export default function RootLayout({ children }: { children: ReactNode }) {
  return <html lang="zh-CN"><body><SessionProvider>{children}</SessionProvider></body></html>
}
