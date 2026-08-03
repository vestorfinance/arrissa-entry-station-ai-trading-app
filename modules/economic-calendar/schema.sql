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
