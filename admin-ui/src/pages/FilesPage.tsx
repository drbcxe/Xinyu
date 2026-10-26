import { useOutletContext } from 'react-router'
import ArtifactPanel from '../components/ArtifactPanel'
import type { Project } from '../types'

export default function FilesPage() {
  const project = useOutletContext<Project>()
  return <ArtifactPanel key={project.id} project={project} />
}
