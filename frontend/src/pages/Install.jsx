import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Check, Copy, AlertTriangle } from 'lucide-react'
import * as api from '../services/api.js'

// The Community installation guide.
//
// Written from deployment.md, which is the runbook that actually built the live
// server, rather than from how an install of this shape usually goes. Where the
// two differ the runbook wins, and where the runbook is about the CLOUD install
// it is adapted rather than copied: a Community box seeds no database dump,
// generates its own keys instead of inheriting them, and takes one user.
//
// Every command here is one that was run.

// HTTPS rather than SSH: a public clone needs no key, and asking a reader to set
// one up before step three loses the ones who would have got there.
const REPO = 'https://github.com/vestorfinance/arrissa-all-in-one-ai-trading-platform-exeness-api.git'

const STEPS = [
  {
    id: 'server',
    title: 'The server, and what goes on it',
    body: 'A small VPS is enough: two cores and 4GB carries the app, Postgres and a browser. '
        + 'Everything below assumes Ubuntu 22.04 or 24.04 and a user who can sudo.',
    code: `sudo apt update && sudo apt upgrade -y
sudo apt install -y git python3-venv python3-pip postgresql

# Caddy, which terminates HTTPS and serves the built frontend
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \\
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \\
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy`,
  },
  {
    id: 'database',
    title: 'A database and a role',
    body: 'The schema creates itself on first boot, so nothing is imported here. All this step '
        + 'does is make somewhere for it to go. Keep the password: the next step needs it.',
    code: `DBPASS=$(python3 -c "import secrets,string; \\
  print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(28)))")

sudo -u postgres psql -c "CREATE ROLE entrystation LOGIN PASSWORD '$DBPASS';"
sudo -u postgres createdb -O entrystation entrystation

echo "Database password: $DBPASS"   # write it down now`,
  },
  {
    id: 'code',
    title: 'The code',
    body: 'Clone it wherever you like; everything below assumes /opt/entrystation.',
    code: `sudo mkdir -p /opt/entrystation && sudo chown "$USER" /opt/entrystation
git clone ${REPO} /opt/entrystation

cd /opt/entrystation
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -r backend/requirements.txt`,
    note: 'Some packages compile from source on newer Pythons and the install can take several '
        + 'minutes. That is normal; let it finish rather than interrupting it.',
  },
  {
    id: 'secrets',
    title: 'Its secrets',
    body: 'Two keys and a database URL. Generate the keys here and keep them: FERNET_KEY encrypts '
        + 'every broker session and provider API key in the database, so losing it does not lock '
        + 'you out — it makes what is already stored unreadable, permanently.',
    code: `cd /opt/entrystation/backend
python3 -c "from cryptography.fernet import Fernet; print('FERNET_KEY=' + Fernet.generate_key().decode())"
python3 -c "import secrets; print('JWT_SECRET=' + secrets.token_urlsafe(48))"

# put those two lines, and this one, in /opt/entrystation/backend/.env
# DATABASE_URL=postgresql://entrystation:YOUR_DB_PASSWORD@localhost:5432/entrystation`,
    warn: 'Back up .env somewhere that is not the server. It is the one file a rebuild cannot '
        + 'reconstruct.',
  },
  {
    id: 'service',
    title: 'Running it for good',
    body: 'A systemd unit, so it starts with the machine and comes back if it falls over. The '
        + 'edition variable is what makes this a Community install: nobody is billed, the first '
        + 'account is the owner, and the modules page is theirs.',
    code: `sudo tee /etc/systemd/system/entrystation.service <<'EOF'
[Unit]
Description=EntryStation (Community)
After=network.target postgresql.service

[Service]
Type=simple
WorkingDirectory=/opt/entrystation/backend
Environment=PATH=/opt/entrystation/.venv/bin
Environment=HOME=/opt/entrystation
Environment=ENTRYSTATION_EDITION=community
ExecStart=/opt/entrystation/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload && sudo systemctl enable --now entrystation
curl -s -o /dev/null -w "%{http_code}\\n" http://127.0.0.1:8000/docs   # expect 200`,
  },
  {
    id: 'frontend',
    title: 'The frontend',
    body: 'Built once, then served as static files. Build it on your own machine if you would '
        + 'rather not put Node on the server; the app calls /api on the same origin, so there is '
        + 'no URL to configure at build time.',
    code: `cd /opt/entrystation/frontend
npm ci && npm run build      # writes frontend/dist`,
  },
  {
    id: 'caddy',
    title: 'HTTPS, and the front door',
    body: 'Caddy serves the built files, proxies the API, and gets its own certificate over '
        + 'HTTP-01 the first time it starts. Point an A record at the server before this step, or '
        + 'the certificate cannot be issued.',
    code: `sudo tee /etc/caddy/Caddyfile <<'EOF'
yourdomain.com {
    encode zstd gzip
    root * /opt/entrystation/frontend/dist

    handle /api/* { reverse_proxy 127.0.0.1:8000 }
    handle /ws/*  { reverse_proxy 127.0.0.1:8000 }

    # Hashed build assets: a miss must be a real 404, never the SPA's HTML,
    # or a CDN will cache that HTML under a .js URL and the app stops loading.
    @asset_hit {
        path /assets/*
        file
    }
    handle @asset_hit {
        header Cache-Control "public, max-age=31536000, immutable"
        file_server
    }
    handle /assets/* {
        header Cache-Control "no-store"
        respond 404
    }

    handle {
        try_files {path} /index.html
        file_server
        header Cache-Control "no-cache"
    }
}
EOF

sudo systemctl reload caddy`,
  },
  {
    id: 'browser',
    title: 'A browser, if you are trading Exness',
    body: 'The Exness login runs a real browser, because the sign-in page is guarded by reCAPTCHA '
        + 'and a device check that raw HTTP cannot pass. Skip this step entirely if you are using '
        + 'TradeLocker or no broker yet.',
    code: `sudo apt install -y xvfb libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 \\
  libcups2t64 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \\
  libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2t64 libatspi2.0-0t64 fonts-liberation

# real Microsoft Edge
curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \\
  | sudo gpg --dearmor -o /usr/share/keyrings/microsoft-edge.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-edge.gpg] \\
https://packages.microsoft.com/repos/edge stable main" \\
  | sudo tee /etc/apt/sources.list.d/microsoft-edge.list
sudo apt update && sudo apt install -y microsoft-edge-stable

# a display for it to be headed on — headless scores worst against reCAPTCHA
sudo tee /etc/systemd/system/xvfb.service <<'EOF'
[Unit]
Description=Xvfb virtual display :99
After=network.target
[Service]
ExecStart=/usr/bin/Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp
Restart=always
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now xvfb

# and tell the app's OWN service about all three
sudo mkdir -p /etc/systemd/system/entrystation.service.d
printf '[Service]\\nEnvironment=DISPLAY=:99\\nEnvironment=EXNESS_HEADLESS=0\\nEnvironment=PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright\\n' \\
  | sudo tee /etc/systemd/system/entrystation.service.d/browser.conf
sudo systemctl daemon-reload && sudo systemctl restart entrystation`,
    warn: 'These three variables are per-SERVICE, not per-machine. Xvfb running and Edge installed '
        + 'is not enough on its own: without them on the unit the login fails and reports that the '
        + 'sign-in page had no email field.',
  },
  {
    id: 'first',
    title: 'Your account',
    body: 'Open your domain and sign up. A Community instance takes exactly one person — the first '
        + 'account created is the owner, and registration closes for good the moment it exists. If '
        + 'the box has no mail server, the verification code is handed back to the page instead of '
        + 'emailed, which is safe because that path only exists while the instance has no users.',
  },
  {
    id: 'modules',
    title: 'Modules',
    body: 'The free ones install themselves the first time the app starts, so the economic '
        + 'calendar, market news, TradeLocker, Telegram and Visuals are already there. Anything '
        + 'paid is bought from the store on entrystation.com and arrives on its own: the box asks '
        + 'what it owns, proves it is itself, and applies the licence. Modules → Check for '
        + 'purchases does the same by hand.',
  },
]

const TROUBLE = [
  { q: 'The page loads but nothing appears, or the console says a module script was served as text/html',
    a: 'A stale index.html is asking for a bundle that no longer exists, and something between you '
     + 'and the server answered with the SPA\u2019s HTML instead of a 404. The /assets rules in the '
     + 'Caddyfile above prevent it; if a CDN has already cached the bad response, a fresh build '
     + '(new hashes) is the way out.' },
  { q: 'Couldn\u2019t connect your Exness account: sign-in email field not found',
    a: 'The browser never started. Check the SERVICE, not the machine: '
     + 'tr \'\\0\' \'\\n\' < /proc/$(systemctl show -p MainPID --value entrystation)/environ '
     + 'should show DISPLAY, EXNESS_HEADLESS and PLAYWRIGHT_BROWSERS_PATH. If they are missing, the '
     + 'browser step above was skipped or the unit was not reloaded.' },
  { q: 'The backend will not start and the log mentions relation "users" does not exist',
    a: 'A module loaded before core\u2019s schema. Restart the service once; core creates its tables '
     + 'before any module is imported. If it persists, check DATABASE_URL actually points at the '
     + 'database you created.' },
  { q: 'Signup says registration is closed',
    a: 'It is, and permanently: a Community instance is single-user and an account already exists. '
     + 'Log in with it, or run backend/create_user.py on the server.' },
  { q: 'A paid module still shows as unpurchased after buying it',
    a: 'Press Check for purchases on the Modules page. The instance proves itself to the store by '
     + 'serving a challenge, so it has to be reachable over HTTPS from the internet. If it is not, '
     + 'paste the licence key from your email under Licence instead.' },
  { q: 'Caddy will not get a certificate',
    a: 'The A record has to resolve to this server and port 80 has to be open, because that is where '
     + 'the challenge arrives. dig +short yourdomain.com A should return the server\u2019s address.' },
]

// Syntax colour, without a highlighter library.
//
// A guide is mostly commands, and an unbroken wall of one colour is where a
// reader loses their place — colour here separates a comment from a command from
// the value you are meant to substitute, which is the whole job.
//
// One pass, one regex, six kinds of token. It does not parse shell and is not
// trying to: it recognises the six things that matter in these blocks and leaves
// everything else alone, which is why an unusual line degrades to plain text
// rather than to something coloured wrongly.
const TOKEN = new RegExp([
  '(#[^\\n]*)',                                        // comment
  "('[^']*'|\"[^\"]*\")",                                // string
  '(\\$\\{?[A-Za-z_]\\w*\\}?|\\$\\([^)]*\\))',              // $VAR and $(…)
  '(\\s--?[A-Za-z][\\w-]*)',                              // flag
  '\\b(sudo|apt|apt-get|curl|git|python3|pip|npm|cd|echo|printf|tee|'
  + 'systemctl|mkdir|chown|psql|createdb|gpg|source)\\b',   // command
].join('|'), 'g')

const CLASS = ['tok-c', 'tok-s', 'tok-v', 'tok-f', 'tok-k']

function highlight(code) {
  const out = []
  let last = 0, m, i = 0
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

export default function Install() {
  const [cfg, setCfg] = useState(null)
  useEffect(() => { api.appConfig().then(setCfg).catch(() => {}) }, [])
  const name = cfg?.app_name || 'EntryStation'

  // The tab, and what a search result shows. appConfig sets the title to the app
  // name for every page, so this has to be set after it and on every change.
  useEffect(() => {
    document.title = `Install ${name} Community — self-hosted installation guide`
  }, [name])

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
          {STEPS.map((s, i) => (
            <a href={'#' + s.id} key={s.id}><b>{i + 1}</b>{s.title}</a>
          ))}
          <a href="#update"><b>{STEPS.length + 1}</b>Updating</a>
          <a href="#trouble"><b>{STEPS.length + 2}</b>When it goes wrong</a>
        </aside>

        <main className="ins-main">
          <h1 className="ins-title">Install {name} Community</h1>
          <p className="ins-lede">
            From a bare Ubuntu server to a running instance. Ten steps, about twenty minutes, and
            every command is one that was actually run.
          </p>

          <div className="home-panel ins-pre">
            <h2>Before you start</h2>
            <ul className="home-ticks">
              <li><Check size={15} /> A VPS running Ubuntu 22.04 or 24.04, with 2 cores and 4GB</li>
              <li><Check size={15} /> A domain, with an A record already pointing at it</li>
              <li><Check size={15} /> Ports 80 and 443 open, which is how the certificate is issued</li>
              <li><Check size={15} /> An API key from OpenAI, Anthropic, DeepSeek or any of the others</li>
            </ul>
          </div>

          {STEPS.map((s, i) => (
            <section className="ins-step" id={s.id} key={s.id}>
              <h2><span className="ins-n">{i + 1}</span>{s.title}</h2>
              <p>{s.body}</p>
              {s.code && <Code>{s.code}</Code>}
              {s.note && <p className="ins-note">{s.note}</p>}
              {s.warn && (
                <p className="ins-warn"><AlertTriangle size={15} /><span>{s.warn}</span></p>
              )}
            </section>
          ))}

          <section className="ins-step" id="update">
            <h2><span className="ins-n">{STEPS.length + 1}</span>Updating</h2>
            <p>
              Pull, reinstall anything new, rebuild the frontend, restart. The schema migrates
              itself on the way up, so there is no separate migration step to remember.
            </p>
            <Code>{`cd /opt/entrystation
git pull
. .venv/bin/activate && pip install -r backend/requirements.txt
cd frontend && npm ci && npm run build
sudo systemctl restart entrystation`}</Code>
          </section>

          <section className="ins-step" id="trouble">
            <h2><span className="ins-n">{STEPS.length + 2}</span>When it goes wrong</h2>
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
              The service log answers most things first:{' '}
              <code>journalctl -u entrystation -n 100 --no-pager</code>
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
