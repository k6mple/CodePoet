import StartupStatus from '@/components/StartupStatus'
import Creator from '@/components/Creator'

export default function HomePage() {
  return (
    <main>
      <p className="eyebrow">CODEPOET / CORE DEMO</p>
      <h1>代码诗</h1>
      <p className="intro">廉洁主题生成式可视化创作平台</p>
      <StartupStatus />
      <Creator />
    </main>
  )
}
