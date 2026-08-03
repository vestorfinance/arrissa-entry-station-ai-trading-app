"""
Daily Watch List — what to watch today, at which exact times, around which prices.

MOSTLY NO AI. The funnel is code and maths; the model is used for exactly one
thing, reading the last 24 hours of news and political posts. Order of work:

  1. CALENDAR (code)   upcoming events → every instrument they touch, directly
                       (the event's own currency / instruments) or indirectly
                       (a US print moves the indices, crypto and the JPY crosses).
  2. VOLATILITY (code)  of those, keep the ones actually moving — the week's ATR
                       against the month's. There is deliberately NO trend filter:
                       requiring an orderly moving-average stack threw out the
                       violent, newsworthy markets that matter most.
  3. SENTIMENT (code)  of those, keep the ones where the retail crowd is lopsided
                       enough to matter — but ONLY where the crowd is actually
                       measured. Myfxbook covers no index, energy or crypto, and a
                       missing reading is not a boring one, so those pass through.
  4. NEWS (the ONE AI step) the system agent reads all high-impact news + Truth
                       posts from the last 24h and names interesting instruments
                       out of the WHOLE unfiltered universe — independent of 1-3.

  FINAL LIST = the survivors of 1→2→3  ∪  whatever the news agent named.

Then, per instrument, still in code:
  TIMES   exact UTC clock points, never ranges —
            · a relevant news story's time + 15 min
            · that instrument's session open + 15 min
            · a major (high-impact) event's own time
  PRICES  support and resistance from the candles: swing pivots, the previous
          day's high/low/close, the 20-day range, classic floor pivots.

The agent behind step 4 ships with the app (templates/daily-watch-list-agent.json),
is seeded under a fixed id, is owned by the app owner and is editable on the canvas
like any other agent. Chain of thought is OFF.
"""
import json
import os
import re
import sys
import threading
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))   # engine modules live at the project root

import db
import market_hours

TEMPLATE = Path(__file__).parent.parent / "templates" / "daily-watch-list-agent.json"
AGENT_ID = "00000000-0000-4000-a000-000000000001"       # fixed: the same agent on every host
DEFAULT_HOURS = "0,6"                                   # UTC hours the watch list is built at

UNIVERSE = {
    "major_fx": ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"],
    "minor_fx": ["EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD",
                 "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD", "GBPNZD",
                 "AUDJPY", "AUDNZD", "AUDCAD", "AUDCHF",
                 "NZDJPY", "NZDCAD", "NZDCHF", "CADJPY", "CADCHF", "CHFJPY"],
    "indices": ["US30", "NASDAQ", "US500", "DE30"],
    "metals": ["XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD"],
    "energy": ["USOIL"],
    "crypto": ["BTCUSD", "ETHUSD"],
}
SYMBOLS = [s for group in UNIVERSE.values() for s in group]
CATEGORY_OF = {s: cat for cat, names in UNIVERSE.items() for s in names}

CURRENCIES = ("USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD")

# Non-FX instruments don't wear their currencies in the name.
NON_FX_LEGS = {
    "US30": ["USD"], "US500": ["USD"], "USTEC": ["USD"], "NASDAQ": ["USD"],
    "DE30": ["EUR"], "BTCUSD": ["USD"], "ETHUSD": ["USD"],
    "XAUUSD": ["USD"], "XAGUSD": ["USD"], "XPTUSD": ["USD"], "XPDUSD": ["USD"],
    "USOIL": ["USD"], "UKOIL": ["USD"], "XTIUSD": ["USD"], "XBRUSD": ["USD"],
}

# Who else an event moves. A US print doesn't only move USD pairs: it repricies
# the indices, sets the risk tone the JPY crosses trade off, and moves crypto.
INDIRECT = {
    "USD": ["US30", "US500", "USTEC", "NASDAQ", "DE30", "BTCUSD", "ETHUSD",
            "XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD", "USOIL",
            "EURJPY", "GBPJPY", "AUDJPY", "NZDJPY", "CADJPY", "CHFJPY"],
    "EUR": ["DE30"],
    "CNY": ["AUDUSD", "NZDUSD", "AUDJPY", "NZDJPY", "USOIL", "XAGUSD", "XPTUSD"],
    "GBP": [], "JPY": [], "CHF": [], "AUD": [], "NZD": [], "CAD": [],
}

# Session opens in local time — converted to UTC per day, so DST is never guessed.
SESSION_OPENS = {
    "Tokyo": (ZoneInfo("Asia/Tokyo"), 9, 0),
    "London": (ZoneInfo("Europe/London"), 8, 0),
    "New York": (ZoneInfo("America/New_York"), 8, 0),
    "US cash open": (ZoneInfo("America/New_York"), 9, 30),
    "Frankfurt": (ZoneInfo("Europe/Berlin"), 9, 0),
}
SESSIONS_FOR_CURRENCY = {
    "JPY": ["Tokyo"], "AUD": ["Tokyo"], "NZD": ["Tokyo"],
    "EUR": ["London"], "GBP": ["London"], "CHF": ["London"],
    "USD": ["New York"], "CAD": ["New York"],
}
SESSIONS_FOR_SYMBOL = {
    "US30": ["US cash open"], "US500": ["US cash open"],
    "USTEC": ["US cash open"], "NASDAQ": ["US cash open"],
    "DE30": ["Frankfurt"], "BTCUSD": ["London", "US cash open"],
    "ETHUSD": ["London", "US cash open"],
    # metals trade the London fix and the COMEX session; oil the NYMEX session
    "XAUUSD": ["London", "New York"], "XAGUSD": ["London", "New York"],
    "XPTUSD": ["London", "New York"], "XPDUSD": ["London", "New York"],
    "USOIL": ["New York"], "UKOIL": ["London"],
    "XTIUSD": ["New York"], "XBRUSD": ["London"],
}

# Which impact levels count. The calendar tags most real market movers "moderate"
# (Employment Cost Index, Chicago PMI, Michigan Sentiment all are), so "high" alone
# is far too narrow; news uses high/medium/low, where medium still carries stories
# worth a clock point.
CALENDAR_IMPACT = "high,moderate"
NEWS_IMPACT = "high,medium"

NEWS_AFTER_MIN = 15          # a story is worth watching 15 min after it lands
SESSION_AFTER_MIN = 15       # …and 15 min after the open, once the noise clears
VOL_MIN_EXPANSION = 0.95     # last week's ATR vs the month's — below this it's dead
SENTIMENT_EXTREME = 15       # crowd % away from 50/50 before it's interesting

_running = threading.Lock()          # one build at a time, whoever asks for it


# ── symbol helpers ─────────────────────────────────────────────────────────────
def legs(symbol):
    """The currencies an instrument is exposed to."""
    s = (symbol or "").upper()
    if s in NON_FX_LEGS:
        return list(NON_FX_LEGS[s])
    found = [c for c in CURRENCIES if c in s]
    return found[:2] if found else []


def _resolve_universe(log=print):
    """Map the universe onto what this account actually trades (nasdaq → USTEC).

    One instrument this account cannot trade is normal — brokers carry different
    lists, and skipping it is right. NONE of them resolving is not a small list,
    it is a broken account, and it must not be reported as a finished one.

    That is exactly what happened: an owner's Exness account was being refused,
    all 39 symbols failed, and the endpoint answered `status: "ok", watching: 0`
    every morning with `error: null`. The list looked empty because the market
    was quiet, when in fact nothing had been read at all."""
    import market
    out, why = {}, ""
    for name in SYMBOLS:
        try:
            out[market.resolve_symbol(name)] = CATEGORY_OF[name]
        except Exception as e:
            why = why or f"{type(e).__name__}: {e}"
            log(f"[watchlist] skipping {name} — not tradable on this account")
    if not out:
        raise RuntimeError(
            f"none of the {len(SYMBOLS)} instruments could be read on the owner's active "
            f"account — the watch list cannot be built until that account works again. "
            f"First reason: {why or 'no reason given'}")
    if len(out) < len(SYMBOLS):
        log(f"[watchlist] {len(out)}/{len(SYMBOLS)} instruments tradable on this account")
    return out


# ── 1. calendar: which instruments today's events touch ────────────────────────
def stage_calendar(symbols, as_of, log=print):
    """The day ahead OF `as_of` → the instruments those events move, directly and
    indirectly. The window is anchored to as_of, not to the wall clock, so a build
    for the 00:00 slot sees the whole day's events rather than whatever happens to
    be left when it runs. Events keep their exact times — they are clock anchors."""
    import registry
    econ = registry.get("calendar")     # a module — absent is normal, not an error
    if econ is None:
        log("[watchlist] no calendar module installed — skipping the calendar stage")
        return {}, []
    try:
        # high AND moderate. Filtering to "high" alone threw away 22 of one day's 26
        # events — every USD print included (Employment Cost Index, Chicago PMI,
        # Michigan Sentiment are all tagged moderate), which is why the US
        # instruments could never enter the funnel.
        res = econ.query(impact=CALENDAR_IMPACT, since=as_of,
                         until=as_of + timedelta(days=1), limit=120)
        events = res.get("events") or []
    except Exception as e:
        log(f"[watchlist] calendar unavailable: {e}")
        events = []

    hit = {}
    for ev in events:
        cur = (ev.get("currency") or "").upper()
        named = {str(s).upper() for s in (ev.get("instruments") or [])}
        for sym in symbols:
            why = None
            if sym.upper() in named or (cur and cur in legs(sym)):
                why = f"{cur} {ev.get('event')}"                      # directly exposed
            elif sym.upper() in {s.upper() for s in INDIRECT.get(cur, [])}:
                why = f"{cur} {ev.get('event')} (indirect)"
            if why:
                hit.setdefault(sym, []).append({"event": ev.get("event"), "currency": cur,
                                                "time": ev.get("time"), "impact": ev.get("impact"),
                                                "why": why})
    log(f"[watchlist] calendar: {len(events)} events in the day ahead touch {len(hit)} instruments")
    return hit, events


# ── 2. trend + volatility, from candles ────────────────────────────────────────
def _atr(candles, n=14):
    if len(candles) < n + 1:
        return None
    trs = [max(c["high"] - c["low"], abs(c["high"] - p["close"]), abs(c["low"] - p["close"]))
           for p, c in zip(candles[-(n + 1):-1], candles[-n:])]
    return sum(trs) / len(trs) if trs else None


def _sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None


def measure(symbol, d1):
    """Volatility, direction and the price levels — all of it maths, no model.
    `trend` is reported for information only; it does NOT filter anything. A
    moving-average stack demands an orderly market, which threw out exactly the
    instruments worth watching — USDJPY on BoJ day read "flat" while travelling
    4.1 ATR in five days with volatility at 1.89× its own baseline."""
    if len(d1) < 25:
        return None
    closes = [c["close"] for c in d1]
    highs = [c["high"] for c in d1]
    lows = [c["low"] for c in d1]
    last, prev = closes[-1], d1[-2]
    atr14, atr5 = _atr(d1, 14), _atr(d1, 5)
    sma20, sma50 = _sma(closes, 20), _sma(closes, 50)
    if not (atr14 and sma20):
        return None

    up = last > sma20 and (sma50 is None or sma20 > sma50)
    down = last < sma20 and (sma50 is None or sma20 < sma50)
    move_atr = (last - closes[-6]) / atr14 if len(closes) > 6 else 0.0
    expansion = atr5 / atr14 if atr5 else 0.0

    return {
        "price": last,
        "trend": "up" if up else "down" if down else "flat",
        "move_in_atr_5d": round(move_atr, 2),
        "volatility_expansion": round(expansion, 3),
        "atr_d1": atr14,
        "volatile": expansion >= VOL_MIN_EXPANSION,
        "levels": _levels(d1, highs, lows, closes, prev, last),
    }


def _swings(highs, lows, span=2):
    """Fractal swing highs/lows — a bar higher (lower) than `span` bars each side."""
    hi, lo = [], []
    for i in range(span, len(highs) - span):
        window_h = highs[i - span:i + span + 1]
        window_l = lows[i - span:i + span + 1]
        if highs[i] == max(window_h):
            hi.append(highs[i])
        if lows[i] == min(window_l):
            lo.append(lows[i])
    return hi, lo


def _levels(d1, highs, lows, closes, prev, last):
    """Support and resistance, computed — swing pivots, yesterday's high/low/close,
    the 20-day range and the classic floor pivot. Nearest ones to price win."""
    sw_hi, sw_lo = _swings(highs, lows)
    p = (prev["high"] + prev["low"] + prev["close"]) / 3
    candidates = {
        "prev_high": prev["high"], "prev_low": prev["low"], "prev_close": prev["close"],
        "high_20d": max(highs[-20:]), "low_20d": min(lows[-20:]),
        "pivot": p, "r1": 2 * p - prev["low"], "s1": 2 * p - prev["high"],
    }
    for i, v in enumerate(sorted(sw_hi[-6:], reverse=True)):
        candidates[f"swing_high_{i + 1}"] = v
    for i, v in enumerate(sorted(sw_lo[-6:])):
        candidates[f"swing_low_{i + 1}"] = v

    digits = _digits(last)
    above = sorted({round(v, digits) for v in candidates.values() if v > last})[:3]
    below = sorted({round(v, digits) for v in candidates.values() if v < last}, reverse=True)[:3]
    named = {k: round(v, digits) for k, v in candidates.items()}
    return {"resistance": above, "support": below, "reference": named}


def _digits(price):
    a = abs(price or 0)
    return 5 if a < 10 else 3 if a < 1000 else 2 if a < 10000 else 1


# ── 3. sentiment, in code ──────────────────────────────────────────────────────
def stage_sentiment(symbols, log=print):
    """Retail positioning per instrument; interesting = a lopsided crowd."""
    import registry
    sentiment = registry.get("sentiment")      # a module: may not be installed
    if sentiment is None:
        log("[watchlist] retail sentiment module not installed — skipping this stage")
        return {}
    try:
        rows = sentiment.query(symbols=list(symbols), min_positions=500).get("sentiment", [])
    except Exception as e:
        log(f"[watchlist] sentiment unavailable: {e}")
        return {}
    out = {}
    for r in rows:
        long_pct = r.get("long_percent")
        if long_pct is None:
            continue
        skew = abs(float(long_pct) - 50)
        out[r["symbol"]] = {
            "long_percent": float(long_pct),
            "short_percent": r.get("short_percent"),
            "crowd": "long" if float(long_pct) > 50 else "short",
            "skew": round(skew, 1),
            "interesting": skew >= SENTIMENT_EXTREME,
        }
    log(f"[watchlist] sentiment: {sum(1 for v in out.values() if v['interesting'])} lopsided "
        f"of {len(out)} measured")
    return out


# ── 4. the one AI step: news + political posts over the whole universe ─────────
_PICK_SYSTEM = (
    "You read a finished news assessment and list the instruments it names as "
    "interesting today. Output JSON: {\"symbols\": {\"<SYMBOL>\": \"<the one-line reason "
    "given>\"}}. Use ONLY symbols from the allowed list, exactly as spelled there. "
    "Include an instrument only if the assessment actually names it as interesting; "
    "an empty object is the right answer for a quiet news day.")


def stage_news_agent(symbols, log=print):
    """Run the system agent once over the WHOLE unfiltered universe. Returns
    ({symbol: why}, cost_usd) — the only model spend in the build."""
    import analysis_agent, billing, main, user_session
    agent = agent_row()
    if not agent or not (agent["flow"] or {}).get("nodes"):
        log("[watchlist] news agent not seeded — skipping the news step")
        return {}, 0.0

    import ai_keys
    provider, model, key = ai_keys.resolve(agent["user_id"], billing.DEFAULT_MODEL)
    listing = ", ".join(sorted(symbols))
    request = (
        "Which of these instruments do the last 24 hours of news and political posts make "
        f"interesting to watch today ({datetime.now(timezone.utc).strftime('%A %d %B %Y')} UTC)?\n"
        f"Choose only from this list: {listing}")
    ctx = {"provider": provider, "api_key": key, "model": model,
           "user_id": agent["user_id"], "agent_tools": {}, "last_user": request}
    try:
        with user_session.as_user(agent["user_id"]):
            res = analysis_agent.run_flow(agent["flow"], request, ctx,
                                          agent_id=None, source="watchlist")
        text = res.get("response") or ""
        out = analysis_agent._llm(ctx, {}, _PICK_SYSTEM,
                                  f"Allowed symbols: {listing}\n\nAssessment:\n{text}",
                                  want_json=True) or {}
    except Exception as e:
        log(f"[watchlist] news agent failed: {e!r}")
        return {}, 0.0

    cost = billing.cost_of(ctx.get("_usage"), ctx.get("_usage_model") or model) if ctx.get("_usage") else 0.0
    picked = {}
    for sym, why in (out.get("symbols") or {}).items():
        s = str(sym).upper().strip()
        if s in symbols:
            picked[s] = str(why)[:300]
    log(f"[watchlist] news agent named {len(picked)} instruments (${cost:.4f})")
    return picked, cost


# ── times: exact clock points, never ranges ────────────────────────────────────
def _hhmm(dt):
    return dt.astimezone(timezone.utc).strftime("%H:%M")


def _parse_time(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def session_times(symbol, day):
    """Session opens that matter for this instrument, +15 min, as UTC clock points."""
    names = SESSIONS_FOR_SYMBOL.get(symbol.upper())
    if not names:
        names = []
        for cur in legs(symbol):
            for s in SESSIONS_FOR_CURRENCY.get(cur, []):
                if s not in names:
                    names.append(s)
    out = []
    for name in names:
        tz, hour, minute = SESSION_OPENS[name]
        local = datetime.combine(day, datetime.min.time(), tz).replace(hour=hour, minute=minute)
        label = name if name.endswith("open") else f"{name} open"
        out.append({"time": _hhmm(local + timedelta(minutes=SESSION_AFTER_MIN)),
                    "anchor": "session",
                    "why": f"{label} +{SESSION_AFTER_MIN}m"})
    return out


def instrument_times(symbol, day, events, articles, now):
    """The three rules, in code: news +15, session open +15, and a major event's
    own time. Only clock points that are still ahead of us today."""
    out = []
    for ev in events:
        t = _parse_time(ev.get("time"))
        if t and t >= now:
            out.append({"time": _hhmm(t), "anchor": "event",
                        "why": f"{ev.get('currency')} {ev.get('event')}"})
    for a in articles:
        t = _parse_time(a.get("time"))
        if t:
            t = t + timedelta(minutes=NEWS_AFTER_MIN)
            if t >= now:
                out.append({"time": _hhmm(t), "anchor": "news",
                            "why": f"+{NEWS_AFTER_MIN}m after: {str(a.get('title'))[:90]}"})
    for s in session_times(symbol, day):
        hh, mm = (int(x) for x in s["time"].split(":"))
        when = datetime.combine(day, datetime.min.time(), timezone.utc).replace(hour=hh, minute=mm)
        if when >= now:
            out.append(s)

    seen, unique = set(), []
    for t in sorted(out, key=lambda x: x["time"]):
        if t["time"] not in seen:
            seen.add(t["time"])
            unique.append(t)
    return unique


# How a currency gets written in a headline. Used only as a fallback, because a
# bare code is worthless here: "USD" appears in nearly every FX headline, which is
# how EURUSD ended up anchored to a British-Pound story.
CURRENCY_WORDS = {
    "USD": ("DOLLAR", "GREENBACK"), "EUR": ("EURO",), "GBP": ("POUND", "STERLING"),
    "JPY": ("YEN",), "CHF": ("FRANC",), "AUD": ("AUSSIE", "AUSTRALIAN DOLLAR"),
    "NZD": ("KIWI", "NEW ZEALAND DOLLAR"), "CAD": ("LOONIE", "CANADIAN DOLLAR"),
}


def _articles_for(symbol, articles):
    """Stories that are actually ABOUT this instrument.

    The article's own `instruments` tagging is the signal; the title is only a
    fallback, and then it must name the pair itself, or — when the story is tagged
    to nothing at all — both of its currencies by name. A shared currency code is
    NOT a match: every FX headline mentions the dollar."""
    sym = symbol.upper()
    pair_forms = {sym}
    ls = legs(symbol)
    if len(ls) == 2 and len(sym) == 6:
        base, quote = sym[:3], sym[3:]
        pair_forms |= {f"{base}/{quote}", f"{base}-{quote}", f"{base} {quote}"}

    out = []
    for a in articles:
        named = {str(i).upper() for i in (a.get("instruments") or [])}
        title = str(a.get("title") or "").upper()
        if sym in named:
            out.append(a)
        elif any(form in title for form in pair_forms):
            out.append(a)
        elif not named and len(ls) == 2 and all(
                any(w in title for w in CURRENCY_WORDS.get(c, ())) for c in ls):
            out.append(a)
    return out[:4]


# ── the build ──────────────────────────────────────────────────────────────────
def run_once(slot=None, force=False, as_of=None, log=print):
    """Build the list. `as_of` sets the clock the times are measured against — pass
    today at 00:00 to build the list as the 00:00 slot would have, with every one
    of the day's anchors still ahead. The DATA is always live; only the clock moves."""
    now = as_of or datetime.now(timezone.utc)
    run_date, slot = now.date(), slot or slot_name(now.hour)
    if not force and get(run_date, slot):
        log(f"[watchlist] {run_date} {slot} already built")
        return get(run_date, slot)
    if not _running.acquire(blocking=False):
        raise RuntimeError("a watch-list build is already in progress")

    started = _time.time()
    payload = {"status": "ok", "symbols": {}, "cost_usd": 0.0, "funnel": {}}
    try:
        agent = agent_row() or (seed(log) and agent_row())
        uid = (agent or {}).get("user_id") or _owner_user()
        if not uid:
            raise RuntimeError("no owner account to read market data through")
        import user_session, market, billing, registry
        news_mod = registry.get("news")
        if not user_session.has_connection(uid):
            raise RuntimeError("the system agent's owner has no connected broker account")

        with user_session.as_user(uid):
            universe = _resolve_universe(log)
            # Only plan what can actually be traded during this list's day. A watch
            # list built on a Saturday used to name EURUSD levels and 14:00 windows
            # for a market that would not open until Sunday night — times nobody
            # could act on, and an EA polling them for nothing. At the weekend that
            # leaves crypto, which is the only thing still trading.
            tradable, closed = market_hours.split(universe, now)
            hours = market_hours.state(now)
            if closed:
                log(f"[watchlist] market hours: {hours['note']} — "
                    f"skipping {len(closed)} closed instrument(s), "
                    f"planning {len(tradable)}")
            if not tradable:
                # Nothing trades at all. Say so as a finished list rather than an
                # error: an empty weekend list is the correct answer, not a failure.
                payload.update({
                    "symbols": {}, "considered": 0, "cost_usd": 0.0,
                    "agent_id": AGENT_ID,
                    "funnel": {"market_hours": hours, "universe": len(universe),
                               "closed": sorted(closed),
                               "calendar": [], "volatility": [], "sentiment": [],
                               "news": [], "final": []},
                })
                save(run_date, slot, payload)
                log("[watchlist] nothing is trading — empty list saved")
                return payload
            universe = tradable
            symbols = list(universe)

            # 1 ─ calendar
            cal_hits, events = stage_calendar(symbols, now, log)
            stage1 = list(cal_hits)
            # The calendar gate asks "does this instrument have a catalyst today".
            # At the weekend nothing does — there are no releases — so gating on it
            # excluded the crypto that was still trading and produced an empty list
            # on the one day the list should be crypto. When the rest of the market
            # is shut, structure and news are the only evidence there is, so
            # everything still trading goes through to be judged on those.
            if not hours["fx_open"]:
                stage1 = list(symbols)
                log(f"[watchlist] no calendar at the weekend — judging all "
                    f"{len(stage1)} on volatility and news instead")

            # 2 ─ trend + volatility (measure everything: the survivors need levels,
            #     and so does anything the news agent adds later)
            measured = {}
            for sym in symbols:
                try:
                    d1 = market.candles(sym, timeframe="D1", count=60)["candles"]
                    m = measure(sym, d1)
                    if m:
                        measured[sym] = m
                except Exception as e:
                    log(f"[watchlist] {sym}: no candles ({str(e)[:60]})")
            stage2 = [s for s in stage1 if measured.get(s, {}).get("volatile")]

            # 3 ─ sentiment. It gates ONLY what it actually covers: Myfxbook has no
            #     positioning for the indices, energies or crypto, and missing data
            #     is not a boring crowd — penalising it excluded US30, DE30, USOIL
            #     and BTC from the code path entirely. Uncovered instruments carry
            #     on with the calendar + volatility evidence they already earned.
            senti = stage_sentiment(symbols, log)
            stage3 = [s for s in stage2 if s not in senti or senti[s].get("interesting")]
            uncovered = [s for s in stage3 if s not in senti]
            log(f"[watchlist] sentiment gate: {len(stage3)} through "
                f"({len(uncovered)} of them uncovered, so not penalised)")

            # the 24 hours BEFORE as_of — the news a build at that moment could know
            try:
                articles = news_mod.query(impact=NEWS_IMPACT, since=now - timedelta(hours=24),
                                          until=now, limit=60).get("articles", [])
            except Exception as e:
                log(f"[watchlist] news unavailable: {e}")
                articles = []

        # 4 ─ the one AI step, over the WHOLE unfiltered universe
        news_picks, cost = stage_news_agent(symbols, log)

        final = list(dict.fromkeys(stage3 + list(news_picks)))
        log(f"[watchlist] funnel: calendar {len(stage1)} → volatility {len(stage2)} → "
            f"sentiment {len(stage3)} + news {len(news_picks)} = {len(final)}")

        out = {}
        for sym in final:
            m = measured.get(sym) or {}
            times = instrument_times(sym, run_date, cal_hits.get(sym, []),
                                     _articles_for(sym, articles), now)
            lv = m.get("levels") or {}
            reasons = []
            if sym in stage3:
                # Say what actually qualified it. At the weekend there is no
                # calendar, so claiming one is a reason nobody could check.
                reasons.append("calendar + volatility + crowd positioning" if hours["fx_open"]
                               else "volatility + crowd positioning (no calendar at the weekend)")
            if sym in news_picks:
                reasons.append(news_picks[sym])
            out[sym] = {
                "times": [t["time"] for t in times],          # exact UTC clock points
                "prices": (lv.get("resistance") or []) + (lv.get("support") or []),
                "support": lv.get("support") or [],
                "resistance": lv.get("resistance") or [],
                "price_now": round(m["price"], _digits(m["price"])) if m.get("price") else None,
                "trend": m.get("trend"),
                "volatility_expansion": m.get("volatility_expansion"),
                "sentiment": senti.get(sym),
                "events": cal_hits.get(sym, []),
                "time_reasons": times,
                "source": ("code+news" if (sym in stage3 and sym in news_picks)
                           else "news" if sym in news_picks else "code"),
                "why": " · ".join(reasons)[:400],
            }

        payload.update({
            "symbols": out,
            "considered": len(symbols),
            "cost_usd": round(cost, 6),
            "model": billing.DEFAULT_MODEL,
            "agent_id": AGENT_ID,
            "funnel": {"market_hours": hours, "universe": len(symbols),
                       "closed": sorted(closed),
                       "calendar": stage1, "volatility": stage2,
                       "sentiment": stage3, "news": list(news_picks), "final": final},
        })
    except Exception as e:
        payload["status"] = "error"
        payload["error"] = str(e)[:500]
        log(f"[watchlist] FAILED: {e!r}")
    finally:
        _running.release()

    payload["duration_ms"] = int((_time.time() - started) * 1000)
    save(run_date, slot, payload)
    log(f"[watchlist] {run_date} {slot} {payload['status']}: {len(payload['symbols'])} to watch "
        f"in {payload['duration_ms'] // 1000}s (${payload['cost_usd']})")
    return get(run_date, slot)


# ── seeding (the agent ships with the app) ─────────────────────────────────────
def _owner_user():
    import admin_api
    with db.connect() as conn:
        for email in sorted(admin_api.SUPER_OWNERS):
            row = conn.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()
            if row:
                return row["id"]
        row = conn.execute("SELECT user_id FROM admins ORDER BY created_at LIMIT 1").fetchone()
        if row:
            return row["user_id"]
        row = conn.execute("SELECT id FROM users ORDER BY created_at LIMIT 1").fetchone()
        return row["id"] if row else None


def seed(log=print):
    """Insert the system agent, or upgrade it when the app ships a NEW template
    version. Same version ⇒ never touched, so an admin's canvas edits stick."""
    from psycopg.types.json import Json
    try:
        tpl = json.loads(TEMPLATE.read_text())
        version = (tpl["flow"] or {}).get("_template", 1)
        uid = _owner_user()
        if not uid:
            return False                       # no users yet — try again next boot
        with db.connect() as conn:
            row = conn.execute("SELECT flow FROM analysis_agents WHERE id = %s", (AGENT_ID,)).fetchone()
            if row:
                stored = (row["flow"] or {}).get("_template")
                # NO MARKER MEANS A PERSON HAS EDITED THIS FLOW. The builder saves
                # {nodes, edges, cot} and does not carry `_template` forward, so a
                # hand-edited flow reads as version 1 — and every boot quietly
                # replaced it with the template. Somebody rebuilt this agent as an
                # Octo flow and lost it to a restart. An upgrade may only ever
                # touch a flow that is still ours.
                if stored is None or stored >= version:
                    return False
                conn.execute(
                    "UPDATE analysis_agents SET name = %s, description = %s, flow = %s, "
                    "updated_at = now() WHERE id = %s",
                    (tpl["name"], tpl.get("description", ""), Json(tpl["flow"]), AGENT_ID))
                conn.commit()
                log(f"[watchlist] upgraded system agent to template v{version}")
                return True
            conn.execute(
                """INSERT INTO analysis_agents (id, user_id, name, description, status, flow, is_system)
                   VALUES (%s, %s, %s, %s, 'active', %s, TRUE) ON CONFLICT (id) DO NOTHING""",
                (AGENT_ID, uid, tpl["name"], tpl.get("description", ""), Json(tpl["flow"])))
            conn.commit()
        log(f"[watchlist] seeded system agent “{tpl['name']}”")
        return True
    except Exception as e:
        log(f"[watchlist] seed failed: {e!r}")
        return False


def agent_row():
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id, user_id, name, flow FROM analysis_agents WHERE id = %s", (AGENT_ID,)).fetchone()
    return dict(row) if row else None


# ── schedule settings (admin-changeable) ───────────────────────────────────────
def hours():
    raw = None
    try:
        with db.connect() as conn:
            row = conn.execute("SELECT watchlist_hours_utc FROM admin_settings WHERE id = 1").fetchone()
        raw = row["watchlist_hours_utc"] if row else None
    except Exception:
        pass
    raw = raw or os.getenv("WATCHLIST_HOURS_UTC") or DEFAULT_HOURS
    out = []
    for part in str(raw).replace(";", ",").split(","):
        part = part.strip().split(":")[0]
        if part.isdigit() and 0 <= int(part) <= 23 and int(part) not in out:
            out.append(int(part))
    return sorted(out) or [0, 6]


def set_hours(value):
    if isinstance(value, (list, tuple)):
        value = ",".join(str(v) for v in value)
    cleaned = []
    for part in str(value).replace(";", ",").split(","):
        part = part.strip().split(":")[0]
        if not part.isdigit() or not (0 <= int(part) <= 23):
            raise ValueError(f"'{part}' is not a UTC hour (0-23)")
        if int(part) not in cleaned:
            cleaned.append(int(part))
    if not cleaned:
        raise ValueError("give at least one UTC hour, e.g. 0,6")
    text = ",".join(str(h) for h in sorted(cleaned))
    with db.connect() as conn:
        conn.execute("INSERT INTO admin_settings (id, watchlist_hours_utc) VALUES (1, %s) "
                     "ON CONFLICT (id) DO UPDATE SET watchlist_hours_utc = EXCLUDED.watchlist_hours_utc",
                     (text,))
        conn.commit()
    return sorted(cleaned)


def slot_name(hour):
    return f"{int(hour):02d}:00"


def next_run_at(now=None):
    now = now or datetime.now(timezone.utc)
    todays = [now.replace(hour=h, minute=0, second=0, microsecond=0) for h in hours()]
    upcoming = [t for t in todays if t > now]
    return upcoming[0] if upcoming else todays[0] + timedelta(days=1)


# ── persistence ────────────────────────────────────────────────────────────────
def save(run_date, slot, payload):
    from psycopg.types.json import Json
    dumps = lambda o: json.dumps(o, default=str)
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO daily_watch_lists
                 (run_date, run_slot, status, error, model, agent_id, considered, symbols,
                  funnel, cost_usd, duration_ms, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
               ON CONFLICT (run_date, run_slot) DO UPDATE SET
                 status = EXCLUDED.status, error = EXCLUDED.error, model = EXCLUDED.model,
                 agent_id = EXCLUDED.agent_id, considered = EXCLUDED.considered,
                 symbols = EXCLUDED.symbols, funnel = EXCLUDED.funnel,
                 cost_usd = EXCLUDED.cost_usd, duration_ms = EXCLUDED.duration_ms,
                 created_at = now()""",
            (run_date, slot, payload["status"], payload.get("error"), payload.get("model"),
             payload.get("agent_id"), payload.get("considered"),
             Json(payload.get("symbols") or {}, dumps=dumps),
             Json(payload.get("funnel") or {}, dumps=dumps),
             payload.get("cost_usd"), payload.get("duration_ms")))
        conn.commit()


def get(run_date=None, slot=None):
    with db.connect() as conn:
        if run_date and slot:
            row = conn.execute(
                "SELECT * FROM daily_watch_lists WHERE run_date = %s AND run_slot = %s",
                (run_date, slot)).fetchone()
        elif run_date:
            row = conn.execute(
                "SELECT * FROM daily_watch_lists WHERE run_date = %s "
                "ORDER BY run_slot DESC LIMIT 1", (run_date,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM daily_watch_lists ORDER BY run_date DESC, run_slot DESC LIMIT 1"
            ).fetchone()
    return dict(row) if row else None


def history(days=7):
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT run_date, run_slot, status, considered, symbols, funnel, created_at
                 FROM daily_watch_lists ORDER BY run_date DESC, run_slot DESC LIMIT %s""",
            (max(1, min(int(days or 7) * 2, 120)),)).fetchall()
    return [dict(r) for r in rows]


def status():
    last = get()
    return {
        "schedule_utc": [slot_name(h) for h in hours()],
        "hours_utc": hours(),
        "next_run_utc": next_run_at().isoformat().replace("+00:00", "Z"),
        "running": _running.locked(),
        "method": ["calendar (code)", "volatility (code)", "sentiment (code)",
                   "news + political posts (the one AI step)"],
        "agent": {"analysis_agent_id": AGENT_ID, "seeded": bool(agent_row()),
                  "editable_at": "/analysis-agents/" + AGENT_ID},
        "last_run": {
            "date": str(last["run_date"]), "slot": last["run_slot"], "status": last["status"],
            "watching": len(last["symbols"] or {}), "considered": last["considered"],
            "funnel": last.get("funnel"), "built_at": last["created_at"],
        } if last else None,
        "universe": {k: len(v) for k, v in UNIVERSE.items()},
    }


# ── schedule loop ──────────────────────────────────────────────────────────────
def _loop():
    _time.sleep(120)                                  # let the app finish booting
    try:
        seed()
    except Exception:
        pass
    while True:
        try:
            now = datetime.now(timezone.utc)
            if now.hour in hours() and not get(now.date(), slot_name(now.hour)):
                run_once(slot=slot_name(now.hour))
        except Exception as e:
            print(f"[watchlist] scheduled build failed: {e!r}", flush=True)
        _time.sleep(60)


def start():
    threading.Thread(target=_loop, daemon=True, name="watchlist").start()


if __name__ == "__main__":
    # manual build: `python watchlist.py`, or `python watchlist.py 00:00` to build
    # the list as that slot would have — same live data, clock set to that hour.
    at = None
    if len(sys.argv) > 1:
        hour = int(sys.argv[1].split(":")[0])
        at = datetime.now(timezone.utc).replace(hour=hour, minute=0, second=0, microsecond=0)
    row = run_once(force=True, as_of=at)
    print(json.dumps({"today_watch_list": {
        "date": str(row["run_date"]), "run_utc": row["run_slot"],
        "symbols": row["symbols"]}}, indent=2, default=str))
