"""
Daily Market Scan — the app's own built-in analysis agent.

Unlike the agents users draw on the canvas, this one lives in code and ships with
the app: a fixed universe (major + minor FX, the big indices, BTC/ETH), a fixed
method, and a fixed schedule. It runs once a day at 00:00 UTC, picks the symbols
worth trading that day, says WHEN (which UTC windows, anchored to real calendar
events and session overlaps) and at WHAT PRICE, and stores the result in
`daily_market_scans` — one row per day, forever readable through /api/daily-scan.

How a scan is built:
  1. Deterministic maths over live candles (D1 + H1) for every symbol in the
     universe — trend, momentum in ATR units, volatility expansion, where price
     sits in its 20-day range, how close the range edges are. No model involved,
     so the shortlist is reproducible and free.
  2. That shortlist plus the day's macro context (economic calendar, high-impact
     news, retail sentiment, Fed odds) goes to ONE model call, which picks the
     day's best setups and writes the windows and levels.
  3. The picks are validated (symbols must be real, times must be HH:MM, levels
     must be numbers) before they're saved — a model can't put a fictional
     instrument or a malformed window into the table.

The model is whichever provider the app holds a key for; nothing here is billed
to a user, because nobody asked for it — it's a system job.
"""
import json
import os
import re
import sys
import threading
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))   # engine modules live at the project root

import db

# ── the universe ───────────────────────────────────────────────────────────────
# Written as the user names them; resolved against the account's live instrument
# list at scan time (nasdaq → USTEC, dax → DE30…), and anything this broker
# doesn't offer is skipped rather than failing the scan.
UNIVERSE = {
    "major_fx": ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"],
    "minor_fx": ["EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD",
                 "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD", "GBPNZD",
                 "AUDJPY", "AUDNZD", "AUDCAD", "AUDCHF",
                 "NZDJPY", "NZDCAD", "NZDCHF", "CADJPY", "CADCHF", "CHFJPY"],
    "indices": ["US30", "NASDAQ", "US500", "DE30"],
    "crypto": ["BTCUSD", "ETHUSD"],
}

SCAN_HOUR_UTC = int(os.getenv("DAILY_SCAN_HOUR_UTC", "0"))    # 00:00 UTC by default
SHORTLIST = 14            # how many symbols the model gets to choose from
MAX_PICKS = 6             # how many it may return
D1_BARS, H1_BARS = 60, 120

# Session windows in UTC — the model anchors its trade windows to these and to the
# day's scheduled events, so "when" is never a vague "during London".
SESSIONS = {
    "Sydney": (21, 6), "Tokyo": (0, 9), "London": (7, 16), "New York": (12, 21),
    "London/NY overlap": (12, 16),
}


# ── deterministic feature maths ────────────────────────────────────────────────
def _sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None


def _atr(candles, n=14):
    """Average true range over the last n bars."""
    if len(candles) < n + 1:
        return None
    trs = []
    for prev, cur in zip(candles[-(n + 1):-1], candles[-n:]):
        trs.append(max(cur["high"] - cur["low"],
                       abs(cur["high"] - prev["close"]),
                       abs(cur["low"] - prev["close"])))
    return sum(trs) / len(trs) if trs else None


def _pct(a, b):
    return round((a - b) / b * 100, 3) if (a is not None and b) else None


def _features(symbol, d1, h1):
    """Everything the shortlist ranks on, from candles alone. `d1`/`h1` are candle
    lists (oldest first). Returns None when there isn't enough history."""
    if len(d1) < 25:
        return None
    closes = [c["close"] for c in d1]
    highs = [c["high"] for c in d1]
    lows = [c["low"] for c in d1]
    last = closes[-1]

    atr14 = _atr(d1, 14)
    atr5 = _atr(d1, 5)
    hi20, lo20 = max(highs[-20:]), min(lows[-20:])
    span = hi20 - lo20

    # "excitement": volatility expanding vs its own baseline, real directional
    # travel measured in ATRs (so instruments compare like for like), and how
    # close price is to a 20-day edge that a break would run from.
    expansion = round(atr5 / atr14, 3) if (atr5 and atr14) else None
    move_atr = round((last - closes[-6]) / atr14, 2) if (atr14 and len(closes) > 6) else None
    edge_atr = round(min(hi20 - last, last - lo20) / atr14, 2) if atr14 else None
    score = 0.0
    if expansion:
        score += min(expansion, 2.5) * 2.0
    if move_atr is not None:
        score += min(abs(move_atr), 4.0)
    if edge_atr is not None:
        score += max(0.0, 2.0 - edge_atr)          # nearer an edge = more interesting

    sma20, sma50 = _sma(closes, 20), _sma(closes, 50)
    trend = "flat"
    if sma20 and sma50:
        trend = "up" if last > sma20 > sma50 else "down" if last < sma20 < sma50 else \
            "up-weak" if last > sma20 else "down-weak" if last < sma20 else "flat"

    return {
        "symbol": symbol,
        "price": round(last, 8),
        "trend": trend,
        "change_1d_pct": _pct(last, closes[-2]),
        "change_5d_pct": _pct(last, closes[-6]) if len(closes) > 6 else None,
        "atr_d1": round(atr14, 8) if atr14 else None,
        "atr_h1": round(_atr(h1, 14), 8) if len(h1) > 15 else None,
        "volatility_expansion": expansion,          # >1 = the last week is hotter than the month
        "move_in_atr_5d": move_atr,
        "range_20d": {"high": round(hi20, 8), "low": round(lo20, 8),
                      "position_pct": round((last - lo20) / span * 100, 1) if span else None},
        "atr_to_range_edge": edge_atr,              # <1 = a break is within a day's range
        "sma20": round(sma20, 8) if sma20 else None,
        "sma50": round(sma50, 8) if sma50 else None,
        "score": round(score, 3),
    }


# ── gathering ──────────────────────────────────────────────────────────────────
def _universe_pairs():
    for category, names in UNIVERSE.items():
        for name in names:
            yield category, name


def gather(log=print):
    """Live candles → features for every symbol we can resolve. Never raises for a
    single bad symbol; it's reported in `errors` and the scan continues."""
    import market
    rows, errors = [], []
    for category, name in _universe_pairs():
        try:
            symbol = market.resolve_symbol(name)
            d1 = market.candles(symbol, timeframe="D1", count=D1_BARS)["candles"]
            h1 = market.candles(symbol, timeframe="H1", count=H1_BARS)["candles"]
            f = _features(symbol, d1, h1)
            if not f:
                errors.append({"symbol": name, "error": "not enough history"})
                continue
            f["category"] = category
            f["requested_as"] = name
            rows.append(f)
        except Exception as e:
            errors.append({"symbol": name, "error": str(e)[:200]})
    log(f"[daily-scan] gathered {len(rows)} symbols, {len(errors)} skipped")
    return rows, errors


def _macro(symbols):
    """The day's shared context: scheduled events, high-impact news, crowd
    positioning and Fed odds. Every source is optional — a stale or broken fetcher
    must not stop the scan, it just narrows what the model reasons over."""
    out = {}
    try:
        import registry
        econ = registry.get("calendar")      # a module: may not be installed
        ev = econ.query(impact="high", days=1, limit=40).get("events", []) if econ else []
        out["calendar"] = [{"time": e.get("event_time"), "currency": e.get("currency"),
                            "event": e.get("event"), "impact": e.get("impact"),
                            "forecast": e.get("forecast"), "previous": e.get("previous")}
                           for e in ev]
    except Exception as e:
        out["calendar_error"] = str(e)[:160]
    try:
        import registry
        news = registry.get("news")
        arts = news.query(impact="high", hours=24, limit=25).get("articles", [])
        out["news"] = [{"time": a.get("time"), "title": a.get("title"),
                        "instruments": a.get("instruments"), "impact": a.get("impact"),
                        "score": a.get("impact_score")} for a in arts]
    except Exception as e:
        out["news_error"] = str(e)[:160]
    try:
        import registry
        sentiment = registry.get("sentiment")      # a module: may not be installed
        rows = (sentiment.query(symbols=symbols, min_positions=500).get("sentiment", [])
                if sentiment else [])
        out["sentiment"] = [{"symbol": r.get("symbol"), "long_percent": r.get("long_percent"),
                             "short_percent": r.get("short_percent"), "bias": r.get("bias")}
                            for r in rows]
    except Exception as e:
        out["sentiment_error"] = str(e)[:160]
    try:
        import registry
        fed = registry.get("fedwatch")      # a module: may not be installed
        f = fed.latest()
        out["fed_watch"] = {"summary": f.get("summary"), "next_meeting": f.get("next_meeting"),
                            "current_target_rate_bps": f.get("current_target_rate_bps")}
    except Exception as e:
        out["fed_error"] = str(e)[:160]
    return out


# ── the model call ─────────────────────────────────────────────────────────────
def _pick_model():
    """Any available model — whichever provider there is a key for, cheapest
    first. Returns (provider, model, key) or (None, None, None).

    The scan belongs to the INSTANCE, not to a person, so on a Community box it
    runs on the key of whoever it runs as — which is the same operator whose
    account it uses to read candles."""
    import ai_keys, billing
    uid = _system_user()
    # Through the branded tiers, not a hand-written list of provider+model pairs.
    # That list named claude-3-5-haiku-latest and gpt-4.1-mini, and a scan that
    # picks its own model is a scan that breaks the day one of those names
    # retires — silently, at 00:00, with nobody watching.
    for alias in (billing.DEFAULT_MODEL, "arrissa-pro"):
        try:
            provider, model, key = ai_keys.resolve(uid, alias)
        except Exception:
            continue
        if provider and model and key:
            return provider, model, key
    return None, None, None


_SYSTEM = (
    "You are the market desk's morning scan. From a pre-ranked shortlist of "
    "instruments (already measured for trend, momentum in ATR, volatility expansion "
    "and distance to range edges) plus today's macro context, choose the "
    f"{MAX_PICKS} MOST TRADEABLE instruments for the day ahead and say exactly WHEN "
    "and AT WHAT PRICE each can be traded.\n\n"
    "Output JSON: {\"picks\": [{"
    '"symbol": exact symbol from the shortlist, '
    '"direction": "BUY" | "SELL", '
    '"order_type": "MARKET" | "BUY_STOP" | "SELL_STOP" | "BUY_LIMIT" | "SELL_LIMIT", '
    '"entry": price (for a pending order the trigger; for MARKET the current price), '
    '"sl": price, "tp": price, '
    '"windows": [{"start": "HH:MM", "end": "HH:MM", "why": "session or the event that makes '
    'this window the one to trade"}] — UTC, 1-2 per pick, '
    '"quality": integer 1-5 (5 = ripe to trade), '
    '"why": one or two sentences citing the actual numbers/events that justify it, '
    '"invalidation": what would kill the idea'
    "}], \"summary\": \"2-3 sentences on the day: what's driving it and where the risk is\"}\n\n"
    "RULES: use ONLY symbols from the shortlist. Levels must be real numbers "
    "consistent with the instrument's current price and its ATR — a stop tighter "
    "than half the H1 ATR is noise, a target beyond 3× the D1 ATR is fantasy. A "
    "STOP order triggers beyond the current price in the trade's direction, a LIMIT "
    "waits for price to come back to it — get that right. Anchor windows to the "
    "listed session hours and to scheduled events (avoid entering into a high-impact "
    "release for that currency; trade the reaction instead). Prefer fewer, better "
    "picks over filling the quota.")


def analyse(shortlist, macro, log=print):
    """One model call → the day's picks. Returns (payload, ctx) so the caller can
    price the usage. payload is {} when no provider key is configured."""
    provider, model, key = _pick_model()
    if not key:
        log("[daily-scan] no AI provider key configured — saving the measured shortlist only")
        return {}, {}
    import analysis_agent
    ctx = {"provider": provider, "api_key": key, "model": model}
    user = json.dumps({
        "utc_now": datetime.now(timezone.utc).isoformat(),
        "sessions_utc": {k: f"{a:02d}:00-{b:02d}:00" for k, (a, b) in SESSIONS.items()},
        "shortlist": shortlist,
        "macro": macro,
    }, default=str)[:120000]
    out = analysis_agent._llm(ctx, {}, _SYSTEM, user, want_json=True)
    if not isinstance(out, dict):
        log(f"[daily-scan] model returned nothing usable ({ctx.get('_llm_error')})")
        return {}, ctx
    return out, ctx


# ── validation ─────────────────────────────────────────────────────────────────
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _clean_picks(raw, features_by_symbol, log=print):
    """Keep only picks the app can stand behind, and fix the ones it can.

    A pick is dropped unless it names a symbol this scan actually measured, a real
    side, a stop AND a target, and at least one well-formed UTC window — a "pick"
    without a level or a time answers neither half of the question. Levels are then
    checked against each other (stop and target on the correct sides of entry) and
    the pending type is re-derived from the geometry, so a model that writes
    "SELL_LIMIT" under the market is stored as the SELL_STOP it really is."""
    import analysis_agent
    picks = []
    for p in (raw or [])[:MAX_PICKS]:
        if not isinstance(p, dict):
            continue
        sym = str(p.get("symbol") or "").upper().strip()
        f = features_by_symbol.get(sym)
        direction = str(p.get("direction") or "").upper().strip()
        if not f or direction not in ("BUY", "SELL"):
            log(f"[daily-scan] dropped pick {sym or '?'}: unknown symbol or side")
            continue

        windows = []
        for w in (p.get("windows") or [])[:3]:
            if isinstance(w, dict) and _HHMM.match(str(w.get("start") or "")) \
                    and _HHMM.match(str(w.get("end") or "")):
                windows.append({"start": w["start"], "end": w["end"],
                                "why": str(w.get("why") or "")[:200]})

        price = f.get("price")
        order_type = str(p.get("order_type") or "MARKET").upper().strip()
        if order_type not in ("MARKET", "BUY_STOP", "SELL_STOP", "BUY_LIMIT", "SELL_LIMIT"):
            order_type = "MARKET"
        entry, sl, tp = _f(p.get("entry")), _f(p.get("sl")), _f(p.get("tp"))
        if order_type == "MARKET" and entry is None:
            entry = price                                  # "at market" = where it is now
        if order_type != "MARKET" and entry and price:
            order_type = analysis_agent._pending_type(direction, entry, price) or order_type

        if not (entry and sl and tp and windows):
            log(f"[daily-scan] dropped pick {sym}: needs entry, sl, tp and a window")
            continue
        ok = (sl < entry < tp) if direction == "BUY" else (tp < entry < sl)
        if not ok:
            log(f"[daily-scan] dropped pick {sym}: levels on the wrong side "
                f"({direction} entry={entry} sl={sl} tp={tp})")
            continue

        q = _f(p.get("quality"))
        picks.append({
            "symbol": sym,
            "category": f.get("category"),
            "direction": direction,
            "order_type": order_type,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "rr": round(abs(tp - entry) / abs(entry - sl), 2) if entry != sl else None,
            "price_at_scan": price,
            "windows_utc": windows,
            "quality": int(max(1, min(5, q))) if q else None,
            "why": str(p.get("why") or "")[:600],
            "invalidation": str(p.get("invalidation") or "")[:300],
        })
    return picks


# ── persistence ────────────────────────────────────────────────────────────────
def save(scan_date, payload):
    """Upsert the day's scan — re-running a day replaces it rather than duplicating."""
    from psycopg.types.json import Json
    dumps = lambda o: json.dumps(o, default=str)
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO daily_market_scans
                 (scan_date, status, error, model, universe_count, picks, summary,
                  features, macro, cost_usd, duration_ms, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
               ON CONFLICT (scan_date) DO UPDATE SET
                 status = EXCLUDED.status, error = EXCLUDED.error, model = EXCLUDED.model,
                 universe_count = EXCLUDED.universe_count, picks = EXCLUDED.picks,
                 summary = EXCLUDED.summary, features = EXCLUDED.features,
                 macro = EXCLUDED.macro, cost_usd = EXCLUDED.cost_usd,
                 duration_ms = EXCLUDED.duration_ms, created_at = now()""",
            (scan_date, payload["status"], payload.get("error"), payload.get("model"),
             payload.get("universe_count"), Json(payload.get("picks") or [], dumps=dumps),
             payload.get("summary"), Json(payload.get("features") or [], dumps=dumps),
             Json(payload.get("macro") or {}, dumps=dumps), payload.get("cost_usd"),
             payload.get("duration_ms")))
        conn.commit()


def get(scan_date=None):
    """One day's scan — the latest when no date is given."""
    with db.connect() as conn:
        if scan_date:
            row = conn.execute("SELECT * FROM daily_market_scans WHERE scan_date = %s",
                               (scan_date,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM daily_market_scans ORDER BY scan_date DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def history(days=7):
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT scan_date, status, model, universe_count, summary, picks, created_at
               FROM daily_market_scans ORDER BY scan_date DESC LIMIT %s""",
            (max(1, min(int(days or 7), 90)),)).fetchall()
    return [dict(r) for r in rows]


# ── the system user (whose broker session the scan reads through) ──────────────
def _system_user():
    """Candles need a live broker session. Prefer an explicit override, then an
    admin with an active account, then any user with one."""
    override = os.getenv("DAILY_SCAN_USER_ID")
    if override:
        return override
    # Any user with an active account will do; brokers keep their own connection
    # tables, so the check for "is actually connected" belongs to the broker.
    import brokers
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT e.user_id FROM exness_settings e
                 LEFT JOIN admins a ON a.user_id = e.user_id
                WHERE e.active_account IS NOT NULL
             ORDER BY (a.user_id IS NULL), e.user_id LIMIT 20""").fetchall()
    for r in rows:
        if brokers.has_connection(r["user_id"]):
            return r["user_id"]
    return None


# ── one scan ───────────────────────────────────────────────────────────────────
def run_once(force=False, log=print):
    """Build and store today's scan. Returns the saved payload (without the bulky
    per-symbol features). Safe to call twice — it upserts."""
    today = datetime.now(timezone.utc).date()
    if not force and get(today):
        log(f"[daily-scan] {today} already scanned")
        return get(today)

    started = _time.time()
    payload = {"status": "ok", "universe_count": 0}
    try:
        uid = _system_user()
        if not uid:
            raise RuntimeError("no user with a connected account — the scan reads live "
                               "candles through a broker session")
        import user_session
        with user_session.as_user(uid):
            features, errors = gather(log)
            if not features:
                raise RuntimeError("no market data gathered: " + json.dumps(errors[:3], default=str))
            symbols = [f["symbol"] for f in features]
            macro = _macro(symbols)

        ranked = sorted(features, key=lambda f: f["score"], reverse=True)
        shortlist = ranked[:SHORTLIST]
        by_symbol = {f["symbol"]: f for f in features}

        # The scan gets ONE shot a day, so a reply that parses but survives no
        # validation (wrong-side stops, invented symbols) is worth one retry —
        # the shortlist is already paid for and the call is ~$0.001.
        picks, ctx = [], {}
        for attempt in (1, 2):
            out, ctx = analyse(shortlist, macro, log)
            picks = _clean_picks(out.get("picks"), by_symbol, log)
            if picks or not ctx.get("api_key"):
                break
            log(f"[daily-scan] attempt {attempt} produced no usable picks — retrying")

        import billing
        usage = ctx.get("_usage")
        payload.update({
            "picks": picks,
            "summary": (str(out.get("summary"))[:1500] if out.get("summary") else None),
            "model": ctx.get("model"),
            "universe_count": len(features),
            "features": features,
            "macro": {**macro, "errors": errors, "shortlist": [f["symbol"] for f in shortlist]},
            "cost_usd": billing.cost_of(usage, ctx.get("_usage_model") or ctx.get("model")) if usage else 0,
        })
        if not payload["picks"]:
            payload["status"] = "error"
            payload["error"] = ctx.get("_llm_error") or "the model returned no usable picks"
    except Exception as e:
        payload["status"] = "error"
        payload["error"] = str(e)[:500]
        log(f"[daily-scan] FAILED: {e!r}")

    payload["duration_ms"] = int((_time.time() - started) * 1000)
    save(today, payload)
    log(f"[daily-scan] {today} {payload['status']}: {len(payload.get('picks') or [])} picks "
        f"from {payload['universe_count']} symbols in {payload['duration_ms']}ms")
    return {k: v for k, v in payload.items() if k != "features"}


# ── schedule ───────────────────────────────────────────────────────────────────
def scan_hour() -> int:
    """The configured hour, or the built-in default.

    `admin_settings.daily_scan_hour_utc` has existed since the schedule was
    added and was read by nothing — the constant won every time, so setting it
    did nothing at all. NULL means "use the default", never "off"."""
    try:
        import db
        with db.connect() as conn:
            row = conn.execute("SELECT daily_scan_hour_utc h FROM admin_settings "
                               "WHERE id = 1").fetchone()
        h = row["h"] if row else None
        return int(h) if h is not None and 0 <= int(h) <= 23 else SCAN_HOUR_UTC
    except Exception:
        return SCAN_HOUR_UTC


def scan_enabled() -> bool:
    try:
        import db
        with db.connect() as conn:
            row = conn.execute("SELECT daily_scan_enabled e FROM admin_settings "
                               "WHERE id = 1").fetchone()
        return True if not row or row["e"] is None else bool(row["e"])
    except Exception:
        return True


def next_run_at(now=None):
    now = now or datetime.now(timezone.utc)
    nxt = now.replace(hour=scan_hour(), minute=0, second=0, microsecond=0)
    return nxt + timedelta(days=1) if nxt <= now else nxt


def status():
    last = get()
    return {
        "schedule": f"daily at {scan_hour():02d}:00 UTC",
        "hour_utc": scan_hour(),
        "enabled": scan_enabled(),
        "next_run_utc": next_run_at().isoformat().replace("+00:00", "Z"),
        "last_scan_date": str(last["scan_date"]) if last else None,
        "last_status": last["status"] if last else None,
        "last_picks": len(last["picks"] or []) if last else 0,
        "universe": {k: len(v) for k, v in UNIVERSE.items()},
    }


def _loop():
    """Sleeps until the next scan time, runs, repeats. On boot it also fills in
    today's scan if the server was down when it was due."""
    _time.sleep(90)                     # let the app finish booting first
    try:
        if not get(datetime.now(timezone.utc).date()):
            run_once()
    except Exception as e:
        print(f"[daily-scan] catch-up failed: {e!r}", flush=True)
    while True:
        wait = max(60, (next_run_at() - datetime.now(timezone.utc)).total_seconds())
        _time.sleep(wait)
        try:
            if not scan_enabled():
                continue
            run_once(force=True)        # a new day: always a fresh scan
        except Exception as e:
            print(f"[daily-scan] run failed: {e!r}", flush=True)


def start():
    threading.Thread(target=_loop, daemon=True, name="daily-scan").start()


if __name__ == "__main__":          # manual run: python daily_scan.py
    print(json.dumps(run_once(force=True), indent=2, default=str))
