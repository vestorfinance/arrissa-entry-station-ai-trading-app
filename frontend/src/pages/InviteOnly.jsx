import { useNavigate } from 'react-router-dom'
import { Mail, MessageCircle } from 'lucide-react'
import { useAppName } from '../services/appConfig.js'

// Registrations are currently disabled — the signup form is kept but this screen
// is shown instead (see REGISTRATIONS_ENABLED in App.jsx). Flip that flag to
// re-open registration.
export default function InviteOnly() {
  const navigate = useNavigate()
  const appName = useAppName()
  return (
    <div className="auth-wrap">
      <div className="auth-topbrand">{appName}</div>

      <div className="auth-card">
        <div className="auth-head">
          <h1 className="auth-title">Invite only</h1>
          <p className="auth-subtitle">{appName} is currently available by invitation only.</p>
        </div>

        <div className="invite-box">
          <p className="invite-lead">To request access, get in touch:</p>
          <a className="invite-line" href="mailto:arrissa.ai@gmail.com">
            <Mail size={17} strokeWidth={1.9} /> arrissa.ai@gmail.com
          </a>
          <a className="invite-line" href="https://wa.me/27732716360" target="_blank" rel="noreferrer">
            <MessageCircle size={17} strokeWidth={1.9} /> WhatsApp +27 73 271 6360
          </a>
        </div>

        <button className="auth-continue" onClick={() => navigate('/login')}>Back to log in</button>
      </div>
    </div>
  )
}
