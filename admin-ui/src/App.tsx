import { Navigate, Route, Routes } from 'react-router'

import RelationshipPage from './pages/RelationshipPage'

export default function App() {
  return (
    <Routes>
      <Route path="/assistant/*" element={<RelationshipPage />} />
      <Route path="*" element={<Navigate to="/assistant" replace />} />
    </Routes>
  )
}
