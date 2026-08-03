# News fetcher

Two scrapers plus a shared, rule-based impact scorer.

| Script | Source | How |
| --- | --- | --- |
| `fxstreet_news.py` | FXStreet | Internal tRPC endpoint `postsListMultifeed.batchList` — no auth, just browser-like headers. Each article page embeds a schema.org `NewsArticle` JSON-LD block with the summary and full body. |
| `investing_news.py` | Investing.com | The JSON news API needs a Bearer token minted server-side, so this reads the rendered category pages and parses the `__NEXT_DATA__` blob (`newsStore._news`, and `_article` for bodies). Cloudflare requires `curl_cffi` Chrome impersonation. |
| `impact.py` | — | Deterministic, offline. A curated alias dictionary extracts the instruments a headline names; a weighted keyword lexicon scores 0–100 and buckets into high / medium / low. |

**FXStreet caps a listing at 10 posts per feed** — 11 or more returns `400`.
Investing's limit is just a client-side slice of the rendered page.

## Usage

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/python3 fxstreet_news.py --limit 10
.venv/bin/python3 investing_news.py --limit 15 --no-content
.venv/bin/python3 fxstreet_news.py --out news.json
```

## In the backend

`backend/news.py` imports both modules and runs them on a **jittered 30–60s**
loop, saving into the `news_articles` table.

**Listings are cheap, article pages are not.** Every cycle lists both sources,
but only opens the page of an article we have never stored — or one the source
has revised (its timestamp moved). A steady-state cycle is therefore two listing
calls and zero page scrapes; verified at 14.3s for the first cycle (60 articles
fetched) and 4.9s for the next (0 fetched). If one source fails the other still
saves, and the failure surfaces in `/news/status`.

Articles are keyed by `sha256(source | source id)`, so re-listing updates a row
rather than duplicating it. Unlike sentiment, this **is** a history — articles
accumulate.

| Endpoint | What it returns |
| --- | --- |
| `GET /api/v1/news?symbol=XAUUSD&hours=6` | What's been written about gold in the last 6 hours |
| `GET /api/v1/news?impact=high&range=today` | Today's market-moving stories |
| `GET /api/v1/news?q=Fed&min_score=60` | High-scoring Fed coverage |
| `GET /api/v1/news?since=…&until=…` | Any explicit window on release time |
| `GET /api/v1/news?...&full=true` | Include the article body and impact reasons |
| `GET /api/v1/news/latest` | Newest first |
| `GET /api/v1/news/status` | Fetcher health: last cycle, counts, last error |

Filters combine. Symbols are forgiving — `gold`, `XAU/USD`, `cable`, `nas100`
resolve to whatever `impact.py` calls that market. Bodies are omitted unless
`full=true`, so listings stay light.

Reads **never** trigger a fetch.

## Notes / caveats

- The impact score is keyword-based, not semantic: "act" matches the regulation
  lexicon, so an unrelated headline can pick up points. Treat `impact_score` as
  a triage hint, not a verdict — `full=true` returns `impact_reasons` so you can
  see exactly which terms fired.
- Instruments are extracted from title + description only (what the piece is
  *about*), never the body, which would over-tag on price tables.
- Investing.com bodies fall back to the listing snippet when the article page
  doesn't parse.
