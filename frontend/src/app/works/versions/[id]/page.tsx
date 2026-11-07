import { WorkViewer } from '@/components/Works'
export default async function WorkPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  return <main><WorkViewer id={id} /></main>
}
