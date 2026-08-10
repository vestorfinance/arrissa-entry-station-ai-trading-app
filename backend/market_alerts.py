"""Alerts about the market, not about the app.

`notifications.py` answers "is anything set up wrongly here" — a standing list of
problems, checked when someone opens the page. This is the opposite kind: things
that happen at a moment and are worth interrupting somebody for.

    · a Truth Social post that moves markets
    · a high-impact news story
    · a high-impact economic release, five minutes out
    · that release printing its actual number

The detections are the ones `data_triggers.py` already performs for the flow
builder, reading the same module providers. What is different is who they are
for: a trigger starts an agent, this puts a toast on a screen with a sound. So
the sources are shared and the delivery is not.

Rows are the record, not the notification. A row exists once per event — the
`key` is a natural id from the source, so two workers, a restart mid-pass, or an
overlapping poll cannot produce it twice — and each client asks what has appeared
since it last looked. That way a browser opened an hour later still sees what it
missed, which a broadcast-only design loses.
"""
import json
import threading
import time
from datetime import datetime, timedelta, timezone

import db

TICK = 45                     # how often to look
LEAD_S = 5 * 60               # "five minutes before a release"
KEEP_DAYS = 3                 # alerts older than this are of no use to anybody

_stop = threading.Event()
_last_error = None
_last_run = None


def _now():
    return datetime.now(timezone.utc)


def _parse(v):
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


# Currency → the country whose flag the UI should show. The frontend has the same
# table for instruments; this one exists because a calendar event carries a
# currency and nothing else, and the alert should say WHOSE number it is.
CCY_COUNTRY = {
    "USD": "us", "EUR": "eu", "GBP": "gb", "JPY": "jp", "CHF": "ch", "AUD": "au",
    "NZD": "nz", "CAD": "ca", "CNY": "cn", "CNH": "cn", "ZAR": "za", "SEK": "se",
    "NOK": "no", "DKK": "dk", "PLN": "pl", "TRY": "tr", "MXN": "mx", "SGD": "sg",
    "HKD": "hk", "INR": "in", "BRL": "br", "RUB": "ru", "KRW": "kr", "THB": "th",
}


def _emit(key, kind, title, body, *, at=None, impact=None, country=None,
          symbols=None, url=None, sound="notice"):
    """Record one alert. Silently does nothing if it already exists.

    ON CONFLICT DO NOTHING is what makes the whole thing safe to run twice: the
    key comes from the source (a post id, an event name plus its time), so the
    same happening always produces the same row."""
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO market_alerts (key, kind, title, body, at, impact, country, "
            "                           symbols, url, sound) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (key) DO NOTHING",
            (key[:200], kind, title[:300], (body or "")[:1200], at or _now(),
             impact, country, json.dumps(list(symbols or [])), url, sound))
        conn.commit()


def _truth_alerts():
    """Market-moving Truth Social posts."""
    import registry
    p = registry.get("truth")
    if not p:
        return
    r = p.query(user="trump", hours=6, limit=40, impact="high")
    for post in (r.get("posts") or []):
        # Only a real judgement counts. A post the classifier could not label is
        # not evidence of anything, and waking somebody for it would teach them
        # to ignore the sound.
        if (post.get("impact") or "") != "high":
            continue
        body = (post.get("content") or "").strip()
        _emit(f"truth:{post.get('post_id')}", "truth",
              f"{post.get('handle') or 'Truth Social'} — market-moving post",
              body[:500],
              at=_parse(post.get("datetime")), impact="high", country="us",
              sound="alert")


def _news_alerts():
    """High-impact news stories."""
    import registry
    p = registry.get("news")
    if not p:
        return
    r = p.query(hours=6, limit=50)
    for a in (r.get("articles") or []):
        imp = str(a.get("impact") or "").lower()
        # HIGH only, and the vocabulary matters: the news module grades
        # high/medium/low while the calendar says high/moderate/low. Testing for
        # "moderate" here silently matches nothing, and testing for both would
        # have let every mid-tier story ring the alarm — roughly eighty a day,
        # which teaches people to ignore the sound.
        if imp != "high":
            continue
        raw = a.get("instruments")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw.replace("'", '"'))
            except Exception:
                raw = [x.strip(" []'\"") for x in raw.split(",")]
        syms = [str(x).upper() for x in (raw or [])][:6]
        _emit(f"news:{a.get('id') or a.get('url') or a.get('title')}", "news",
              (a.get("title") or "Market news")[:200],
              (a.get("summary") or a.get("text") or "")[:500],
              at=_parse(a.get("time")), impact=imp, symbols=syms,
              url=a.get("url"), sound="alert")


def _calendar_alerts():
    """Five minutes before a high-impact release, and when its actual prints."""
    import registry
    p = registry.get("calendar")
    if not p:
        return
    now = _now()

    # — about to land —
    try:
        r = p.next_events(limit=60)
    except Exception:
        r = {}
    for e in (r.get("events") or []):
        if str(e.get("impact") or "").lower() not in ("high",):
            continue
        at = _parse(e.get("time"))
        if not at:
            continue
        delta = (at - now).total_seconds()
        # The window is the lead time back one tick, not "anything sooner than
        # five minutes" — otherwise an event four minutes out qualifies on every
        # pass and only the dedup key stops a stream of them.
        if not (LEAD_S - TICK * 2 <= delta <= LEAD_S):
            continue
        ccy = str(e.get("currency") or "").upper()
        _emit(f"pre:{e.get('event')}|{e.get('time')}", "calendar_soon",
              f"{e.get('event')} in {max(1, int(delta // 60))} min",
              f"High-impact {ccy} release at "
              f"{at.astimezone().strftime('%H:%M')}."
              + (f" Forecast {e.get('forecast')}." if e.get("forecast") else ""),
              at=at, impact="high", country=CCY_COUNTRY.get(ccy), sound="notice")

    # — printed —
    try:
        r2 = p.query(released=True, limit=60,
                     since=(now - timedelta(hours=2)).isoformat(),
                     until=now.isoformat())
    except Exception:
        r2 = {}
    for e in (r2.get("events") or []):
        if str(e.get("impact") or "").lower() not in ("high",):
            continue
        actual = e.get("actual")
        if actual in (None, "", "-"):
            continue
        ccy = str(e.get("currency") or "").upper()
        bits = [f"Actual {actual}"]
        if e.get("forecast"):
            bits.append(f"forecast {e['forecast']}")
        if e.get("previous"):
            bits.append(f"previous {e['previous']}")
        _emit(f"out:{e.get('event')}|{e.get('time')}", "calendar_out",
              f"{e.get('event')} — {actual}",
              ", ".join(bits) + ".",
              at=_parse(e.get("time")) or now, impact="high",
              country=CCY_COUNTRY.get(ccy), sound="alert")


def run_once() -> dict:
    """One pass over every source. A source that fails must not stop the others."""
    got = {}
    for name, fn in (("truth", _truth_alerts), ("news", _news_alerts),
                     ("calendar", _calendar_alerts)):
        try:
            fn()
            got[name] = "ok"
        except Exception as e:
            got[name] = f"{type(e).__name__}: {str(e)[:120]}"
    try:
        with db.connect() as conn:
            conn.execute("DELETE FROM market_alerts WHERE created_at < now() - "
                         "make_interval(days => %s)", (KEEP_DAYS,))
            conn.commit()
    except Exception:
        pass
    return got


def since(iso=None, limit=30) -> dict:
    """Alerts newer than `iso`. The client's own watermark, not a server cursor.

    Returned newest-last so a client can toast them in the order they happened,
    and `now` comes back for the client to send next time — a clock read on the
    client would drift against the database's."""
    args, where = [], []
    at = _parse(iso)
    if at:
        where.append("created_at > %s")
        args.append(at)
    q = ("SELECT key, kind, title, body, at, impact, country, symbols, url, sound, "
         "       created_at FROM market_alerts")
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY created_at DESC LIMIT %s"
    args.append(max(1, min(limit, 100)))
    with db.connect() as conn:
        rows = conn.execute(q, args).fetchall()
        now = conn.execute("SELECT now() AS n").fetchone()["n"]
    out = []
    for r in reversed(rows):
        out.append({"key": r["key"], "kind": r["kind"], "title": r["title"],
                    "body": r["body"], "at": r["at"].isoformat() if r["at"] else None,
                    "impact": r["impact"], "country": r["country"],
                    "symbols": r["symbols"] or [], "url": r["url"],
                    "sound": r["sound"] or "notice",
                    "created_at": r["created_at"].isoformat()})
    return {"alerts": out, "now": now.isoformat()}


def status() -> dict:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT count(*) AS n, max(created_at) AS newest FROM market_alerts").fetchone()
    return {"stored": row["n"],
            "newest": row["newest"].isoformat() if row["newest"] else None,
            "last_run": _last_run, "last_error": _last_error, "tick_seconds": TICK}


def _loop():
    global _last_error, _last_run
    # Not immediately: module providers and the fetchers are still coming up on a
    # fresh boot, and a first pass then just records three "not installed" errors.
    if _stop.wait(60):
        return
    while not _stop.is_set():
        try:
            out = run_once()
            _last_run = _now().isoformat()
            bad = {k: v for k, v in out.items() if v != "ok"}
            _last_error = json.dumps(bad) if bad else None
        except Exception as e:
            _last_error = f"{type(e).__name__}: {str(e)[:200]}"
        if _stop.wait(TICK):
            return


def start():
    threading.Thread(target=_loop, name="market-alerts", daemon=True).start()


def stop():
    _stop.set()
