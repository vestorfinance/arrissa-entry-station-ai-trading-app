"""
TradeLockerTrader — the canonical Trader adapter for TradeLocker.

It subclasses broker_base.TraderBase (which supplies ALL the shared logic:
calculators, level auto-detect, NL parser, close/break-even/lock-profit
orchestration, polling live-stream). This file implements ONLY the native
primitives, translating TradeLocker's wire format into the canonical shape.

⚠ LIVE-VERIFICATION SURFACE
TradeLocker returns most reads as COLUMNAR arrays whose column order is defined
by GET /trade/config (positionsConfig / ordersConfig / ordersHistoryConfig /
accountDetailsConfig). We decode those generically via `_rows()`, then map the
column ids to canonical keys with defensive multi-key fallbacks below. The exact
column ids and a couple of endpoint path styles (`/trade/positions` + accNum
header vs `/trade/accounts/{id}/...`) are the ONE thing that must be checked
against a live TradeLocker account — everything above this adapter is fixed.
Adjust the mapping tables / `_p()` paths here and nothing else changes.
"""
import time

import db
import auth
from broker_base import TraderBase
from . import api


# canonical MT5-style type int -> TradeLocker (side, type)
def _otype_to_tl(otype: int):
    side = "buy" if otype % 2 == 0 else "sell"
    kind = "market" if otype <= 1 else ("limit" if otype in (2, 3) else "stop")
    return side, kind


def _side_to_int(side) -> int:
    return 0 if str(side).lower().startswith("b") else 1


# canonical timeframe minutes -> TradeLocker resolution string
_RESOLUTION = {1: "1m", 3: "3m", 5: "5m", 10: "10m", 15: "15m", 30: "30m",
               60: "1H", 120: "2H", 240: "4H", 1440: "1D", 10080: "1W", 43200: "1M"}

# TradeLocker groups instruments into only 3 coarse types (FOREX / EQUITY_CFD /
# CRYPTO), so we re-classify into the canonical categories (Majors / Minors /
# Metals / Indices / Energies / Crypto / Exotic / Stocks) that category= queries
# and the agent's group words expect.
_MAJOR_CCYS = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD"}
_ALL_CCYS = _MAJOR_CCYS | {"ZAR", "SGD", "HKD", "CNH", "SEK", "NOK", "DKK", "PLN",
                           "MXN", "TRY", "CZK", "HUF", "ILS", "RUB", "THB", "INR",
                           "AED", "SAR"}
_METALS = ("XAU", "XAG", "XPT", "XPD")
_ENERGY_HINTS = ("OIL", "WTI", "BRENT", "NGAS", "NATGAS", "USOIL", "UKOIL", "GAS")


def _categorize(symbol: str, tl_type: str) -> str:
    """Map TradeLocker's coarse type + the symbol to a canonical category."""
    sym = str(symbol).upper()
    tl = str(tl_type or "").upper()
    if "CRYPTO" in tl:
        return "Crypto"
    base, quote = sym[:3], sym[3:]
    is_fx6 = len(sym) == 6 and base in _ALL_CCYS and quote in _ALL_CCYS
    if "FOREX" in tl or is_fx6:
        if is_fx6 and base in _MAJOR_CCYS and quote in _MAJOR_CCYS:
            return "Majors" if "USD" in (base, quote) else "Minors"
        return "Exotic"
    if sym[:3] in _METALS:
        return "Metals"
    if any(h in sym for h in _ENERGY_HINTS):
        return "Energies"
    if "EQUITY" in tl or "CFD" in tl or "INDEX" in tl:
        return "Indices"
    return "Stocks"


def _num(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


class TradeLockerTrader(TraderBase):
    # TradeLocker rate-limits harder than the app's other broker and answers 429
    # rather than degrading, so the profit monitor idles slower and only tightens
    # up when the trigger is actually near. The inherited monitor does the rest.
    LOCK_POLL_MIN_S = 3.0
    LOCK_POLL_MAX_S = 60.0

    def __init__(self, account_id, user_id):
        self.account = str(account_id)
        self.user_id = user_id
        self._config = None
        self._instr_cache = None
        self._by_tiid = {}       # tradableInstrumentId -> symbol
        self._by_symbol = {}     # symbol -> {tiid, route_trade, route_info}
        self._detail_cache = {}  # symbol -> the sizing numbers, fetched once each
        self._load_session()

    # ── session / DB ────────────────────────────────────────────────────────────
    def _load_session(self):
        from . import api
        with db.connect() as conn:
            row = conn.execute(
                "SELECT a.acc_num, a.environment, a.currency, a.name, a.session_id, "
                "       a.status, s.access_enc, s.refresh_enc "
                "FROM tradelocker_accounts a "
                "JOIN tradelocker_user_sessions s ON s.id = a.session_id "
                "WHERE a.user_id = %s AND a.account_id = %s",
                (self.user_id, self.account),
            ).fetchone()
        if not row:
            import brokers
            raise brokers.NoAccount(
                f"TradeLocker account {self.account} is not connected on this "
                f"instance. Pick one on the Accounts page.")
        # Kept in the table so an old pointer can explain itself rather than
        # reading as "never connected" — but it cannot be traded.
        if row["status"] and str(row["status"]).upper() != "ACTIVE":
            import brokers
            raise brokers.NoAccount(
                f"TradeLocker account {self.account} is {str(row['status']).lower()}, "
                f"not active. Choose another account on the Accounts page.")
        self.acc_num = row["acc_num"]
        self.environment = row["environment"]
        self.session_id = row["session_id"]
        self.meta = {"currency": row["currency"], "environment": row["environment"],
                     "acc_num": row["acc_num"], "name": row["name"]}
        self.session = api.TLSession(
            auth.decrypt(row["access_enc"]), auth.decrypt(row["refresh_enc"]),
            row["environment"], acc_num=row["acc_num"], on_rotate=self._persist_tokens)

    def _persist_tokens(self, access, refresh):
        with db.connect() as conn:
            conn.execute(
                "UPDATE tradelocker_user_sessions SET access_enc = %s, refresh_enc = %s, "
                "updated_at = now() WHERE id = %s",
                (auth.encrypt(access), auth.encrypt(refresh), self.session_id))
            conn.commit()

    # account-scoped path (instruments / state / place order)
    def _ap(self, suffix: str) -> str:
        # account-scoped path — used for LISTS/creates (GET positions, POST orders)
        return f"/trade/accounts/{self.account}{suffix}"

    def _tp(self, suffix: str) -> str:
        # item-level WRITES (close/modify/cancel a specific position or order) live at
        # /trade/positions/{id} and /trade/orders/{id} — the account comes from the
        # accNum header, NOT a /accounts/{id} segment. Using the account-scoped path
        # here 404s (TradeLocker's two path styles — see the header comment).
        return f"/trade{suffix}"

    # ── columnar decode (config-driven) ─────────────────────────────────────────
    def _cfg(self) -> dict:
        if self._config is None:
            try:
                data = self.session.get_json("/trade/config")
                self._config = data.get("d", data) if isinstance(data, dict) else {}
            except Exception:
                self._config = {}
        return self._config

    def _columns(self, cfg_name: str):
        grp = (self._cfg() or {}).get(cfg_name) or {}
        cols = grp.get("columns") or grp.get("cols") or []
        out = []
        for c in cols:
            if isinstance(c, dict):
                out.append(c.get("id") or c.get("name"))
            else:
                out.append(c)
        return [c for c in out if c]

    def _rows(self, data, dkey: str, cfg_name: str):
        """Turn a TL response into a list of plain dicts, whether it came back as
        objects or as columnar arrays keyed by /trade/config."""
        d = data.get("d", data) if isinstance(data, dict) else data
        raw = d.get(dkey) if isinstance(d, dict) else d
        if raw is None and isinstance(d, dict):
            raw = d.get(dkey.lower()) or []
        cols = self._columns(cfg_name)
        rows = []
        for r in (raw or []):
            if isinstance(r, dict):
                rows.append(r)
            elif isinstance(r, (list, tuple)) and cols:
                rows.append({cols[i]: r[i] for i in range(min(len(cols), len(r)))})
            elif isinstance(r, (list, tuple)):
                rows.append({str(i): v for i, v in enumerate(r)})
        return rows

    @staticmethod
    def _pick(row: dict, *keys, default=None):
        for k in keys:
            if k in row and row[k] not in (None, ""):
                return row[k]
        return default

    # ── instruments (build the symbol/route index) ─────────────────────────────
    def instrument(self, symbol):
        """One instrument, with the numbers you can actually size a trade from.

        TradeLocker's instrument LIST carries names, ids and routes — and zero
        for contract size, lot step and tick size, with a default 5 digits that
        is wrong for gold. Those live on a separate per-instrument endpoint, so
        the risk engine could not value a single symbol on this broker: every
        SL/TP call came back "cannot value XAUUSD: contract_size/price missing".

        Fetched lazily and cached per symbol. The list is one request for
        hundreds of instruments; details are one more for each symbol somebody
        actually asks about, which on a rate-limited API is the right trade."""
        ins = super().instrument(symbol)
        sym = str(ins.get("symbol", "")).upper()
        if ins.get("contract_size"):
            return ins
        merged = {**ins, **(self._details(sym) or {})}
        # Write it back into the cached list so the next caller pays nothing.
        for row in (self._instr_cache or []):
            if str(row.get("symbol", "")).upper() == sym:
                row.update(merged)
                return row
        return merged

    def _details(self, symbol) -> dict:
        meta = self._by_symbol.get(str(symbol).upper())
        if not meta or not meta.get("tiid"):
            return {}
        cached = self._detail_cache.get(str(symbol).upper())
        if cached is not None:
            return cached
        out = {}
        try:
            path = f"/trade/instruments/{meta['tiid']}"
            if meta.get("route_info"):
                path += f"?routeId={meta['route_info']}&locale=en"
            body = self.session.get_json(path)
            d = body.get("d") if isinstance(body, dict) and "d" in body else body
            if isinstance(d, dict):
                # tickSize arrives as a banded list — [{leftRangeLimit, tickSize}] —
                # because some instruments tick differently at different prices.
                # The first band is the one that applies at any normal price.
                ticks = d.get("tickSize")
                tick = 0.0
                if isinstance(ticks, list) and ticks:
                    tick = _num((ticks[0] or {}).get("tickSize", 0))
                elif ticks:
                    tick = _num(ticks)
                out = {k: v for k, v in {
                    "contract_size": _num(d.get("lotSize")),
                    "volume_step": _num(d.get("lotStep")),
                    "volume_min": _num(d.get("minLot")),
                    "volume_max": _num(d.get("maxLot")),
                    "tick_size": tick,
                    "currency": d.get("quotingCurrency") or None,
                    "leverage": _num(d.get("leverage")) or None,
                }.items() if v}
                if tick > 0:
                    # Digits FROM the tick, not the feed's default 5 — gold ticks
                    # at 0.01 and quoting it to five places is simply wrong.
                    out["digits"] = max(0, len(f"{tick:.10f}".rstrip("0").split(".")[1]))
        except Exception as e:
            print(f"[tradelocker] no details for {symbol}: {e}", flush=True)
        self._detail_cache[str(symbol).upper()] = out
        return out

    def instruments(self):
        if self._instr_cache is not None:
            return self._instr_cache
        data = self.session.get_json(self._ap("/instruments"))
        raw = self._rows(data, "instruments", "instrumentsConfig")
        out = []
        for i in raw:
            symbol = str(self._pick(i, "name", "symbol", "ticker", default="")).upper()
            if not symbol:
                continue
            tiid = self._pick(i, "tradableInstrumentId", "id")
            route_trade, route_info = None, None
            for rt in (i.get("routes") or i.get("routesList") or []):
                rtype = str(rt.get("type", "")).upper()
                if rtype == "TRADE":
                    route_trade = rt.get("id")
                elif rtype == "INFO":
                    route_info = rt.get("id")
            self._by_tiid[str(tiid)] = symbol
            self._by_symbol[symbol] = {"tiid": tiid, "route_trade": route_trade,
                                       "route_info": route_info}
            digits = int(_num(self._pick(i, "quotesFxDecimalPlaces", "precision",
                                         "digits", default=5)))
            out.append({
                "symbol": symbol,
                "international": self._pick(i, "description", "name", default=symbol),
                "category": _categorize(symbol, self._pick(i, "type", "assetClass", default="")),
                "digits": digits,
                "tick_size": _num(self._pick(i, "tickSize", "minPriceIncrement", default=0)),
                "contract_size": _num(self._pick(i, "contractSize", "lotSize", default=0)),
                "volume_min": _num(self._pick(i, "minLotSize", "minQty", "volume_min", default=0)),
                "volume_max": _num(self._pick(i, "maxLotSize", "maxQty", "volume_max", default=0)),
                "volume_step": _num(self._pick(i, "lotStep", "qtyStep", "volume_step", default=0)),
                "currency": self._pick(i, "currency", "profitCurrency", default=None),
                "tradable_instrument_id": tiid,
            })
        self._instr_cache = out
        return out

    def _route(self, symbol, kind="info"):
        self.instruments()
        info = self._by_symbol.get(str(symbol).upper())
        if not info:
            raise ValueError(f"instrument {symbol} not tradable on this account")
        return info["tiid"], (info["route_trade"] if kind == "trade" else info["route_info"])

    # ── quotes / prices ─────────────────────────────────────────────────────────
    # TradeLocker rate-limits, and /trade/quotes returns BOTH sides in one
    # response. Asking twice for one quote — once for the bid, once for the ask —
    # doubled every price read in the app for nothing. QUOTE_TTL_S is what makes
    # that structural rather than a fix in one caller: any two reads of the same
    # symbol inside the window share a single request.
    QUOTE_TTL_S = 0.75

    def quote(self, symbol: str) -> dict:
        """{bid, ask} in ONE call, cached for a moment."""
        sym = self.instrument(symbol)["symbol"]
        hit = getattr(self, "_quotes", {}).get(sym)
        if hit and (time.time() - hit["at"]) < self.QUOTE_TTL_S:
            return hit["q"]

        tiid, route = self._route(sym, "info")
        data = self.session.get_json("/trade/quotes",
                                     params={"routeId": route, "tradableInstrumentId": tiid})
        d = data.get("d", data) if isinstance(data, dict) else data
        bid = _num(self._pick(d, "bp", "bid", "bidPrice", default=0))
        ask = _num(self._pick(d, "ap", "ask", "askPrice", default=bid))
        if not bid and not ask:
            last = _num(self._pick(d, "lp", "last", "price", default=0))
            bid = ask = last
        q = {"bid": bid, "ask": ask}
        if not hasattr(self, "_quotes"):
            self._quotes = {}
        self._quotes[sym] = {"at": time.time(), "q": q}
        return q

    def price(self, symbol: str, side: str = "bid") -> float:
        q = self.quote(symbol)
        return q["ask"] if str(side).lower() == "ask" else q["bid"]

    def _parse_bars(self, data):
        d = data.get("d", data) if isinstance(data, dict) else data
        bars = (d.get("barDetails") or d.get("bars") or d.get("priceHistory") or []) \
            if isinstance(d, dict) else (d or [])
        out = []
        for b in bars:
            if isinstance(b, dict):
                out.append({"t": int(_num(self._pick(b, "t", "time", "timestamp"))),
                            "o": _num(self._pick(b, "o", "open")),
                            "h": _num(self._pick(b, "h", "high")),
                            "l": _num(self._pick(b, "l", "low")),
                            "c": _num(self._pick(b, "c", "close")),
                            "v": _num(self._pick(b, "v", "volume", default=0))})
            elif isinstance(b, (list, tuple)) and len(b) >= 5:
                out.append({"t": int(_num(b[0])), "o": _num(b[1]), "h": _num(b[2]),
                            "l": _num(b[3]), "c": _num(b[4]), "v": _num(b[5]) if len(b) > 5 else 0})
        out.sort(key=lambda x: x["t"])   # oldest-first, regardless of API order
        return out

    def raw_candles(self, symbol, tf_minutes, count, side, end_ms=None):
        import time as _t
        tiid, route = self._route(symbol, "info")
        resolution = _RESOLUTION.get(int(tf_minutes), "15m")
        count = int(count) or 100
        to_ms = int(_t.time() * 1000) if (end_ms is None or end_ms >= 9007199254740000) else int(end_ms)
        span_ms = count * int(tf_minutes) * 60 * 1000
        # TradeLocker's history is time-range based, so a naive [now - count*tf, now]
        # window can fall ENTIRELY inside a market closure (weekend/holiday) and come
        # back empty (s=no_data). Progressively widen the lookback until we have
        # enough candles, so a closed market shows the LAST AVAILABLE candles.
        out, err = [], None
        for pad_days in (3, 10, 30, 120):
            from_ms = to_ms - int(span_ms * 1.5) - pad_days * 86400 * 1000
            try:
                data = self.session.get_json("/trade/history", params={
                    "routeId": route, "tradableInstrumentId": tiid, "resolution": resolution,
                    "from": from_ms, "to": to_ms})
                out = self._parse_bars(data)
            except Exception as e:
                err = e                     # transient? get_json already retried 5x — try a wider window
                continue
            if len(out) >= count:
                break
        if not out and err:
            raise err                       # surface a real error instead of silent 0 candles
        return out[-count:] if count else out

    # ── account state ───────────────────────────────────────────────────────────
    def _state_row(self):
        # state is a SINGLE flat array of values keyed by accountDetailsConfig,
        # e.g. {"d":{"accountDetailsData":[8662.76, 3875.99, ...]}} — not rows.
        data = self.session.get_json(self._ap("/state"))
        d = data.get("d", data) if isinstance(data, dict) else data
        vals = (d.get("accountDetailsData") if isinstance(d, dict) else None) or []
        cols = self._columns("accountDetailsConfig")
        if vals and not isinstance(vals[0], (list, tuple)):
            if cols:
                return {cols[i]: vals[i] for i in range(min(len(cols), len(vals)))}
            return {str(i): v for i, v in enumerate(vals)}
        rows = self._rows(data, "accountDetailsData", "accountDetailsConfig")
        return rows[0] if rows else {}

    def balance(self):
        r = self._state_row()
        bal = _num(self._pick(r, "balance", "cashBalance", default=0))
        eq = _num(self._pick(r, "equity", "projectedBalance", default=bal))
        return {
            "balance": bal, "equity": eq,
            "margin": _num(self._pick(r, "usedMargin", "initialMarginReq",
                                      "marginUsed", default=0)),
            "free_margin": _num(self._pick(r, "availableFunds", "freeMargin",
                                           "marginAvailable", default=eq)),
        }

    def account_info(self):
        r = self._state_row()
        return {"account": self.account, "currency": self.meta.get("currency"),
                "settings": {"leverage": int(_num(self._pick(r, "leverage",
                                                             "marginLeverage", default=1)) or 1)},
                "state": r}

    # ── positions / orders ──────────────────────────────────────────────────────
    def _sym_of(self, row):
        tiid = str(self._pick(row, "tradableInstrumentId", "instrumentId", default=""))
        return self._by_tiid.get(tiid) or self._pick(row, "symbol", "instrument", default=tiid)

    def positions(self):
        self.instruments()  # ensure the tiid->symbol index is built
        data = self.session.get_json(self._ap("/positions"))
        out = []
        for r in self._rows(data, "positions", "positionsConfig"):
            side = self._pick(r, "side", default="buy")
            out.append({
                "position_id": self._pick(r, "id", "positionId", "position_id"),
                "instrument": self._sym_of(r),
                "type": _side_to_int(side),
                "volume": _num(self._pick(r, "qty", "quantity", "volume", default=0)),
                "open_price": _num(self._pick(r, "avgPrice", "openPrice", "price", default=0)),
                "sl": _num(self._pick(r, "stopLoss", "stopLossPrice", "sl", default=0)) or None,
                "tp": _num(self._pick(r, "takeProfit", "takeProfitPrice", "tp", default=0)) or None,
                "profit": _num(self._pick(r, "unrealizedPl", "unrealizedPnL", "pnl", default=0)),
                "open_time": self._pick(r, "openDate", "openTime", default=None),
            })
        return out

    def orders(self):
        self.instruments()
        data = self.session.get_json(self._ap("/orders"))
        out = []
        for r in self._rows(data, "orders", "ordersConfig"):
            side = str(self._pick(r, "side", default="buy")).lower()
            otype = str(self._pick(r, "type", default="limit")).lower()
            base = 0 if side.startswith("b") else 1
            code = base + (2 if "limit" in otype else (4 if "stop" in otype else 0))
            out.append({
                "order_id": self._pick(r, "id", "orderId", "order_id"),
                "ticket_id": self._pick(r, "id", "orderId", "order_id"),
                "instrument": self._sym_of(r),
                "type": code,
                "volume": _num(self._pick(r, "qty", "quantity", "volume", default=0)),
                "price": _num(self._pick(r, "price", "limitPrice", "stopPrice", default=0)),
                "sl": _num(self._pick(r, "stopLoss", "sl", default=0)) or None,
                "tp": _num(self._pick(r, "takeProfit", "tp", default=0)) or None,
            })
        return out

    def history(self, from_ms, to_ms, instrument=None):
        self.instruments()
        try:
            data = self.session.get_json(self._ap("/ordersHistory"))
        except Exception:
            return []
        rows = self._rows(data, "ordersHistory", "ordersHistoryConfig")
        out = []
        for r in rows:
            status = str(self._pick(r, "status", default="")).upper()
            if status and status not in ("FILLED", "CLOSED"):
                continue
            ts = int(_num(self._pick(r, "closeTime", "lastModified", "createdDate", default=0)))
            if from_ms and ts and ts < int(from_ms):
                continue
            if to_ms and ts and ts > int(to_ms):
                continue
            sym = self._sym_of(r)
            if instrument and str(sym).upper() != str(instrument).upper():
                continue
            side = str(self._pick(r, "side", default="buy")).lower()
            out.append({
                "instrument": sym, "type": 0 if side.startswith("b") else 1,
                "volume": _num(self._pick(r, "filledQty", "qty", default=0)),
                "open_price": _num(self._pick(r, "avgPrice", "price", default=0)),
                "close_price": _num(self._pick(r, "closePrice", "avgPrice", default=0)),
                "profit": _num(self._pick(r, "pnl", "profit", "realizedPnL", default=0)),
                "reason": 0, "sl": None, "tp": None,
                "swap": _num(self._pick(r, "swap", default=0)),
                "commission": _num(self._pick(r, "commission", "fee", default=0)),
                "open_time": self._pick(r, "createdDate", "openTime", default=None),
                "close_time": ts or None,
                "position_id": self._pick(r, "positionId", "id", default=None),
            })
        return out

    # ── live stream (polling, rate-limit aware) ─────────────────────────────────
    def live_stream(self, on_snapshot, stop_event, refresh_s=3.0, debounce_ms=300):
        """Poll positions every `refresh_s` and account state HALF as often (it
        changes slowly) — respecting TradeLocker's per-route limits (GET_POSITIONS
        ~1/s, GET_ACCOUNTS_STATE ~2/s). On a rate-limit (HTTP 429 / Cloudflare 1015)
        we back right off (~60s) so we don't cascade into more 429s, then resume."""
        import time
        bal, last_bal, fails = None, 0.0, 0
        while not stop_event.is_set():
            try:
                positions = self.positions()
                now = time.time()
                if bal is None or now - last_bal >= refresh_s * 2:
                    bal = self.balance()
                    last_bal = now
                rows = []
                for p in positions:
                    is_buy = int(p.get("type", 0)) % 2 == 0
                    rows.append({"position_id": str(p.get("position_id")),
                                 "symbol": p.get("instrument"),
                                 "side": "buy" if is_buy else "sell",
                                 "volume": float(p.get("volume") or 0),
                                 "profit": round(float(p.get("profit") or 0), 2),
                                 "open_price": float(p.get("open_price") or 0),
                                 "sl": p.get("sl"), "tp": p.get("tp")})
                b = bal or {}
                balv = float(b.get("balance") or 0)
                eq = float(b.get("equity") or balv)
                on_snapshot({"account": self.account, "balance": round(balv, 2),
                             "equity": round(eq, 2), "floating_profit": round(eq - balv, 2),
                             "positions": rows, "ts": int(now * 1000)})
                fails = 0
                stop_event.wait(refresh_s)
            except Exception as e:
                msg = str(e)
                if "429" in msg or "1015" in msg or "rate limit" in msg.lower():
                    stop_event.wait(60)        # rate limited — pause, then resume
                    continue
                fails += 1
                if fails >= 3:
                    try:
                        on_snapshot({"error": msg})
                    except Exception:
                        pass
                stop_event.wait(min(refresh_s * fails, 30))

    # ── writes (canonical -> TradeLocker native) ────────────────────────────────
    def _submit_order(self, otype, symbol, volume, price, sl, tp, deviation):
        tiid, route = self._route(symbol, "trade")
        side, kind = _otype_to_tl(otype)
        body = {
            "tradableInstrumentId": tiid, "routeId": route, "qty": float(volume),
            "side": side, "type": kind, "validity": "GTC",
        }
        if kind != "market":
            body["price"] = float(price)
        if sl:
            body["stopLoss"] = float(sl)
            body["stopLossType"] = "absolute"
        if tp:
            body["takeProfit"] = float(tp)
            body["takeProfitType"] = "absolute"
        res = self.session.send_json("POST", self._ap("/orders"), body)
        d = res.get("d", res) if isinstance(res, dict) else res
        oid = self._pick(d, "orderId", "id", "order_id", default=None) if isinstance(d, dict) else None
        # For a market order TL returns an orderId now; the positionId appears after
        # fill. We surface the orderId as both so callers have an immediate handle.
        return {"order": {"position_id": oid, "ticket_id": oid, "price": price,
                          "raw": d}}

    def _submit_close(self, position_id, volume, price):
        # partial close (qty) rides as a query param; full close sends none.
        params = {"qty": float(volume)} if volume is not None else {}
        r = self.session.request("DELETE", self._tp(f"/positions/{position_id}"), params=params)
        if r.status_code >= 400:
            raise api.TradeLockerError(
                f"close position {position_id} -> {r.status_code}: {r.text[:200]}")
        try:
            d = r.json()
            d = d.get("d", d) if isinstance(d, dict) else d
        except Exception:
            d = {}
        return {"position": {"result_code": self._pick(d, "status", "result", default="ok")
                             if isinstance(d, dict) else "ok"}}

    def _submit_modify_position(self, position_id, sl, tp):
        # only send the levels being set — a takeProfit/stopLoss of 0 is rejected
        # (e.g. break-even on a position with no TP would otherwise send takeProfit:0).
        body = {}
        if sl:
            body["stopLoss"] = float(sl)
        if tp:
            body["takeProfit"] = float(tp)
        return self.session.send_json("PATCH", self._tp(f"/positions/{position_id}"), body)

    def _submit_modify_order(self, ticket, price, sl, tp):
        body = {"price": float(price or 0),
                "stopLoss": float(sl or 0), "takeProfit": float(tp or 0)}
        return self.session.send_json("PATCH", self._tp(f"/orders/{ticket}"), body)

    def _submit_cancel_order(self, ticket):
        return self.session.send_json("DELETE", self._tp(f"/orders/{ticket}"), {})
