import { useState } from 'react'
import { useNavigate, useLocation, Navigate, Link} from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'
import { useAppName, useAppConfig } from '../services/appConfig.js'

function GoogleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  )
}

export default function Login() {
  const { isAuthed, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const appName = useAppName()
  const cfg = useAppConfig()
  const [step, setStep] = useState('email')     // email → password
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  if (isAuthed) return <Navigate to="/dashboard" replace />
  // Render nothing rather than guess: painting a login form and pulling it away
  // a moment later is worse than a blank instant.
  if (cfg === null) return null
  // A fresh Community install has no account to log in to. The form would ask
  // for a password nobody has set and hide the way forward in small print at
  // the bottom, so the first page is the one that makes the account.
  if (cfg.setup) return <Navigate to="/signup" replace />
  // Google sign-in is a hosted-service arrangement — it needs an OAuth client
  // registered against a known domain. A self-hosted box has no such client and
  // never will, so offering the button there is offering something that cannot
  // work.
  const community = cfg.edition === 'community'
  const from = location.state?.from?.pathname || '/dashboard'

  function continueEmail(e) {
    e.preventDefault()
    setError('')
    if (!email.trim()) { setError('Enter your email address.'); return }
    setStep('password')
  }

  async function signIn(e) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(email.trim(), password)
      navigate(from, { replace: true })
    } catch (err) {
      setError(err.message || 'Sign in failed.')
    } finally {
      setLoading(false)
    }
  }

  function google() {
    setError('Google sign-in isn’t set up yet — continue with your email.')
  }

  return (
    <div className="auth-wrap">
      <div className="auth-topbrand">{appName}</div>

      <div className="auth-card">
        <div className="auth-head">
          <h1 className="auth-title">Log in to start trading</h1>
          <p className="auth-subtitle">AI-assisted analysis, management and execution.</p>
        </div>

        {error && <div className="alert alert--danger">{error}</div>}

        {step === 'email' ? (
          <form className="auth-form" onSubmit={continueEmail}>
            {!community && (
              <>
                <button type="button" className="auth-oauth" onClick={google}>
                  <GoogleIcon /> Continue with Google
                </button>
                <div className="auth-or"><span>OR</span></div>
              </>
            )}

            <input
              className="auth-input"
              type="email"
              autoComplete="email"
              placeholder="Email address"
              value={email}
              autoFocus
              onChange={(e) => setEmail(e.target.value)}
            />
            <button className="auth-continue" type="submit">Continue</button>
            {/* Community is single-user: registration closes for good once the
                one account exists, so this link could only ever land on a
                refusal. It is the right link on the hosted service. */}
            {!community && (
              <p className="auth-alt">New here?{' '}
                <button type="button" className="auth-link" onClick={() => navigate('/signup')}>Create an account</button>
              </p>
            )}
          </form>
        ) : (
          <form className="auth-form" onSubmit={signIn}>
            <button type="button" className="auth-back" onClick={() => { setStep('email'); setError('') }}>
              ‹ {email}
            </button>
            <input
              className="auth-input"
              type="password"
              autoComplete="current-password"
              placeholder="Password"
              value={password}
              autoFocus
              onChange={(e) => setPassword(e.target.value)}
            />
            <button className="auth-continue" type="submit" disabled={loading}>
              {loading ? 'Signing in…' : 'Continue'}
            </button>
          </form>
        )}

        {/* They were words, not links. Somebody deciding whether to hand over
            an email should be able to read what they are agreeing to. */}
        <div className="auth-legal">
          <Link to="/terms">Terms of use</Link>
          <span className="auth-legal-sep">|</span>
          <Link to="/privacy">Privacy policy</Link>
          <span className="auth-legal-sep">|</span>
          <Link to="/licence">Licence</Link>
        </div>
      </div>
    </div>
  )
}
