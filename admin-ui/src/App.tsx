import { Navigate, Route, Routes } from 'react-router'

import AppShell from './components/AppShell'
import ChatPage from './pages/ChatPage'
import ComparisonsPage from './pages/ComparisonsPage'
import CreateProjectPage from './pages/CreateProjectPage'
import FileDetailPage from './pages/FileDetailPage'
import FilesPage from './pages/FilesPage'
import MemoryPage from './pages/MemoryPage'
import NotFoundPage from './pages/NotFoundPage'
import OverviewPage from './pages/OverviewPage'
import ProjectLayout from './pages/ProjectLayout'
import ProjectOverviewPage from './pages/ProjectOverviewPage'
import ProjectsPage from './pages/ProjectsPage'
import RelationshipPage from './pages/RelationshipPage'
import './App.css'

export default function App() {
  return (
    <Routes>
      <Route path="/assistant/*" element={<RelationshipPage />} />
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/assistant" replace />} />
        <Route path="projects" element={<ProjectsPage />} />
        <Route path="projects/new" element={<CreateProjectPage />} />
        <Route path="projects/:projectId" element={<ProjectLayout />}>
          <Route index element={<ProjectOverviewPage />} />
          <Route path="chat" element={<ChatPage />} />
          <Route path="memories" element={<MemoryPage />} />
          <Route path="files" element={<FilesPage />} />
          <Route path="files/:artifactId" element={<FileDetailPage />} />
          <Route path="comparisons" element={<ComparisonsPage />} />
        </Route>
        <Route path="overview" element={<OverviewPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}
