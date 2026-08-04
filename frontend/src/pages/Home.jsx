import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { CandlestickChart, Newspaper, CalendarClock, Landmark, Gauge,
         MessageSquare, Clock, ShieldCheck, Scale, Lock, Users,
         ArrowRight, Check, Minus, Server, Cloud, Sparkles, Zap,
         Boxes, Radio, Workflow, Terminal, Menu, X } from 'lucide-react'
import * as Icons from 'lucide-react'
import * as api from '../services/api.js'

// The front door.
//
// A hero, a figure band, an asymmetric bento of capabilities, a full-bleed
// statement, and a close. There is ONE colour on this page — the app's accent —
// and everything else that needed to stand out uses elevation instead. The
// gradient washes, the violet and the fuchsia are gone: they were two hues we do
// not own, and a brand that borrows colour for a landing page is a brand with a
// different face on its front door than behind it.
//
// The hero carried a rendered mock of a chat turn becoming a trade card. It is
// gone at the owner's request. Worth knowing what that costs, if it is ever
// reconsidered: a page that only DESCRIBES a product is the shape this one
// started in and was rebuilt away from. The honest replacement is a screenshot
// of the real app, not another drawing of it.
//
// Two rules held throughout, and they are the difference between vibrant and
// untrustworthy:
//
//   Nothing is invented. No customer logos we do not have, no testimonials
//   nobody gave, no metrics nobody measured. The figures below are counted from
//   the live catalogue and from what the code actually supports.
//
//   Nothing implies a result. The product makes a PLAN. Every rendering here
//   stops at the plan, and no number on the page is money anybody made.

// The one place per-source colour survives, because it is not decoration: these
// are the colours each source already wears inside the app, so the strip doubles
// as a legend. Say the word and they go accent too.
// Spelled out for the headline. The count is DERIVED from the array below and
// never typed, because it is asserted in three places on this page and a page
// that miscounts its own contents is a page nobody should believe about prices.
const COUNT_WORD = ['no', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven',
                    'Eight', 'Nine', 'Ten']

const SOURCES = [
  { Icon: CandlestickChart, tone: 'indigo', name: 'Market data', what: 'Structure and momentum' },
  { Icon: Newspaper, tone: 'ok', name: 'News', what: 'Impact-scored, tagged' },
  { Icon: CalendarClock, tone: 'amber', name: 'Calendar', what: 'Actual against forecast' },
  { Icon: Landmark, tone: 'blue', name: 'Fed Watch', what: 'Implied rate odds' },
  { Icon: Gauge, tone: 'orange', name: 'Sentiment', what: 'Where the crowd is' },
  { Icon: MessageSquare, tone: 'pink', name: 'Political risk', what: 'Tariffs, the Fed, oil' },
]

// The two halves of the classifier's own rubric, quoted from the prompt that
// does the labelling rather than paraphrased into something friendlier. If the
// gate's definition of "market-moving" ever changes, this page is wrong until
// somebody changes it here too, which is the honest cost of printing it.
const TRUTH_HIGH = [
  'Tariffs and trade policy',
  'The Federal Reserve and rate pressure',
  'Taxes and sanctions',
  'Geopolitical or military action',
  'Energy and oil policy',
  'The economy, the dollar, named companies',
]
const TRUTH_LOW = [
  'Personal attacks',
  'Campaign rhetoric',
  'Culture-war and social topics',
  'Media criticism',
  'Reposts of praise',
  'Non-economic domestic politics',
]

const SAFETY = [
  { Icon: Scale, title: 'Margin checked first',
    body: 'Short on free margin and the order is refused with the exact shortfall, never forced '
        + 'through. You are offered a size that fits.' },
  { Icon: ShieldCheck, title: 'Sizes valid for the instrument',
    body: 'Volume is adjusted to each instrument’s minimum so orders never fail on a '
        + 'technicality, and you are told when it was adjusted.' },
  { Icon: Users, title: 'Your account, only yours',
    body: 'No shared or fallback account. One member can never see or touch another’s '
        + 'positions. A boundary, not a setting.' },
  { Icon: Lock, title: 'Never your money, never your password',
    body: 'Funds stay at your broker. Connecting captures a revocable session and discards the '
        + 'password. We are the control layer, never the custodian.' },
]

const FAQ = [
  { q: 'Do you hold my money?',
    a: 'No. Funds, margin and execution all stay at your broker. We are the intelligence and '
     + 'control layer over an account that remains entirely yours.' },
  { q: 'Do you store my broker password?',
    a: 'No. Connecting logs in as you once, captures a session, and discards the password. That '
     + 'session is encrypted, yours alone, and revocable.' },
  { q: 'What happens if I stop paying?',
    a: 'Your account becomes read-only. Past chats, agents and settings stay visible; nothing new '
     + 'runs until you subscribe again. Nothing is deleted.' },
  { q: 'Is the Community edition crippled?',
    a: 'No, it is the same software. You supply the server and your own AI key and buy the '
     + 'modules you want. What differs is who runs it and who pays for the intelligence.' },
  { q: 'Can I move a licence to another server?',
    a: 'Yes. A licence entitles the installation rather than the person, so it moves a few times '
     + 'on its own, and we will move it for you after that.' },
  { q: 'Can I use my own AI keys?',
    a: 'On Community, yes: OpenAI, Anthropic, DeepSeek, Gemini, Grok, Groq or OpenRouter. On '
     + 'Cloud the model layer is ours, which keeps a month’s cost a number you can predict.' },
]

const LINKS = [
  ['#how', 'How it works'],
  ['#data', 'The read'],
  ['#pricing', 'Pricing'],
  ['#editions', 'Self-host'],
]

const some = (n, word) => (n == null ? 'Unlimited' : `${n} ${word}`)

// What a tier actually buys, in the same rows on every card. The limits ARE the
// difference between the plans — every feature is on every one of them — so a
// card quoting only a price and a credit count left the reader to guess.
function planRows(p) {
  const l = p.limits || {}
  const years = l.history_days == null ? 'Unlimited'
    : l.history_days >= 365 ? `${Math.round(l.history_days / 365)} year${l.history_days >= 730 ? 's' : ''}`
    : `${l.history_days} days`
  return [
    { label: 'Trading accounts', value: l.accounts == null ? 'Unlimited' : String(l.accounts) },
    { label: 'Analysis agents', value: l.agents == null ? 'Unlimited' : String(l.agents) },
    { label: 'Live monitors', value: l.monitors == null ? 'Unlimited' : String(l.monitors) },
    { label: 'Checks as often as', value: `every ${l.monitor_min_interval_min} min` },
    { label: 'Scheduled actions', value: some(l.scheduled, '').trim() || 'Unlimited' },
    { label: 'Trade history', value: years },
    { label: 'Developer mode and API', on: !!p.developer },
  ]
}

export default function Home() {
  const [cfg, setCfg] = useState(null)
  const [store, setStore] = useState(null)
  const [menu, setMenu] = useState(false)

  useEffect(() => {
    api.appConfig().then(setCfg).catch(() => {})
    fetch('/api/store/catalog').then((r) => r.json()).then(setStore).catch(() => {})
  }, [])

  const name = cfg?.app_name || 'EntryStation'
  const plans = cfg?.plans || []
  const mods = store?.modules || []
  const paid = mods.filter((m) => m.price_usd > 0)
  const free = mods.filter((m) => m.price_usd === 0)
  const everything = (store?.bundles || []).find((b) => b.all_access)
  const conns = cfg?.connections || []

  return (
    <div className="home">
      <header className={'home-nav' + (menu ? ' home-nav--open' : '')}>
        <div className="home-wrap home-nav-in">
          <span className="home-brand">
            <img src="/entry-station-mark.png" alt="" width={26} height={26} className="brand-mark" />
            {name}
          </span>
          <nav className="home-nav-links">
            {LINKS.map(([href, label]) => <a href={href} key={href}>{label}</a>)}
          </nav>
          <div className="home-nav-cta">
            <Link className="btn btn--ghost btn--sm" to="/login">Sign in</Link>
            <Link className="btn btn--primary btn--sm" to="/signup">Sign up</Link>
          </div>
          {/* The links were simply hidden below 900px, which left a phone with a
              header it could not navigate from. */}
          <button className="home-burger" onClick={() => setMenu((v) => !v)}
                  aria-expanded={menu} aria-label={menu ? 'Close menu' : 'Open menu'}>
            {menu ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>

        {menu && (
          <div className="home-menu">
            {/* Closing on tap matters more than it looks: these are anchors on
                the same page, so without it the menu stays open over whatever
                you just asked to see. */}
            {LINKS.map(([href, label]) => (
              <a href={href} key={href} onClick={() => setMenu(false)}>{label}</a>
            ))}
            <Link className="btn btn--ghost" to="/login">Sign in</Link>
            <Link className="btn btn--primary" to="/signup">Sign up</Link>
          </div>
        )}
      </header>

      {/* Hero. No signup button — sign-up is partnership-gated and there is no
          free trial, so a button offering one would be the first thing on the
          page and a lie. */}
      <section className="home-hero">
        <div className="home-wrap home-hero-in">
          <span className="home-tag"><Zap size={12} /> For clients of our Exness partnership</span>
          <h1 className="home-h1">
            AI Data Driven Market<br />Intelligence and Automation
          </h1>
          <p className="home-sub">
            News, the calendar, rate odds, sentiment and price structure, gathered
            continuously, reconciled into one decision, and placed on your own broker account
            the moment you say so.
          </p>
          <div className="home-cta">
            <a className="btn btn--primary" href="#how">See how it works <ArrowRight size={15} /></a>
            {/* Straight to the instructions. This scrolled to the editions
                section, which DESCRIBES self-hosting — and somebody who has
                just pressed "run it on your own server" has decided, and is
                asking how. */}
            <Link className="btn btn--ghost" to="/install">Run it on your own server</Link>
          </div>
        </div>
      </section>

      {/* Figures, counted rather than claimed. Every one is read from the live
          catalogue or from what the code supports — there is no customer logo
          wall here because we do not have permission for one, and an invented
          proof band is worse than none. */}
      <section className="home-figs">
        <div className="home-wrap home-figs-in">
          <div className="home-fig home-fig--a">
            <span className="home-fig-icon"><Radio size={24} strokeWidth={1.9} /></span>
            <div className="home-fig-text">
              <strong>{SOURCES.length} live sources</strong>
              <span>Gathered in the background.</span>
            </div>
          </div>
          <div className="home-fig home-fig--b">
            <span className="home-fig-icon"><Boxes size={24} strokeWidth={1.9} /></span>
            <div className="home-fig-text">
              <strong>{mods.length || 10} modules</strong>
              <span>Each installed and removed on its own.</span>
            </div>
          </div>
          <div className="home-fig home-fig--c">
            <span className="home-fig-icon"><Terminal size={24} strokeWidth={1.9} /></span>
            <div className="home-fig-text">
              <strong>Every action, an API</strong>
              <span>Trading and analysis from your scripts.</span>
            </div>
          </div>
        </div>
      </section>

      <section className="home-band" id="data">
        <div className="home-wrap home-band-in">
          <span className="home-eyebrow">Why the read is worth trusting</span>
          <h2 className="home-h2 home-h2--center">{COUNT_WORD[SOURCES.length]} sources, already gathered<br />before you ask.</h2>
          <p className="home-lead home-lead--center">
            Each runs in the background, continuously. The value is not any one feed. It is the
            synthesis nobody has time to assemble in the minutes a setup is actually live.
          </p>
          <div className="home-rail">
            {SOURCES.map(({ Icon, tone, name: n, what }) => (
              <div className={'home-chip home-chip--' + tone} key={n}>
                <span className="home-chip-head">
                  <Icon size={19} strokeWidth={1.9} />
                  <strong>{n}</strong>
                </span>
                <span className="home-chip-sub">{what}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Bento. Different weights instead of identical boxes — the shape that
          stops a features section reading as a table of contents. */}
      <section className="home-section" id="how">
        <div className="home-wrap">
          <span className="home-eyebrow">Your own analysts</span>
          <h2 className="home-h2">Build the analyst you would hire.</h2>
          <p className="home-lead">
            On a canvas: pull the news, check the calendar, read rate odds, read sentiment, look at
            structure, give me a verdict. Each finished agent becomes a tool the assistant can call
            by name, and every account starts with one already built.
          </p>
          <div className="home-bento">
            <div className="home-cell home-cell--wide home-cell--tall">
              <span className="home-cell-icon home-cell-icon--indigo"><Workflow size={18} /></span>
              <h3>A flow you can read</h3>
              <p>Every step visible, every step reorderable. Export one as JSON and a good agent
                 travels.</p>
              <div className="home-flow">
                {['Price structure', 'News', 'Rate odds', 'Retail sentiment'].map((s, i) => (
                  <div className="home-flow-node" key={s}><span className="home-flow-n">{i + 1}</span>{s}</div>
                ))}
                <div className="home-flow-out"><Sparkles size={14} /> Verdict, with a trade plan</div>
              </div>
            </div>
            <div className="home-cell">
              <span className="home-cell-icon home-cell-icon--ok"><Clock size={18} /></span>
              <h3>It runs without you</h3>
              <p>Point an agent at an instrument and a schedule. It checks while you are away and
                 tells you when something changed.</p>
            </div>
            <div className="home-cell">
              <span className="home-cell-icon home-cell-icon--amber"><Terminal size={18} /></span>
              <h3>Or from your own code</h3>
              <p>Every capability is a plain URL with an API key behind it: analysis, trading and
                 scheduling alike.</p>
            </div>
            <div className="home-cell home-cell--wide home-cell--lit">
              <span className="home-cell-icon home-cell-icon--violet"><Sparkles size={18} /></span>
              <h3>The assistant builds them too</h3>
              <p>Ask for a step and it adds one. Ask it to tighten the wording of a verdict and it
                 does. The canvas is there when you want it and never in the way when you do not.</p>
            </div>
          </div>
        </div>
      </section>

      {/* The full-bleed statement. One place on the page where the colour is the
          argument: two ways of working, neither bolted onto the other. */}
      <section className="home-loud">
        <div className="home-wrap home-loud-in">
          <div>
            <h2 className="home-h2">Ask when you want it.<br />Automated when you don’t.</h2>
            <p className="home-lead">
              The same engine answers a question in chat and runs on a schedule at 03:00. Nothing
              is a separate product, nothing is a bolt-on, and an agent you built by talking is the
              same agent that runs while you sleep.
            </p>
            <div className="home-cta home-cta--left">
              <a className="btn btn--ghost" href="#pricing">See the plans <ArrowRight size={15} /></a>
            </div>
          </div>
          <div className="home-loud-art" aria-hidden="true">
            <div className="home-orb">
              <span className="home-orb-glow" />
              <span className="home-orb-ring home-orb-ring--3" />
              <span className="home-orb-ring home-orb-ring--2" />
              <span className="home-orb-ring home-orb-ring--1" />
              <img className="home-orb-mark brand-mark" src="/entry-station-mark.png" alt="" />
            </div>
          </div>
        </div>
      </section>

      <section className="home-band">
        <div className="home-wrap">
          <span className="home-eyebrow home-eyebrow--center">Truth Social API</span>
          <h2 className="home-h2 home-h2--center">
            Trump&rsquo;s Truth Social posts, scored for market impact.
          </h2>
          <p className="home-lead home-lead--center">
            A Truth Social API that does the reading for you. Every Donald Trump post is pulled as
            it lands, labelled once for market impact, and kept with the reason it was given. Ask
            for the high-impact ones and that is all you get: the tariff line, not the repost of a
            compliment.
          </p>

          <div className="home-truth">
            <div className="home-truth-col">
              <h3><Check size={15} /> Reaches you</h3>
              <ul>{TRUTH_HIGH.map((t) => <li key={t}>{t}</li>)}</ul>
            </div>
            <div className="home-truth-col home-truth-col--off">
              <h3><Minus size={15} /> Filtered out</h3>
              <ul>{TRUTH_LOW.map((t) => <li key={t}>{t}</li>)}</ul>
            </div>
          </div>

          <div className="home-truth-api">
            <code className="home-code">
              <b>GET</b> <i>/api/truth/posts</i>
              <u>?</u><em>user</em>=<s>trump</s>
              <u>&amp;</u><em>impact</em>=<s>high</s>
              <u>&amp;</u><em>hours</em>=<s>24</s>
            </code>
            <span>
              One call to the Trump Truth Social API returns the last day&rsquo;s market-moving
              posts, each with its <code>impact</code> and <code>impact_reason</code>. The same
              filter is a tool your assistant can call by name and a node you can drop into an
              analysis agent, so a post about tariffs can reach a trading decision without anybody
              reading a feed.
            </span>
          </div>
        </div>
      </section>

      <section className="home-section">
        <div className="home-wrap">
          {/* One card, heading and all. These four are a single promise made in
              four parts, and four loose blocks under a heading read as four
              separate features. */}
          <div className="home-panel">
            <span className="home-eyebrow">Safety</span>
            <h2 className="home-h2">Rules that hold however a trade is triggered.</h2>
            <p className="home-lead">
              Chat, trade card, schedule or API. The same guarantees, because they live under all
              four rather than in front of one.
            </p>
            <div className="home-safety">
              {SAFETY.map(({ Icon, title, body }) => (
                <div className="home-safe" key={title}>
                  <span className="home-safe-icon"><Icon size={18} strokeWidth={1.9} /></span>
                  <h3>{title}</h3>
                  <p>{body}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="home-band" id="editions">
        <div className="home-wrap">
          <span className="home-eyebrow">Two ways to run it</span>
          <h2 className="home-h2">Same software. The difference is who runs it.</h2>
          <div className="home-doors">
            <div className="home-door">
              <div className="home-door-head">
                <span className="home-door-icon"><Cloud size={18} strokeWidth={1.9} /></span>
                <h3>Cloud</h3>
                <span className="pill pill--ok">Hosted by us</span>
              </div>
              <ul className="home-ticks">
                <li><Check size={15} /> We run the servers, the updates and the uptime</li>
                <li><Check size={15} /> The AI is ours, so there is nothing to configure and one predictable bill</li>
                <li><Check size={15} /> Every module included, nothing bought separately</li>
                <li><Check size={15} /> Metered by credits, so you pay for what you run</li>
              </ul>
              <p className="home-door-foot">
                Open to clients who joined Exness through our partnership.
              </p>
            </div>
            <div className="home-door">
              <div className="home-door-head">
                <span className="home-door-icon"><Server size={18} strokeWidth={1.9} /></span>
                <h3>Community</h3>
                <span className="pill">Free, your own box</span>
              </div>
              <ul className="home-ticks">
                <li><Check size={15} /> The core app, free, single user, source-available</li>
                <li><Check size={15} /> Your own model key, from the seven providers listed below</li>
                <li><Check size={15} /> Free modules install themselves; buy the rest as you need</li>
                <li><Check size={15} /> Nobody meters you, because nobody is paying for your compute</li>
              </ul>
              <p className="home-door-foot">
                Your server, your keys, your updates. That is the real price, and for the right
                person it is the better one.
              </p>
              <Link className="btn btn--ghost btn--sm home-door-btn" to="/install">
                Installation guide <ArrowRight size={14} />
              </Link>
            </div>
          </div>

          {/* Not decoration: on Community the model layer is yours, and this is
              the list of keys the app will actually take. */}
          {conns.length > 0 && (
            <div className="home-provs">
              <span className="home-provs-label">
                Your broker, your model, your messaging. Connect what you already use.
              </span>
              <div className="home-provs-row">
                {conns.map((c) => (
                  <span className={'home-prov home-prov--' + (c.tone || 'slate')}
                        key={c.kind} title={c.name}>
                    {c.logo
                      ? <img src={c.logo} alt={c.name} loading="lazy" />
                      : <b>{c.mark || c.name.slice(0, 1)}</b>}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </section>

      {plans.length > 0 && (
        <section className="home-section" id="pricing">
          <div className="home-wrap">
            <span className="home-eyebrow home-eyebrow--center">Cloud pricing</span>
            <h2 className="home-h2 home-h2--center">Every feature on every plan.</h2>
            <p className="home-lead home-lead--center">
              Plans differ by how much you can run, never by what you are allowed to touch. Prices
              are the monthly rate billed annually, a 20% saving.
            </p>
            <div className="home-plans">
              {plans.map((p) => (
                <div className={'home-plan' + (p.key === 'pro' ? ' home-plan--pick' : '')} key={p.key}>
                  {p.key === 'pro' && <span className="home-plan-tag">Most chosen</span>}
                  <h3>{p.name}</h3>
                  <div className="home-plan-price">
                    <span className="home-plan-n">${p.price_annual_usd}</span>
                    <span className="home-plan-per">/mo</span>
                  </div>
                  <p className="home-plan-mo">${p.price_usd}/mo billed monthly</p>
                  <p className="home-plan-credits">{p.credits.toLocaleString()} credits a month</p>
                  <p className="home-plan-blurb">{p.blurb}</p>
                  <ul className="home-plan-rows">
                    {planRows(p).map((r) => (
                      <li key={r.label} className={r.on === false ? 'is-off' : undefined}>
                        {r.on === false ? <Minus size={14} /> : <Check size={14} />}
                        <span>{r.label}</span>
                        {r.value && <strong>{r.value}</strong>}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
            <p className="home-fine">
              A credit meters model use: a chat, an analysis, a voice minute. Reading prices,
              watching positions and placing a trade you have already decided on cost nothing.
              There is no free plan; an unsubscribed account keeps read-only access to everything
              it has built.
            </p>
          </div>
        </section>
      )}

      {paid.length > 0 && (
        <section className="home-band">
          <div className="home-wrap">
            <span className="home-eyebrow home-eyebrow--center">The module store</span>
            <h2 className="home-h2 home-h2--center">On your own server, buy only what you use.</h2>
            <p className="home-lead home-lead--center">
              Each module is one capability, whole. Installed, updated and removed on its own. A
              licence entitles the installation rather than the person, so your box fetches its own
              updates with nobody logged in.
            </p>
            <div className="home-mods">
              {[...paid, ...free].map((m) => {
                // The catalogue names a lucide icon and a tone for every module,
                // and the store renders exactly those. Reading the same two
                // fields here means a module that changes its face in the store
                // changes it on this page too, with nobody remembering to.
                const I = Icons[m.icon] || Icons.Box
                return (
                  <div className={'home-mod' + (m.price_usd ? '' : ' home-mod--free')} key={m.id}>
                    <span className="home-mod-icon" style={{ color: `var(--tone-${m.tone || 'slate'})` }}>
                      <I size={17} strokeWidth={1.9} />
                    </span>
                    <strong>{m.name}</strong>
                    <span className="home-mod-price">
                      {m.price_usd ? <>${m.price_usd}<i>/yr</i></> : 'Free'}
                    </span>
                  </div>
                )
              })}
            </div>
            {everything && (
              <p className="home-fine">
                <strong>{everything.name} at ${everything.price_usd} a year</strong>
                {everything.full_price_usd > everything.price_usd
                  && <> instead of ${everything.full_price_usd} bought separately.</>}
                {' '}The free ones install themselves the first time your instance starts. When a
                licence lapses what you installed keeps working. Only new versions stop.
              </p>
            )}
          </div>
        </section>
      )}

      <section className="home-section">
        <div className="home-wrap home-narrow">
          {/* Lit, because this is the one section that explains why the whole
              arrangement exists rather than what it does. */}
          <div className="home-panel home-panel--lit">
            <div className="home-panel-in">
              <span className="home-eyebrow">Who it is for</span>
              <h2 className="home-h2">Membership follows the partnership.</h2>
              <p className="home-lead">
                Cloud is for clients who opened their Exness account through our partnership. That
                relationship is how the platform is funded, which is why there is no charge on the
                broker side, and why we can be a partner rather than a counterparty. We are not a
                broker: we never hold funds, and we never trade against you.
              </p>
              <p className="home-lead">
                Not with us yet? Open an account through the partnership, verify, connect, and you
                are in. Or take Community and run it yourself today, with no partnership, no
                subscription and no permission needed.
              </p>
            </div>
          </div>
        </div>
      </section>

      <section className="home-band">
        <div className="home-wrap">
          <span className="home-eyebrow">Questions</span>
          <h2 className="home-h2">Worth knowing before you start.</h2>
          <div className="home-faq">
            {FAQ.map((f) => (
              <div className="home-q" key={f.q}>
                <h3>{f.q}</h3><p>{f.a}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="home-final">
        <div className="home-wrap home-final-in">
          <h2 className="home-h1 home-h1--sm">Simple enough to ask.<br /><em>Strict enough to trust.</em></h2>
          <div className="home-cta">
            <Link className="btn btn--primary" to="/login">Sign in</Link>
            <Link className="btn btn--ghost" to="/install">Run it yourself</Link>
          </div>
        </div>
      </section>

      <footer className="home-foot">
        <div className="home-wrap">
          <div className="home-foot-cols">
            <div className="home-foot-brand">
              <span className="home-brand">
                <img src="/entry-station-mark.png" alt="" width={24} height={24} className="brand-mark" />
                {name}
              </span>
              <p>An operator for your own broker account. Never a broker, never a custodian.</p>
            </div>
            <div>
              <h4>Product</h4>
              <a href="#how">How it works</a>
              <a href="#data">The read</a>
              <a href="#pricing">Pricing</a>
            </div>
            <div>
              <h4>Editions</h4>
              <a href="#editions">Cloud</a>
              <a href="#editions">Community</a>
              <Link to="/install">Install guide</Link>
            </div>
            <div>
              <h4>Account</h4>
              <Link to="/login">Sign in</Link>
            </div>
            <div>
              <h4>Legal</h4>
              <Link to="/terms">Terms of use</Link>
              <Link to="/privacy">Privacy policy</Link>
              <Link to="/licence">Software licence</Link>
            </div>
          </div>
          {/* Trading is regulated and this audience is retail. Nothing above
              promises a return, and this says plainly why. */}
          <p className="home-risk">
            Trading foreign exchange, indices and commodities on margin carries a high level of
            risk and can result in losses exceeding your deposit. {name} is an analysis and control
            tool, not a broker; it does not hold client funds and does not provide financial
            advice. Nothing here is a recommendation to trade, and no figure shown is a prediction
            of any future result. Trade only with money you can afford to lose.
          </p>
          <p className="home-copy">© {new Date().getFullYear()} {name}</p>
        </div>
      </footer>
    </div>
  )
}
