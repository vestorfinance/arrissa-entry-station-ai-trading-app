-- Arrissa Exness API — database schema

CREATE TABLE IF NOT EXISTS users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- signup profile fields (added after launch)
ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_name  TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone      TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS country    TEXT;   -- ISO-2 country code


-- Email-verified signups in progress (before the user record is created).
CREATE TABLE IF NOT EXISTS signups (
    email      TEXT PRIMARY KEY,
    code       TEXT NOT NULL,                    -- 6-digit verification code
    verified   BOOLEAN NOT NULL DEFAULT false,
    attempts   INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    key_prefix TEXT NOT NULL,          -- e.g. "ak_live_" + first chars, shown in UI
    last_four  TEXT NOT NULL,          -- last 4 chars, shown in UI
    key_hash   TEXT NOT NULL,          -- sha256 of the full key (used for auth lookup)
    key_plain  TEXT,                   -- full key, kept so the API guide can pre-fill it
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);

-- Time-based market orders: a promise our server executes at run_at.
CREATE TABLE IF NOT EXISTS scheduled_orders (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID REFERENCES users(id) ON DELETE CASCADE,
    symbol      TEXT NOT NULL,
    side        TEXT NOT NULL,               -- buy | sell
    volume      DOUBLE PRECISION NOT NULL DEFAULT 0.1,
    sl          DOUBLE PRECISION NOT NULL DEFAULT 0,
    tp          DOUBLE PRECISION NOT NULL DEFAULT 0,
    sl_points   INTEGER,
    tp_points   INTEGER,
    deviation   INTEGER NOT NULL DEFAULT 0,
    run_at      TIMESTAMPTZ NOT NULL,
    status      TEXT NOT NULL DEFAULT 'scheduled',  -- scheduled|executing|executed|failed|cancelled
    account     BIGINT,                             -- which account to execute on
    result      JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    executed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_sched_due ON scheduled_orders(status, run_at);

-- Per-user Exness credentials (password Fernet-encrypted) + account selection.
CREATE TABLE IF NOT EXISTS exness_settings (
    user_id             UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    exness_email        TEXT,
    exness_password_enc TEXT,               -- Fernet-encrypted, never returned
    selected_accounts   JSONB NOT NULL DEFAULT '[]',
    auto_connect_future BOOLEAN NOT NULL DEFAULT false,
    active_account      BIGINT,                 -- the account API actions target
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Which broker the user's ACTIVE account belongs to. `active_account` continues
-- to hold the account ref for whichever broker (Exness MT5 number OR TradeLocker
-- accountId — both numeric). Together they name the active (broker, account).
ALTER TABLE exness_settings ADD COLUMN IF NOT EXISTS active_broker TEXT NOT NULL DEFAULT 'exness';
-- Which accounts the user has made AVAILABLE to this app, per broker:
--   {"exness": ["63791908"], "tradelocker": ["1679604"]}
-- Distinct from `active_account`, which is the one it acts on right now. A
-- broker with no entry here has not been chosen for yet, and everything it owns
-- is available — a user who has never opened the page is not opting out.
ALTER TABLE exness_settings ADD COLUMN IF NOT EXISTS available_accounts JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Scheduled work carries its broker so the executor uses the right adapter even
-- if the user later switches their active broker.
ALTER TABLE scheduled_orders  ADD COLUMN IF NOT EXISTS broker TEXT NOT NULL DEFAULT 'exness';
-- (scheduled_actions gets the same column, below — after the table exists.)


-- Per-user AI provider keys (Fernet-encrypted) + which models are enabled.
CREATE TABLE IF NOT EXISTS ai_settings (
    user_id          UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    openai_key_enc   TEXT,
    anthropic_key_enc TEXT,
    deepseek_key_enc TEXT,
    selected_models  JSONB NOT NULL DEFAULT '[]',   -- [{provider, model}]
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Saved chat conversations (ChatGPT-style history). Full turn objects in messages.
CREATE TABLE IF NOT EXISTS chats (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT NOT NULL DEFAULT 'New chat',
    messages   JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chats_user ON chats(user_id, updated_at DESC);

-- Per-user long-term memory (MEMORY.md) — durable notes about the user the agent
-- maintains and the user can view/edit. NOT chat transcript context.
CREATE TABLE IF NOT EXISTS user_memory (
    user_id    UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    content    TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- CME FedWatch: one row per poll (every 60–120s) of market-implied Fed rate
-- probabilities. `fetched_at` is OUR poll time — freshness/staleness is judged
-- on it; `as_of` is CME's own "Data as of" stamp, which moves far more slowly.
CREATE TABLE IF NOT EXISTS fedwatch_snapshots (
    id             BIGSERIAL PRIMARY KEY,
    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    as_of          TEXT,                  -- CME stamp as printed, e.g. "23 Jul 2026 04:02:38" (CT)
    as_of_ts       TIMESTAMPTZ,           -- the same stamp parsed (America/Chicago)
    current_rate   TEXT,                  -- current target rate, bps band e.g. "350-375"
    next_meeting   TEXT,                  -- nearest FOMC meeting, e.g. "29 Jul26"
    ease           DOUBLE PRECISION,      -- % probability of a cut at that meeting
    no_change      DOUBLE PRECISION,
    hike           DOUBLE PRECISION,
    data           JSONB NOT NULL         -- the full parsed payload (distribution, meetings, …)
);
CREATE INDEX IF NOT EXISTS idx_fedwatch_fetched ON fedwatch_snapshots(fetched_at DESC);

-- Economic calendar: HIGH-impact releases only, keyed by a hash of the event
-- name + currency + scheduled time, so re-scraping the same occurrence updates
-- it (actual/forecast/previous arrive later) instead of duplicating it.
CREATE TABLE IF NOT EXISTS calendar_events (
    event_key   TEXT PRIMARY KEY,       -- sha256(event|currency|event_time)[:32]
    source_id   TEXT,                   -- Investing.com row id (can change between scrapes)
    event       TEXT NOT NULL,
    currency    TEXT,
    country     TEXT,
    event_time  TIMESTAMPTZ NOT NULL,   -- scheduled release time (UTC from the widget)
    impact      TEXT,
    actual      TEXT,                   -- null until the number prints
    forecast    TEXT,
    previous    TEXT,
    instruments TEXT[] NOT NULL DEFAULT '{}',   -- what this release moves
    first_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at TIMESTAMPTZ             -- when we first saw an actual
);
CREATE INDEX IF NOT EXISTS idx_cal_time ON calendar_events(event_time);
CREATE INDEX IF NOT EXISTS idx_cal_ccy ON calendar_events(currency, event_time);
CREATE INDEX IF NOT EXISTS idx_cal_src ON calendar_events(source_id);
CREATE INDEX IF NOT EXISTS idx_cal_instr ON calendar_events USING GIN (instruments);


-- Versatile scheduled actions: ANY trading action run at a future time.
CREATE TABLE IF NOT EXISTS scheduled_actions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID REFERENCES users(id) ON DELETE CASCADE,
    action      TEXT NOT NULL,                 -- close | place_order | break_even | ...
    params      JSONB NOT NULL DEFAULT '{}',   -- args for that action (minus account)
    account     BIGINT,
    run_at      TIMESTAMPTZ NOT NULL,
    status      TEXT NOT NULL DEFAULT 'scheduled',  -- scheduled|executing|executed|failed|cancelled
    result      JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    executed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_sched_act_due ON scheduled_actions(status, run_at);
ALTER TABLE scheduled_actions ADD COLUMN IF NOT EXISTS broker TEXT NOT NULL DEFAULT 'exness';

-- Analysis agents: user-built flow graphs (nodes + edges) that the main chat
-- agent can call as tools. Each node carries free text describing what it should
-- do; the engine turns that into analysis-API calls at run time. `flow` is the
-- React Flow graph {nodes, edges}. status active ⇒ exposed to the chat agent.
CREATE TABLE IF NOT EXISTS analysis_agents (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'draft',          -- draft | active | paused
    flow        JSONB NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_analysis_agents_user ON analysis_agents(user_id, updated_at DESC);

-- App-level admin settings (single row). Holds the OpenAI API key used for
-- Whisper voice transcription so it doesn't depend on any one user's key.
CREATE TABLE IF NOT EXISTS admin_settings (
    id             INTEGER PRIMARY KEY DEFAULT 1,
    openai_key_enc TEXT,                              -- Fernet-encrypted
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT admin_settings_singleton CHECK (id = 1)
);
-- SMTP (outgoing email) settings live on the same admin row.
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS smtp_host     TEXT;
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS smtp_port     INTEGER;
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS smtp_user     TEXT;
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS smtp_pass_enc TEXT;   -- Fernet-encrypted
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS smtp_from     TEXT;
-- TradeLocker developer/partner API key — app-level (one key for the whole app),
-- used for the BrandSocket stream + higher Trade-API rate limits. Fernet-encrypted.
-- App-level AI provider keys (Fernet-encrypted). The whole app runs on THESE keys —
-- there is no bring-your-own-key. arrissa-chat → deepseek, arrissa-pro → openai.
-- Take entitled module updates without being asked. On unless turned off: the
-- instances that most need a fix are the unattended ones.
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS auto_update BOOLEAN DEFAULT TRUE;
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS deepseek_key_enc  TEXT;
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS anthropic_key_enc TEXT;
-- Paystack (payments): both test + live keys (secrets Fernet-encrypted, public keys
-- are public so plaintext) plus which environment is currently live-serving.
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS paystack_mode            TEXT NOT NULL DEFAULT 'test';  -- test | live
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS paystack_test_secret_enc TEXT;
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS paystack_test_public     TEXT;
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS paystack_live_secret_enc TEXT;
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS paystack_live_public     TEXT;
-- Private invite code: while registration is invite-only, a link carrying this code
-- (/signup?invite=CODE) is the only way to reach + complete signup.
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS signup_invite_code TEXT;
-- App-level settings the admin manages from the panel.
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS app_name          TEXT;      -- brand name (NULL → 'EntryStation')
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS registrations_open BOOLEAN;  -- NULL → the code default (invite-only)

-- Per-user chat preferences that must persist across reloads/devices.
CREATE TABLE IF NOT EXISTS user_prefs (
    user_id       UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    chat_model    TEXT,                          -- "provider:model" last used
    chat_accounts JSONB NOT NULL DEFAULT '[]',   -- account numbers selected in chat
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- (legacy) early single-value risk defaults — superseded by the risk_settings
-- table below. Kept so old rows don't error; no longer read by the engine.
ALTER TABLE user_prefs ADD COLUMN IF NOT EXISTS default_risk_pct    REAL;
ALTER TABLE user_prefs ADD COLUMN IF NOT EXISTS default_risk_money  REAL;
ALTER TABLE user_prefs ADD COLUMN IF NOT EXISTS risk_basis          TEXT NOT NULL DEFAULT 'equity';
-- The user's own standing instructions to the chat agent, APPENDED to the
-- built-in prompt. There is deliberately no way to replace that prompt: it is
-- what teaches the assistant when to reach for each tool and the exact format
-- the app renders as a trade card.
ALTER TABLE user_prefs ADD COLUMN IF NOT EXISTS agent_instructions   TEXT;
ALTER TABLE user_prefs ADD COLUMN IF NOT EXISTS default_trade_style TEXT NOT NULL DEFAULT 'intraday';

-- Per-user risk-management parameters. One row per SCOPE: account = '' is the
-- profile-wide default; account = a broker account number/id is that account's
-- override (any non-null field overrides the profile). The SL/TP engine, the chat
-- agent and the risk-status check all read these; a trade with no explicit risk
-- falls back to the resolved risk_pct (and only to 2% if nothing at all is set).
CREATE TABLE IF NOT EXISTS risk_settings (
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    account       TEXT NOT NULL DEFAULT '',      -- '' = profile default; else account number/id
    risk_pct      REAL,                          -- risk % of the account per trade
    reward_rr     REAL,                          -- reward : risk per trade (2 = 2R)
    max_dd_day    REAL,                          -- max drawdown per day, %
    max_dd_week   REAL,                          -- max drawdown per week, %
    max_dd_month  REAL,                          -- max drawdown per month, %
    trading_hours JSONB NOT NULL DEFAULT '[]',   -- [{"start":"10:00","end":"12:00"}, …]
    trading_tz    TEXT NOT NULL DEFAULT 'UTC',   -- IANA tz the hours are stated in
    risk_basis    TEXT NOT NULL DEFAULT 'equity',-- equity | balance
    trade_style   TEXT,                          -- scalp | intraday | swing | position
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, account)
);


-- Analysis-agent execution history: one row per run of an agent's flow, capturing
-- every node's reasoning/result and any opinion it formed, so a run can be replayed
-- and audited. Trimmed to the most recent runs per agent by the writer.
CREATE TABLE IF NOT EXISTS analysis_runs (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id   UUID NOT NULL REFERENCES analysis_agents(id) ON DELETE CASCADE,
    user_id    UUID REFERENCES users(id) ON DELETE CASCADE,
    request    TEXT,                          -- the input the agent was run on
    response   TEXT,                          -- the final answer
    trace      JSONB NOT NULL DEFAULT '[]',   -- [{node,kind,name,text,result,opinion}, …]
    steps      INTEGER,
    status     TEXT NOT NULL DEFAULT 'ok',    -- ok | error
    error      TEXT,
    source     TEXT,                          -- 'test' | 'chat'
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_analysis_runs ON analysis_runs (agent_id, created_at DESC);
-- LLM token usage + estimated cost per run.
ALTER TABLE analysis_runs ADD COLUMN IF NOT EXISTS tokens_in        INTEGER;
ALTER TABLE analysis_runs ADD COLUMN IF NOT EXISTS tokens_out       INTEGER;
ALTER TABLE analysis_runs ADD COLUMN IF NOT EXISTS tokens_cache_hit INTEGER;
ALTER TABLE analysis_runs ADD COLUMN IF NOT EXISTS llm_calls        INTEGER;
ALTER TABLE analysis_runs ADD COLUMN IF NOT EXISTS usage_model      TEXT;
ALTER TABLE analysis_runs ADD COLUMN IF NOT EXISTS cost_usd         NUMERIC(12,6);

-- Scheduled agents ("Trigger on Intervals" node). The SCHEDULE ITSELF lives in
-- the flow, where the user set it — this table holds only what the flow cannot
-- know: when it last ran, when it is next due, and how it went. Deleting the
-- agent drops it; editing the node's interval simply changes what is read next
-- tick, so the two can never disagree.
CREATE TABLE IF NOT EXISTS analysis_schedules (
    agent_id    UUID PRIMARY KEY REFERENCES analysis_agents(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    spec        TEXT NOT NULL DEFAULT '',      -- the schedule as read, for the log
    next_run_at TIMESTAMPTZ,
    last_run_at TIMESTAMPTZ,
    last_status TEXT,                          -- ok | error | skipped
    last_error  TEXT,
    runs        INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_analysis_schedules_due ON analysis_schedules (next_run_at);

-- ── Billing: plans, credits, (simulated) Paystack transactions ─────────────────
-- One row per user holding their current plan + subscription status. plan NULL /
-- status 'inactive' = unsubscribed → view-only, 0 credits (no free tier). Credits
-- are NOT stored here; the balance is SUM(delta) over credit_ledger.
CREATE TABLE IF NOT EXISTS user_billing (
    user_id    UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    plan       TEXT,                               -- trader|pro|max|elite ; NULL = unsubscribed
    status     TEXT NOT NULL DEFAULT 'inactive',   -- active | inactive | cancelled
    interval   TEXT NOT NULL DEFAULT 'monthly',    -- monthly | annual
    renews_at  TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only credit ledger. Balance = SUM(delta). delta > 0 grant (subscription,
-- topup, adjust), delta < 0 spend (chat, analysis, voice) or reset (cancel).
CREATE TABLE IF NOT EXISTS credit_ledger (
    id         BIGSERIAL PRIMARY KEY,
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    delta      INTEGER NOT NULL,
    reason     TEXT NOT NULL,                       -- subscription|topup|chat|analysis|voice|cancel|adjust
    ref        TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_credit_ledger_user ON credit_ledger(user_id, created_at DESC);

-- (Simulated) Paystack transactions. A 'checkout' creates a pending row; the
-- 'simulate' step completes it success|declined — standing in for the Paystack
-- callback we'll wire later. reference is the Paystack-style idempotency key.
CREATE TABLE IF NOT EXISTS billing_transactions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reference    TEXT UNIQUE NOT NULL,
    kind         TEXT NOT NULL,                     -- subscription | topup
    plan         TEXT,                              -- subscription: trader|pro|max|elite
    pack         TEXT,                              -- topup: boost|power|bulk
    interval     TEXT,                              -- monthly | annual
    amount_usd   NUMERIC(12,2),
    credits      INTEGER,
    status       TEXT NOT NULL DEFAULT 'pending',   -- pending | success | declined
    provider     TEXT NOT NULL DEFAULT 'paystack',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_billing_tx_user ON billing_transactions(user_id, created_at DESC);
ALTER TABLE billing_transactions ADD COLUMN IF NOT EXISTS amount_zar INTEGER;   -- charge in Rands (major unit)

-- The Paystack plan_code for each of our plans × interval × environment. Populated
-- by the admin "create/sync plans" action (calls the Paystack Plan API).
CREATE TABLE IF NOT EXISTS paystack_plans (
    mode       TEXT NOT NULL,                 -- test | live
    plan_key   TEXT NOT NULL,                 -- trader|pro|max|elite
    interval   TEXT NOT NULL,                 -- monthly | annual
    plan_code  TEXT NOT NULL,                 -- Paystack PLN_...
    amount_zar INTEGER NOT NULL,              -- charge in Rands (major unit)
    name       TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (mode, plan_key, interval)
);

-- Paystack customer/subscription identifiers so a cancel can disable the recurring
-- subscription on Paystack (not only downgrade locally).
ALTER TABLE user_billing ADD COLUMN IF NOT EXISTS paystack_customer_code     TEXT;
ALTER TABLE user_billing ADD COLUMN IF NOT EXISTS paystack_subscription_code TEXT;
ALTER TABLE user_billing ADD COLUMN IF NOT EXISTS paystack_email_token       TEXT;

-- ── Admin backend ──────────────────────────────────────────────────────────────
-- Delegated admins. The two hardcoded super-owners (DEMO_ALLOWED) stay in code as
-- an unremovable fallback; this table lets us add/remove others without a deploy.
CREATE TABLE IF NOT EXISTS admins (
    user_id    UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    email      TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'admin',   -- owner | admin | support
    added_by   TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Every mutating admin action, for accountability.
CREATE TABLE IF NOT EXISTS admin_audit_log (
    id          BIGSERIAL PRIMARY KEY,
    admin_email TEXT NOT NULL,
    action      TEXT NOT NULL,                  -- credits.adjust | plan.set | user.suspend | …
    target_type TEXT,                           -- user | transaction | setting | …
    target_id   TEXT,
    meta        JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_admin_audit_created ON admin_audit_log(created_at DESC);

-- Account status so a suspended user can't chat/trade.
ALTER TABLE users ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';  -- active | suspended


-- ── Daily Market Scan ──────────────────────────────────────────────────────────
-- The app's own built-in analysis agent (backend/daily_scan.py) runs once a day at
-- 00:00 UTC over the whole universe (major + minor FX, indices, BTC/ETH) and stores
-- the day's tradeable picks — direction, order type, levels and the UTC windows they
-- can be traded in. One row per day; re-running a day upserts it.
CREATE TABLE IF NOT EXISTS daily_market_scans (
    scan_date      DATE PRIMARY KEY,
    status         TEXT NOT NULL DEFAULT 'ok',      -- ok | error
    error          TEXT,
    model          TEXT,                            -- which model wrote the picks
    universe_count INTEGER,                         -- symbols actually measured
    picks          JSONB NOT NULL DEFAULT '[]',     -- the day's setups
    summary        TEXT,
    features       JSONB NOT NULL DEFAULT '[]',     -- per-symbol measurements (audit trail)
    macro          JSONB NOT NULL DEFAULT '{}',     -- calendar / news / sentiment / fed context
    cost_usd       NUMERIC(12, 6),
    duration_ms    INTEGER,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_daily_scans_date ON daily_market_scans(scan_date DESC);
-- Daily Market Scan schedule (system-wide, admin-editable). Hour is UTC; the
-- worker re-reads this every minute, so a change takes effect without a restart.
-- Read by daily_scan.scan_hour()/scan_enabled(). NULL means "use the built-in
-- default" rather than "off" — a column nobody has set must not silently
-- disable a feature.
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS daily_scan_hour_utc INTEGER;
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS daily_scan_enabled  BOOLEAN;

-- ── Daily Watch List (system analysis agent) ───────────────────────────────────
-- The watch-list agent ships with the app (templates/daily-watch-list-agent.json)
-- and is seeded under a fixed id, so it exists on every host. is_system marks it so
-- the UI can show it as part of the app rather than a user's own agent.
ALTER TABLE analysis_agents ADD COLUMN IF NOT EXISTS is_system BOOLEAN NOT NULL DEFAULT FALSE;

-- One row per scheduled build (twice a day by default): which instruments are worth
-- watching, and the UTC times / price levels to watch them at. NOT signals.
CREATE TABLE IF NOT EXISTS daily_watch_lists (
    run_date    DATE NOT NULL,
    run_slot    TEXT NOT NULL,                    -- the schedule slot, e.g. '00:00'
    status      TEXT NOT NULL DEFAULT 'ok',       -- ok | error
    error       TEXT,
    model       TEXT,
    agent_id    UUID,                             -- the system agent that produced it
    considered  INTEGER,                          -- instruments assessed
    symbols     JSONB NOT NULL DEFAULT '{}',      -- {SYMBOL: {times, prices, interest, why}}
    cost_usd    NUMERIC(12, 6),
    duration_ms INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_date, run_slot)
);
CREATE INDEX IF NOT EXISTS idx_watch_lists_date ON daily_watch_lists(run_date DESC, run_slot DESC);

-- The watch list's schedule, as UTC hours ('0,6'). Admin-editable; the worker
-- re-reads it every minute so a change needs no restart.
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS watchlist_hours_utc TEXT;
-- The watch list records its funnel (which instruments survived each stage), so a
-- reader can see why an instrument is on the list — or why the list is short.
ALTER TABLE daily_watch_lists ADD COLUMN IF NOT EXISTS funnel JSONB NOT NULL DEFAULT '{}';

-- Analysis API request sharing: inside this window one analysis per user+agent+
-- instrument+style is run and shared with every other caller, who are charged the
-- fraction below instead of full price. Admin → Settings → Analysis API.
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS analysis_window_seconds    INTEGER;
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS analysis_cached_charge_pct INTEGER;
-- Bring-your-own-key on a METERED edition: the user supplies the model, we
-- charge a markup on what those tokens would have cost us instead of the cost.
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS byok_enabled BOOLEAN;
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS byok_markup_pct INTEGER;
-- Which real model each branded tier runs on, as 'provider:model'. A provider
-- retires a model with a few weeks' notice; that should be a field, not a release.
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS tier_chat_model TEXT;
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS tier_pro_model TEXT;
-- The model ANALYSIS AGENTS run on, chosen separately from the chat model: a
-- model picked for a conversation should not silently become the one a live
-- trading robot's signals depend on.
ALTER TABLE user_prefs ADD COLUMN IF NOT EXISTS analysis_model TEXT;
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS analysis_share_enabled     BOOLEAN;
-- The id the Analysis API stamps on a run and puts in the trade comment
-- (XAUUSD_K7M2PQXT4B1V), so a position in MT5 can be traced back to its analysis.
ALTER TABLE analysis_runs ADD COLUMN IF NOT EXISTS analysis_id TEXT;
CREATE INDEX IF NOT EXISTS idx_analysis_runs_analysis_id ON analysis_runs(analysis_id);
-- An admin can publish one of their analysis agents: any user may then RUN it
-- (through the Analysis API or the app), while only its owner can edit it.
ALTER TABLE analysis_agents ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_analysis_agents_public ON analysis_agents(is_public) WHERE is_public;
