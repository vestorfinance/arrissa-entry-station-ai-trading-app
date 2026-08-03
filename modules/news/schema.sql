CREATE TABLE IF NOT EXISTS news_articles (
    article_key    TEXT PRIMARY KEY,      -- sha256(source|source_id)[:32]
    source         TEXT NOT NULL,         -- fxstreet | investing.com
    source_id      TEXT NOT NULL,
    published_at   TIMESTAMPTZ NOT NULL,  -- last edit time when the article was revised
    title          TEXT NOT NULL,
    description    TEXT,
    body           TEXT,
    category       TEXT,
    url            TEXT,
    instruments    TEXT[] NOT NULL DEFAULT '{}',
    impact_level   TEXT,                  -- high | medium | low
    impact_score   INTEGER,               -- 0–100
    impact_reasons TEXT[] NOT NULL DEFAULT '{}',
    first_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_news_published ON news_articles(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_impact ON news_articles(impact_level, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_instr ON news_articles USING GIN (instruments);
