import { useState, useMemo, useRef, useEffect } from 'react'
import { useNavigate, Navigate } from 'react-router-dom'
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
  const [exnessEmail, setExnessEmail] = useState('')
  const [exnessPassword, setExnessPassword] = useState('')
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
    if (!exnessEmail) setExnessEmail(email.trim())      // default to the signup email
    setStep('exness')
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
        exness_email: (exnessEmail || email).trim(), exness_password: exnessPassword,
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
            {step === 'exness' && 'Connect your Exness account to finish.'}
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
            <button className="auth-continue" type="submit" disabled={password.length < 8}>Continue</button>
            <p className="auth-alt">
              <button type="button" className="auth-link" onClick={() => { setStep('phone'); setError('') }}>Back</button>
            </p>
          </form>
        )}

        {step === 'exness' && (
          <form className="auth-form" onSubmit={complete}>
            <input className="auth-input" type="email" autoFocus placeholder="Exness account email"
                   value={exnessEmail} onChange={(e) => setExnessEmail(e.target.value)} />
            <input className="auth-input" type="password" placeholder="Your Exness password"
                   value={exnessPassword} onChange={(e) => setExnessPassword(e.target.value)} />
            <p className="auth-note">Enter the email on your Exness account (may differ from your login email). Used once to securely connect it — your password is never stored.</p>
            <button className="auth-continue" type="submit" disabled={loading || !exnessPassword}>
              {loading ? 'Connecting…' : 'Connect & create account'}
            </button>
            <p className="auth-alt">
              <button type="button" className="auth-link" onClick={() => { setStep('password'); setError('') }}>Back</button>
            </p>
          </form>
        )}

        <div className="auth-legal">
          <span>Terms of use</span>
          <span className="auth-legal-sep">|</span>
          <span>Privacy policy</span>
        </div>
      </div>
    </div>
  )
}
