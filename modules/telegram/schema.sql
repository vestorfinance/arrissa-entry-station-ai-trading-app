-- Where a Telegram bot last got to, per user. `offset` is Telegram's own cursor:
-- an update is only delivered once, and only acknowledging it advances the mark,
-- so a restart mid-conversation resumes rather than replaying or losing it.
CREATE TABLE IF NOT EXISTS telegram_state (
    user_id     UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    last_update BIGINT NOT NULL DEFAULT 0,
    last_chat   TEXT,                       -- the chat that spoke most recently
    -- The last few turns, so "and enter" after "analyse gold" means something.
    -- Trimmed on write: this is context for a chat window, not an archive.
    history     JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE telegram_state ADD COLUMN IF NOT EXISTS history JSONB NOT NULL DEFAULT '[]'::jsonb;

-- Every chat this user's bot has been spoken to in. There is no Bot API call
-- that lists them — a bot only learns a chat when someone writes to it — so the
-- listener records each one as it arrives. Without this, choosing where to send
-- would mean asking a trader to find a numeric chat id in Telegram's UI.
CREATE TABLE IF NOT EXISTS telegram_chats (
    user_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    chat_id   TEXT NOT NULL,
    title     TEXT,                            -- group name, or the person's name
    kind      TEXT,                            -- private | group | supergroup | channel
    last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, chat_id)
);
