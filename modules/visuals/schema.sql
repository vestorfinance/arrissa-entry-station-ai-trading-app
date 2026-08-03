-- A rendered image, kept so it can be shown in the app and attached to a message
-- without being re-drawn or pushed through the model as base64.
--
-- The bytes live in the row rather than on disk: a visual belongs to one user,
-- and a foreign key gets deletion, isolation and backup right for free, where a
-- directory of PNGs would get all three wrong. They are ~40 KB and they expire.
CREATE TABLE IF NOT EXISTS visuals (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL DEFAULT 'chart',     -- chart | card | html
    title      TEXT,
    png        BYTEA NOT NULL,
    width      INTEGER,
    height     INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_visuals_user ON visuals(user_id, created_at DESC);
