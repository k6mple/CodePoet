import Link from 'next/link'
import StartupStatus from './StartupStatus'

export default function SiteFooter() {
  return <footer className="site-footer">
    <div className="site-footer__inner">
      <div>
        <p className="footer-brand">代码诗 · CodePoet</p>
        <p className="footer-note">廉洁文化生成式视觉创作 · core-demo</p>
      </div>
      <div className="footer-links">
        <Link href="/fixture">Canvas 校验</Link>
        <span>Canvas 2D / 800 × 600</span>
      </div>
      <StartupStatus />
    </div>
  </footer>
}
