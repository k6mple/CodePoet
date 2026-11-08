import { WorkViewer } from '@/components/Works'

export default async function WorkPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  return <main id="main-content" className="page"><WorkViewer id={id} /></main>
}
