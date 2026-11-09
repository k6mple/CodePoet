import { TemplateViewer } from '@/components/TemplateLibrary'

export default async function TemplatePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  return <main id="main-content" className="page"><TemplateViewer id={id} /></main>
}
