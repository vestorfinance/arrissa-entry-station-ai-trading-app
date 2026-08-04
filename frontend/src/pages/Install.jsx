import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Check, Copy, AlertTriangle, Laptop, Server, ExternalLink, Apple } from 'lucide-react'
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

// ── Windows ───────────────────────────────────────────────────────────────────
const WINDOWS = [
  {
    id: 'w-docker',
    title: 'Install Docker Desktop',
    body: 'Docker Desktop runs the whole stack: the app, its database, the web server, and the '
        + 'browser it logs into your broker with. You install this one thing and nothing else.',
    link: { label: 'Download Docker Desktop for Windows', url: 'https://www.docker.com/products/docker-desktop/' },
    note: 'Windows 10 or 11, 64-bit. The installer turns on WSL 2 for you. If it complains about '
        + 'virtualisation, it needs to be enabled in your BIOS — restart, enter setup, and look '
        + 'for Intel VT-x or AMD-V.',
    warn: 'Restart the machine after installing, then open Docker Desktop once and wait for the '
        + 'whale icon to stop animating. Nothing below works until it says Engine running.',
  },
  {
    id: 'w-check',
    title: 'Check it works',
    body: 'In PowerShell. Both lines should print a version. If they do not, Docker Desktop is '
        + 'not running yet.',
    code: 'docker --version\ndocker compose version',
  },
  {
    id: 'w-code',
    title: 'Get the code',
    body: 'Git for Windows if you do not have it, then one clone. This is the public edition: '
        + 'core, the free modules, and the store client that fetches anything you buy.',
    link: { label: 'Download Git for Windows', url: 'https://git-scm.com/download/win' },
    code: `git clone ${REPO} $env:USERPROFILE\\entrystation
cd $env:USERPROFILE\\entrystation`,
  },
  {
    id: 'w-env',
    title: 'Settings and keys',
    body: 'Three secrets, generated on your machine and never leaving it. Run this in PowerShell '
        + 'from the folder you just cloned into.',
    code: `Copy-Item .env.docker.example .env

$fernet = python -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
$jwt    = python -c "import secrets; print(secrets.token_urlsafe(48))"
$dbpass = python -c "import secrets; print(secrets.token_urlsafe(24))"

Add-Content .env "DOMAIN=:80"
Add-Content .env "HTTP_PORT=127.0.0.1:8477"
Add-Content .env "HTTPS_PORT=127.0.0.1:8443"
Add-Content .env "FERNET_KEY=$fernet"
Add-Content .env "JWT_SECRET=$jwt"
Add-Content .env "DB_PASSWORD=$dbpass"`,
    note: 'No Python? Install it from python.org, or open .env in Notepad and paste any three '
        + 'long random strings of your own. They only have to be secret, not special.',
    warn: 'Back up .env. Losing FERNET_KEY makes every stored broker session and API key '
        + 'permanently unreadable. There is no recovery, by design.',
  },
  {
    id: 'w-run',
    title: 'Start it',
    body: 'The first build takes several minutes, most of it downloading the browser. After that '
        + 'it starts in seconds.',
    code: 'docker compose up -d --build\ndocker compose logs -f app',
    note: 'Wait for "Application startup complete", then press Ctrl-C. That stops watching the '
        + 'logs, not the app.',
  },
  {
    id: 'w-open',
    title: 'Open it',
    body: 'Go to http://localhost:8477. The first account you create is the owner, and '
        + 'registration closes behind it: a Community instance is single-user, so nobody can sign '
        + 'up on your machine afterwards.',
    note: '8477 rather than the usual port 80, so it cannot collide with anything else you run. '
        + 'It answers on this machine only.',
  },
]

// ── macOS ─────────────────────────────────────────────────────────────────────
const MACOS = [
  {
    id: 'm-docker',
    title: 'Install Docker Desktop',
    body: 'Docker Desktop runs the whole stack: the app, its database, the web server, and the '
        + 'browser it logs into your broker with. You install this one thing and nothing else.',
    link: { label: 'Download Docker Desktop for Mac', url: 'https://www.docker.com/products/docker-desktop/' },
    note: 'Pick the right build. Apple menu → About This Mac: if it says Apple M1/M2/M3/M4, take '
        + 'Apple Silicon; if it says Intel, take Intel. The wrong one will not run.',
    warn: 'Open Docker Desktop once after installing and leave it running. Nothing below works '
        + 'until the whale icon in the menu bar stops animating.',
  },
  {
    id: 'm-check',
    title: 'Check it works',
    body: 'In Terminal. Both lines should print a version.',
    code: 'docker --version\ndocker compose version',
  },
  {
    id: 'm-code',
    title: 'Get the code',
    body: 'macOS asks to install the developer tools the first time you run git. Say yes; it is a '
        + 'one-time prompt.',
    code: `git clone ${REPO} ${DIR}\ncd ${DIR}`,
  },
  {
    id: 'm-env',
    title: 'Settings and keys',
    body: 'Three secrets, generated on your machine and never leaving it. Paste the whole block '
        + 'at once — it is one command.',
    code: `cp .env.docker.example .env

cat >> .env <<EOF
DOMAIN=:80
HTTP_PORT=127.0.0.1:8477
HTTPS_PORT=127.0.0.1:8443
DOCKER_PLATFORM=linux/arm64
FERNET_KEY=$(python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())")
JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
DB_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
EOF`,
    note: 'The DOCKER_PLATFORM line is for Apple Silicon. On an Intel Mac, delete it.',
    warn: 'Back up .env. Losing FERNET_KEY makes every stored broker session and API key '
        + 'permanently unreadable. There is no recovery, by design.',
  },
  {
    id: 'm-run',
    title: 'Start it',
    body: 'The first build takes several minutes, most of it downloading the browser. After that '
        + 'it starts in seconds.',
    code: 'docker compose up -d --build\ndocker compose logs -f app',
    note: 'Wait for "Application startup complete", then press Ctrl-C. That stops watching the '
        + 'logs, not the app.',
  },
  {
    id: 'm-open',
    title: 'Open it',
    body: 'Go to http://localhost:8477. The first account you create is the owner, and '
        + 'registration closes behind it: a Community instance is single-user, so nobody can sign '
        + 'up on your machine afterwards.',
    note: '8477 rather than the usual port 80, so it cannot collide with anything else you run. '
        + 'It answers on this machine only.',
  },
]

const TROUBLE = [
  {
    q: 'docker: command not found',
    a: 'Docker Desktop is installed but not running, or the terminal was open before you '
       + 'installed it. Start Docker Desktop, wait for the whale to stop animating, then close '
       + 'and reopen your terminal. On a server, log out and back in after the usermod line.',
  },
  {
    q: 'Port 80 is already allocated',
    a: 'Something else on the machine is already serving the web. On a server that is usually '
       + 'Apache or nginx: `sudo systemctl disable --now apache2 nginx`. On Windows it is often '
       + 'IIS or Skype. Nothing else may hold 80 or 443, because that is where the certificate '
       + 'check and the site both arrive.',
  },
  {
    q: 'The site will not load, or the certificate fails',
    a: 'Three things in order. Does the domain point at the server — `dig +short yourdomain.com` '
       + 'should print its IP. Are 80 and 443 open — `sudo ufw status`. And is it behind '
       + 'Cloudflare with the orange cloud on? Turn it to DNS only for the first start, and if '
       + 'you turn it back on set SSL/TLS to Full, never Flexible.',
  },
  {
    q: 'It redirects forever, or says too many redirects',
    a: 'Cloudflare SSL/TLS is set to Flexible. Flexible tells Cloudflare to talk to your server '
       + 'over plain http while the app is redirecting everything to https, so the two send each '
       + 'other in circles. Set it to Full.',
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
    q: 'Remove it completely, and everything it holds',
    danger: true,
    a: 'This deletes the database, every account and setting, the modules you installed including '
       + 'ones you paid for, your broker session, the browser profile and the images. None of it '
       + 'is recoverable and nothing asks twice. Purchases are not lost, because a licence lives '
       + 'on the store, but the installation id does: a reinstall is a NEW installation and '
       + 'anything bought will need re-binding to it.',
    before: 'If there is any chance you want it back, take these first. The dump is your data; '
          + '.env holds FERNET_KEY, without which a restored dump is unreadable.',
    backup: `cd ~/entrystation
docker compose exec -T db pg_dump -U entrystation entrystation > ~/entrystation-backup.sql
cp .env ~/entrystation-env-backup`,
    code: `cd ~/entrystation

# containers, networks, and the volumes: database, modules, browser session
docker compose down -v --remove-orphans

# the code, and .env with it
cd ~ && rm -rf ~/entrystation

# the images it built and the build cache, which is most of the disk
docker image prune -af
docker builder prune -af`,
    after: 'On a server, also remove its block from /etc/caddy/Caddyfile and reload '
         + '(sudo systemctl reload caddy), or Caddy keeps answering for a domain that now '
         + 'proxies to nothing.',
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
  // Default to what they are actually using. Asking somebody on a Mac to pick
  // "macOS" from three buttons is a question with an answer already on screen.
  const [where, setWhere] = useState(() => {
    const p = (navigator.userAgent || '').toLowerCase()
    if (p.includes('win')) return 'windows'
    if (p.includes('mac')) return 'macos'
    return 'vps'
  })
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

  // Two shapes of server, and they are not a detail. A box that already serves
  // other sites cannot hand over 80 and 443, and a guide that assumes it can
  // fails at the last step with an error that only mentions a port number.
  // The Ubuntu path, in full, with nothing assumed.
  //
  // Caddy runs ON THE MACHINE and the app runs on a port of its own behind it.
  // That is not the only arrangement that works — the stack ships a Caddy of its
  // own and can take 80 and 443 directly — but it is the one to teach, because
  // it is the one that survives contact with a real server. Adding a second site
  // later, or arriving at a box that already has one, does not require undoing
  // anything.
  const VPS = useMemo(() => [
    {
      id: 'v-vps',
      title: 'Get a server',
      body: 'Any provider will do: Hetzner, DigitalOcean, Vultr, Contabo. Ask for Ubuntu 22.04 or '
          + '24.04, 2 vCPU and 4GB of memory. When it is built you are given an IP address, a '
          + 'username (usually root) and a password or an SSH key.',
      note: '4GB is not padding. The app logs into your broker with a real browser, and a browser '
          + 'is the heaviest thing on the box.',
    },
    {
      id: 'v-dns',
      title: 'Point your domain at it',
      body: `Wherever you bought ${D}, open its DNS settings and add one record. Type A, name @ `
          + `for the bare domain (or the subdomain you want), value ${H}. That is all a domain is `
          + 'here: a name that answers with your server’s address.',
      code: `# run this on your OWN machine, not the server
dig +short ${D}
# it should print ${H}`,
      note: 'Give it a few minutes. Do this before starting anything: the HTTPS certificate is '
          + 'issued by proving you control the domain, and that check is made against DNS.',
      warn: `On Cloudflare set the record to DNS only — the grey cloud — until the site `
          + 'is up. The orange cloud proxies the certificate check and it can fail. Afterwards you '
          + 'may switch it on, but set SSL/TLS to Full: Flexible makes Cloudflare talk http to a '
          + 'server that redirects to https, and the two loop forever.',
    },
    {
      id: 'v-ssh',
      title: 'Log in to the server',
      body: 'Terminal on Mac or Linux, PowerShell on Windows — both have ssh built in. '
          + 'Everything after this runs on the server.',
      code: `ssh ${U}@${H}`,
      note: 'The first time, it asks whether you trust the host. Type yes.',
    },
    {
      id: 'v-docker',
      title: 'Install Docker',
      body: 'Docker’s own installer, the one their documentation gives. Compose comes with '
          + 'it. The middle lines let you run docker without typing sudo every time.',
      code: `curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ${U}
sudo systemctl enable --now docker
newgrp docker

docker --version
docker compose version`,
      note: 'Both version lines must print. If they do not, log out and back in and try again.',
    },
    {
      id: 'v-caddy',
      title: 'Install Caddy',
      body: 'Caddy is the web server that sits in front. It answers on 80 and 443, gets a free '
          + 'certificate for your domain, renews it on its own for as long as it runs, and passes '
          + 'requests through to the app. It is installed on the machine rather than in the '
          + 'stack, so the same Caddy can serve this and anything else you put here later.',
      code: `sudo apt update
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl

curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list

sudo apt update
sudo apt install -y caddy
caddy version`,
      note: 'Already have nginx or Apache on this server? Keep it. Skip this step and write the '
          + 'equivalent reverse proxy in that instead — step 8 shows what it has to do.',
    },
    {
      id: 'v-ports',
      title: 'Open the firewall',
      body: 'Caddy needs 80 and 443. The app itself needs nothing open: it listens only on the '
          + 'machine’s own address, and Caddy reaches it from inside.',
      code: `sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
sudo ufw status`,
      warn: 'Do not open 8477. If it were reachable from outside, the app would be answering the '
          + 'internet directly, unencrypted, with the proxy bypassed entirely.',
    },
    {
      id: 'v-code',
      title: 'Get the code',
      body: 'The public edition: core, the free modules, and the store client that fetches '
          + 'anything you buy.',
      code: `git clone ${REPO} ~/entrystation\ncd ~/entrystation`,
    },
    {
      id: 'v-env',
      title: 'Settings and keys',
      body: 'The app takes port 8477 instead of 80, bound to the machine itself so nothing '
          + 'outside can reach it. DOMAIN=:80 tells the stack’s own Caddy to serve plain '
          + 'http internally and not to go looking for a certificate — the Caddy you just '
          + 'installed does that part.',
      code: `cp .env.docker.example .env

cat >> .env <<EOF
DOMAIN=:80
HTTP_PORT=127.0.0.1:8477
HTTPS_PORT=127.0.0.1:8443
DOCKER_PLATFORM=
FERNET_KEY=$(python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())")
JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
DB_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
EOF

cat .env`,
      note: '8477 rather than 3000 or 8080 on purpose: those are the first ports anything else on '
          + 'the box will already have taken. Leave DOCKER_PLATFORM empty on a server — it '
          + 'defaults to amd64, which is what Microsoft publishes Edge for, and Edge is what the '
          + 'broker login uses.',
      warn: 'Copy .env somewhere off the server. Losing FERNET_KEY makes every stored broker '
          + 'session and API key permanently unreadable.',
    },
    {
      id: 'v-caddyfile',
      title: 'Tell Caddy about your domain',
      body: `One site block: serve ${D}, get its certificate, and pass everything to the app on `
          + '8477. The headers are the ordinary ones — they stop the site being framed by '
          + 'another, and stop a browser guessing at content types.',
      code: `sudo tee -a /etc/caddy/Caddyfile <<'EOF'

${D} {
    encode zstd gzip

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options    "nosniff"
        X-Frame-Options           "SAMEORIGIN"
        -Server
    }

    reverse_proxy 127.0.0.1:8477
}
EOF

sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy`,
      note: 'The blank line before the block matters. Without it Caddy reads it as part of '
          + 'whatever site block came before it. `caddy validate` tells you before a reload does.',
      warn: 'tee -a, with the -a. Without it you replace the whole Caddyfile, and every other '
          + 'site on this server stops answering.',
    },
    {
      id: 'v-run',
      title: 'Start it',
      body: 'The first build takes several minutes, most of it downloading the browser. After '
          + 'that it starts in seconds.',
      code: `docker compose up -d --build
docker compose logs -f app`,
      note: 'Wait for "Application startup complete", then press Ctrl-C — that stops '
          + 'watching the log, not the app.',
    },
    {
      id: 'v-check',
      title: 'Check the chain',
      body: 'Three links: the app answers locally, Caddy is running, and the domain comes back '
          + 'through it. Test them in that order and whichever fails is where the problem is.',
      code: `docker compose ps
curl -s -o /dev/null -w "app  %{http_code}\n" http://127.0.0.1:8477
systemctl is-active caddy
curl -s -o /dev/null -w "site %{http_code}\n" https://${D}`,
      note: 'app 200 and site 200 means it is done. app 200 with a failing site is Caddy or DNS; '
          + 'app failing is the stack, and `docker compose logs app` will say why.',
    },
    {
      id: 'v-open',
      title: 'Open it',
      body: `Go to https://${D}. The first account you create is the owner, and registration `
          + 'closes behind it: a Community instance is single-user, so nobody can sign up on your '
          + 'server afterwards.',
    },
  ], [H, U, D])

  const steps = where === 'windows' ? WINDOWS : where === 'macos' ? MACOS : VPS
  const after = steps.length
  const dir = where === 'windows' ? '$env:USERPROFILE\\entrystation' : '~/entrystation'

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
            <button className={'ins-where-btn' + (where === 'windows' ? ' ins-where-btn--on' : '')}
                    onClick={() => setWhere('windows')}>
              <Laptop size={17} strokeWidth={1.9} />
              <b>Windows</b>
              <span>On your own PC. No domain needed.</span>
            </button>
            <button className={'ins-where-btn' + (where === 'macos' ? ' ins-where-btn--on' : '')}
                    onClick={() => setWhere('macos')}>
              <Apple size={17} strokeWidth={1.9} />
              <b>macOS</b>
              <span>On your own Mac. No domain needed.</span>
            </button>
            <button className={'ins-where-btn' + (where === 'vps' ? ' ins-where-btn--on' : '')}
                    onClick={() => setWhere('vps')}>
              <Server size={17} strokeWidth={1.9} />
              <b>Ubuntu VPS</b>
              <span>Your own domain, HTTPS, running around the clock.</span>
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
                <li><Check size={15} /> {where === 'windows'
                  ? 'Windows 10 or 11, 64-bit, with 8GB of memory'
                  : 'macOS 13 or later, with 8GB of memory'}</li>
                <li><Check size={15} /> About 6GB of free disk, most of it the browser</li>
                <li><Check size={15} /> An API key from OpenAI, Anthropic, DeepSeek or any other
                  provider. You can add it after installing.</li>
                <li><Check size={15} /> No domain, no certificate, no server. It runs at
                  http://localhost:8477 on this machine, on a port nothing else uses.</li>
              </ul>
            </div>
          )}

          {where === 'vps' && (
            <div className="ins-callout">
              <b>How the pieces fit</b>
              <p>
                Caddy sits at the front of the server on ports 80 and 443. It gets the HTTPS
                certificate for your domain and renews it on its own. Behind it, EntryStation runs
                on port <code>8477</code>, bound to the machine itself so nothing from outside can
                reach it directly. Caddy passes requests through. That is what a reverse proxy is,
                and it is why the same server can serve this and other sites without either
                knowing about the other.
              </p>
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
                <details key={t.q} className={t.danger ? 'ins-faq-danger' : undefined}>
                  <summary>{t.q}</summary>
                  <p>{t.a}</p>
                  {/* Answers that end in a command get the same copyable block
                      as the steps do. Retyping a command out of a paragraph is
                      how a flag gets dropped, and on the destructive one that
                      is the difference between removing a container and
                      removing a database. */}
                  {t.before && <p className="ins-note">{t.before}</p>}
                  {t.backup && <Code>{t.backup}</Code>}
                  {t.code && <Code>{t.code}</Code>}
                  {t.after && <p className="ins-note">{t.after}</p>}
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
