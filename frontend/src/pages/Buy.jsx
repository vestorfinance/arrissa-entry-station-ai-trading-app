import { useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'

// The page a Community operator lands on when they click Buy on their own
// machine. PUBLIC on purpose: the buyer has no account here and never will —
// what they have is an instance and an email, and that is all a purchase needs.
// Sending them to a login form was the bug: they were being asked to sign in to
// a service they were trying to pay for.
export default function Buy() {
  const { id } = useParams()
  const [params] = useSearchParams()
  // The instance travels in the URL from their own Module Store. If it did not
  // arrive, they can type it — their Settings page shows it.
  const instance = params.get('instance') || ''
  const [email, setEmail] = useState(params.get('email') || '')
  // Where to send them when the payment clears. It travels from their own
  // Module Store and has to be handed on to the checkout — this page was
  // dropping it, which is the whole reason a buyer ended up looking at a
  // receipt instead of their own modules page. It is never shown or typed:
  // it is the address of the machine that sent them, not an answer they have.
  const returnUrl = params.get('return_url') || ''
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function go() {
    setBusy(true); setErr('')
    if (!instance) {
      setErr('Open the Module Store on your own EntryStation and press Buy there. '
             + 'The purchase has to know which installation it is for, and only your '
             + 'instance can say.')
      setBusy(false)
      return
    }
    try {
      const q = new URLSearchParams({ product: id, instance, email })
      if (returnUrl) q.set('return_url', returnUrl)
      const r = await fetch(`/api/store/checkout?${q}`)
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || 'Could not start the purchase.')
      window.location.href = d.authorization_url   // hand over to Paystack
    } catch (e) { setErr(e.message); setBusy(false) }
  }

  return (
    <div className="auth-wrap">
      <div className="auth-card">
        <h1 className="auth-title">Buy {id}</h1>
        <p className="auth-subtitle">
          {instance
            /* It knows which installation this is for, so it says what happens
               next instead of asking a question it already has the answer to. */
            ? <>The licence activates on the installation you came from. You will be taken
               straight back to it.</>
            : <>Start this from the Module Store on your own EntryStation — the Buy button
               there knows which installation to licence.</>}
        </p>
        {/* No field for it. The id is not shown anywhere in the app any
            more, so asking somebody to type it would be asking for something
            they have no way to find — and a form you cannot fill in is worse
            than no form. A purchase starts inside the instance being licensed,
            where the link carries the id by itself. */}
        <label className="field">
          <span className="field-label">Email for the licence key</span>
          <input className="input" type="email" value={email} placeholder="you@example.com"
                 onChange={(e) => setEmail(e.target.value)} />
        </label>
        {err && <div className="alert alert--danger" style={{ marginTop: 10 }}>{err}</div>}
        <div className="modal-actions" style={{ marginTop: 14 }}>
          <button className="btn btn--primary" disabled={busy || !instance || !email} onClick={go}>
            {busy ? 'Starting…' : 'Continue to payment'}
          </button>
        </div>
      </div>
    </div>
  )
}
