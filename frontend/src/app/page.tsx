import Link from 'next/link'
import Creator from '@/components/Creator'

export default function HomePage() {
  return <main id="main-content" className="page page--studio">
    <header className="page-intro page-intro--studio">
      <div className="studio-hero__copy">
        <span className="studio-eyebrow">东方美学 · 生成式视觉创作工坊</span>
        <h1>以代码，写一幅清正</h1>
        <p>择莲、竹、梅、兰之清雅意象，融参数律动、知识图谱与几何图层，绘就一幅可溯源、可复现的数字画卷。</p>
      </div>
      <div className="page-intro__actions studio-hero__actions">
        <Link href="/templates" className="button button--secondary button--small">浏览文化模板</Link>
        <Link href="/works" className="button button--ghost button--small">漫步作品展厅</Link>
      </div>
    </header>
    <Creator />
  </main>
}
