"""
Market data: OHLC candles and live prices straight off the Exness session the
trading engine already holds open.

This one deliberately does NOT cache into Postgres — candles are always
available from the source, are large, and go stale the moment they're stored.
Every read hits Exness live through the shared authenticated session.
"""
import trading_api
from datetime import datetime, timezone

# Timeframes the Exness candles endpoint accepts, in minutes. Probed against the
# live API: 2, 7, 90, 180, 360, 480 and 720 are all rejected with a 400.
TIMEFRAMES = {
    "M1": 1, "M3": 3, "M5": 5, "M10": 10, "M15": 15, "M30": 30,
    "H1": 60, "H2": 120, "H4": 240, "D1": 1440, "W1": 10080, "MN1": 43200,
}
_SPELLINGS = {
    "1M": "M1", "3M": "M3", "5M": "M5", "10M": "M10", "15M": "M15", "30M": "M30",
    "1H": "H1", "2H": "H2", "4H": "H4", "1D": "D1", "1W": "W1", "1MN": "MN1",
    "HOURLY": "H1", "DAILY": "D1", "WEEKLY": "W1", "MONTHLY": "MN1",
    "DAY": "D1", "WEEK": "W1", "MONTH": "MN1", "MN": "MN1", "D": "D1", "W": "W1",
}
MAX_COUNT = 5000
_NOW = 9007199254740991      # the API's "latest" sentinel (max safe integer)


def resolve_timeframe(value) -> tuple:
    """('M15', 15) from 'M15', '15m', 15, 'daily'… Raises on anything the API
    would reject, naming what is supported."""
    raw = str(value if value is not None else "M15").upper().strip().replace(" ", "")
    name = _SPELLINGS.get(raw, raw)
    if name in TIMEFRAMES:
        return name, TIMEFRAMES[name]
    if raw.isdigit():                       # a raw number of minutes
        minutes = int(raw)
        for n, m in TIMEFRAMES.items():
            if m == minutes:
                return n, m
    raise ValueError(
        f"unsupported timeframe '{value}' — use one of {', '.join(TIMEFRAMES)} "
        "(or the equivalent minutes: " + ", ".join(str(m) for m in TIMEFRAMES.values()) + ")")


def _resolve_on(symbol: str, account) -> str:
    import agent
    t = trading_api.trader(account)
    resolved = agent._resolve_symbol(t, symbol)
    if not any(i["symbol"].upper() == str(resolved).upper() for i in t.instruments()):
        raise ValueError(f"unknown instrument '{symbol}' — not tradable on this account")
    return next(i["symbol"] for i in t.instruments()
                if i["symbol"].upper() == str(resolved).upper())


def resolve_symbol(symbol: str, account=None) -> str:
    """'gold' → XAUUSD, 'nasdaq' → USTEC, against a live instrument list.

    With no account named this walks the same fallback every other read uses, so
    one refused account cannot decide that nothing is tradable. It could: the
    daily watch list asked this for 39 instruments, the owner's active account
    was being refused, and all 39 "failed" without a single other account ever
    being tried.

    When an account IS named, it resolves on THAT account and nowhere else. The
    callers that pass one are mid-read on a chosen account, and a name resolved
    against a different broker's list would be silently wrong."""
    if account is not None:
        return _resolve_on(symbol, account)
    return _read(lambda a: _resolve_on(symbol, a),
                 retry_on=lambda e: isinstance(e, ValueError)
                 and "not tradable on this account" in str(e))[0]


def _parse_end(value):
    """'end' as ISO or epoch ms → epoch ms. None means 'up to now'."""
    if value in (None, "", "now"):
        return _NOW
    text = str(value).strip()
    if text.isdigit():
        ms = int(text)
        return ms if ms > 10 ** 12 else ms * 1000        # tolerate epoch seconds
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"can't read end='{value}' — use ISO (2026-07-24T12:00:00Z) or epoch ms")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


# ── reading market data through whatever account still works ──────────────────
#  Prices are the same for every account on a login. The account is only the
#  ROUTE to the gateway, so one refused account should never cost the caller its
#  candles — an assistant asking for a BTCUSD chart wants BTCUSD, not a lecture
#  about which account was active at the time.
#
#  A refusal is remembered briefly so the next call does not walk into the same
#  wall, and forgotten after that, because accounts come back.
_REFUSED = {}                  # account -> when it refused us
_REFUSED_TTL_S = 600


def _is_refusal(exc) -> bool:
    """A broker saying "not this account", as opposed to a real failure."""
    import brokers
    return brokers.is_refusal(exc)


def _candidates(preferred=None):
    """Accounts to try, best first: the one asked for, then the active one, then
    the rest the user has chosen."""
    import db
    out, seen = [], set()

    def add(a):
        if a in (None, ""):
            return
        a = str(a)
        if a not in seen:
            seen.add(a)
            out.append(a)

    add(preferred)
    add(trading_api._active_ctx.get())
    uid = trading_api._active_user_ctx.get()
    if uid:
        try:
            with db.connect() as conn:
                row = conn.execute(
                    "SELECT selected_accounts, active_account FROM exness_settings "
                    "WHERE user_id = %s", (uid,)).fetchone()
            if row:
                add(row["active_account"])
                for a in (row["selected_accounts"] or []):
                    add(a)
        except Exception:
            pass
    return out


def _login_accounts():
    """Every account this user owns, at EVERY broker they have connected.

    Only reached when the shortlist has run out. It used to ask the active
    broker alone, so a user whose Exness accounts were all refused never fell
    through to the TradeLocker account sitting right there and working — price
    data is price data, and gold is gold whoever quotes it.

    Order matters: the active broker first, because it is the one they chose."""
    import brokers, trading_api as ta
    uid = ta._active_user_ctx.get()
    active = ta._active_broker_ctx.get() or brokers.default_broker() or ""
    names = sorted(brokers.providers(), key=lambda b: (b != active, b))
    out, seen = [], set()
    for name in names:
        p = brokers.get(name)
        # The user matters: a broker that keeps its accounts in OUR database — as
        # TradeLocker does — cannot answer without knowing whose they are, and
        # silently returned nothing when it was not told.
        fn = getattr(p, "login_accounts", None) if p else None
        if not fn:
            continue
        try:
            for a in fn(uid):
                if str(a) not in seen:
                    seen.add(str(a))
                    out.append(str(a))
        except Exception:
            continue
    return out


def _read(run, account=None, retry_on=None):
    """Run `run(acct)` through the first account that can actually serve it.
    Returns (result, account_used).

    A REFUSAL moves on to the next account; a bad timeframe is the caller's
    error and is raised, because trying it on five accounts would fail five
    times and say nothing new. `retry_on` widens that for a caller who knows
    another kind of failure is also worth another account — symbol resolution
    passes "not tradable on this account", which is the whole point: a broker
    that does not carry gold is not a verdict on gold.""" 
    import time as _t
    now = _t.time()
    tried, last, seen_tried = [], None, set()
    for acct in _candidates(account):
        seen_tried.add(acct)
        if now - _REFUSED.get(acct, 0) < _REFUSED_TTL_S:
            continue
        try:
            return run(acct), acct
        except Exception as e:
            if not (_is_refusal(e) or (retry_on and retry_on(e))):
                raise
            _REFUSED[acct] = now
            tried.append(acct)
            last = e

    # The shortlist is exhausted. Ask the login what else it has — a user whose
    # three chosen accounts are all refused usually owns others that are not.
    for acct in _login_accounts():
        if acct in seen_tried or now - _REFUSED.get(acct, 0) < _REFUSED_TTL_S:
            continue
        try:
            return run(acct), acct
        except Exception as e:
            if not (_is_refusal(e) or (retry_on and retry_on(e))):
                raise
            _REFUSED[acct] = now
            last = e

    # Everything is marked refused. That marking may be stale, so clear it and
    # give the preferred account one honest attempt before failing.
    if tried or _REFUSED:
        _REFUSED.clear()
        for acct in _candidates(account)[:1]:
            try:
                return run(acct), acct
            except Exception as e:
                last = e
    import brokers
    # Not a fault: the accounts are known, none of them would serve this. A 500
    # would say the server broke, which sends the one person who can fix it
    # looking in the wrong place.
    raise last or brokers.NoAccount(
        "No account on this login could serve market data. Check an account is "
        "connected and active on the Accounts page.")


def candles(symbol, timeframe="M15", count=100, price="bid", end=None, account=None) -> dict:
    """The last `count` candles for `symbol` on `timeframe`, oldest first.

    `price` selects the bid or ask series; `end` walks back from a past moment
    instead of from now. `account` names WHICH account to read them through —
    without it the caller's active account is used, which is wrong the moment a
    caller asks about a different one, and fails in that account's name.""" 
    tf_name, tf_minutes = resolve_timeframe(timeframe)
    n = max(1, min(int(count or 100), MAX_COUNT))
    side = str(price or "bid").lower()
    if side not in ("bid", "ask"):
        raise ValueError("price must be 'bid' or 'ask'")

    def pull(acct):
        sym_ = resolve_symbol(symbol, acct)
        return sym_, trading_api.trader(acct).raw_candles(
            sym_, tf_minutes, n, side, _parse_end(end))

    (sym, rows), served_by = _read(pull, account)

    out = [{
        "time": _iso(r["t"]),
        "epoch_ms": r["t"],
        "open": r["o"], "high": r["h"], "low": r["l"], "close": r["c"],
        "volume": r.get("v"),
    } for r in rows]

    return {
        "symbol": sym,
        "timeframe": tf_name,
        "timeframe_minutes": tf_minutes,
        "price": side,
        # Which account actually served it. Prices are identical across accounts,
        # so this is provenance, not a caveat — but a silent substitution should
        # still be visible to anyone who looks.
        "account": served_by,
        "count": len(out),
        "requested": n,
        "from": out[0]["time"] if out else None,
        "to": out[-1]["time"] if out else None,
        "candles": out,
    }


def _split_csv(value) -> list:
    """['XAUUSD','GBPUSD','gold'] from 'XAUUSD,GBPUSD,gold' (or a list). Blanks
    dropped; separators are comma / semicolon / whitespace."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else \
        str(value).replace(";", ",").replace("|", ",").split(",")
    out = []
    for it in items:
        for part in str(it).split():          # also tolerate space-separated
            s = part.strip()
            if s:
                out.append(s)
    return out


def candles_multi(symbols, timeframes, count=100, price="bid", end=None, account=None) -> dict:
    """Candles for SEVERAL symbols and/or SEVERAL timeframes in one call — the
    cartesian product. `symbols`/`timeframes` may be comma-separated strings
    ('XAUUSD,GBPUSD,gold' / 'H4,H1,M15') or lists.

    Symbols are resolved and de-duplicated (so 'gold' and 'XAUUSD' collapse to one)
    and timeframes validated/de-duplicated. Each entry in `series` has the SAME
    shape as a single candles() reply; a bad symbol/timeframe is collected in
    `errors` rather than failing the whole request."""
    errors = []

    resolved, seen = [], set()
    for raw in (_split_csv(symbols) or []):
        try:
            sym = resolve_symbol(raw, account)
        except ValueError as e:
            errors.append({"symbol": raw, "error": str(e)})
            continue
        if sym.upper() not in seen:
            seen.add(sym.upper())
            resolved.append(sym)

    tfs, tseen = [], set()
    for raw in (_split_csv(timeframes) or ["M15"]):
        try:
            name, _ = resolve_timeframe(raw)
        except ValueError as e:
            errors.append({"timeframe": raw, "error": str(e)})
            continue
        if name not in tseen:
            tseen.add(name)
            tfs.append(name)

    series = []
    for sym in resolved:
        for tf in tfs:
            try:
                series.append(candles(sym, timeframe=tf, count=count, price=price,
                                      end=end, account=account))
            except ValueError as e:
                errors.append({"symbol": sym, "timeframe": tf, "error": str(e)})

    return {
        "multi": True,
        "symbols": resolved,
        "timeframes": tfs,
        "price": str(price or "bid").lower(),
        "requested_count": max(1, min(int(count or 100), MAX_COUNT)),
        "series_count": len(series),
        "series": series,
        "errors": errors,
    }


def quote(symbol, account=None) -> dict:
    """Live bid / ask / spread for one instrument."""
    def pull(acct):
        sym_ = resolve_symbol(symbol, acct)
        t = trading_api.trader(acct)
        ins = next((i for i in t.instruments() if i["symbol"] == sym_), {})
        return sym_, ins, t.price(sym_, "bid"), t.price(sym_, "ask")

    (sym, instrument, bid, ask), _ = _read(pull, account)
    digits = int(instrument.get("digits", 5))
    return {
        "symbol": sym,
        "bid": bid,
        "ask": ask,
        "spread": round(ask - bid, digits),
        "digits": digits,
        "time": _iso(int(datetime.now(timezone.utc).timestamp() * 1000)),
    }


def chart(symbol, timeframe="M15", count=150, account=None) -> dict:
    """A chart payload for the chat UI: candles plus the account's own trades on
    this instrument, so entries, stops and targets can be drawn on the price.

    Each level is marked `in_range` when it falls inside the candles' own high/low
    band. A stop 400 points below a tight intraday window would flatten the
    candles if the chart auto-scaled to it, so the UI draws off-range levels as
    labels instead of squeezing the price action."""
    data = candles(symbol, timeframe=timeframe, count=count, account=account)
    sym = data["symbol"]
    lo = min((c["low"] for c in data["candles"]), default=None)
    hi = max((c["high"] for c in data["candles"]), default=None)

    def level(value):
        v = float(value or 0)
        if not v:
            return None
        return {"price": v, "in_range": bool(lo is not None and lo <= v <= hi)}

    # The overlay is the one part that IS account-specific, and it is optional:
    # a chart with no trades drawn on it is still a chart. An account that will
    # not answer costs its own markers, never the price.
    try:
        t = trading_api.trader(account)
    except Exception:
        t = None
    trades, orders = [], []
    try:
        for p in (t.positions() if t else []):
            if (p.get("instrument") or "").upper() != sym.upper():
                continue
            trades.append({
                "position_id": str(p.get("position_id")),
                "side": "buy" if int(p.get("type", 0)) % 2 == 0 else "sell",
                "volume": p.get("volume"),
                "open_price": level(p.get("open_price")),
                "sl": level(p.get("sl")),
                "tp": level(p.get("tp")),
                "profit": p.get("profit"),
                "opened_at": p.get("open_time"),
            })
    except Exception:
        pass
    try:
        for o in (t.orders() if t else []):
            if (o.get("instrument") or "").upper() != sym.upper():
                continue
            orders.append({
                "ticket": str(o.get("order_id") or o.get("ticket") or ""),
                "side": "buy" if int(o.get("type", 0)) % 2 == 0 else "sell",
                "volume": o.get("volume"),
                "price": level(o.get("price") or o.get("open_price")),
                "sl": level(o.get("sl")),
                "tp": level(o.get("tp")),
            })
    except Exception:
        pass

    return {
        "chart": True,                    # the chat UI renders on this flag
        **data,                           # carries `account`: whoever served the candles
        "price_range": {"low": lo, "high": hi},
        "trades": trades,
        "orders": orders,
        # The account the OVERLAY belongs to, which is the one that was asked
        # for even when its markers could not be read.
        "overlay_account": int(account) if account else (int(t.account) if t else None),
    }


def timeframes() -> dict:
    return {"timeframes": [{"name": n, "minutes": m} for n, m in TIMEFRAMES.items()],
            "max_count": MAX_COUNT}
