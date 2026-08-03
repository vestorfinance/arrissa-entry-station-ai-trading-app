import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'

// Gate routes behind auth. Unauthenticated users are sent to /login and
// returned to where they were headed after signing in.
export default function ProtectedRoute({ children }) {
  const { isAuthed } = useAuth()
  const location = useLocation()
  if (!isAuthed) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }
  return children
}
