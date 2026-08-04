import { useState, useMemo, useRef, useEffect } from 'react'
import { useNavigate, Navigate, Link} from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'
import * as api from '../services/api.js'
import { COUNTRIES, flagUrl, countryByDial } from '../data/countries.js'
import { useAppName } from '../services/appConfig.js'

function defaultCountry() {
  const region = (navigator.language || '').split('-')[1]
  return COUNTRIES.find((c) => c.code === (region || '').toUpperCase())
    || COUNTRIES.find((c) => c.code === 'US')
    || COUNTRIES[0]
}

// Country picker with GitHub circle-flag SVGs (no emoji) + search.
function CountrySelect({ value, onChange }) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const ref = useRef(null)

  useEffect(() => {
    function onDoc(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  const list = useMemo(() => {
    const s = q.trim().toLowerCase()
    if (!s) return COUNTRIES
    return COUNTRIES.filter((c) => c.name.toLowerCase().includes(s) || c.dial.includes(s.replace('+', '')))
  }, [q])

  return (
    <div className="cc" ref={ref}>
      <button type="button" className="cc-select" onClick={() => setOpen((o) => !o)}>
        <img className="cc-flag" src={flagUrl(value.code)} alt="" />
        <span className="cc-dial">+{value.dial}</span>
      </button>
      {open && (
        <div className="cc-menu">
          <input className="cc-search" autoFocus placeholder="Search country"
                 value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="cc-list">
            {list.map((c) => (
              <button type="button" key={c.code} className="cc-row"
                      onClick={() => { onChange(c); setOpen(false); setQ('') }}>
                <img className="cc-flag" src={flagUrl(c.code)} alt="" />
                <span className="cc-name">{c.name}</span>
                <span className="cc-dial cc-dial--muted">+{c.dial}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// What they agreed to, stored with the acceptance. "They accepted the terms"
// is not a useful record without saying which terms.
const TERMS_VERSION = '2026-08-04'

export default function Signup({ invite = '' }) {
  const { isAuthed, session } = useAuth()
  const appName = useAppName()
  const navigate = useNavigate()
  const [step, setStep] = useState('email')   // email → code → profile
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [shownCode, setShownCode] = useState('')   // single-user install, no SMTP
  const [firstName, setFirstName] = useState('')
  const [lastName, setLastName] = useState('')
  const [country, setCountry] = useState(defaultCountry)
  const [phone, setPhone] = useState('')
  const [password, setPassword] = useState('')
  // The one thing on this page that has to be a deliberate act, so it starts
  // unticked and the button stays dead until it is not.
  const [agreed, setAgreed] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  if (isAuthed) return <Navigate to="/dashboard" replace />

  async function startSignup(e) {
    e.preventDefault(); setError(''); setLoading(true)
    try {
      const r = await api.signupStart(email.trim(), invite)
      // A single-user install with no mail server hands the code back rather
      // than dead-ending on the one signup it will ever accept.
      if (r && r.emailed === false && r.code) { setCode(r.code); setShownCode(r.code) }
      setStep('code')
    }
    catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  async function verify(e) {
    e.preventDefault(); setError(''); setLoading(true)
    try { await api.signupVerify(email.trim(), code.trim(), invite); setStep('name') }
    catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  async function resend() {
    setError('')
    try {
      const r = await api.signupStart(email.trim(), invite)
      if (r && r.emailed === false && r.code) { setCode(r.code); setShownCode(r.code) }
    } catch (err) { setError(err.message) }
  }

  function continueName(e) {
    e.preventDefault(); setError('')
    if (!firstName.trim() || !lastName.trim()) { setError('Enter your first and last name.'); return }
    setStep('phone')
  }

  function continuePhone(e) {
    e.preventDefault(); setError('')
    if (phone.replace(/\D/g, '').length < 4) { setError('Enter your phone number.'); return }
    setStep('password')
  }

  function continuePassword(e) {
    e.preventDefault(); setError('')
    if (password.length < 8) { setError('Password must be at least 8 characters.'); return }
    if (!agreed) { setError('Please accept the Terms of Use and Privacy Policy to continue.'); return }
    // Straight to the account. Sign-up used to end by asking for an Exness email
    // and password, which was wrong twice: it asked somebody who may have no
    // Exness account for credentials to one, and it asked for the single thing
    // this app is careful never to keep. Setting up what you trade on, and what
    // you think with, happens once you are inside — where it can offer to open
    // an account for you rather than assume you have one.
    complete(e)
  }

  // typing a full "+<dial>…" number auto-captures the country
  function onPhone(v) {
    setPhone(v)
    if (v.trim().startsWith('+')) { const c = countryByDial(v); if (c) setCountry(c) }
  }

  async function complete(e) {
    e.preventDefault(); setError(''); setLoading(true)
    const digits = phone.replace(/[^\d]/g, '')
    const fullPhone = phone.trim().startsWith('+') ? `+${digits}` : `+${country.dial}${digits}`
    try {
      const res = await api.signupComplete({
        email: email.trim(), first_name: firstName.trim(), last_name: lastName.trim(),
        phone: fullPhone, country: country.code, password,
        accept_terms: agreed, terms_version: TERMS_VERSION,
        invite,
      })
      session(res)
      navigate('/dashboard', { replace: true })
    } catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  return (
    <div className="auth-wrap">
      <div className="auth-topbrand">{appName}</div>
      <div className="auth-card">
        <div className="auth-head">
          <h1 className="auth-title">Create your account</h1>
          <p className="auth-subtitle">
            {step === 'email' && 'Enter your email to get started.'}
            {step === 'code' && (shownCode
              ? 'No mail server is configured on this install, so your code is below.'
              : `Enter the 6-digit code we sent to ${email}.`)}
            {step === 'name' && 'What should we call you?'}
            {step === 'phone' && 'Your phone number.'}
            {step === 'password' && 'Create a password to secure your account.'}
          </p>
        </div>

        {error && <div className="alert alert--danger">{error}</div>}

        {step === 'email' && (
          <form className="auth-form" onSubmit={startSignup}>
            <input className="auth-input" type="email" autoFocus placeholder="Email address"
                   value={email} onChange={(e) => setEmail(e.target.value)} />
            <button className="auth-continue" type="submit" disabled={loading}>
              {loading ? 'Sending…' : 'Continue'}
            </button>
            <p className="auth-alt">Already have an account?{' '}
              <button type="button" className="auth-link" onClick={() => navigate('/login')}>Log in</button>
            </p>
          </form>
        )}

        {step === 'code' && (
          <form className="auth-form" onSubmit={verify}>
            <input className="auth-input auth-code" inputMode="numeric" maxLength={6} autoFocus
                   placeholder="000000" value={code}
                   onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} />
            <button className="auth-continue" type="submit" disabled={loading || code.length < 6}>
              {loading ? 'Verifying…' : 'Verify'}
            </button>
            <p className="auth-alt">
              <button type="button" className="auth-link" onClick={resend}>Resend code</button>
              {' · '}
              <button type="button" className="auth-link" onClick={() => { setStep('email'); setError('') }}>Change email</button>
            </p>
          </form>
        )}

        {step === 'name' && (
          <form className="auth-form" onSubmit={continueName}>
            <input className="auth-input" autoFocus placeholder="First name"
                   value={firstName} onChange={(e) => setFirstName(e.target.value)} />
            <input className="auth-input" placeholder="Last name"
                   value={lastName} onChange={(e) => setLastName(e.target.value)} />
            <button className="auth-continue" type="submit"
                    disabled={!firstName.trim() || !lastName.trim()}>Continue</button>
          </form>
        )}

        {step === 'phone' && (
          <form className="auth-form" onSubmit={continuePhone}>
            <div className="phone-row">
              <CountrySelect value={country} onChange={setCountry} />
              <input className="auth-input" inputMode="tel" autoFocus placeholder="Phone number"
                     value={phone} onChange={(e) => onPhone(e.target.value)} />
            </div>
            <button className="auth-continue" type="submit">Continue</button>
            <p className="auth-alt">
              <button type="button" className="auth-link" onClick={() => { setStep('name'); setError('') }}>Back</button>
            </p>
          </form>
        )}

        {step === 'password' && (
          <form className="auth-form" onSubmit={continuePassword}>
            <input className="auth-input" type="password" autoFocus placeholder="Create a password (min 8 characters)"
                   value={password} onChange={(e) => setPassword(e.target.value)} />
            {/* Unticked to begin with, and the button is dead until it is not.
                A box already ticked is not consent, it is a default somebody
                failed to notice. The links open in a new tab so reading them
                does not throw away the form. */}
            <label className="signup-agree">
              <input type="checkbox" checked={agreed}
                     onChange={(e) => { setAgreed(e.target.checked); setError('') }} />
              <span>
                I have read and accept the{' '}
                <a href="/terms" target="_blank" rel="noreferrer">Terms of Use</a>, the{' '}
                <a href="/privacy" target="_blank" rel="noreferrer">Privacy Policy</a> and the{' '}
                <a href="/licence" target="_blank" rel="noreferrer">Software Licence</a>.
                I understand that trading carries a high risk of loss, that this software gives no
                financial advice, and that I am responsible for every trade placed through my
                accounts.
              </span>
            </label>
            <button className="auth-continue" type="submit"
                    disabled={loading || password.length < 8 || !agreed}>
              {loading ? 'Creating your account…' : 'Create account'}
            </button>
            <p className="auth-alt">
              <button type="button" className="auth-link" onClick={() => { setStep('phone'); setError('') }}>Back</button>
            </p>
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
