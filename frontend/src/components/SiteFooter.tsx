import Link from 'next/link'
import StartupStatus from './StartupStatus'

export default function SiteFooter() {
  return <footer className="site-footer">
    <div className="site-footer__inner">
      <div className="footer-identity">
        <p className="footer-brand">
          <span className="footer-seal" aria-hidden="true">印</span>
          代码诗 · CodePoet
        </p>
        <p className="footer-note">新中式廉洁文化生成式视觉艺术创作与数字典藏平台</p>
      </div>
      <div className="footer-meta">
        <div className="footer-links">
          <Link href="/templates">文化模板</Link>
          <Link href="/works">作品展厅</Link>
          <Link href="/search">意象检索</Link>
          <Link href="/fixture">Canvas 校验</Link>
        </div>
        <StartupStatus />
      </div>
    </div>
  </footer>
}
