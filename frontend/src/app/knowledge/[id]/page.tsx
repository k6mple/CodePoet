import KnowledgeViewer from '@/components/KnowledgeViewer'

export default async function KnowledgePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  return <main id="main-content" className="page"><KnowledgeViewer id={id} /></main>
}
