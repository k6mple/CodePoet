import { TemplateEditor } from '@/components/TemplateManager'

export default async function ManageTemplatePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  return <main id="main-content" className="page"><TemplateEditor id={id} /></main>
}
