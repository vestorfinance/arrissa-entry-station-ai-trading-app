"""
Economic calendar integration: a background worker runs the fetcher's own
watching logic — list the week's HIGH-impact releases, then poll refresher.php
through each release — and persists everything it sees into Postgres.

Each occurrence is keyed by a hash of (event name | currency | scheduled time),
so the same release re-scraped an hour later UPDATES its row (the actual prints
after the schedule does) instead of being inserted twice.

`instruments` is stored exactly as the fetcher's impact module produces it. The
query side is forgiving instead: 'gold', 'XAU/USD', 'nas100' and 'USTEC' all
resolve to whatever the calendar itself calls that market.

Reads never fetch — they serve what the worker last saved.
"""
import hashlib
import sys
import threading
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).parent
# The scraper lives INSIDE this module, not at the project root: a module has to
# carry its own dependencies or a ZIP install arrives half-built.
sys.path.insert(0, str(_HERE / "fetcher"))

import economic_calendar as ec     # the fetcher module (fetch_events, watch_next_event, …)
from curl_cffi import requests as creq

import db

LISTING_INTERVAL_S = 300     # re-list the calendar every 5 minutes
BURST_LEAD_S = 5             # start high-frequency polling this many seconds BEFORE a release
BURST_TAIL_S = 30            # keep bursting until the actual prints or this long AFTER the release
BURST_INTERVAL_S = 1.5       # poll cadence during the burst

_last_error = None
_last_error_at = None
_last_listing_at = None
_watching = None             # ISO time of the release currently being watched

# Query synonyms → the ticker the calendar's impact module emits. Stored values
# are never rewritten; this only widens what a caller may type.
ALIASES = {
    "GOLD": "XAUUSD", "XAU": "XAUUSD", "SILVER": "XAGUSD", "XAG": "XAGUSD",
    "COPPER": "XCUUSD", "PLATINUM": "XPTUSD", "PALLADIUM": "XPDUSD",
    "OIL": "USOIL", "CRUDE": "USOIL", "WTI": "USOIL", "XTIUSD": "USOIL",
    "BRENT": "UKOIL", "XBRUSD": "UKOIL",
    "GAS": "XNGUSD", "NGAS": "XNGUSD", "NATGAS": "XNGUSD", "NATURALGAS": "XNGUSD",
    "CABLE": "GBPUSD", "FIBER": "EURUSD", "LOONIE": "USDCAD", "AUSSIE": "AUDUSD",
    "KIWI": "NZDUSD", "SWISSY": "USDCHF",
    "BTC": "BTCUSD", "BITCOIN": "BTCUSD", "XBTUSD": "BTCUSD",
    "ETH": "ETHUSD", "ETHEREUM": "ETHUSD",
    "NAS100": "USTEC", "NASDAQ": "USTEC", "NDX": "USTEC", "NDX100": "USTEC",
    "SPX": "US500", "SPX500": "US500", "SP500": "US500",
    "DOW": "US30", "DJIA": "US30", "WS30": "US30",
    "GER30": "DE30", "GER40": "DE30", "DAX": "DE30", "DAX40": "DE30", "DE40": "DE30",
    "JPN225": "JP225", "NIKKEI": "JP225",
    "EURO50": "STOXX50", "EU50": "STOXX50", "STOXX": "STOXX50", "EUSTX50": "STOXX50",
    "FTSE": "UK100", "UKX": "UK100", "HSI": "HK50", "HANGSENG": "HK50",
    "DXY": "DXY", "USDX": "DXY", "DOLLARINDEX": "DXY",
}


def resolve_symbol(query: str) -> str:
    """'eur/usd' → EURUSD, 'gold' → XAUUSD, 'nas100' → USTEC."""
    q = (query or "").upper().replace("/", "").replace("-", "").replace("_", "").replace(" ", "")
    return ALIASES.get(q, q)


def event_key(event: str, currency: str, event_time: datetime) -> str:
    """Stable id for one occurrence of an event: its name, its currency and the
    exact moment it is scheduled for. The same release scraped repeatedly always
    hashes to the same key, so updates land on one row."""
    when = event_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw = f"{(event or '').strip().lower()}|{(currency or '').strip().upper()}|{when}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ── persistence ─────────────────────────────────────────────────────────────────
def save_events(events: list) -> dict:
    """Upsert scraped events. An actual/forecast/previous already stored is never
    overwritten with null — the listing can lag behind the live delta feed."""
    added = updated = 0
    with db.connect() as conn:
        for e in events:
            when = ec._parse_iso(e["timestamp_utc"])
            key = event_key(e["event"], e["currency"], when)
            row = conn.execute(
                """INSERT INTO calendar_events
                       (event_key, source_id, event, currency, country, event_time,
                        impact, actual, forecast, previous, instruments, released_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           CASE WHEN %s::text IS NOT NULL THEN now() END)
                   ON CONFLICT (event_key) DO UPDATE SET
                       source_id   = EXCLUDED.source_id,
                       impact      = EXCLUDED.impact,
                       country     = COALESCE(EXCLUDED.country, calendar_events.country),
                       instruments = EXCLUDED.instruments,
                       actual      = COALESCE(EXCLUDED.actual, calendar_events.actual),
                       forecast    = COALESCE(EXCLUDED.forecast, calendar_events.forecast),
                       previous    = COALESCE(EXCLUDED.previous, calendar_events.previous),
                       released_at = COALESCE(calendar_events.released_at,
                                              CASE WHEN EXCLUDED.actual IS NOT NULL
                                                   THEN now() END),
                       updated_at  = now()
                   RETURNING (xmax = 0) AS inserted""",
                (key, e.get("id"), e["event"], e.get("currency"), e.get("country"), when,
                 e.get("impact"), e.get("actual"), e.get("forecast"), e.get("previous"),
                 e.get("instruments") or [], e.get("actual")),
            ).fetchone()
            if row["inserted"]:
                added += 1
            else:
                updated += 1
        conn.commit()
    return {"added": added, "updated": updated, "seen": len(events)}


def save_updates(updates: dict) -> int:
    """Persist live refresher.php deltas, which are keyed by the widget's row id."""
    touched = 0
    with db.connect() as conn:
        for rid, upd in (updates or {}).items():
            if upd.get("actual") is None and upd.get("forecast") is None \
                    and upd.get("previous") is None:
                continue
            row = conn.execute(
                """UPDATE calendar_events SET
                       actual      = COALESCE(%s, actual),
                       forecast    = COALESCE(%s, forecast),
                       previous    = COALESCE(%s, previous),
                       released_at = COALESCE(released_at,
                                              CASE WHEN %s::text IS NOT NULL THEN now() END),
                       updated_at  = now()
                   WHERE source_id = %s RETURNING event_key""",
                (upd.get("actual"), upd.get("forecast"), upd.get("previous"),
                 upd.get("actual"), str(rid)),
            ).fetchone()
            if row:
                touched += 1
        conn.commit()
    return touched


# ── time windows ────────────────────────────────────────────────────────────────
def _parse_when(value):
    """ISO in, aware UTC out. A bare date means midnight UTC."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00").replace("/", "-")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"can't read '{value}' as a date/time — use e.g. 2026-07-24T14:30:00Z")
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _window(range=None, hours=0, days=0, since=None, until=None):
    """Resolve the requested time window to (from, to), either side nullable."""
    if since or until:
        return _parse_when(since), _parse_when(until)

    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    r = (range or "").strip().lower()
    if r in ("today", "day"):
        return midnight, midnight + timedelta(days=1)
    if r == "tomorrow":
        return midnight + timedelta(days=1), midnight + timedelta(days=2)
    if r == "yesterday":
        return midnight - timedelta(days=1), midnight
    if r in ("this_week", "week"):
        start = midnight - timedelta(days=now.weekday())
        return start, start + timedelta(days=7)
    if r == "next_week":
        start = midnight - timedelta(days=now.weekday()) + timedelta(days=7)
        return start, start + timedelta(days=7)
    if r == "upcoming":
        return now, None
    if r == "past":
        return None, now

    total = (hours or 0) + (days or 0) * 24
    if total > 0:
        return (now, now + timedelta(hours=total)) if total > 0 else (None, None)
    return None, None          # no window = everything we hold


# ── queries (read the database — never fetch) ───────────────────────────────────
def _shape(r):
    # event_key and instruments are stored for dedup/search but never returned —
    # they are how we track an occurrence, not part of describing it.
    return {
        "event": r["event"],
        "currency": r["currency"],
        "time": ec._iso_z(r["event_time"]),
        "impact": r["impact"],
        "actual": r["actual"],
        "forecast": r["forecast"],
        "previous": r["previous"],
        "released": r["actual"] is not None,
        "updated_at": ec._iso_z(r["updated_at"]),
    }


def query(symbol=None, currency=None, impact=None, range=None, hours=0, days=0,
          since=None, until=None, released=None, q=None, limit=200, order="asc") -> dict:
    """Calendar events matching any combination of: instrument(s), currency(ies),
    impact, a time window (preset, explicit from/to, or N hours/days ahead),
    released-or-not, and a text search on the event name."""
    where, args = [], []

    symbols = [resolve_symbol(s) for s in _split(symbol)]
    if symbols:
        where.append("instruments && %s")      # array overlap: any listed instrument
        args.append(symbols)
    currencies = [c.upper() for c in _split(currency)]
    if currencies:
        where.append("currency = ANY(%s)")
        args.append(currencies)
    impacts = [i.lower() for i in _split(impact)]
    if impacts:
        where.append("impact = ANY(%s)")
        args.append(impacts)

    try:
        start, end = _window(range=range, hours=hours, days=days, since=since, until=until)
    except ValueError as e:
        return {"error": str(e)}
    if start:
        where.append("event_time >= %s")
        args.append(start)
    if end:
        where.append("event_time <= %s")
        args.append(end)

    if released is not None:
        where.append("actual IS NOT NULL" if released else "actual IS NULL")
    if q:
        where.append("event ILIKE %s")
        args.append(f"%{q}%")

    sql = "SELECT * FROM calendar_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY event_time {'DESC' if str(order).lower() == 'desc' else 'ASC'} LIMIT %s"
    args.append(min(int(limit), 1000))

    with db.connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return {
        "source": "investing_economic_calendar",
        "window": {"from": ec._iso_z(start) if start else None,
                   "to": ec._iso_z(end) if end else None},
        "filters": {"symbols": symbols or None, "currencies": currencies or None,
                    "impact": impacts or None, "released": released, "search": q},
        "count": len(rows),
        "events": [_shape(r) for r in rows],
    }


def next_events(symbol=None, currency=None, limit=5) -> dict:
    """The next releases still ahead of us."""
    return query(symbol=symbol, currency=currency, range="upcoming", limit=limit)


def _split(value):
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        parts = value
    else:
        parts = str(value).split(",")
    return [p.strip() for p in parts if str(p).strip()]


def status() -> dict:
    """Worker health: last listing scrape, what is stored, what is next."""
    with db.connect() as conn:
        counts = conn.execute(
            "SELECT count(*) AS total, "
            "count(*) FILTER (WHERE actual IS NOT NULL) AS released, "
            "count(*) FILTER (WHERE event_time > now()) AS upcoming, "
            "min(event_time) AS first, max(event_time) AS last FROM calendar_events"
        ).fetchone()
        nxt = conn.execute(
            "SELECT event, currency, event_time FROM calendar_events "
            "WHERE event_time > now() ORDER BY event_time LIMIT 1").fetchone()
    # `last_fetch_at` / `age_seconds` / `healthy` are the shape the admin panel
    # reads from every source. This reported only `last_listing_at`, so the
    # calendar showed "no pull" for a fetcher that had never missed one.
    from datetime import datetime, timezone
    _age = ((datetime.now(timezone.utc) - _last_listing_at).total_seconds()
            if _last_listing_at else None)
    return {
        "running": _started,
        "healthy": bool(_age is not None and _age <= LISTING_INTERVAL_S * 3),
        "last_fetch_at": ec._iso_z(_last_listing_at) if _last_listing_at else None,
        "age_seconds": round(_age, 1) if _age is not None else None,
        "watching": _watching,
        "last_listing_at": ec._iso_z(_last_listing_at) if _last_listing_at else None,
        "listing_interval_seconds": LISTING_INTERVAL_S,
        "impact_tracked": "high",
        "events_total": counts["total"],
        "events_released": counts["released"],
        "events_upcoming": counts["upcoming"],
        "earliest_event": ec._iso_z(counts["first"]) if counts["first"] else None,
        "latest_event": ec._iso_z(counts["last"]) if counts["last"] else None,
        "next_event": {"event": nxt["event"], "currency": nxt["currency"],
                       "time": ec._iso_z(nxt["event_time"])} if nxt else None,
        "last_error": _last_error,
        "last_error_at": ec._iso_z(_last_error_at) if _last_error_at else None,
    }


# ── TradingView source (JSON, carries actuals, reachable from the VPS) ───────────
# The old forexprostools/Investing scraper sits behind a Cloudflare JS challenge
# that 403s our datacenter IP, and Forex Factory's JSON has no `actual` field. The
# TradingView economic-calendar API returns title/currency/date/importance + actual/
# forecast/previous as clean JSON and answers fine from the VPS.
TV_URL = "https://economic-calendar.tradingview.com/events"
TV_ORIGIN = "https://in.tradingview.com"
TV_COUNTRIES = "US,CA,JP,DE,FR,IT,ES,CH,AU,NZ,GB,CN"
_TV_IMPACT = {1: "high", 0: "moderate", -1: "low"}


def _tv_fmt(v, scale=None):
    """A TradingView value → display string, keeping a unit suffix (%, K, M, B…)."""
    if v is None:
        return None
    s = ("%g" % v) if isinstance(v, (int, float)) else str(v).strip()
    if not s:
        return None
    suf = (scale or "").strip()
    return s + suf if suf in ("%", "K", "M", "B", "T") else s


def fetch_events_tv(min_importance: int = 0, days_back: int = 2, days_fwd: int = 8) -> list:
    """The economic calendar from TradingView (JSON, with actuals), mapped into the
    event shape save_events expects. min_importance: -1 all, 0 medium+high, 1 high."""
    now = datetime.now(timezone.utc)
    params = {
        "from": (now - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "to":   (now + timedelta(days=days_fwd)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "countries": TV_COUNTRIES,
        "minImportance": min_importance,
    }
    try:
        r = creq.get(TV_URL, params=params, headers={"Origin": TV_ORIGIN},
                     timeout=30, impersonate="chrome")
        if r.status_code != 200:
            return []
        rows = r.json().get("result") or []
    except Exception:
        return []
    out = []
    for e in rows:
        title = e.get("title")
        ccy = (e.get("currency") or "").upper()
        if not title:
            continue
        try:
            when = datetime.fromisoformat(str(e["date"]).replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            continue
        scale = e.get("scale")
        out.append({
            "id": str(e["id"]) if e.get("id") is not None else None,
            "timestamp_utc": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event": title, "currency": ccy, "country": (e.get("country") or "").upper(),
            "impact": _TV_IMPACT.get(e.get("importance"), "low"),
            "actual": _tv_fmt(e.get("actual"), scale),
            "forecast": _tv_fmt(e.get("forecast"), scale),
            "previous": _tv_fmt(e.get("previous"), scale),
            "instruments": ec.impact_module.instruments_for_event(ccy, title),
        })
    return out


# ── background worker ───────────────────────────────────────────────────────────
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


def start_worker():
    global _started
    if _started:
        return
    _started = True
    _stop.clear()
    threading.Thread(target=_loop, daemon=True).start()


def _burst(target_ts):
    """Poll TradingView rapidly around a release until the target event's actual
    prints, or until BURST_TAIL_S past its scheduled time — so the number lands
    within seconds of release, not on the next 5-minute re-list."""
    try:
        deadline = ec._parse_iso(target_ts) + timedelta(seconds=BURST_TAIL_S)
    except Exception:
        return
    while datetime.now(timezone.utc) < deadline and not _stop.is_set():
        evs = fetch_events_tv(min_importance=0)
        if evs:
            save_events(evs)
            targets = [e for e in evs if e.get("timestamp_utc") == target_ts]
            if targets and all(t.get("actual") for t in targets):
                return                      # actual captured — done early
        _stop.wait(BURST_INTERVAL_S)


def _loop():
    """List the calendar from TradingView, and around each release run a high-
    frequency burst (5s before → actual, or 30s after) so the actual is captured
    within seconds. TradingView carries actuals and answers from the VPS."""
    global _last_error, _last_error_at, _last_listing_at, _watching
    while not _stop.is_set():
        wait = LISTING_INTERVAL_S
        try:
            events = fetch_events_tv(min_importance=0)
            if not events:
                raise RuntimeError("TradingView returned no events")
            save_events(events)
            _last_listing_at = datetime.now(timezone.utc)
            _last_error, _last_error_at = None, None

            now = datetime.now(timezone.utc)
            # an event releasing now-ish (5s before → 30s after) with no actual yet?
            imminent = None
            for e in sorted(events, key=lambda e: e.get("timestamp_utc") or ""):
                try:
                    secs = (ec._parse_iso(e["timestamp_utc"]) - now).total_seconds()
                except Exception:
                    continue
                if -BURST_TAIL_S <= secs <= BURST_LEAD_S and not e.get("actual"):
                    imminent = e
                    break
            if imminent is not None:
                _watching = imminent["timestamp_utc"]
                try:
                    _burst(imminent["timestamp_utc"])
                finally:
                    _watching = None
                wait = 2        # re-list immediately to lock the actual + find the next
            else:
                upcoming = []
                for e in events:
                    try:
                        if ec._parse_iso(e["timestamp_utc"]) > now:
                            upcoming.append(e)
                    except Exception:
                        pass
                if upcoming:
                    until_next = min((ec._parse_iso(e["timestamp_utc"]) - now).total_seconds() for e in upcoming)
                    # wake ~BURST_LEAD_S before the next release so the burst starts on time
                    wait = min(LISTING_INTERVAL_S, max(1, until_next - BURST_LEAD_S))
        except Exception as e:
            _last_error, _last_error_at = str(e), datetime.now(timezone.utc)
            _watching = None
        _stop.wait(max(wait, 1))
