"""
Browser-usable trading endpoints (GET + api_key query param).

Every endpoint wraps a real exness_trading.ExnessTrader method so each documented
URL is a working example. `side` is always REQUIRED (never defaulted).

Auth: `api_key` query param (validated against api_keys.key_hash). All requests
act on the configured demo trading account.
"""
import sys
import json
import time as _time
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # import engine from project root

from fastapi import APIRouter, HTTPException, Query
from psycopg.types.json import Json

import auth
import db

import contextvars

router = APIRouter(prefix="/api/v1")

_traders = {}
_active_ctx = contextvars.ContextVar("active_account", default=None)
_active_broker_ctx = contextvars.ContextVar("active_broker", default=None)
_active_user_ctx = contextvars.ContextVar("active_user", default=None)


def resolve_broker(user_id, account_ref):
    """Which broker owns `account_ref` for this user.

    Each broker answers for itself through `owns_account`; core does not read any
    broker's table. A broker that keeps a definitive list of the accounts it owns
    — TradeLocker does — settles it. One that cannot, because any number on the
    login is valid, says yes to anything and so is asked last."""
    if user_id is None or account_ref is None:
        return None
    import brokers
    catch_all = None
    for bid, p in brokers.providers().items():
        fn = getattr(p, "owns_account", None)
        if fn is None:
            continue
        try:
            if not fn(user_id, account_ref):
                continue
        except Exception:
            continue
        # A broker that owns a listed account is definitive; one that owns
        # everything is only a fallback.
        if getattr(p, "owns_any_account", False):
            catch_all = catch_all or bid
        else:
            return bid
    # Nobody claimed it. With one broker installed that broker still gets it, so
    # the caller hears its specific "that account isn't connected" rather than a
    # vague one; with several, nothing is guessed.
    return catch_all or brokers.default_broker()


def trader(account=None, broker=None):
    """Return the canonical Trader adapter for `account` (or the current user's
    active account). Resolves the broker and builds the matching adapter —
    the adapter comes from whichever broker module owns that account, and every
    broker exposes the same canonical interface, so callers are broker-agnostic.
    NEVER falls back to a hardcoded account. Cached per (broker, user, account) so
    users never share an adapter."""
    acct = account if account is not None else _active_ctx.get()
    if acct is None:
        import brokers
        raise brokers.NoAccount(
            "No active trading account. Connect a broker on the Accounts page, "
            "then set one of its accounts active.")
    uid = _active_user_ctx.get()
    if broker is None:
        import brokers
        broker = (resolve_broker(uid, acct) if account is not None else None) \
            or _active_broker_ctx.get() or brokers.default_broker() or "exness"

    key = (broker, str(uid), str(acct))
    t = _traders.get(key)
    if t is None:
        import brokers
        t = brokers.adapter(broker, acct, uid)   # the broker module builds it
        _traders[key] = t
    return t


def _active_account(user_id):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT active_account FROM exness_settings WHERE user_id = %s", (user_id,)
        ).fetchone()
    return row["active_account"] if row and row["active_account"] else None


def api_user(api_key: str):
    if not api_key:
        raise HTTPException(401, "Missing api_key")
    kh = auth.hash_key(api_key)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id, user_id FROM api_keys WHERE key_hash = %s AND revoked_at IS NULL",
            (kh,),
        ).fetchone()
        if not row:
            raise HTTPException(401, "Invalid or revoked api_key")
        conn.execute("UPDATE api_keys SET last_used_at = now() WHERE id = %s", (row["id"],))
        conn.commit()
    # every action runs on THIS user's own Exness session + active account
    import user_session
    user_session.bind(row["user_id"])   # sets session AND active account (no hardcoded fallback)
    return row


# ══ READS ═══════════════════════════════════════════════════════════════════════
@router.get("/server")
def server(api_key: str = Query(...)):
    api_user(api_key)
    return trader().server()


@router.get("/balance")
def balance(api_key: str = Query(...)):
    """Account stats: balance, equity, margin, free margin + floating profit."""
    api_user(api_key)
    b = trader().balance()
    b["floating_profit"] = round(float(b.get("equity", 0)) - float(b.get("balance", 0)), 2)
    return b


@router.get("/positions")
def positions(api_key: str = Query(...)):
    api_user(api_key)
    return {"positions": trader().positions()}


@router.get("/orders")
def orders(api_key: str = Query(...)):
    api_user(api_key)
    return {"orders": trader().orders()}


MIN_FIELDS = ["symbol", "international", "category", "digits", "volume_min", "volume_step"]


def _filter_instruments(category=None, search=None, symbol=None):
    ins = trader().instruments()
    if symbol:
        ins = [i for i in ins if i.get("symbol", "").upper() == symbol.upper()]
    if search:
        s = search.upper()
        ins = [i for i in ins
               if s in i.get("symbol", "").upper() or s in i.get("international", "").upper()]
    if category:
        c = category.lower()
        ins = [i for i in ins if i.get("category", "").lower() == c]
    return ins


@router.get("/instruments")
def instruments(
    api_key: str = Query(...),
    category: str = Query(None),
    search: str = Query(None),
    symbol: str = Query(None),
    data: str = Query("full"),   # 'full' | 'min'
):
    """List instruments. Filter by category, substring search, or exact symbol.
    data=min returns only the essential fields per symbol."""
    api_user(api_key)
    ins = _filter_instruments(category, search, symbol)
    if data == "min":
        ins = [{k: i.get(k) for k in MIN_FIELDS} for i in ins]
    return {"count": len(ins), "instruments": ins}


@router.get("/instruments/symbols")
def instrument_symbols(api_key: str = Query(...), category: str = Query(None),
                       search: str = Query(None)):
    """Symbols only — a flat list of symbol strings (with optional category /
    search filters). Use search for symbol lookup, e.g. search=CAD."""
    api_user(api_key)
    ins = _filter_instruments(category, search, None)
    return {"count": len(ins), "symbols": [i.get("symbol") for i in ins]}


@router.get("/instruments/categories")
def instrument_categories(api_key: str = Query(...)):
    """Distinct instrument categories with a count of symbols in each."""
    api_user(api_key)
    from collections import Counter
    cats = Counter(i.get("category", "") for i in trader().instruments())
    return {"categories": [{"category": c, "count": n} for c, n in sorted(cats.items())]}


@router.get("/symbol")
def symbol_details(api_key: str = Query(...), symbol: str = Query(...)):
    """Everything about ONE symbol: full spec, live bid/ask/spread, and the
    account's open positions and pending orders on it."""
    api_user(api_key)
    t = trader()
    sym = symbol.upper()
    instrument = next((i for i in t.instruments() if i.get("symbol", "").upper() == sym), None)
    if not instrument:
        raise HTTPException(404, f"Unknown symbol: {symbol}")

    bid = t.price(instrument["symbol"], "bid")
    ask = t.price(instrument["symbol"], "ask")
    digits = int(instrument.get("digits", 5))

    positions = [p for p in t.positions() if (p.get("instrument") or "").upper() == sym]
    orders = [o for o in t.orders() if (o.get("instrument") or "").upper() == sym]
    floating = round(sum(float(p.get("profit", 0) or 0) for p in positions), 2)
    net_volume = round(
        sum((p.get("volume", 0) if int(p.get("type", 0)) % 2 == 0 else -p.get("volume", 0))
            for p in positions), 2)

    return {
        "symbol": instrument["symbol"],
        "instrument": instrument,
        "price": {"bid": bid, "ask": ask, "spread": round(ask - bid, digits)},
        "positions": positions,
        "orders": orders,
        "summary": {
            "open_positions": len(positions),
            "pending_orders": len(orders),
            "net_volume": net_volume,
            "floating_profit": floating,
        },
    }


@router.get("/price")
def price(api_key: str = Query(...), symbol: str = Query(...), side: str = Query(...)):
    api_user(api_key)  # side = bid | ask
    return {"symbol": symbol, "side": side, "price": trader().price(symbol, side)}


@router.get("/total-profit")
def total_profit(api_key: str = Query(...)):
    api_user(api_key)
    return {"total_profit": trader().total_profit()}


# ══ SL/TP CALCULATOR (analysis) ═════════════════════════════════════════════════
@router.get("/calc/sltp")
def calc_sltp(api_key: str = Query(...), symbol: str = Query(...),
              volume: float = Query(...), side: str = Query(...),
              entry: float = Query(None), level: float = Query(None),
              points: float = Query(None), money: float = Query(None),
              mode: str = Query("tp")):
    """
    Versatile SL/TP ⇄ points ⇄ money calculator — for ANY conversion on a trade.
    Give `symbol`, `volume`, `side`; `entry` defaults to the live quote; then
    pass EXACTLY ONE of `money` (target $, with mode=tp|sl), `points` (distance,
    with mode), or `level` (a price → resulting $ P/L, mode auto-detected).
    """
    api_user(api_key)
    if side not in ("buy", "sell"):
        raise HTTPException(400, "side must be 'buy' or 'sell'")
    try:
        return trader().sltp_calc(symbol, volume, side, entry=entry, level=level,
                                  points=points, money=money, mode=mode)
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/calc/risk")
def calc_risk(api_key: str = Query(...), symbol: str = Query(...), side: str = Query(...),
              entry: float = Query(None),
              risk_pct: float = Query(None), risk_money: float = Query(None),
              sl: float = Query(None), sl_points: float = Query(None),
              tp: float = Query(None), tp_points: float = Query(None),
              rr: float = Query(None), volume: float = Query(None),
              basis: str = Query(None)):
    """
    Complete, symbol-aware risk/reward engine — the ONE call for position sizing
    and stop/target placement, so the agent never does risk maths by hand.

    risk_money = stop_distance · $/price · volume. Give ANY TWO of {risk, stop,
    volume} and it solves the third:
      • SIZE     — risk_pct|risk_money + sl|sl_points  → the VOLUME to risk that much.
      • STOP     — risk_pct|risk_money + volume        → WHERE the stop goes.
      • VALIDATE — sl|sl_points + volume               → what the trade really risks.

    Risk: `risk_pct` (% of `basis` = equity|balance) or `risk_money` (account ccy).
    Stop: `sl` (a PRICE) or `sl_points` (distance). Target (optional): `rr`
    (reward:risk → TP at rr× the stop) or `tp`/`tp_points`. `entry` defaults to the
    live quote. Everything returned — volume (snapped to step/min), sl/tp prices,
    realised risk/reward, rr, per-point value, margin — is in the account currency.
    """
    api_user(api_key)
    if side not in ("buy", "sell"):
        raise HTTPException(400, "side must be 'buy' or 'sell'")
    s = _risk_settings(_active_user_ctx.get(), _active_ctx.get())
    # When sizing (a stop but no volume) and no risk stated, use the user's settings.
    if volume is None and (sl is not None or sl_points is not None):
        risk_pct, risk_money = _effective_risk(s, risk_pct, risk_money)
    if rr is None and tp is None and tp_points is None and s["reward_rr"]:
        rr = s["reward_rr"]
    try:
        return trader().risk_plan(symbol, side, entry=entry, risk_pct=risk_pct,
                                  risk_money=risk_money, sl=sl, sl_points=sl_points,
                                  tp=tp, tp_points=tp_points, rr=rr, volume=volume,
                                  basis=basis or s["risk_basis"])
    except Exception as e:
        raise HTTPException(400, str(e))


RISK_DEFAULT_PCT = 2.0   # the ONLY hardcoded fallback — used only when a user set nothing


def _risk_settings(uid, account=None):
    """Resolve a user's risk parameters for `account`: the account-scoped row
    (account = the number/id) overrides the profile-wide row (account = ''), field
    by field. Returns every parameter (None where the user set nothing). The 2%
    fallback for risk_pct is applied only at use, by `_effective_risk`."""
    base = {"risk_pct": None, "reward_rr": None, "max_dd_day": None,
            "max_dd_week": None, "max_dd_month": None, "trading_hours": [],
            "trading_tz": "UTC", "risk_basis": "equity", "trade_style": None,
            "scope": "none"}
    if not uid:
        return base
    acct = str(account) if account not in (None, "") else None
    try:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM risk_settings WHERE user_id = %s AND account = ANY(%s)",
                (uid, [x for x in ("", acct) if x is not None])).fetchall()
    except Exception:
        return base
    prof = next((r for r in rows if r["account"] == ""), None)
    over = next((r for r in rows if r["account"] == acct), None) if acct else None

    def merged(field, empty=None):
        if over and over.get(field) not in (None, empty):
            return over.get(field)
        if prof and prof.get(field) not in (None, empty):
            return prof.get(field)
        return base[field]

    out = dict(base)
    for f in ("risk_pct", "reward_rr", "max_dd_day", "max_dd_week", "max_dd_month", "trade_style"):
        out[f] = merged(f)
    out["trading_hours"] = merged("trading_hours", []) or []
    out["trading_tz"] = merged("trading_tz", "") or "UTC"
    out["risk_basis"] = merged("risk_basis", "") or "equity"
    out["scope"] = "account" if over else ("profile" if prof else "none")
    return out


def _effective_risk(settings, risk_pct, risk_money):
    """Fill risk from the user's settings when a request states none; fall back to
    RISK_DEFAULT_PCT (2%) only if the user configured nothing at all."""
    if risk_pct is None and risk_money is None:
        risk_pct = settings.get("risk_pct")
        if risk_pct is None:
            risk_pct = RISK_DEFAULT_PCT
    return risk_pct, risk_money


def _trading_hours_status(hours, tz):
    """Is 'now' inside an allowed trading window? Windows are [{start,end}] as
    'HH:MM' in `tz` (IANA). No windows ⇒ unrestricted (always open)."""
    if not hours:
        return {"restricted": False, "open": True, "now": None, "tz": tz or "UTC", "windows": []}
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(tz or "UTC"))
    except Exception:
        now, tz = datetime.now(timezone.utc), "UTC"
    cur = now.hour * 60 + now.minute

    def mins(x):
        h, m = str(x).split(":")
        return int(h) * 60 + int(m)

    open_now = False
    for w in hours:
        try:
            s, e = mins(w["start"]), mins(w["end"])
        except Exception:
            continue
        if (s <= cur < e) if s <= e else (cur >= s or cur < e):   # handle past-midnight wrap
            open_now = True
            break
    return {"restricted": True, "open": open_now, "tz": tz,
            "now": now.strftime("%H:%M"), "windows": hours}


def risk_status(uid=None, account=None):
    """Live risk dashboard for the active (or given) account: realised drawdown
    today / this week / this month (with open floating folded in) against the
    user's limits, plus whether their trading hours allow a trade right now.
    All money in the account currency."""
    uid = uid or _active_user_ctx.get()
    acct = account if account is not None else _active_ctx.get()
    t = trader(acct) if acct is not None else trader()
    s = _risk_settings(uid, acct if acct is not None else getattr(t, "account", None))
    bal = t.balance() or {}
    balance = float(bal.get("balance") or 0) or 0.0
    equity = float(bal.get("equity") or 0) or 0.0
    floating = round(equity - balance, 2)

    now = datetime.now().astimezone()
    ms = lambda dt: int(dt.timestamp() * 1000)
    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    monday = start_today - timedelta(days=now.weekday())
    month1 = start_today.replace(day=1)

    def realised(frm):
        try:
            return float(t.pnl_summary(ms(frm), ms(now)).get("net_profit") or 0)
        except Exception:
            return 0.0

    def leg(frm, limit_pct):
        realised_pl = realised(frm)
        pl_incl = realised_pl + floating                    # fold in open floating
        dd_money = max(0.0, -pl_incl)                        # drawdown = the loss, if any
        dd_pct = round(dd_money / balance * 100, 3) if balance else None
        return {"realised": round(realised_pl, 2), "pl_including_floating": round(pl_incl, 2),
                "drawdown": round(dd_money, 2), "drawdown_pct": dd_pct,
                "limit_pct": limit_pct,
                "limit_money": round(balance * limit_pct / 100, 2) if (limit_pct and balance) else None,
                "breached": bool(limit_pct and dd_pct is not None and dd_pct >= limit_pct)}

    dd = {"day": leg(start_today, s["max_dd_day"]),
          "week": leg(monday, s["max_dd_week"]),
          "month": leg(month1, s["max_dd_month"])}
    hours = _trading_hours_status(s["trading_hours"], s["trading_tz"])
    breaches = [k for k in ("day", "week", "month") if dd[k]["breached"]]
    parts = []
    if not hours["open"]:
        parts.append("outside allowed trading hours")
    if breaches:
        parts.append("drawdown limit hit (" + ", ".join(breaches) + ")")
    return {
        "account_currency": t.account_currency(), "scope": s["scope"],
        "balance": round(balance, 2), "equity": round(equity, 2), "floating": floating,
        "risk_pct": s["risk_pct"], "reward_rr": s["reward_rr"], "risk_basis": s["risk_basis"],
        "trade_style": s["trade_style"], "drawdown": dd, "trading_hours": hours,
        "breaches": breaches, "can_trade_now": bool(hours["open"] and not breaches),
        "note": ("Do NOT open new trades — " + "; ".join(parts) + ".") if parts else "Within all risk limits — OK to trade.",
    }


@router.get("/calc/auto-sltp")
def calc_auto_sltp(api_key: str = Query(...), symbol: str = Query(...), side: str = Query(...),
                   style: str = Query(None), entry: float = Query(None),
                   risk_pct: float = Query(None), risk_money: float = Query(None),
                   rr: float = Query(None), basis: str = Query(None),
                   sl_mode: str = Query("structure")):
    """
    SMART auto SL/TP + position sizing — the agent gives only symbol, side and a
    style; the engine reads live candles and decides everything else.

    It places the STOP from market structure (recent swing high/low) with an ATR
    floor and the broker's stop-level as guards, the TARGET from a style-defaulted
    reward:risk (override with `rr`), and the LOT SIZE so hitting the stop loses
    exactly the risk budget. `style`: scalp | intraday | swing | position.

    Risk budget: pass `risk_pct` (% of `basis` equity|balance) or `risk_money`; if
    BOTH are omitted the user's saved default risk settings are used. `sl_mode`:
    structure (default) | atr | swing. Everything returned is in the account
    currency — volume, sl/tp prices, realised risk/reward, ATR and margin.
    """
    api_user(api_key)
    if side not in ("buy", "sell"):
        raise HTTPException(400, "side must be 'buy' or 'sell'")
    s = _risk_settings(_active_user_ctx.get(), _active_ctx.get())
    risk_pct, risk_money = _effective_risk(s, risk_pct, risk_money)
    if rr is None:
        rr = s["reward_rr"]
    try:
        return trader().auto_sltp(symbol, side, style=style or s["trade_style"] or "intraday",
                                  entry=entry, risk_pct=risk_pct, risk_money=risk_money,
                                  rr=rr, basis=basis or s["risk_basis"], sl_mode=sl_mode)
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/risk-status")
def risk_status_endpoint(api_key: str = Query(...)):
    """Live risk dashboard for the active account: realised drawdown today / this
    week / this month vs the user's limits (open floating folded in), and whether
    their configured trading hours allow a new trade right now. `can_trade_now`
    is the single gate. All money in the account currency."""
    api_user(api_key)
    try:
        return risk_status()
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/calc/point-value")
def calc_point_value(api_key: str = Query(...), symbol: str = Query(...),
                     volume: float = Query(1.0), price: float = Query(None)):
    """USD value of a one-point move (and of a full 1.0 price move) for `volume`
    lots. `price` optional — defaults to the live quote (needed for USD-base FX)."""
    api_user(api_key)
    return trader().point_value(symbol, volume, price)


@router.get("/calc/profit-target")
def calc_profit_target(api_key: str = Query(...), symbol: str = Query(...),
                       entry: float = Query(...), target: float = Query(...),
                       volume: float = Query(...), side: str = Query(...),
                       mode: str = Query("tp")):
    """Exact TP/SL price for a target dollar amount.
    mode=tp → `target` is the profit wanted; mode=sl → the loss accepted."""
    api_user(api_key)
    if mode not in ("tp", "sl"):
        raise HTTPException(400, "mode must be 'tp' or 'sl'")
    if side not in ("buy", "sell"):
        raise HTTPException(400, "side must be 'buy' or 'sell'")
    return trader().profit_target(symbol, entry, target, volume, side, mode)


@router.get("/calc/basket-target")
def calc_basket_target(api_key: str = Query(...), target: float = Query(...),
                       positions: str = Query(None), mode: str = Query("tp"),
                       split: str = Query("equal"), symbol: str = Query(None),
                       apply: bool = Query(False)):
    """
    Per-position TP/SL so that when ALL are hit the basket totals `target` USD.

    `positions`: JSON list of {symbol, entry, volume, side}. Omit it to use the
    account's LIVE open positions (optionally filtered by `symbol`).
    `split`: equal (target/N each) | weighted (∝ exposure → equal points).
    `apply=true`: write each computed level onto the live position (keeps the
    other side unchanged). Requires a position_id per leg (live positions have it).
    """
    api_user(api_key)
    if mode not in ("tp", "sl"):
        raise HTTPException(400, "mode must be 'tp' or 'sl'")
    t = trader()

    if positions:
        rows = json.loads(positions)
    else:
        rows = []
        for p in t.positions():
            if symbol and (p.get("instrument") or "").upper() != symbol.upper():
                continue
            rows.append({
                "symbol": p.get("instrument"),
                "entry": float(p.get("open_price") or 0),
                "volume": float(p.get("volume") or 0),
                "side": "buy" if int(p.get("type", 0)) % 2 == 0 else "sell",
                "position_id": p.get("position_id"),
                "sl": p.get("sl") or 0, "tp": p.get("tp") or 0,
            })
    if not rows:
        raise HTTPException(400, "no positions to compute (none open / none matched)")

    res = t.basket_target(rows, target, mode=mode, split=split)
    for leg, row in zip(res["legs"], rows):
        leg["position_id"] = row.get("position_id")

    if apply:
        applied = []
        for leg, row in zip(res["legs"], rows):
            pid = leg.get("position_id")
            if not pid:
                applied.append({"position_id": None, "ok": False,
                                "error": "no position_id for this leg"})
                continue
            # preserve the level we're NOT setting so modify doesn't clear it
            kw = {"tp": leg["level"], "sl": float(row.get("sl") or 0)} if mode == "tp" \
                else {"sl": leg["level"], "tp": float(row.get("tp") or 0)}
            try:
                t.modify_position(pid, **kw)
                applied.append({"position_id": pid, "ok": True, **kw})
            except Exception as e:
                applied.append({"position_id": pid, "ok": False, "error": str(e)})
        res["applied"] = applied
    return res


# ══ MARKET ORDERS ═══════════════════════════════════════════════════════════════
@router.get("/place-order")
def place_order(
    api_key: str = Query(...),
    symbol: str = Query(...),
    side: str = Query(...),                 # REQUIRED: buy | sell
    volume: float = Query(0.1),
    sl: float = Query(0),
    tp: float = Query(0),
    sl_points: int = Query(None),
    tp_points: int = Query(None),
    deviation: int = Query(0),
    emergency: bool = Query(False),         # skip the margin pre-check
):
    api_user(api_key)
    if side not in ("buy", "sell"):
        raise HTTPException(400, "side must be 'buy' or 'sell'")
    try:
        return trader().place_order(symbol, volume, side, sl=sl, tp=tp, deviation=deviation,
                                    sl_points=sl_points, tp_points=tp_points, emergency=emergency)
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/place-order-bulk")
def place_order_bulk(
    api_key: str = Query(...),
    symbol: str = Query(...),
    side: str = Query(...),                 # REQUIRED: buy | sell
    count: int = Query(5),
    volume: float = Query(0.1),
    sl: float = Query(0),
    tp: float = Query(0),
    sl_points: int = Query(None),
    tp_points: int = Query(None),
    delay_ms: int = Query(0),
    deviation: int = Query(0),
    emergency: bool = Query(False),         # skip the batch margin pre-check
):
    api_user(api_key)
    t = trader()
    if side == "buy":
        fn = t.market_buy_bulk
    elif side == "sell":
        fn = t.market_sell_bulk
    else:
        raise HTTPException(400, "side must be 'buy' or 'sell'")
    return fn(symbol, volume, count, sl=sl, tp=tp, sl_points=sl_points,
              tp_points=tp_points, delay_ms=delay_ms, deviation=deviation, emergency=emergency)


@router.get("/snipe")
def snipe(
    api_key: str = Query(...),
    symbol: str = Query(...),
    side: str = Query(...),                 # REQUIRED: buy | sell
    volume: float = Query(0.1),
    price: float = Query(None),
    sl: float = Query(0),
    tp: float = Query(0),
    sl_points: int = Query(None),
    tp_points: int = Query(None),
    deviation: int = Query(1000),
):
    api_user(api_key)
    if side not in ("buy", "sell"):
        raise HTTPException(400, "side must be 'buy' or 'sell'")
    return trader().snipe(symbol, volume, side, price=price, sl=sl, tp=tp,
                          sl_points=sl_points, tp_points=tp_points, deviation=deviation)


# ══ PENDING ORDERS ══════════════════════════════════════════════════════════════
@router.get("/pending-order")
def pending_order(
    api_key: str = Query(...),
    symbol: str = Query(...),
    side: str = Query(...),                 # REQUIRED: buy_limit|sell_limit|buy_stop|sell_stop
    price: float = Query(...),
    volume: float = Query(0.1),
    sl: float = Query(0),
    tp: float = Query(0),
    sl_points: int = Query(None),
    tp_points: int = Query(None),
):
    api_user(api_key)
    return trader().pending_order(symbol, volume, side, price, sl=sl, tp=tp,
                                  sl_points=sl_points, tp_points=tp_points)


@router.get("/pending-bulk")
def pending_bulk(
    api_key: str = Query(...),
    symbol: str = Query(...),
    side: str = Query(...),                 # REQUIRED: buy_limit|sell_limit|buy_stop|sell_stop
    count: int = Query(5),
    price: float = Query(...),
    step_points: int = Query(0),
    volume: float = Query(0.1),
    sl: float = Query(0),
    tp: float = Query(0),
    sl_points: int = Query(None),
    tp_points: int = Query(None),
    delay_ms: int = Query(0),
):
    api_user(api_key)
    return trader().pending_bulk(symbol, volume, side, count, price, step_points=step_points,
                                 sl=sl, tp=tp, sl_points=sl_points, tp_points=tp_points,
                                 delay_ms=delay_ms)


@router.get("/modify-order")
def modify_order(
    api_key: str = Query(...),
    ticket: str = Query(...),
    price: float = Query(None),
    sl: float = Query(None),
    tp: float = Query(None),
):
    api_user(api_key)
    return trader().modify_order(ticket, price=price, sl=sl, tp=tp)


@router.get("/cancel-order")
def cancel_order(api_key: str = Query(...), ticket: str = Query(...)):
    api_user(api_key)
    return trader().cancel_order(ticket)


@router.get("/cancel-orders")
def cancel_orders(api_key: str = Query(...), ticket: str = Query(None), symbol: str = Query(None)):
    api_user(api_key)  # no ticket/symbol → cancels ALL pending
    return {"result": trader().cancel_orders(ticket=ticket, symbol=symbol)}


# ══ POSITION MANAGEMENT ═════════════════════════════════════════════════════════
@router.get("/close")
def close(api_key: str = Query(...), position_id: str = Query(None),
          symbol: str = Query(None), volume: float = Query(None), only: str = Query(None)):
    # Versatile: no filter → ALL. only=profit|loss closes just winners|losers.
    api_user(api_key)
    return {"result": trader().close(position_id=position_id, symbol=symbol,
                                     volume=volume, only=only)}


@router.get("/break-even")
def break_even(api_key: str = Query(...), position_id: str = Query(None),
               symbol: str = Query(None), offset_points: int = Query(0)):
    api_user(api_key)
    return {"result": trader().break_even(position_id=position_id, symbol=symbol,
                                          offset_points=offset_points)}


@router.get("/modify-position")
def modify_position(api_key: str = Query(...), position_id: str = Query(...),
                    sl: float = Query(0), tp: float = Query(0)):
    api_user(api_key)
    return trader().modify_position(position_id, sl=sl, tp=tp)


@router.get("/delete-sltp")
def delete_sltp(api_key: str = Query(...), position_id: str = Query(None),
                symbol: str = Query(None), which: str = Query("both")):
    api_user(api_key)  # which = both | sl | tp
    sl = which in ("both", "sl")
    tp = which in ("both", "tp")
    return {"result": trader().remove_levels(position_id=position_id, symbol=symbol, sl=sl, tp=tp)}


# ══ PROFIT ══════════════════════════════════════════════════════════════════════
@router.get("/lock-profit")
def lock_profit(api_key: str = Query(...), percent: float = Query(...),
                position_id: str = Query(None), symbol: str = Query(None)):
    api_user(api_key)
    return {"result": trader().lock_profit(percent, position_id=position_id, symbol=symbol)}


# ── lock-profit-money: server-side background monitor ───────────────────────────
class _Stop(Exception):
    pass


class _Monitor:
    def __init__(self):
        self.thread = None
        self.stop_evt = threading.Event()
        self.state = {"running": False}


_monitor = _Monitor()


def _run_monitor(percent, ref, account):
    m = _monitor

    def on_update(cur, peak):
        m.state.update(current=cur, peak=peak)
        if m.stop_evt.is_set():
            raise _Stop()

    try:
        res = trader(account).lock_profit_money(percent, ref=ref, on_update=on_update)
        m.state.update(running=False, result=res, triggered=res.get("triggered"))
    except _Stop:
        m.state.update(running=False, stopped=True)
    except Exception as e:
        m.state.update(running=False, error=str(e))


@router.get("/lock-profit-money")
def lock_profit_money(api_key: str = Query(...), percent: float = Query(...),
                      ref: str = Query("peak")):
    """
    START a server-side monitor: watches total floating profit over the live
    WebSocket and closes ALL trades if it retraces to `percent`% of its
    reference (ref=peak default, or start). Returns immediately; keeps running
    server-side. Poll /lock-profit-money/status or /lock-profit-money/stop.
    """
    api_user(api_key)
    m = _monitor
    if m.state.get("running"):
        return {"status": "already running", **m.state}
    account = int(trader().account)   # capture active account for the thread
    m.stop_evt.clear()
    m.state = {"running": True, "percent": percent, "ref": ref, "account": account}
    m.thread = threading.Thread(target=_run_monitor, args=(percent, ref, account), daemon=True)
    m.thread.start()
    return {"status": "monitoring started", "percent": percent, "ref": ref, "account": account}


@router.get("/lock-profit-money/status")
def lock_profit_money_status(api_key: str = Query(...)):
    api_user(api_key)
    return _monitor.state


@router.get("/lock-profit-money/stop")
def lock_profit_money_stop(api_key: str = Query(...)):
    api_user(api_key)
    if not _monitor.state.get("running"):
        return {"status": "not running"}
    _monitor.stop_evt.set()
    return {"status": "stopping"}


# ══ ACCOUNT & HISTORY ═══════════════════════════════════════════════════════════
_RANGES = ["today", "yesterday", "this_week", "last_week", "last_2_weeks",
           "last_month", "last_3_months", "last_6_months"]


def _range_bounds(key):
    now = datetime.now().astimezone()
    ms = lambda dt: int(dt.timestamp() * 1000)
    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    monday = start_today - timedelta(days=now.weekday())
    if key == "today":         return ms(start_today), ms(now)
    if key == "yesterday":     return ms(start_today - timedelta(days=1)), ms(start_today)
    if key == "this_week":     return ms(monday), ms(now)
    if key == "last_week":     return ms(monday - timedelta(days=7)), ms(monday)
    if key == "last_2_weeks":  return ms(now - timedelta(days=14)), ms(now)
    if key == "last_month":    return ms(now - timedelta(days=30)), ms(now)
    if key == "last_3_months": return ms(now - timedelta(days=90)), ms(now)
    if key == "last_6_months": return ms(now - timedelta(days=180)), ms(now)
    raise HTTPException(400, f"range must be one of: {', '.join(_RANGES)}")


@router.get("/account")
def account(api_key: str = Query(...), range: str = Query("last_month")):
    """Account info + balance/floating profit + realised P/L totals over a period
    (all-time-ish; pass range=last_6_months for the widest window)."""
    api_user(api_key)
    t = trader()
    b = t.balance()
    b["floating_profit"] = round(float(b.get("equity", 0)) - float(b.get("balance", 0)), 2)
    frm, to = _range_bounds(range)
    return {"account": int(t.account), "info": t.account_info(),
            "balance": b, "pnl_range": range, "pnl": t.pnl_summary(frm, to)}


@router.get("/history")
def history(api_key: str = Query(...), range: str = Query("today"), symbol: str = Query(None)):
    """Closed-trade history for a period with a P/L summary.
    range = today | yesterday | this_week | last_week | last_2_weeks |
            last_month | last_3_months | last_6_months."""
    api_user(api_key)
    frm, to = _range_bounds(range)
    t = trader()
    return {"range": range, "from": frm, "to": to,
            "summary": t.pnl_summary(frm, to, instrument=symbol),
            "trades": t.history(frm, to, instrument=symbol)}


@router.get("/closed-trades")
def closed_trades(api_key: str = Query(...), range: str = Query("today"),
                  symbol: str = Query(None), only: str = Query(None),
                  reason: str = Query(None), limit: int = Query(50)):
    """SURGICAL closed-trade details — per-trade symbol, side, volume, entry/exit,
    profit and CLOSE REASON (take_profit / stop_loss / manual / stop_out …).
    Filtered + capped so it never dumps thousands of rows.
      range  = today | yesterday | this_week | last_week | last_month | …
      only   = profit | loss
      reason = take_profit | stop_loss | manual | stop_out | expert | rollover
      limit  = max rows (most-recent first), capped at 200."""
    api_user(api_key)
    frm, to = _range_bounds(range)
    return trader().closed_trades(frm, to, instrument=symbol, limit=limit,
                                  only=only, reason=reason)


# ══ COMMAND ═════════════════════════════════════════════════════════════════════
@router.get("/trade")
def trade(api_key: str = Query(...), command: str = Query(...)):
    api_user(api_key)  # natural language, e.g. "open 0.1 XAUUSD 2000 sl 2000 tp"
    return trader().trade(command)


# ══ SCHEDULED (TIME-BASED) MARKET ORDERS ════════════════════════════════════════
def _compute_run_at(run_at, hours, minutes, seconds):
    if run_at:
        try:
            dt = datetime.fromisoformat(run_at)
        except Exception:
            raise HTTPException(400, "run_at must be ISO datetime, e.g. 2026-07-22T21:30:00")
        return dt.astimezone() if dt.tzinfo is None else dt
    total = (hours or 0) * 3600 + (minutes or 0) * 60 + (seconds or 0)
    if total <= 0:
        raise HTTPException(400, "Provide run_at (ISO) OR hours/minutes/seconds > 0")
    return datetime.now().astimezone() + timedelta(seconds=total)


def _ser_sched(r):
    return {
        "id": str(r["id"]), "account": r.get("account"),
        "symbol": r["symbol"], "side": r["side"], "volume": r["volume"],
        "sl": r["sl"], "tp": r["tp"], "sl_points": r["sl_points"], "tp_points": r["tp_points"],
        "run_at": r["run_at"].isoformat() if r["run_at"] else None,
        "status": r["status"], "result": r["result"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "executed_at": r["executed_at"].isoformat() if r["executed_at"] else None,
    }


@router.get("/schedule-order")
def schedule_order(
    api_key: str = Query(...),
    symbol: str = Query(...),
    side: str = Query(...),                 # REQUIRED: buy | sell
    volume: float = Query(0.1),
    sl: float = Query(0),
    tp: float = Query(0),
    sl_points: int = Query(None),
    tp_points: int = Query(None),
    deviation: int = Query(0),
    run_at: str = Query(None),              # ISO datetime, e.g. 2026-07-22T21:30:00
    hours: int = Query(0),
    minutes: int = Query(0),
    seconds: int = Query(0),
):
    """Promise a MARKET order to be executed at a future time by the server.
    Give an absolute run_at OR a relative hours/minutes/seconds from now."""
    user = api_user(api_key)
    if side not in ("buy", "sell"):
        raise HTTPException(400, "side must be 'buy' or 'sell'")
    account = int(trader().account)   # the active account this order runs on
    broker = _active_broker_ctx.get() or "exness"
    when = _compute_run_at(run_at, hours, minutes, seconds)
    with db.connect() as conn:
        row = conn.execute(
            """INSERT INTO scheduled_orders
               (user_id, account, broker, symbol, side, volume, sl, tp, sl_points, tp_points, deviation, run_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id, run_at, created_at""",
            (user["user_id"], account, broker, symbol, side, volume, sl, tp, sl_points, tp_points, deviation, when),
        ).fetchone()
        conn.commit()
    secs = (row["run_at"] - datetime.now(row["run_at"].tzinfo)).total_seconds()
    return {"id": str(row["id"]), "account": account, "symbol": symbol, "side": side, "volume": volume,
            "run_at": row["run_at"].isoformat(), "seconds_until": round(secs, 1),
            "status": "scheduled"}


@router.get("/scheduled-orders")
def scheduled_orders(api_key: str = Query(...), status: str = Query(None)):
    user = api_user(api_key)
    q = "SELECT * FROM scheduled_orders WHERE user_id = %s"
    args = [user["user_id"]]
    if status:
        q += " AND status = %s"
        args.append(status)
    q += " ORDER BY run_at DESC LIMIT 100"
    with db.connect() as conn:
        rows = conn.execute(q, args).fetchall()
    return {"scheduled_orders": [_ser_sched(r) for r in rows]}


@router.get("/scheduled-orders/cancel")
def cancel_scheduled(api_key: str = Query(...), id: str = Query(...)):
    user = api_user(api_key)
    with db.connect() as conn:
        row = conn.execute(
            """UPDATE scheduled_orders SET status = 'cancelled'
               WHERE id = %s AND user_id = %s AND status = 'scheduled' RETURNING id""",
            (id, user["user_id"]),
        ).fetchone()
        conn.commit()
    if not row:
        raise HTTPException(404, "Not found or no longer cancellable")
    return {"status": "cancelled", "id": id}


# ══ SCHEDULED (TIME-BASED) ACTIONS — ANY trading action, versatile ══════════════
def _ser_action(r):
    return {
        "id": str(r["id"]), "account": r.get("account"), "action": r["action"],
        "params": r["params"], "run_at": r["run_at"].isoformat() if r["run_at"] else None,
        "status": r["status"], "result": r["result"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "executed_at": r["executed_at"].isoformat() if r["executed_at"] else None,
    }


@router.get("/schedule-action")
def schedule_action(
    api_key: str = Query(...),
    action: str = Query(...),                # close | place_order | break_even | ...
    params: str = Query("{}"),               # JSON object of that action's arguments
    account: int = Query(None),              # defaults to the active account
    run_at: str = Query(None),               # ISO datetime OR use relative below
    hours: int = Query(0),
    minutes: int = Query(0),
    seconds: int = Query(0),
):
    """Schedule ANY trading action to run at a future time on the server.
    e.g. action=close params={"symbol":"gold"} seconds=30 → close gold in 30s."""
    import agent
    user = api_user(api_key)
    if action not in agent.SCHEDULABLE_ACTIONS:
        raise HTTPException(400, f"action must be one of: {sorted(agent.SCHEDULABLE_ACTIONS)}")
    try:
        p = json.loads(params) if isinstance(params, str) else (params or {})
        if not isinstance(p, dict):
            raise ValueError
    except Exception:
        raise HTTPException(400, "params must be a JSON object, e.g. {\"symbol\":\"XAUUSD\"}")
    acct = account if account is not None else int(trader().account)
    when = _compute_run_at(run_at, hours, minutes, seconds)
    with db.connect() as conn:
        row = conn.execute(
            """INSERT INTO scheduled_actions (user_id, account, action, params, run_at)
               VALUES (%s,%s,%s,%s,%s) RETURNING id, run_at, created_at""",
            (user["user_id"], acct, action, Json(p), when),
        ).fetchone()
        conn.commit()
    secs = (row["run_at"] - datetime.now(row["run_at"].tzinfo)).total_seconds()
    return {"id": str(row["id"]), "account": acct, "action": action, "params": p,
            "run_at": row["run_at"].isoformat(), "seconds_until": round(secs, 1),
            "status": "scheduled"}


@router.get("/scheduled-actions")
def scheduled_actions(api_key: str = Query(...), status: str = Query(None)):
    user = api_user(api_key)
    q = "SELECT * FROM scheduled_actions WHERE user_id = %s"
    args = [user["user_id"]]
    if status:
        q += " AND status = %s"
        args.append(status)
    q += " ORDER BY run_at DESC LIMIT 100"
    with db.connect() as conn:
        rows = conn.execute(q, args).fetchall()
    return {"scheduled_actions": [_ser_action(r) for r in rows]}


@router.get("/scheduled-actions/cancel")
def cancel_scheduled_action(api_key: str = Query(...), id: str = Query(...)):
    user = api_user(api_key)
    with db.connect() as conn:
        row = conn.execute(
            """UPDATE scheduled_actions SET status = 'cancelled'
               WHERE id = %s AND user_id = %s AND status = 'scheduled' RETURNING id""",
            (id, user["user_id"]),
        ).fetchone()
        conn.commit()
    if not row:
        raise HTTPException(404, "Not found or no longer cancellable")
    return {"status": "cancelled", "id": id}


# ══ TRUTH SOCIAL (analysis) ═════════════════════════════════════════════════════
@router.get("/artificial-sentiment")
def artificial_sentiment(
    api_key: str = Query(...),
    symbol: str = Query(...),            # any instrument the account can price
    timeframe: str = Query("M15"),       # M1|M5|M15|M30|H1|H4|D1
    count: int = Query(200),             # candles to reconstruct from (40–1000)
    compare: bool = Query(False),        # also return Myfxbook's real retail read
):
    """Who controls this market, reconstructed from its own candles.

    Myfxbook reports how ITS users are positioned — one number, for the symbols it
    covers, behind a daily quota. This answers the same question from price
    structure alone (swings, liquidity sweeps, volume and wick absorption), so it
    works on ANY instrument, on ANY timeframe, as often as you like.

    Returns bulls/bears percentages, the estimated average entry of each side, how
    much of each side is TRAPPED (underwater, and therefore future forced flow),
    and the events that moved the number — because a percentage nobody can check
    is worth little.

    `compare=true` puts Myfxbook's real retail reading beside it. The disagreement
    is the useful part: retail heavily long against a bearish footprint is the
    classic trapped-retail setup, which neither number tells you on its own.

    It is a MODEL, not a measurement — every response says so in `method`."""
    import artificial_sentiment as art
    api_user(api_key)          # binds this user's own session + active account
    try:
        res = (art.compare if compare else art.read)(symbol, timeframe=timeframe, count=count)
    except Exception as e:
        raise HTTPException(502, f"could not read candles for {symbol}: {e}")
    if res.get("error"):
        raise HTTPException(422, res["error"])
    return res


@router.get("/market/candles")
def market_candles(
    api_key: str = Query(...),
    symbol: str = Query(...),        # one, or a comma list: XAUUSD,GBPUSD,gold
    timeframe: str = Query("M15"),   # one, or a comma list: H4,H1,M15
    count: int = Query(100),         # how many candles back (max 5000)
    price: str = Query("bid"),       # bid | ask series
    end: str = Query(None),          # walk back from a past moment instead of now
    account: int = Query(None),      # read them through THIS account (default: the active one)
):
    """OHLC candles straight off the live Exness session — oldest first.

    `symbol` is forgiving: `gold`, `nasdaq`, `btc` and `cable` resolve against the
    account's own instrument list. Nothing is cached; every call is live.

    MULTI: pass comma-separated `symbol` and/or `timeframe` to fetch many series in
    one request, e.g. `symbol=XAUUSD,GBPUSD,gold&timeframe=H4,H1,M15&count=50`. The
    reply is then `{multi:true, symbols, timeframes, series:[…], errors:[…]}`, one
    series per symbol×timeframe (each shaped like a single-symbol reply). A single
    symbol AND single timeframe still returns the flat single-series object."""
    import market
    api_user(api_key)
    try:
        if len(market._split_csv(symbol)) <= 1 and len(market._split_csv(timeframe)) <= 1:
            return market.candles(symbol, timeframe=timeframe, count=count, price=price,
                                  end=end, account=account)
        return market.candles_multi(symbol, timeframe, count=count, price=price,
                                    end=end, account=account)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/market/quote")
def market_quote(api_key: str = Query(...), symbol: str = Query(...)):
    """Live bid / ask / spread for one instrument."""
    import market
    api_user(api_key)
    try:
        return market.quote(symbol)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/market/chart")
def market_chart(
    api_key: str = Query(...),
    symbol: str = Query(...),
    timeframe: str = Query("M15"),
    count: int = Query(150),
    account: int = Query(None),
):
    """Candles plus the account's own trades on that instrument — entry, stop and
    target, each flagged `in_range` when it falls inside the candles' price band.
    This is what the chat renders as a live chart."""
    import market
    api_user(api_key)
    try:
        return market.chart(symbol, timeframe=timeframe, count=count, account=account)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/market/timeframes")
def market_timeframes(api_key: str = Query(...)):
    """The timeframes the feed accepts, and the candle ceiling per request."""
    import market
    api_user(api_key)
    return market.timeframes()


# ── background scheduler thread ──────────────────────────────────────────────────
_scheduler_started = False


def start_scheduler():
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    threading.Thread(target=_scheduler_loop, daemon=True).start()
    threading.Thread(target=_actions_loop, daemon=True).start()



def _actions_loop():
    """Executes due scheduled_actions via the shared agent tool dispatcher, so any
    action the agent can do can also be scheduled."""
    while True:
        try:
            import agent
            with db.connect() as conn:
                due = conn.execute(
                    "SELECT * FROM scheduled_actions WHERE status = 'scheduled' AND run_at <= now() "
                    "ORDER BY run_at LIMIT 10"
                ).fetchall()
            for row in due:
                with db.connect() as conn:
                    got = conn.execute(
                        "UPDATE scheduled_actions SET status = 'executing' "
                        "WHERE id = %s AND status = 'scheduled' RETURNING id",
                        (row["id"],),
                    ).fetchone()
                    conn.commit()
                if not got:
                    continue
                try:
                    args = dict(row["params"] or {})
                    if row.get("account") is not None:
                        args["account"] = row["account"]
                    res = agent.execute_tool(row["action"], args, row.get("user_id"))
                    status = "failed" if isinstance(res, dict) and res.get("error") else "executed"
                except Exception as e:
                    res, status = {"error": str(e)}, "failed"
                with db.connect() as conn:
                    conn.execute(
                        "UPDATE scheduled_actions SET status = %s, result = %s, executed_at = now() "
                        "WHERE id = %s",
                        (status, Json(res), row["id"]),
                    )
                    conn.commit()
        except Exception:
            pass
        _time.sleep(1)


def _scheduler_loop():
    while True:
        try:
            with db.connect() as conn:
                due = conn.execute(
                    "SELECT * FROM scheduled_orders WHERE status = 'scheduled' AND run_at <= now() "
                    "ORDER BY run_at LIMIT 10"
                ).fetchall()
            for row in due:
                # claim it so it can't run twice
                with db.connect() as conn:
                    got = conn.execute(
                        "UPDATE scheduled_orders SET status = 'executing' "
                        "WHERE id = %s AND status = 'scheduled' RETURNING id",
                        (row["id"],),
                    ).fetchone()
                    conn.commit()
                if not got:
                    continue
                try:
                    import user_session
                    with user_session.as_user(row.get("user_id")):   # the user's OWN account
                        res = trader(row.get("account"), broker=row.get("broker")).place_order(
                            row["symbol"], row["volume"], row["side"], sl=row["sl"], tp=row["tp"],
                            deviation=row["deviation"], sl_points=row["sl_points"], tp_points=row["tp_points"])
                    status = "executed"
                except Exception as e:
                    res = {"error": str(e)}
                    status = "failed"
                with db.connect() as conn:
                    conn.execute(
                        "UPDATE scheduled_orders SET status = %s, result = %s, executed_at = now() "
                        "WHERE id = %s",
                        (status, Json(res), row["id"]),
                    )
                    conn.commit()
        except Exception:
            pass
        _time.sleep(1)
