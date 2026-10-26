import { useLocation } from 'react-router'

export default function NavigationNotice() {
  const { state } = useLocation()
  const notice = typeof state === 'object' && state !== null && 'notice' in state && typeof state.notice === 'string'
    ? state.notice : ''
  return notice ? <p className="navigation-notice" role="status">{notice}</p> : null
}
