import { useEffect, useState, useCallback } from 'react'
import DashboardLayout from '../components/DashboardLayout.jsx'
import * as api from '../services/api.js'
import { useBilling, billingChanged } from '../services/billing.js'

const fmt = (n) => Number(n || 0).toLocaleString()
const cap = (s) => (s || '').replace(/^\w/, (c) => c.toUpperCase())

// Load the Paystack Inline script once, on demand.
function loadPaystack() {
  return new Promise((resolve, reject) => {
    if (window.PaystackPop) return resolve()
    const s = document.createElement('script')
    s.src = 'https://js.paystack.co/v1/inline.js'
    s.async = true
    s.onload = () => resolve()
    s.onerror = () => reject(new Error('Could not load Paystack. Check your connection.'))
    document.body.appendChild(s)
  })
}

export default function Billing() {
  return (
    <DashboardLayout title="Plans & Billing">
      <BillingBody />
    </DashboardLayout>
  )
}

function BillingBody() {
  const billing = useBilling()
  const [catalog, setCatalog] = useState(null)
  const [interval, setInterval] = useState('monthly')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState('')
  const [simTx, setSimTx] = useState(null)          // simulated-provider fallback modal
  const [payStatus, setPayStatus] = useState(null)  // {status:'verifying'|'success'|'declined'|'error', text?}

  useEffect(() => { api.getBillingCatalog().then(setCatalog).catch((e) => setError(e.message)) }, [])
  useEffect(() => { if (billing?.interval) setInterval(billing.interval) }, [billing?.interval])

  const finishVerify = useCallback(async (reference) => {
    setPayStatus({ status: 'verifying' })
    try {
      const r = await api.billingVerify(reference)
      if (r.status === 'success') billingChanged()
      setPayStatus({ status: r.status })
    } catch (e) {
      setPayStatus({ status: 'error', text: e.message })
    }
  }, [])

  const payPaystack = useCallback(async (tx) => {
    try {
      await loadPaystack()
    } catch (e) { setError(e.message); return }
    const opts = {
      key: tx.public_key,
      email: tx.email,
      ref: tx.reference,
      currency: 'ZAR',
      callback: (resp) => finishVerify(resp.reference),
      onClose: () => {},
    }
    if (tx.plan_code) opts.plan = tx.plan_code          // subscription
    else opts.amount = tx.amount_kobo                    // one-off top-up
    window.PaystackPop.setup(opts).openIframe()
  }, [finishVerify])

  const startCheckout = useCallback(async (payload, tag) => {
    setError(null); setBusy(tag)
    try {
      const tx = await api.billingCheckout(payload)
      if (tx.provider === 'paystack') await payPaystack(tx)
      else setSimTx(tx)                                  // no keys for this env → simulate
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy('')
    }
  }, [payPaystack])

  async function cancelPlan() {
    if (!confirm('Cancel your subscription? Your account becomes view-only with 0 credits until you resubscribe.')) return
    setBusy('cancel'); setError(null)
    try { await api.billingCancel(); billingChanged() } catch (e) { setError(e.message) } finally { setBusy('') }
  }

  if (!catalog || !billing) return <p className="muted" style={{ padding: 20 }}>Loading…</p>

  const plans = catalog.plans
  const currentOrder = billing.active ? (plans.find((p) => p.key === billing.plan)?.order || 0) : 0
  const pct = billing.monthly_credits ? Math.min(100, Math.round((billing.credits / billing.monthly_credits) * 100)) : 0

  return (
    <div className="settings-stack settings-stack--wide">
      {error && <div className="alert alert--danger">{error}</div>}

      {/* current status + credit meter */}
      <section className="card">
        <div className="card-head">
          <div>
            <h2 className="card-title">Your plan</h2>
            <p className="card-sub">
              {billing.active
                ? `You're on ${billing.plan_name}${billing.interval === 'annual' ? ' (annual)' : ''}. Credits reset each cycle.`
                : 'No active subscription — your account is view-only until you subscribe.'}
            </p>
          </div>
          {billing.active
            ? <span className="pill pill--ok">Active</span>
            : <span className="pill pill--warn">Unsubscribed</span>}
        </div>
        <div className="card-body">
          <div className="credit-meter">
            <div className="credit-meter-top">
              <span className="credit-balance">{fmt(billing.credits)} <span className="credit-unit">credits</span></span>
              {billing.active && <span className="muted">of {fmt(billing.monthly_credits)} / cycle</span>}
            </div>
            <div className="credit-bar"><span style={{ width: `${pct}%` }} /></div>
          </div>
          {billing.active && (
            <button className="btn btn--danger btn--sm" disabled={busy === 'cancel'} onClick={cancelPlan} style={{ marginTop: 14 }}>
              {busy === 'cancel' ? 'Cancelling…' : 'Cancel subscription'}
            </button>
          )}
        </div>
      </section>

      {/* interval toggle */}
      <div className="pill-row billing-interval">
        <button className={'pill-opt' + (interval === 'monthly' ? ' pill-opt--on' : '')} onClick={() => setInterval('monthly')}>Monthly</button>
        <button className={'pill-opt' + (interval === 'annual' ? ' pill-opt--on' : '')} onClick={() => setInterval('annual')}>Annual · save 20%</button>
      </div>

      {/* plan grid */}
      <div className="plan-grid">
        {plans.map((p) => {
          const price = interval === 'annual' ? p.price_zar_annual : p.price_zar
          const isCurrent = billing.active && billing.plan === p.key
          const label = isCurrent ? 'Current plan'
            : !billing.active ? 'Subscribe'
              : p.order > currentOrder ? 'Upgrade' : 'Switch'
          return (
            <div className={'plan-card' + (isCurrent ? ' plan-card--current' : '') + (p.developer ? ' plan-card--elite' : '')} key={p.key}>
              <div className="plan-name">
                {p.name}
                {p.developer && <span className="plan-dev-pill">Developer</span>}
              </div>
              <div className="plan-price">
                <span className="plan-amount">R{fmt(price)}</span><span className="plan-per">/mo</span>
              </div>
              <div className="plan-billed">
                {interval === 'annual' ? `billed R${fmt(p.price_zar_annual * 12)}/yr` : `or R${fmt(p.price_zar_annual)}/mo billed yearly`}
              </div>
              <div className="plan-credits">{fmt(p.credits)} credits / mo</div>
              <p className="plan-blurb">{p.blurb}</p>
              <ul className="plan-features">
                <li>All features included</li>
                <li>{p.limits.accounts == null ? 'Unlimited' : p.limits.accounts} connected accounts</li>
                <li>{p.limits.agents == null ? 'Unlimited' : p.limits.agents} custom analysis agents</li>
                <li>{p.limits.monitors == null ? 'Unlimited' : p.limits.monitors} monitors · min {p.limits.monitor_min_interval_min}-min</li>
                <li className={p.developer ? '' : 'plan-feat--off'}>
                  {p.developer ? 'Developer mode + programmatic API' : 'Developer mode — Elite only'}
                </li>
              </ul>
              <button
                className={'btn btn--block ' + (isCurrent ? 'btn--ghost' : 'plan-cta')}
                disabled={isCurrent || busy === 'plan:' + p.key}
                onClick={() => startCheckout({ plan: p.key, interval }, 'plan:' + p.key)}>
                {busy === 'plan:' + p.key ? 'Starting…' : label}
              </button>
            </div>
          )
        })}
      </div>

      {/* credit packs */}
      <section className="card">
        <div className="card-head">
          <div>
            <h2 className="card-title">Top up credits</h2>
            <p className="card-sub">Out of credits before your cycle resets? Add a one-off pack — keeps your monitors running.</p>
          </div>
        </div>
        <div className="card-body">
          <div className="pack-row">
            {catalog.packs.map((pk) => (
              <div className="pack-card" key={pk.key}>
                <div className="pack-name">{pk.name}</div>
                <div className="pack-credits">{fmt(pk.credits)} credits</div>
                <div className="pack-price">R{fmt(pk.price_zar)}</div>
                <button className="btn btn--sm btn--block pack-buy"
                  disabled={!billing.active || busy === 'pack:' + pk.key}
                  onClick={() => startCheckout({ pack: pk.key }, 'pack:' + pk.key)}>
                  {busy === 'pack:' + pk.key ? 'Starting…' : 'Buy'}
                </button>
              </div>
            ))}
          </div>
          {!billing.active && <p className="muted" style={{ marginTop: 10 }}>Subscribe to a plan before buying top-up packs.</p>}
        </div>
      </section>

      {payStatus && <PayStatusModal state={payStatus} onClose={() => setPayStatus(null)} />}
      {simTx && <SimulateModal tx={simTx} onClose={() => setSimTx(null)} />}
    </div>
  )
}

// Result of a real Paystack payment (after the pop-up closes and we verify it).
function PayStatusModal({ state, onClose }) {
  const s = state.status
  return (
    <div className="modal-overlay" onClick={s === 'verifying' ? undefined : onClose}>
      <div className="modal paystack-modal" onClick={(e) => e.stopPropagation()}>
        {s === 'verifying' && (
          <div className="paystack-result"><div className="chat-spinner" style={{ margin: '6px auto 12px' }} />
            <div className="paystack-result-title">Confirming payment…</div>
            <p className="modal-body">Verifying your transaction with Paystack.</p>
          </div>
        )}
        {s === 'success' && (
          <div className="paystack-result">
            <div className="paystack-result-title">Payment successful</div>
            <p className="modal-body">Your plan is active and credits have been added.</p>
            <button className="btn plan-cta btn--block" onClick={onClose}>Done</button>
          </div>
        )}
        {(s === 'declined' || s === 'error') && (
          <div className="paystack-result">
            <div className="paystack-result-title">{s === 'declined' ? 'Payment not completed' : 'Something went wrong'}</div>
            <p className="modal-body">{state.text || 'Nothing was charged and your plan is unchanged.'}</p>
            <button className="btn btn--ghost btn--block" onClick={onClose}>Close</button>
          </div>
        )}
      </div>
    </div>
  )
}

// Fallback used only when Paystack has no keys for the current environment.
function SimulateModal({ tx, onClose }) {
  const [busy, setBusy] = useState('')
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  async function pay(outcome) {
    setBusy(outcome); setError(null)
    try {
      const r = await api.billingSimulate(tx.reference, outcome)
      setResult(r.status)
      if (r.status === 'success') billingChanged()
    } catch (e) { setError(e.message) } finally { setBusy('') }
  }

  const what = tx.kind === 'subscription' ? `${cap(tx.plan)} plan · ${tx.interval}` : `${fmt(tx.credits)} credit top-up`

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal paystack-modal" onClick={(e) => e.stopPropagation()}>
        <button className="modal-x" onClick={onClose} aria-label="Close" style={{ fontSize: 20, lineHeight: 1 }}>×</button>
        <div className="paystack-brand">Paystack <span className="pill pill--muted">simulated · no keys</span></div>
        {!result ? (
          <>
            <div className="paystack-amount">R{fmt(tx.amount_zar)}</div>
            <div className="paystack-what">{what}</div>
            <div className="paystack-ref">Ref: {tx.reference}</div>
            {error && <div className="alert alert--danger">{error}</div>}
            <p className="modal-body" style={{ marginTop: 14 }}>No Paystack keys for the current environment — choose an outcome to simulate.</p>
            <div className="paystack-actions">
              <button className="btn plan-cta btn--block" disabled={!!busy} onClick={() => pay('success')}>{busy === 'success' ? 'Processing…' : 'Simulate successful payment'}</button>
              <button className="btn btn--danger btn--block" disabled={!!busy} onClick={() => pay('declined')}>{busy === 'declined' ? 'Processing…' : 'Simulate declined payment'}</button>
            </div>
          </>
        ) : (
          <div className="paystack-result">
            <div className="paystack-result-title">{result === 'success' ? 'Payment successful' : 'Payment declined'}</div>
            <p className="modal-body">{result === 'success' ? 'Your plan is active and credits have been added.' : 'Nothing was charged and your plan is unchanged.'}</p>
            <button className={'btn btn--block ' + (result === 'success' ? 'plan-cta' : 'btn--ghost')} onClick={onClose}>{result === 'success' ? 'Done' : 'Close'}</button>
          </div>
        )}
      </div>
    </div>
  )
}

