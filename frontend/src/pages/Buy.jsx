import { useEffect, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import * as Icons from 'lucide-react'
import { Puzzle, ShieldCheck, ArrowRight, Check, Lock } from 'lucide-react'

// The page a Community operator lands on when they click Buy on their own
// machine. PUBLIC on purpose: the buyer has no account here and never will —
// what they have is an instance and an email, and that is all a purchase needs.
// Sending them to a login form was the bug: they were being asked to sign in to
// a service they were trying to pay for.
//
// It has to LOOK like somewhere it is safe to type an email and reach for a
// card, because that is precisely what it asks for. "Buy truth-social" over a
// bare input reads as a phishing page, and somebody who hesitates there is
// somebody the product has already lost. So it names the thing properly, shows
// the price it is about to charge, says who takes the money and says what
// happens afterwards. None of that is decoration — each one is a question a
// person is asking themselves with their hand on the mouse.
export default function Buy() {
  const { id } = useParams()
  const [params] = useSearchParams()
  const instance = params.get('instance') || ''
  // Where to send them when the payment clears. It travels from their own
  // Module Store and has to be handed on to the checkout — this page was
  // dropping it, which is the whole reason a buyer ended up looking at a
  // receipt instead of their own modules page. It is never shown or typed:
  // it is the address of the machine that sent them, not an answer they have.
  const returnUrl = params.get('return_url') || ''
  const [email, setEmail] = useState(params.get('email') || '')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [item, setItem] = useState(null)

  // What is being bought, from the store's own catalogue rather than from the
  // address bar. The id there is a slug — `truth-social` — and showing that to
  // a buyer as the product name was most of what made this look improvised.
  useEffect(() => {
    fetch('/api/store/catalog')
      .then((r) => r.json())
      .then((d) => {
        const all = [...(d.modules || []), ...(d.bundles || [])]
        setItem(all.find((x) => x.id === id) || null)
      })
      .catch(() => {})       // the page still works priced or not
  }, [id])

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

  const Icon = (item && Icons[item.icon]) || Puzzle
  const name = item?.name || id
  const price = item?.price_usd

  return (
    <div className="buy-wrap">
      <a className="buy-brand" href="https://entrystation.com">
        <img src="/entry-station-mark.png" alt="" width={26} height={26} className="brand-mark" />
        EntryStation
      </a>

      <div className="buy-card">
        <div className="buy-item">
          <span className="buy-item-icon"><Icon size={21} strokeWidth={1.8} /></span>
          <div className="buy-item-text">
            <h1 className="buy-name">{name}</h1>
            {item?.tagline && <p className="buy-tagline">{item.tagline}</p>}
          </div>
          {price != null && <span className="buy-price">${price}<em>/yr</em></span>}
        </div>

        <ul className="buy-gets">
          <li><Check size={14} strokeWidth={2.4} /> Installs on your instance by itself</li>
          <li><Check size={14} strokeWidth={2.4} /> Updates included for the year</li>
          <li><Check size={14} strokeWidth={2.4} /> Keeps working if it lapses — only new versions stop</li>
        </ul>

        <label className="field">
          <span className="field-label">Email for the receipt and licence key</span>
          <input className="input" type="email" autoComplete="email" value={email}
                 placeholder="you@example.com"
                 onChange={(e) => setEmail(e.target.value)} />
        </label>

        {err && <div className="alert alert--danger">{err}</div>}

        <button className="btn btn--primary buy-go" disabled={busy || !instance || !email}
                onClick={go}>
          {busy ? 'Opening payment…' : <>Continue to payment <ArrowRight size={15} strokeWidth={2.2} /></>}
        </button>

        <div className="buy-next">
          <span className="buy-next-title"><ShieldCheck size={13} strokeWidth={2} /> What happens next</span>
          <ol>
            <li>You come straight back to your own Module Store.</li>
            <li>{name} installs itself — there is no key to type.</li>
            <li>The receipt and key are emailed to you, in case you move servers.</li>
          </ol>
        </div>
      </div>

      {/* Who takes the money, said with their marks rather than a sentence.
          Somebody about to enter a card is asking exactly this, and the answer
          they trust is a logo they already recognise.

          These are the official files, not drawings of them. Both wordmarks are
          drawn for light backgrounds — Paystack's is near-black, Visa's is its
          blue — so each has a reversed copy for the dark theme, changing only
          that one colour. Mastercard is left exactly as issued: its circles
          read on either background, and altering it would be the one change
          nobody is permitted to make. */}
      <div className="buy-trust">
        <span className="buy-trust-label"><Lock size={11} strokeWidth={2.4} /> Secure payment by</span>
        <span className="buy-marks">
          <img className="buy-mark buy-mark--onlight" src="/logos/paystack.svg" alt="Paystack" />
          <img className="buy-mark buy-mark--ondark" src="/logos/paystack-dark.svg" alt="Paystack" />
          <i className="buy-marks-sep" />
          <img className="buy-mark buy-mark--onlight buy-mark--card" src="/logos/visa.svg" alt="Visa" />
          <img className="buy-mark buy-mark--ondark buy-mark--card" src="/logos/visa-dark.svg" alt="Visa" />
          <img className="buy-mark buy-mark--card buy-mark--mc" src="/logos/mastercard.svg" alt="Mastercard" />
        </span>
      </div>

      <p className="buy-foot">
        {instance
          ? <>Licensed to the installation you came from.</>
          : <>Start this from the Module Store on your own EntryStation — the Buy button
             there knows which installation to licence.</>}
      </p>
    </div>
  )
}
