import StartupStatus from '@/components/StartupStatus'

export default function HomePage() {
  return (
    <main>
      <p className="eyebrow">CODEPOET / CORE DEMO</p>
      <h1>代码诗</h1>
      <p className="intro">廉洁主题生成式可视化创作平台</p>
      <StartupStatus />
      <p className="note">当前已配置应用启动与连接检查。创作画布、登录及作品保存将在后续任务中实现。</p>
    </main>
  )
}
