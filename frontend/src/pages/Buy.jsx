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
  const [instance, setInstance] = useState(params.get('instance') || '')
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
          {params.get('instance')
            /* It knows which installation this is for, so it says what happens
               next instead of asking a question it already has the answer to. */
            ? <>The licence activates on the installation you came from. You will be taken
               straight back to it.</>
            : <>The licence is tied to the installation that uses it. Your key is emailed to
               you and works on the instance below.</>}
        </p>
        {/* Only when it did not arrive on the link. In the ordinary case the
            buyer's own Module Store put it there and there is nothing to ask —
            and an instance id on screen is a licensing primitive in front of
            somebody who has no decision to make with it. */}
        {!params.get('instance') && (
          <label className="field">
            <span className="field-label">Your instance (domain or id)</span>
            <input className="input" value={instance} placeholder="trader.example.com"
                   onChange={(e) => setInstance(e.target.value)} />
          </label>
        )}
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
