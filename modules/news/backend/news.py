"""
News integration: a background fetcher polls TradingView and FXStreet every
30–60 seconds and saves articles into Postgres, annotated by the fetchers' own
rule-based impact scorer (instruments mentioned + impact level/score/reasons).

TradingView is a real JSON API (news-headlines.tradingview.com) covering forex,
crypto and index feeds, and it tags every story with the symbols it concerns —
so we keep only stories about instruments this app actually trades and drop the
single stocks and ETFs those same feeds are full of. FXStreet is the FX-native
second source. Investing.com was dropped on 2026-08-01: it 403s from this VPS's
IP and had been silently contributing nothing since 27 July.

Listings are cheap, article pages are not — so each cycle lists everything but
only fetches the body of articles we have never stored, or whose source has
revised them. A steady state cycle is therefore two listing calls and no page
scrapes at all.

Reads never fetch — they serve what the fetcher last saved.
"""
import hashlib
import random
import sys
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The fetchers travel inside this module — a module carries its dependencies.
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE / "fetcher"))

import fxstreet_news as fx          # the FXStreet fetcher module
import tradingview_news as tvn      # the TradingView headline API
import impact as impact_module      # their shared rule-based scorer

import db

POLL_MIN_S, POLL_MAX_S = 30, 60     # jittered
FXSTREET_LIMIT = 10                 # per feed — FXStreet's tRPC endpoint 400s above this
CONTENT_WORKERS = 5                 # parallel article-page fetches (new articles only)

IMPACT_LEVELS = ("high", "medium", "low")

_last_error = None
_last_error_at = None
_last_by_source = {}     # per-source listing counts from the last cycle
_last_fetch_at = None
_last_added = 0

# Query synonyms → the tickers impact.py emits. Stored values are never rewritten.
ALIASES = {
    "GOLD": "XAUUSD", "XAU": "XAUUSD", "SILVER": "XAGUSD", "XAG": "XAGUSD",
    "COPPER": "XCUUSD", "PLATINUM": "XPTUSD", "PALLADIUM": "XPDUSD",
    "OIL": "USOIL", "CRUDE": "USOIL", "WTI": "USOIL", "XTIUSD": "USOIL",
    "BRENT": "UKOIL", "XBRUSD": "UKOIL",
    "GAS": "XNGUSD", "NGAS": "XNGUSD", "NATGAS": "XNGUSD", "NATURALGAS": "XNGUSD",
    "CABLE": "GBPUSD", "FIBER": "EURUSD", "LOONIE": "USDCAD", "AUSSIE": "AUDUSD",
    "KIWI": "NZDUSD", "SWISSY": "USDCHF",
    "BTC": "BTCUSD", "BITCOIN": "BTCUSD", "XBTUSD": "BTCUSD",
    "ETH": "ETHUSD", "ETHEREUM": "ETHUSD", "XRP": "XRPUSD", "RIPPLE": "XRPUSD",
    "SOL": "SOLUSD", "SOLANA": "SOLUSD", "DOGE": "DOGEUSD", "LTC": "LTCUSD",
    "NAS100": "USTEC", "NASDAQ": "USTEC", "NDX": "USTEC", "NDX100": "USTEC",
    "SPX": "US500", "SPX500": "US500", "SP500": "US500",
    "DOW": "US30", "DJIA": "US30", "WS30": "US30",
    "GER30": "DE30", "GER40": "DE30", "DAX": "DE30", "DAX40": "DE30", "DE40": "DE30",
    "JPN225": "JP225", "NIKKEI": "JP225",
    "EURO50": "STOXX50", "EU50": "STOXX50", "STOXX": "STOXX50",
    "FTSE": "UK100", "UKX": "UK100", "HSI": "HK50", "HANGSENG": "HK50",
    "USDX": "DXY", "DOLLARINDEX": "DXY",
}


def resolve_symbol(query: str) -> str:
    q = (query or "").upper().replace("/", "").replace("-", "").replace("_", "").replace(" ", "")
    return ALIASES.get(q, q)


def article_key(source: str, source_id: str) -> str:
    """Stable id for an article: its source plus that source's own id."""
    return hashlib.sha256(f"{source}|{source_id}".encode()).hexdigest()[:32]


def _parse_dt(value):
    """Parse a source timestamp. FXStreet sends 7 fractional digits, which
    fromisoformat rejects — trim to microseconds. TradingView sends unix seconds."""
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    if "." in text:
        head, _, tail = text.partition(".")
        digits = "".join(c for c in tail if c.isdigit())[:6]
        rest = tail[len(digits):].lstrip("0123456789")
        text = f"{head}.{digits or '0'}{rest}"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── listing (cheap) ─────────────────────────────────────────────────────────────
def _list_fxstreet(limit=FXSTREET_LIMIT) -> list:
    """FXStreet listings — no article pages touched."""
    out = []
    for category, posts in fx.fetch_news(fx.build_listings(limit)).items():
        for p in posts:
            out.append({
                "source": "fxstreet",
                "source_id": str(p["id"]),
                "published_at": _parse_dt(p.get("versionDate") or p.get("publicationDate")),
                "title": (p.get("title") or "").strip(),
                "description": None,          # only on the article page
                "url": p.get("fullUrl"),
                "category": category,
                "meta": None,
            })
    return out


def _list_tradingview() -> list:
    """TradingView's headline API — forex, crypto and index feeds, already cut down
    to stories about instruments this app trades.

    It is a real JSON API rather than a scrape, and it arrives with the publisher's
    OWN symbol tagging, which is stricter than reading the text: a story about the
    yen that never writes "USDJPY" is still tagged FX:USDJPY, and a story about
    Coinbase the company is tagged NASDAQ:COIN and dropped."""
    out = []
    for p in tvn.fetch_headlines():
        out.append({
            "source": "tradingview",
            "source_id": str(p["id"]),
            "published_at": _parse_dt(p.get("published")),
            "title": (p.get("title") or "").strip(),
            "description": None,           # only on the story endpoint
            "url": ("https://www.tradingview.com" + p["storyPath"]) if p.get("storyPath")
                   else p.get("link"),
            "category": p.get("_category"),
            # the instruments TradingView itself tagged — kept whole, and unioned
            # with whatever the text scorer finds, in _fill_content
            "tv_instruments": p.get("_instruments") or [],
            "meta": {"important": (p.get("urgency") == 1)},
        })
    return out


# ── content (expensive — new/revised articles only) ─────────────────────────────
def _fill_content(item: dict) -> dict:
    """Fetch the article page and annotate with instruments + impact."""
    try:
        if item["source"] == "fxstreet":
            content = fx.fetch_article_content(item["url"]) if item.get("url") else {}
            item["description"] = content.get("description") or item.get("description")
            item["body"] = content.get("text")
        else:
            story = tvn.fetch_story(item["source_id"])
            item["description"] = story.get("description") or item.get("description")
            item["body"] = story.get("body") or item.get("description")
    except Exception:
        item["body"] = item.get("body") or item.get("description")

    scored = impact_module.annotate(
        {"title": item["title"], "description": item.get("description"), "text": item.get("body")},
        meta=item.get("meta"),
    )
    # The source's OWN tagging leads, the text scorer adds to it. TradingView
    # knows a yen story is about USDJPY even when the words never appear.
    #
    # Then everything is cut to instruments this app trades. The scorer's alias
    # table includes mega-cap equities, so a story kept for US500 was also coming
    # back tagged AAPL and AMZN — filtering the FEED but not the TAGS let stocks
    # in through the side door, and a symbol search is only as good as its tags.
    merged = (item.get("tv_instruments") or []) + scored["instruments"]
    item["instruments"] = [s for s in dict.fromkeys(merged) if s in tvn.TRADED]
    item["impact_level"] = scored["impact_level"]
    item["impact_score"] = scored["impact_score"]
    item["impact_reasons"] = scored["impact_reasons"]
    return item


# ── persistence ─────────────────────────────────────────────────────────────────
def _known(items: list) -> dict:
    """{article_key: published_at} for the ones we already hold."""
    keys = [article_key(i["source"], i["source_id"]) for i in items]
    if not keys:
        return {}
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT article_key, published_at FROM news_articles WHERE article_key = ANY(%s)",
            (keys,)).fetchall()
    return {r["article_key"]: r["published_at"] for r in rows}


def save(items: list) -> int:
    added = 0
    with db.connect() as conn:
        for i in items:
            row = conn.execute(
                """INSERT INTO news_articles
                       (article_key, source, source_id, published_at, title, description,
                        body, category, url, instruments, impact_level, impact_score,
                        impact_reasons)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (article_key) DO UPDATE SET
                       published_at   = EXCLUDED.published_at,
                       title          = EXCLUDED.title,
                       description    = COALESCE(EXCLUDED.description, news_articles.description),
                       body           = COALESCE(EXCLUDED.body, news_articles.body),
                       instruments    = EXCLUDED.instruments,
                       impact_level   = EXCLUDED.impact_level,
                       impact_score   = EXCLUDED.impact_score,
                       impact_reasons = EXCLUDED.impact_reasons,
                       updated_at     = now()
                   RETURNING (xmax = 0) AS inserted""",
                (article_key(i["source"], i["source_id"]), i["source"], i["source_id"],
                 i["published_at"], i["title"], i.get("description"), i.get("body"),
                 i.get("category"), i.get("url"), i.get("instruments") or [],
                 i.get("impact_level"), i.get("impact_score"), i.get("impact_reasons") or []),
            ).fetchone()
            if row["inserted"]:
                added += 1
        conn.commit()
    return added


def fetch_once() -> dict:
    """One cycle: list both sources, then fetch bodies for new/revised articles."""
    items = []
    errors = []
    per_source = {}
    for name, lister in (("tradingview", _list_tradingview), ("fxstreet", _list_fxstreet)):
        try:
            got = lister()
            per_source[name] = len(got)
            items += got
            # A blocked source does NOT raise: the Investing scraper retries, gives
            # up and returns [], so silence looks exactly like "nothing published".
            # Investing.com went dead on 27 July 2026 (Cloudflare 403 on this VPS's
            # IP, the same block that hit bonds and the calendar) and the fetcher
            # reported "ok" for five days. An empty listing is now stated as a
            # fault, so the next dead source is visible the same day.
            if not got:
                errors.append(f"{name}: listed nothing — the source is empty or blocking us")
        except Exception as e:
            per_source[name] = 0
            errors.append(f"{name}: {e}")
    items = [i for i in items if i["published_at"] and i["title"]]
    if not items and errors:
        raise RuntimeError("; ".join(errors))

    known = _known(items)
    fresh = [i for i in items
             if article_key(i["source"], i["source_id"]) not in known
             or known[article_key(i["source"], i["source_id"])] != i["published_at"]]

    if fresh:
        with ThreadPoolExecutor(max_workers=CONTENT_WORKERS) as pool:
            fresh = list(pool.map(_fill_content, fresh))
        added = save(fresh)
    else:
        added = 0
    return {"ok": True, "listed": len(items), "fetched": len(fresh), "added": added,
            "by_source": per_source, "errors": errors or None}


# ── time windows ────────────────────────────────────────────────────────────────
def _parse_when(value):
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00").replace("/", "-")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"can't read '{value}' as a date/time — use e.g. 2026-07-24T14:30:00Z")
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _window(range=None, minutes=0, hours=0, days=0, since=None, until=None):
    """News looks BACKWARD: hours/days mean 'the last N', not 'the next N'."""
    if since or until:
        return _parse_when(since), _parse_when(until)
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    r = (range or "").strip().lower()
    if r in ("today", "day"):
        return midnight, None
    if r == "yesterday":
        return midnight - timedelta(days=1), midnight
    if r in ("this_week", "week"):
        return midnight - timedelta(days=now.weekday()), None
    if r == "last_7_days":
        return now - timedelta(days=7), None
    total = (minutes or 0) + (hours or 0) * 60 + (days or 0) * 1440
    if total > 0:
        return now - timedelta(minutes=total), None
    return None, None


# ── queries (read the database — never fetch) ───────────────────────────────────
def _shape(r, full=False):
    out = {
        "time": r["published_at"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "title": r["title"],
        "description": r["description"],
        "source": r["source"],
        "category": r["category"],
        "url": r["url"],
        "instruments": list(r["instruments"] or []),
        "impact": r["impact_level"],
        "impact_score": r["impact_score"],
    }
    if full:
        out["body"] = r["body"]
        out["impact_reasons"] = list(r["impact_reasons"] or [])
    return out


def _split(value):
    if not value:
        return []
    parts = value if isinstance(value, (list, tuple)) else str(value).split(",")
    # `any` and `all` mean NO FILTER, not an instrument called ANY.
    #
    # Filters are usually turned off by omitting them, which works when a person
    # is typing the call and fails the moment one is built from a variable:
    # `symbol={{symbol}}` has to be able to say "every instrument", and leaving
    # the parameter out is not something a value can do. So the word is a value.
    off = {"any", "all", "*", "none", "everything"}
    return [str(p).strip() for p in parts
            if str(p).strip() and str(p).strip().lower() not in off]


def query(symbol=None, impact=None, min_score=None, source=None, category=None, q=None,
          range=None, minutes=0, hours=0, days=0, since=None, until=None,
          full=False, limit=50, order="desc") -> dict:
    """Articles matching any combination of: instrument(s), impact level or
    minimum score, source, category, a text search, and a time window on the
    release time."""
    where, args = [], []

    symbols = [resolve_symbol(s) for s in _split(symbol)]
    if symbols:
        where.append("instruments && %s")
        args.append(symbols)
    levels = [l.lower() for l in _split(impact)]
    if levels:
        bad = [l for l in levels if l not in IMPACT_LEVELS]
        if bad:
            return {"error": f"unknown impact '{bad[0]}' — use {', '.join(IMPACT_LEVELS)}"}
        where.append("impact_level = ANY(%s)")
        args.append(levels)
    if min_score is not None:
        where.append("impact_score >= %s")
        args.append(int(min_score))
    sources = [s.lower() for s in _split(source)]
    if sources:
        where.append("lower(source) = ANY(%s)")
        args.append(sources)
    cats = _split(category)
    if cats:
        where.append("category = ANY(%s)")
        args.append(cats)
    if q:
        where.append("(title ILIKE %s OR description ILIKE %s OR body ILIKE %s)")
        args += [f"%{q}%"] * 3

    try:
        start, end = _window(range=range, minutes=minutes, hours=hours, days=days,
                             since=since, until=until)
    except ValueError as e:
        return {"error": str(e)}
    if start:
        where.append("published_at >= %s")
        args.append(start)
    if end:
        where.append("published_at <= %s")
        args.append(end)

    sql = "SELECT * FROM news_articles"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY published_at {'ASC' if str(order).lower() == 'asc' else 'DESC'} LIMIT %s"
    args.append(min(int(limit), 500))

    with db.connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    iso = lambda d: d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if d else None
    return {
        "window": {"from": iso(start), "to": iso(end)},
        "filters": {"symbols": symbols or None, "impact": levels or None,
                    "min_score": min_score, "sources": sources or None,
                    "category": cats or None, "search": q},
        "count": len(rows),
        "articles": [_shape(r, full=full) for r in rows],
    }


def status() -> dict:
    with db.connect() as conn:
        c = conn.execute(
            "SELECT count(*) AS total, "
            "count(*) FILTER (WHERE impact_level = 'high') AS high, "
            "count(*) FILTER (WHERE published_at >= now() - interval '24 hours') AS last_24h, "
            "max(published_at) AS newest FROM news_articles").fetchone()
    age = (datetime.now(timezone.utc) - _last_fetch_at).total_seconds() if _last_fetch_at else None
    iso = lambda d: d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if d else None
    return {
        "running": _started,
        "healthy": bool(age is not None and age <= POLL_MAX_S * 3),
        "last_fetch_at": iso(_last_fetch_at),
        "age_seconds": round(age, 1) if age is not None else None,
        "poll_interval_seconds": [POLL_MIN_S, POLL_MAX_S],
        "articles_total": c["total"],
        "articles_high_impact": c["high"],
        "articles_last_24h": c["last_24h"],
        "newest_article_at": iso(c["newest"]),
        "added_last_cycle": _last_added,
        "sources": ["tradingview", "fxstreet"],
        # How many articles EACH source listed last cycle. The totals above are the
        # sum, and a sum hides a dead half: this is what shows that one source is
        # carrying the feed on its own.
        "listed_by_source": _last_by_source or None,
        # a source's error can carry a giant echoed URL — keep status readable
        "last_error": (_last_error[:300] + "…") if _last_error and len(_last_error) > 300
                      else _last_error,
        "last_error_at": iso(_last_error_at),
    }


# ── background fetcher ──────────────────────────────────────────────────────────
_started = False

# Switching a module off must actually switch it off. `_stop` is what makes the
# difference between "nothing reads this any more" and "this has stopped": the
# loop checks it, and waits on it instead of sleeping, so a disable takes effect
# at once rather than after the current interval.
_stop = threading.Event()


def stop_fetcher():
    """Stop the loop. Idempotent, and safe to call when it never started."""
    global _started
    _stop.set()
    _started = False


def start_fetcher():
    global _started
    if _started:
        return
    _started = True
    _stop.clear()
    threading.Thread(target=_loop, daemon=True).start()


def _loop():
    global _last_error, _last_error_at, _last_fetch_at, _last_added, _last_by_source
    while not _stop.is_set():
        started = _time.monotonic()
        try:
            res = fetch_once()
            _last_fetch_at = datetime.now(timezone.utc)
            _last_added = res["added"]
            _last_by_source = res.get("by_source") or {}
            _last_error, _last_error_at = (None, None) if not res.get("errors") else \
                ("; ".join(res["errors"]), datetime.now(timezone.utc))
            # jittered 30–60s between cycles, minus the time the cycle itself took
            wait = random.uniform(POLL_MIN_S, POLL_MAX_S) - (_time.monotonic() - started)
        except Exception as e:
            _last_error, _last_error_at = str(e), datetime.now(timezone.utc)
            wait = POLL_MIN_S
        _stop.wait(max(wait, 5))


# ── impact over a period, and against the one before it ────────────────────────
#
# "Is today heavier than yesterday?" is the question people actually ask of a
# news feed, and counting articles cannot answer it: ten filler pieces are not
# louder than one central-bank decision. So the measure is the SUM of impact
# scores — volume and severity in one number — with the count and the average
# beside it so a spike caused by one huge story is distinguishable from a spike
# caused by fifty small ones.
IMPACT_WEIGHT = {"high": 3, "medium": 2, "low": 1}


def _previous_window(a, b):
    """The window of the same length immediately before [a, b).

    Comparing today against a fixed 24 hours would be wrong before noon: half a
    day of news would always look quieter than a full one. The comparison window
    is the same LENGTH as the one asked for, ending where it starts."""
    now = datetime.now(timezone.utc)
    end = b or now
    if a is None:
        return None, None
    span = end - a
    return a - span, a


def _summarise(rows) -> dict:
    levels = {"high": 0, "medium": 0, "low": 0}
    scores, weighted, by_source, by_symbol = [], 0, {}, {}
    for r in rows:
        lvl = (r.get("impact") or "").lower()
        if lvl in levels:
            levels[lvl] += 1
        s = r.get("impact_score")
        if s is not None:
            scores.append(float(s))
        weighted += IMPACT_WEIGHT.get(lvl, 0)
        by_source[r.get("source") or "?"] = by_source.get(r.get("source") or "?", 0) + 1
        for sym in (r.get("instruments") or []):
            by_symbol[sym] = by_symbol.get(sym, 0) + 1
    total = sum(scores)
    return {
        "articles": len(rows),
        "by_impact": levels,
        "impact_total": round(total, 1),          # the headline number
        "impact_avg": round(total / len(scores), 1) if scores else 0.0,
        "impact_max": round(max(scores), 1) if scores else 0.0,
        "weighted_level": weighted,
        "by_source": by_source,
        "top_instruments": [{"symbol": k, "articles": v} for k, v in
                            sorted(by_symbol.items(), key=lambda kv: -kv[1])[:10]],
    }


def _change(now_v, then_v):
    if not then_v:
        return None if not now_v else 100.0
    return round((now_v - then_v) / then_v * 100.0, 1)


def impact(symbol=None, impact=None, min_score=None, source=None, category=None, q=None,
           range=None, minutes=0, hours=0, days=0, since=None, until=None,
           compare=True) -> dict:
    """How heavy the news is over a period, and how that compares with the period
    before it. Answers "is today's news impact greater than yesterday's" without
    the caller having to fetch two lists and add them up."""
    filters = dict(symbol=symbol, impact=impact, min_score=min_score, source=source,
                   category=category, q=q)
    a, b = _window(range=range, minutes=minutes, hours=hours, days=days,
                   since=since, until=until)
    if a is None:                       # no window given → the last 24 hours
        a, b = _window(hours=24)

    cur = query(**filters, since=a.isoformat(), until=b.isoformat() if b else None,
                limit=1000)["articles"]
    out = {"window": {"from": a.isoformat().replace("+00:00", "Z"),
                      "to": (b or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")},
           "filters": {k: v for k, v in filters.items() if v not in (None, "", 0)},
           **_summarise(cur)}

    if not compare:
        return out

    pa, pb = _previous_window(a, b)
    prev = query(**filters, since=pa.isoformat(), until=pb.isoformat(), limit=1000)["articles"]
    p = _summarise(prev)
    delta = _change(out["impact_total"], p["impact_total"])
    out["previous"] = {"window": {"from": pa.isoformat().replace("+00:00", "Z"),
                                  "to": pb.isoformat().replace("+00:00", "Z")},
                       **p}
    out["change"] = {
        "impact_total_pct": delta,
        "articles_pct": _change(out["articles"], p["articles"]),
        "high_impact_pct": _change(out["by_impact"]["high"], p["by_impact"]["high"]),
        # Said in words as well as a number, because "heavier" is the answer the
        # question was actually asking for.
        "direction": ("heavier" if (delta or 0) > 10 else
                      "lighter" if (delta or 0) < -10 else "similar"),
    }
    return out
