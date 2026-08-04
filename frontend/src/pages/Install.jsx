import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Check, Copy, AlertTriangle, Laptop, Server, ExternalLink } from 'lucide-react'
import * as api from '../services/api.js'

// The Community installation guide.
//
// Rewritten for Docker, which is how this actually ships. What was here before
// was the bare-metal runbook — apt, Postgres, a venv, Caddy, a systemd unit —
// and it had drifted past being merely long: it pointed at a repository that
// 404s, so the guide stopped working at step three and had stayed that way.
//
// Two paths, because they are genuinely different jobs. On your own machine
// there is no domain, no certificate and no ssh, and pretending otherwise is
// what makes a download feel like a deployment. On a VPS all three matter and
// the commands need YOUR domain in them, so this asks for it and writes them
// out — a reader substituting placeholders by hand gets one wrong at the step
// where nothing has started yet and nothing says why.

const REPO = 'https://github.com/vestorfinance/arrissa-entry-station-ai-trading-app.git'
const DIR = '~/entrystation'

const LOCAL = [
  {
    id: 'l-docker',
    title: 'Install Docker',
    body: 'Docker Desktop brings everything the app needs — the database, the browser it logs '
        + 'into your broker with, the web server — in one install. Nothing else has to be set up.',
    link: { label: 'Download Docker Desktop', url: 'https://www.docker.com/products/docker-desktop/' },
    note: 'Open it once after installing and leave it running. Everything below talks to it.',
  },
  {
    id: 'l-code',
    title: 'Get the code',
    body: 'One clone. This is the public edition: core, the free modules, and the store client '
        + 'that fetches anything you buy.',
    code: `git clone ${REPO} ${DIR}\ncd ${DIR}`,
  },
  {
    id: 'l-env',
    title: 'Settings and keys',
    body: 'Three secrets, generated on your machine and never leaving it, written into .env '
        + 'along with the two settings a local install needs.',
    code: `cp .env.docker.example .env

cat >> .env <<EOF
DOMAIN=:80
DOCKER_PLATFORM=linux/arm64
FERNET_KEY=$(python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())")
JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
DB_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
EOF`,
    note: 'DOCKER_PLATFORM=linux/arm64 is for Apple Silicon. On an Intel Mac, Windows or Linux, '
        + 'delete that line.',
    warn: 'Back up .env. Losing FERNET_KEY makes every stored broker session and API key '
        + 'permanently unreadable. There is no recovery, by design.',
  },
  {
    id: 'l-run',
    title: 'Start it',
    body: 'The first build takes a few minutes, most of it pulling the browser. After that it is '
        + 'seconds.',
    code: `docker compose up -d --build\ndocker compose logs -f app`,
    note: 'Wait for "Application startup complete", then press Ctrl-C. That stops watching the '
        + 'logs, not the app.',
  },
  {
    id: 'l-open',
    title: 'Open it',
    body: 'Go to http://localhost. The first account you create is the owner, and registration '
        + 'closes behind it: a Community instance is single-user, so nobody can sign up on your '
        + 'box afterwards.',
  },
]

const TROUBLE = [
  {
    q: 'The page does not load at all',
    a: 'Check all three containers are up with `docker compose ps`. If caddy keeps restarting it '
       + 'is usually the certificate: the domain has to resolve to this server, and ports 80 and '
       + '443 have to be open, before one can be issued.',
  },
  {
    q: 'Connecting a broker says the browser executable is missing',
    a: 'The image and the Playwright package have drifted apart. Rebuild with '
       + '`docker compose build --no-cache app`. The build asserts those two versions match and '
       + 'fails loudly when they do not, so this cannot ship silently.',
  },
  {
    q: 'A module I bought has not appeared',
    a: 'Open the Module Store. Opening it asks the store what this installation owns and installs '
       + 'anything missing. If it still says Buy, the licence is bound to a different instance — '
       + 'which happens if you reinstalled after purchasing.',
  },
  {
    q: 'The assistant says no AI model is configured',
    a: 'Connect a provider on the Connections page and paste its key. Community runs on your own '
       + 'key rather than ours, so nothing that thinks will work until one is there.',
  },
  {
    q: 'I want to start completely over',
    a: '`docker compose down -v` removes the containers AND the volumes, which is your database, '
       + 'your modules and your broker session. It is not reversible. Keep .env if you want your '
       + 'keys back.',
  },
]

// One pass, one regex, five kinds of token. It does not parse shell and is not
// trying to: it recognises what matters in these blocks and leaves everything
// else alone, so an unusual line degrades to plain text rather than to
// something coloured wrongly.
const TOKEN = new RegExp([
  '(#[^\\n]*)',
  "('[^']*'|\"[^\"]*\")",
  '(\\$\\{?[A-Za-z_]\\w*\\}?|\\$\\([^)]*\\))',
  '(\\s--?[A-Za-z][\\w-]*)',
  '\\b(sudo|apt|apt-get|curl|git|python3|pip|npm|cd|echo|printf|tee|ssh|scp|'
  + 'docker|compose|systemctl|mkdir|chown|usermod|newgrp|ufw|cat|cp|sh)\\b',
].join('|'), 'g')

const CLASS = ['tok-c', 'tok-s', 'tok-v', 'tok-f', 'tok-k']

function highlight(code) {
  const out = []
  let last = 0, m, i = 0
  TOKEN.lastIndex = 0
  while ((m = TOKEN.exec(code)) !== null) {
    if (m.index > last) out.push(code.slice(last, m.index))
    const g = m.slice(1).findIndex((x) => x !== undefined)
    out.push(<span className={CLASS[g]} key={i++}>{m[0]}</span>)
    last = m.index + m[0].length
  }
  if (last < code.length) out.push(code.slice(last))
  return out
}

function Code({ children }) {
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    if (!copied) return
    const t = setTimeout(() => setCopied(false), 1600)
    return () => clearTimeout(t)
  }, [copied])
  return (
    <div className="ins-code">
      <button className="ins-copy"
              onClick={() => { navigator.clipboard?.writeText(children).then(() => setCopied(true)) }}>
        {copied ? <><Check size={13} /> Copied</> : <><Copy size={13} /> Copy</>}
      </button>
      {/* The copy button reads `children`, the raw string — only what is
          rendered is coloured, so nothing markup-shaped ever lands on a
          clipboard. */}
      <pre><code>{highlight(children)}</code></pre>
    </div>
  )
}

function Step({ n, s }) {
  return (
    <section className="ins-step" id={s.id}>
      <h2><span className="ins-n">{n}</span>{s.title}</h2>
      <p>{s.body}</p>
      {s.link && (
        <p>
          <a className="btn btn--primary btn--sm" href={s.link.url} target="_blank" rel="noreferrer">
            {s.link.label} <ExternalLink size={13} />
          </a>
        </p>
      )}
      {s.code && <Code>{s.code}</Code>}
      {s.note && <p className="ins-note">{s.note}</p>}
      {s.warn && <p className="ins-warn"><AlertTriangle size={15} /><span>{s.warn}</span></p>}
    </section>
  )
}

export default function Install() {
  const [cfg, setCfg] = useState(null)
  const [where, setWhere] = useState('local')
  // What the VPS commands need, asked for once and written into every block.
  const [host, setHost] = useState('')
  const [user, setUser] = useState('root')
  const [domain, setDomain] = useState('')

  useEffect(() => { api.appConfig().then(setCfg).catch(() => {}) }, [])
  const name = cfg?.app_name || 'EntryStation'

  useEffect(() => {
    document.title = `Install ${name} Community — Docker installation guide`
  }, [name])

  const H = host.trim() || 'your-server-ip'
  const U = user.trim() || 'root'
  const D = domain.trim() || 'yourdomain.com'

  const VPS = useMemo(() => [
    {
      id: 'v-dns',
      title: 'Point your domain at the server',
      body: `At your domain registrar, add an A record for ${D} pointing to ${H}. Do this first: `
          + 'the certificate is issued by proving you control the domain, and that check happens '
          + 'the moment the app starts.',
      note: 'Give DNS a few minutes to propagate before the last step.',
    },
    {
      id: 'v-ssh',
      title: 'Connect to the server',
      body: 'Everything from here runs on the server, not on your own machine.',
      code: `ssh ${U}@${H}`,
    },
    {
      id: 'v-docker',
      title: 'Install Docker',
      body: "Docker's own installer, the one their documentation gives. Compose comes with it.",
      code: `curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ${U}
newgrp docker`,
      note: 'The last two lines let you run docker without sudo. Skip them and every docker '
          + 'command below needs sudo in front of it.',
    },
    {
      id: 'v-ports',
      title: 'Open the ports',
      body: '80 and 443, plus the ssh you are already using. 80 is not optional: it is how the '
          + 'certificate is issued, even though the app redirects to https afterwards.',
      code: `sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable`,
    },
    {
      id: 'v-code',
      title: 'Get the code',
      body: 'The same public edition as a local install.',
      code: `git clone ${REPO} ~/entrystation\ncd ~/entrystation`,
    },
    {
      id: 'v-env',
      title: 'Settings and keys',
      body: 'Your domain goes in here, and the three secrets are generated on the server. Caddy '
          + 'reads DOMAIN and gets a certificate for it on the first start.',
      code: `cp .env.docker.example .env

cat >> .env <<EOF
DOMAIN=${D}
DOCKER_PLATFORM=
FERNET_KEY=$(python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())")
JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
DB_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
EOF`,
      note: 'Leave DOCKER_PLATFORM empty on a server. It defaults to amd64, which is what '
          + 'Microsoft publishes Edge for, and Edge is what the broker login uses.',
      warn: 'Back up .env somewhere that is not the server. Losing FERNET_KEY makes every stored '
          + 'broker session and API key permanently unreadable.',
    },
    {
      id: 'v-run',
      title: 'Start it',
      body: 'The first build takes a few minutes. Watch the log until the certificate is issued.',
      code: `docker compose up -d --build\ndocker compose logs -f`,
    },
    {
      id: 'v-open',
      title: 'Open it',
      body: `Go to https://${D}. The first account you create is the owner, and registration `
          + 'closes behind it.',
    },
  ], [H, U, D])

  const steps = where === 'local' ? LOCAL : VPS
  const after = steps.length
  const dir = where === 'local' ? DIR : '~/entrystation'

  return (
    <div className="home ins">
      <header className="home-nav">
        <div className="home-wrap home-nav-in">
          <Link className="home-brand" to="/">
            <img src="/entry-station-mark.png" alt="" width={26} height={26} className="brand-mark" />
            {name}
          </Link>
          <div className="home-nav-cta" style={{ marginLeft: 'auto' }}>
            <Link className="btn btn--ghost btn--sm" to="/">Home</Link>
            <Link className="btn btn--primary btn--sm" to="/login">Sign in</Link>
          </div>
        </div>
      </header>

      <div className="home-wrap ins-body">
        <aside className="ins-toc">
          <span className="ins-toc-label">On this page</span>
          {steps.map((s, i) => <a href={'#' + s.id} key={s.id}><b>{i + 1}</b>{s.title}</a>)}
          <a href="#first"><b>{after + 1}</b>First run</a>
          <a href="#modules"><b>{after + 2}</b>Modules</a>
          <a href="#update"><b>{after + 3}</b>Updating</a>
          <a href="#trouble"><b>{after + 4}</b>When it goes wrong</a>
        </aside>

        <main className="ins-main">
          <h1 className="ins-title">Install {name} Community</h1>
          <p className="ins-lede">
            Everything runs in Docker: the app, the database, the browser it logs into your broker
            with. Nothing else has to be installed, configured or kept up to date.
          </p>

          {/* Two genuinely different jobs. A local install has no domain, no
              certificate and no ssh, and pretending otherwise is what makes a
              download feel like a deployment. */}
          <div className="ins-where">
            <button className={'ins-where-btn' + (where === 'local' ? ' ins-where-btn--on' : '')}
                    onClick={() => setWhere('local')}>
              <Laptop size={17} strokeWidth={1.9} />
              <b>On your computer</b>
              <span>Try it, or run it for yourself. No domain needed.</span>
            </button>
            <button className={'ins-where-btn' + (where === 'vps' ? ' ins-where-btn--on' : '')}
                    onClick={() => setWhere('vps')}>
              <Server size={17} strokeWidth={1.9} />
              <b>On a VPS</b>
              <span>Ubuntu, your own domain, HTTPS, running around the clock.</span>
            </button>
          </div>

          {where === 'vps' ? (
            <div className="home-panel ins-pre">
              <h2>Your server</h2>
              <p className="ins-note" style={{ marginTop: 0 }}>
                Fill these in and every command below is written out with them in it.
              </p>
              <div className="ins-form">
                <label className="field">
                  <span className="field-label">Domain</span>
                  <input className="input" value={domain} placeholder="yourdomain.com"
                         onChange={(e) => setDomain(e.target.value)} />
                </label>
                <label className="field">
                  <span className="field-label">Server IP</span>
                  <input className="input" value={host} placeholder="203.0.113.10"
                         onChange={(e) => setHost(e.target.value)} />
                </label>
                <label className="field">
                  <span className="field-label">SSH username</span>
                  <input className="input" value={user} placeholder="root"
                         onChange={(e) => setUser(e.target.value)} />
                </label>
              </div>
            </div>
          ) : (
            <div className="home-panel ins-pre">
              <h2>Before you start</h2>
              <ul className="home-ticks">
                <li><Check size={15} /> A Mac, Windows or Linux machine with 8GB of memory</li>
                <li><Check size={15} /> About 6GB of disk, most of it the browser image</li>
                <li><Check size={15} /> An API key from OpenAI, Anthropic, DeepSeek or any other provider</li>
              </ul>
            </div>
          )}

          {steps.map((s, i) => <Step n={i + 1} s={s} key={s.id} />)}

          <section className="ins-step" id="first">
            <h2><span className="ins-n">{after + 1}</span>First run</h2>
            <p>
              Create your account, and the app asks for the two things it cannot run without: a
              model to think with, and somewhere to trade. Both in one step, and both can wait.
            </p>
            <ul className="home-ticks">
              <li><Check size={15} /> An AI provider and its key. Community runs on your key, not ours.</li>
              <li><Check size={15} /> A broker. TradeLocker connects with a login, and if you have
                no account the same panel offers to open one.</li>
            </ul>
            <p className="ins-note">
              Skipped either? The bell in the top right keeps a list of everything still
              unconfigured and links straight to it.
            </p>
          </section>

          <section className="ins-step" id="modules">
            <h2><span className="ins-n">{after + 2}</span>Modules</h2>
            <p>
              The install ships with the free modules already on it. Everything else — Exness,
              Fed Watch, Retail Sentiment, Bond Yields, Truth Social — is bought from the store
              and arrives on its own.
            </p>
            <p>
              Press Buy inside your own Module Store rather than on the website. The link carries
              which installation it is for, so the licence binds to your box and brings you back
              to it with the module already installed. There is no key to type.
            </p>
            <p className="ins-note">
              Bought something and it has not appeared? Opening the Module Store asks the store
              what this installation owns and installs anything missing.
            </p>
          </section>

          <section className="ins-step" id="update">
            <h2><span className="ins-n">{after + 3}</span>Updating</h2>
            <p>
              One command. It updates the app, the frontend and the bundled modules from the
              image, and fetches new versions of anything you have bought before the app starts.
            </p>
            <Code>{`cd ${dir}\ngit pull\ndocker compose up -d --build`}</Code>
            <p>
              Your database, users, settings, installed modules, broker session and .env live on
              volumes, so none of them are touched. You are told when there is a new version:
              a ribbon at the top of the app, and the bell.
            </p>
          </section>

          <section className="ins-step" id="trouble">
            <h2><span className="ins-n">{after + 4}</span>When it goes wrong</h2>
            <p>Every one of these is something that actually happened, and what it turned out to be.</p>
            <div className="ins-faq">
              {TROUBLE.map((t) => (
                <details key={t.q}>
                  <summary>{t.q}</summary>
                  <p>{t.a}</p>
                </details>
              ))}
            </div>
            <p className="ins-note">
              The log answers most things first: <code>docker compose logs -f app</code>
            </p>
          </section>
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
