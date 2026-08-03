# Economic calendar fetcher

Scrapes the Investing.com / ForexPros economic calendar widget and watches
**high-impact** releases live, so the `actual` is captured within seconds of
printing.

## How it works

The widget at `https://sslecal2.forexprostools.com/` renders each event as a
`<tr>` carrying a GMT `event_timestamp`, an impact rating (the sentiment cell's
`title`: *High/Moderate/Low Volatility Expected*) and actual / forecast /
previous cells. `actual` is empty until the number is released.

Two paths:

1. **Listing** — `fetch_events()` scrapes the week's page (high-impact only by
   default) and annotates each event with the instruments it moves, via
   `impact.py`.
2. **Watching** — `watch_next_event()` finds the next release, opens a window 5s
   before its scheduled time, and polls the widget's JSON delta endpoint
   (`refresher.php`) every 3s until every event at that timestamp has an actual,
   or 90s pass. On timeout it does one HTML reconcile in case the delta feed
   missed one.

Requests go through [`curl_cffi`](https://github.com/lexiforest/curl_cffi)
impersonating Chrome.

## Usage

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/python3 economic_calendar.py --once           # upcoming high-impact events
.venv/bin/python3 economic_calendar.py --once --all     # every impact level
.venv/bin/python3 economic_calendar.py --watch          # watch the next release, then exit
.venv/bin/python3 economic_calendar.py --run            # watch every release, continuously
```

## In the backend

`backend/econ.py` imports this module and runs **its** watching logic on a
background thread: re-scrape the listing every 5 minutes, and when a release is
within 5 minutes hand over to `watch_next_event()` with a `poll_fn` that writes
each delta straight to Postgres — so the `actual` lands in the database as it
prints, not on the next listing pass.

**Only high-impact events are tracked.**

### One row per occurrence

Every occurrence is keyed by `sha256(event name | currency | scheduled time)`,
truncated to 32 chars. The same release scraped again always hashes to the same
key, so a re-scrape **updates** its row rather than inserting a duplicate. A
stored `actual`/`forecast`/`previous` is never overwritten with null, because
the listing page lags behind the live delta feed.

| Endpoint | What it returns |
| --- | --- |
| `GET /api/v1/calendar?symbol=XAUUSD,GBPUSD&range=today` | Today's releases that move gold or cable |
| `GET /api/v1/calendar?currency=USD&released=false` | US numbers still to print |
| `GET /api/v1/calendar?q=CPI&range=next_week` | Next week's inflation prints |
| `GET /api/v1/calendar?since=…&until=…` | Any explicit window (also `range`, `hours`, `days`) |
| `GET /api/v1/calendar/next` | The next releases, soonest first |
| `GET /api/v1/calendar/status` | Worker health: last scrape, counts, what's next, last error |

Filters combine. `symbol` is a **search filter only** — the affected-instrument
list is stored and GIN-indexed so the filter is fast, but it is never part of a
response. Symbols are forgiving — `gold`, `XAU/USD`, `cable`, `nas100` and
`USTEC` all resolve to whatever `impact.py` calls that market; stored values are
never rewritten.

Reads **never** trigger a fetch.

## Notes / caveats

- The widget serves a rolling ~1 week window, so history only accumulates for as
  long as the worker has been running — it cannot backfill the past.
- `refresher.php` is a *delta* feed: it is empty when nothing is releasing. A
  `sandClock` placeholder in `event_actual_formatted` means the number hasn't
  printed yet and is read as null.
- Investing's row ids can change between scrapes; they are stored as `source_id`
  (the delta feed keys on them) but are never the primary key.
