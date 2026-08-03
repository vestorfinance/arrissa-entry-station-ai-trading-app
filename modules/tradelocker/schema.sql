-- ── TradeLocker (second broker) ────────────────────────────────────────────────
-- One row per TradeLocker LOGIN. A user may connect several (demo + live, or
-- different servers). Only the JWT tokens are stored — never the password.
CREATE TABLE IF NOT EXISTS tradelocker_user_sessions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tl_email     TEXT NOT NULL,
    environment  TEXT NOT NULL,               -- 'demo' | 'live'
    server       TEXT NOT NULL,
    access_enc   TEXT NOT NULL,               -- Fernet-encrypted access token
    refresh_enc  TEXT NOT NULL,               -- Fernet-encrypted refresh token
    connected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, tl_email, environment, server)
);

-- The accounts each TradeLocker login exposes. account_id (TL accountId) is what
-- the app's active_account points at; acc_num is the header /trade/* needs.
CREATE TABLE IF NOT EXISTS tradelocker_accounts (
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    account_id  TEXT NOT NULL,                -- TL accountId (numeric string)
    session_id  UUID NOT NULL REFERENCES tradelocker_user_sessions(id) ON DELETE CASCADE,
    acc_num     TEXT NOT NULL,                -- TL accNum (header on /trade/*)
    environment TEXT NOT NULL,                -- 'demo' | 'live'
    currency    TEXT,
    name        TEXT,
    meta        JSONB NOT NULL DEFAULT '{}',
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, account_id)
);

CREATE INDEX IF NOT EXISTS idx_tl_sessions_user ON tradelocker_user_sessions(user_id);

-- What TradeLocker says about the account: ACTIVE, or something else when it has
-- been archived or restricted. Stored rather than filtered away at connect, so a
-- pointer at an archived account can say WHY instead of "not connected".
ALTER TABLE tradelocker_accounts ADD COLUMN IF NOT EXISTS status TEXT;
ALTER TABLE tradelocker_accounts ADD COLUMN IF NOT EXISTS balance TEXT;


-- The app-level partner key lives on core's admin_settings row; the column is
-- this module's, so it travels with the module.
ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS tradelocker_dev_key_enc TEXT;
