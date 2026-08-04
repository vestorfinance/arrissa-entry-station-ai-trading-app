import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { AlertTriangle } from 'lucide-react'
import * as api from '../services/appConfig.js'

// Terms, Privacy and the software licence.
//
// Written from what the app ACTUALLY does rather than from a template, because
// a policy that describes a different product protects nobody: it misleads the
// person reading it and it is worthless the moment anyone checks it against the
// software. Every claim below is one the code can be held to — the password
// that is genuinely never stored, the API key that is genuinely unrecoverable,
// the Community install where we genuinely hold nothing.
//
// The two editions are separated everywhere it matters. On the hosted service
// we hold data and are answerable for it. On a self-hosted Community instance
// the operator holds their own, and saying otherwise would be claiming a
// responsibility we have no way to discharge.
//
// NOT LEGAL ADVICE, and it says so at the top of each document. This is an
// honest, specific description of the product written by its builders; it is
// not a substitute for a lawyer reviewing it against the law where the company
// and its users actually are.

const UPDATED = '4 August 2026'
const COMPANY = 'Arrissa (Pty) Ltd'
const CONTACT = 'arrissa.ai@gmail.com'

function Doc({ title, lede, children }) {
  return (
    <>
      <h1 className="ins-title">{title}</h1>
      <p className="ins-lede">{lede}</p>
      <p className="legal-updated">Last updated {UPDATED}</p>
      {children}
    </>
  )
}

function S({ id, n, title, children }) {
  return (
    <section className="ins-step" id={id}>
      <h2><span className="ins-n">{n}</span>{title}</h2>
      {children}
    </section>
  )
}

// ── the risk warning, said the same way everywhere it appears ────────────────
export function RiskBlock() {
  return (
    <div className="legal-risk">
      <p className="ins-warn" style={{ margin: 0 }}>
        <AlertTriangle size={16} />
        <span>
          <b>Trading carries a high risk of loss.</b> Leveraged trading in foreign exchange,
          indices, commodities and similar instruments can lose you more than you deposit. Most
          retail accounts lose money. Nothing this software produces is advice, a recommendation,
          or a prediction, and no result it has shown before says anything about what it will do
          next.
        </span>
      </p>
    </div>
  )
}

export function Terms() {
  return (
    <Doc title="Terms of Use"
         lede={`The agreement between you and ${COMPANY} for EntryStation, in the plainest language we can write it.`}>
      <RiskBlock />

      <S id="t-what" n="1" title="What this software is, and is not">
        <p>
          EntryStation is a tool. It reads market data, helps you analyse it, and — when you tell
          it to — sends orders to a broker account that is yours. That is the whole of it.
        </p>
        <ul className="home-ticks">
          <li>It is <b>not</b> a broker. It never holds your money. Your funds stay with your
            broker and your relationship about them is with your broker, not with us.</li>
          <li>It is <b>not</b> a financial adviser and gives no financial, investment, tax or
            legal advice. Nothing it outputs is a recommendation to buy or sell anything.</li>
          <li>It is <b>not</b> a managed service, a signal service, or a fund. Nobody here trades
            on your behalf or decides anything for you.</li>
          <li>It makes <b>no promise of profit</b>, and we make none. Any figure, backtest, past
            result or example anywhere in the product or on this site is illustrative and is not a
            forecast.</li>
        </ul>
        <p>
          Every instruction it carries out is one you configured, whether you pressed a button or
          built an agent that presses it for you. An automated action you set up is your action.
        </p>
      </S>

      <S id="t-you" n="2" title="What you are responsible for">
        <ul className="home-ticks">
          <li>Every trade placed through your accounts, including by agents, schedules or
            automations you created or enabled.</li>
          <li>Your own eligibility. Leveraged trading is restricted or prohibited in some
            countries and for some people. Checking that is yours, not ours.</li>
          <li>Your broker relationship, your broker's terms, and anything your broker does or
            fails to do.</li>
          <li>Your credentials, your API keys, and anyone you give access to.</li>
          <li>Reviewing what an automation will do before you let it run against a live account.
            Demo first is not a formality.</li>
        </ul>
        <p>
          You must be at least 18 and legally able to enter this agreement. You must not use the
          software for anything unlawful, including market manipulation or trading on information
          you should not have.
        </p>
      </S>

      <S id="t-ai" n="3" title="About the AI">
        <p>
          The assistant and the analysis agents run on large language models. They are wrong
          sometimes, confidently. They can misread data, miss things, or produce a well-argued
          case for something that turns out to be nonsense. Treat every output as a starting point
          for your own judgement, never as a conclusion, and never as advice.
        </p>
        <p>
          Model providers are third parties with their own terms and their own outages. When one
          is slow, rate-limited or down, parts of the product stop working, and that is outside
          our control.
        </p>
      </S>

      <S id="t-avail" n="4" title="Availability, data and third parties">
        <p>
          Market data, sentiment, calendars, news and rate probabilities come from third-party
          sources. They can be delayed, wrong, incomplete or unavailable, and we do not warrant
          any of it. Prices you see here may differ from your broker's.
        </p>
        <p>
          We do not promise the service will be uninterrupted or error-free. We may change,
          suspend or discontinue any part of it. Where we can give notice of something significant,
          we will.
        </p>
      </S>

      <S id="t-money" n="5" title="Payment, licences and refunds">
        <p>
          Paid plans and paid modules are billed in advance for the period shown at checkout.
          Payments are taken by Paystack; we never see or store your card details.
        </p>
        <ul className="home-ticks">
          <li>A module licence is tied to the <b>installation</b> that bought it, not to a person.
            It may be re-bound to a new installation a limited number of times.</li>
          <li>What lapses when a subscription ends is the right to <b>new versions</b>. What you
            have installed keeps working.</li>
          <li>Because a licence is delivered immediately and cannot be returned, purchases are
            generally non-refundable. If something is genuinely broken and we cannot fix it,
            contact us and we will deal with it fairly.</li>
        </ul>
      </S>

      <S id="t-liability" n="6" title="Limitation of liability">
        <p>
          <b>To the fullest extent the law allows:</b> the software is provided “as is” and “as
          available”, with no warranty of any kind, express or implied, including merchantability,
          fitness for a particular purpose and non-infringement.
        </p>
        <p>
          <b>{COMPANY} is not liable for any trading loss.</b> That includes losses from executed
          orders, orders that failed to execute, orders executed late, slippage, incorrect or
          delayed data, an automation behaving differently from how you expected, an AI output you
          acted on, a broker outage, an internet outage, or the software being unavailable at a
          moment that mattered.
        </p>
        <p>
          We are not liable for indirect, incidental, special, consequential or punitive damages,
          or for lost profits, lost opportunity or lost data, even if we were told such damage was
          possible.
        </p>
        <p>
          Where liability cannot lawfully be excluded, our total liability to you for all claims
          is limited to the amount you paid us in the three months before the event giving rise to
          the claim, or ZAR 1,000 if you paid us nothing.
        </p>
        <p className="legal-note">
          Nothing here excludes liability for fraud, fraudulent misrepresentation, death or
          personal injury caused by negligence, or anything else that cannot be excluded under the
          law that applies to you. Some jurisdictions do not allow certain exclusions, in which
          case those exclusions do not apply to you and the rest still stands.
        </p>
      </S>

      <S id="t-indem" n="7" title="Indemnity">
        <p>
          You agree to indemnify {COMPANY} against any claim, loss or cost arising from your use
          of the software, your trading, your breach of these terms, or your breach of anyone
          else's rights — including your broker's terms or the terms of any data or model provider
          you connect.
        </p>
      </S>

      <S id="t-account" n="8" title="Your account">
        <p>
          Keep your credentials to yourself; you are responsible for what happens under your
          account. You may close it at any time. We may suspend or close an account that breaches
          these terms, is used unlawfully, or threatens the service or other users — and where it
          is reasonable to do so, we will tell you why.
        </p>
      </S>

      <S id="t-selfhost" n="9" title="If you self-host">
        <p>
          The Community edition runs on hardware you control. Use of the software is governed by
          the <Link to="/licence">software licence</Link>; these terms still apply to anything you
          get from us, such as the module store, purchases and updates.
        </p>
        <p>
          On a self-hosted instance <b>you are the operator</b>. You are responsible for securing
          the machine, keeping it updated, backing it up, and for any personal data of anyone else
          you put on it. We have no access to it and cannot recover anything from it.
        </p>
      </S>

      <S id="t-changes" n="10" title="Changes, law and contact">
        <p>
          We may update these terms. Material changes will be notified in the app or by email, and
          continuing to use the service after that means you accept them. The date at the top
          always shows the current version.
        </p>
        <p>
          These terms are governed by the law of the Republic of South Africa, and the courts of
          South Africa have jurisdiction — without taking away any protection you have under the
          mandatory law of the country you live in.
        </p>
        <p>
          Questions, or anything that looks wrong: <a href={`mailto:${CONTACT}`}>{CONTACT}</a>.
        </p>
      </S>
    </Doc>
  )
}

export function Privacy() {
  return (
    <Doc title="Privacy Policy"
         lede="What we hold, why we hold it, and the things we deliberately never keep.">
      <S id="p-who" n="1" title="Who this applies to">
        <p>
          On the hosted service at entrystation.com, {COMPANY} is the controller of your personal
          data and this policy describes what we do with it.
        </p>
        <p>
          On a <b>self-hosted Community instance, we hold nothing</b>. Your database is on your
          machine and we cannot see it, reach it or recover it. The operator of that instance —
          usually you — is the controller. We only receive what your instance sends us when it
          asks the store about licences and updates: an installation id, and which modules it
          owns.
        </p>
      </S>

      <S id="p-what" n="2" title="What we hold">
        <ul className="home-ticks">
          <li><b>Account</b> — name, email, phone, country, and a hash of your password. We cannot
            read your password.</li>
          <li><b>Usage</b> — what you asked the assistant, what your agents ran, and the results,
            so you can look back at them.</li>
          <li><b>Broker connections</b> — the session token your broker issues, encrypted at rest.</li>
          <li><b>Provider keys</b> — any AI provider key you add, encrypted at rest.</li>
          <li><b>Billing</b> — plan, credits, and payment references. Paystack holds the card
            details; we never see them.</li>
          <li><b>Technical</b> — logs and errors needed to keep the service running and secure.</li>
        </ul>
      </S>

      <S id="p-never" n="3" title="What we deliberately never keep">
        <ul className="home-ticks">
          <li><b>Your broker password.</b> It is used once, in the moment, to obtain a session
            token, and then discarded. It is never written to the database. This is why
            reconnecting asks for it again rather than remembering it.</li>
          <li><b>Your card details.</b> They go to Paystack and never reach us.</li>
          <li><b>Your API keys, in readable form.</b> Keys the app issues are stored hashed. Not
            even we can recover one — which is why a lost key is replaced rather than looked up.</li>
        </ul>
      </S>

      <S id="p-why" n="4" title="Why we hold it">
        <p>
          To provide the service you asked for, to bill you for it, to keep it secure, and to meet
          our legal obligations. We do not sell your data. We do not share it for advertising. We
          do not build a profile of you for anyone else's purposes.
        </p>
      </S>

      <S id="p-third" n="5" title="Who else sees it">
        <p>
          Only what is needed to make the thing work, and only for that purpose:
        </p>
        <ul className="home-ticks">
          <li><b>AI providers</b> — the content of a request goes to whichever model answers it.
            On the hosted service that is a provider we chose; on a Community instance it is one
            you chose, and your key.</li>
          <li><b>Your broker</b> — orders, and account queries you asked for.</li>
          <li><b>Data providers</b> — market data, calendars, sentiment, news.</li>
          <li><b>Paystack</b> — payments.</li>
          <li><b>Our hosting provider</b> — the servers this runs on.</li>
        </ul>
        <p>
          Each has its own privacy policy and its own terms. We may also disclose data where the
          law requires it.
        </p>
      </S>

      <S id="p-where" n="6" title="Where it is, and how long we keep it">
        <p>
          The hosted service runs on servers in the European Union. Some third parties above
          operate elsewhere, so data may be processed outside your country.
        </p>
        <p>
          We keep account data while your account is open. Delete your account and we delete or
          anonymise your personal data, except where we must keep records — payment records, for
          example — for as long as the law says.
        </p>
      </S>

      <S id="p-rights" n="7" title="Your rights">
        <p>
          Depending on where you live, you can ask for a copy of your data, ask us to correct it,
          ask us to delete it, object to some processing, or ask for it in a portable form. Write
          to <a href={`mailto:${CONTACT}`}>{CONTACT}</a> and we will answer within a month.
        </p>
        <p>
          If you think we have handled your data badly, tell us first — we would rather fix it —
          but you may also complain to your data protection authority, or in South Africa to the
          Information Regulator.
        </p>
      </S>

      <S id="p-security" n="8" title="Security, honestly stated">
        <p>
          Passwords are hashed. Broker sessions and provider keys are encrypted at rest. Traffic
          is encrypted in transit. Access to production is limited to those who need it.
        </p>
        <p>
          No service can promise it will never be breached, and we will not pretend otherwise. If
          a breach affects your data we will tell you and the relevant authority as the law
          requires.
        </p>
      </S>

      <S id="p-cookies" n="9" title="Cookies">
        <p>
          We use local storage and a session token to keep you signed in and to remember your
          preferences. That is all. There is no advertising tracking and no third-party analytics
          following you around.
        </p>
      </S>

      <S id="p-changes" n="10" title="Changes">
        <p>
          We will update this page when what we do changes, and the date at the top will change
          with it. Anything material will be notified in the app or by email.
        </p>
      </S>
    </Doc>
  )
}

// ── the shell both documents sit in ──────────────────────────────────────────
export default function Legal() {
  const { pathname } = useLocation()
  const name = api.useAppName()
  const isPrivacy = pathname.startsWith('/privacy')

  useEffect(() => {
    document.title = `${isPrivacy ? 'Privacy Policy' : 'Terms of Use'} — ${name}`
  }, [isPrivacy, name])
  useEffect(() => { window.scrollTo(0, 0) }, [pathname])

  return (
    <div className="home ins">
      <header className="home-nav">
        <div className="home-wrap home-nav-in">
          <Link className="home-brand" to="/">
            <img src="/entry-station-mark.png" alt="" width={26} height={26} className="brand-mark" />
            {name}
          </Link>
          <div className="home-nav-cta" style={{ marginLeft: 'auto' }}>
            <Link className="btn btn--ghost btn--sm" to="/terms">Terms</Link>
            <Link className="btn btn--ghost btn--sm" to="/privacy">Privacy</Link>
            <Link className="btn btn--ghost btn--sm" to="/licence">Licence</Link>
          </div>
        </div>
      </header>

      <div className="home-wrap ins-body">
        <main className="ins-main legal-main">
          {isPrivacy ? <Privacy /> : <Terms />}
          <p className="legal-note legal-foot">
            This page describes the product accurately and in good faith. It is not legal advice,
            and it does not replace advice from a professional about your own situation.
          </p>
        </main>
      </div>

      <footer className="home-foot">
        <div className="home-wrap">
          <p className="home-risk">
            Trading foreign exchange, indices and commodities on margin carries a high level of
            risk and can result in losses exceeding your deposit. {name} is an analysis and control
            tool, not a broker; it does not hold client funds and does not provide financial
            advice. Nothing here is a recommendation to trade.
          </p>
          <p className="home-copy">© {new Date().getFullYear()} {name}</p>
        </div>
      </footer>
    </div>
  )
}

// ── the software licence, read from the file that governs the software ───────
export function Licence() {
  const name = api.useAppName()
  const [text, setText] = useState('')
  const [err, setErr] = useState('')

  useEffect(() => {
    document.title = `Software Licence — ${name}`
    window.scrollTo(0, 0)
  }, [name])

  useEffect(() => {
    import('../services/api.js')
      .then((m) => m.licenceText())
      .then((r) => { setText(r.text || ''); if (r.error) setErr(r.error) })
      .catch((e) => setErr(e.message))
  }, [])

  return (
    <div className="home ins">
      <header className="home-nav">
        <div className="home-wrap home-nav-in">
          <Link className="home-brand" to="/">
            <img src="/entry-station-mark.png" alt="" width={26} height={26} className="brand-mark" />
            {name}
          </Link>
          <div className="home-nav-cta" style={{ marginLeft: 'auto' }}>
            <Link className="btn btn--ghost btn--sm" to="/terms">Terms</Link>
            <Link className="btn btn--ghost btn--sm" to="/privacy">Privacy</Link>
          </div>
        </div>
      </header>

      <div className="home-wrap ins-body">
        <main className="ins-main legal-main">
          <h1 className="ins-title">Software Licence</h1>
          <p className="ins-lede">
            The terms the software itself is under. This is the file shipped in the repository,
            served as it is — a licence that exists in two places eventually says two things.
          </p>
          <p className="legal-updated">
            Self-hosting and modification are permitted. Reselling it as a competing hosted
            service is not.
          </p>
          {err && <p className="ins-warn"><AlertTriangle size={15} /><span>{err}</span></p>}
          <pre className="legal-licence">{text || 'Loading…'}</pre>
        </main>
      </div>

      <footer className="home-foot">
        <div className="home-wrap">
          <p className="home-copy">© {new Date().getFullYear()} {name}</p>
        </div>
      </footer>
    </div>
  )
}
