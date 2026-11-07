import Creator from '@/components/Creator'

export default function HomePage() {
  return <main id="main-content" className="page">
    <header className="page-intro">
      <div>
        <h1>以代码，写一幅清正</h1>
        <p>从廉洁文化模板出发，调整参数，生成一幅可保存、可溯源的 Canvas 作品。</p>
      </div>
    </header>
    <Creator />
  </main>
}
