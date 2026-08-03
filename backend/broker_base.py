"""
Broker-agnostic trading logic — the shared spine every non-Exness broker builds on.

`TraderBase` holds ALL the logic that does not depend on a specific broker's HTTP
protocol: the SL/TP ⇄ points ⇄ money calculators, margin math, level auto-detection,
volume normalization, the natural-language command parser, and the versatile
close / break-even / lock-profit / bulk orchestration. It is expressed purely in
terms of a small set of PRIMITIVES a subclass must implement.

The Exness trader (`exness_trading.ExnessTrader`) predates this and keeps its own
copies of the same, proven logic — it is intentionally left untouched. New brokers
(TradeLocker, …) subclass `TraderBase` and implement only the primitives, inheriting
full feature parity for free.

Primitives a subclass MUST implement
------------------------------------
  .account                      -> str/int account id
  .meta                         -> {"currency": <deposit ccy>, ...}
  instruments()                 -> [ {symbol, digits, tick_size, contract_size,
                                       volume_min, volume_max, volume_step,
                                       currency|currency_profit, category,
                                       international}, ... ]
  price(symbol, side)           -> float          (side: "bid" | "ask")
  positions()                   -> [ {position_id, instrument, type(int 0=buy/1=sell),
                                       volume, open_price, sl, tp, profit, open_time}, ...]
  orders()                      -> [ {order_id|ticket_id, instrument, type(int),
                                       volume, price, sl, tp}, ... ]
  balance()                     -> {balance, equity, margin, free_margin}
  account_info()                -> dict (may carry settings.leverage)
  history(from_ms, to_ms, instrument=None) -> [ closed-trade rows ]
  raw_candles(symbol, tf_minutes, count, side, end_ms=None) -> [ {t,o,h,l,c,v}, ... ]
                                (end_ms is OPTIONAL — 'now' when omitted. The
                                 risk engine calls this with four arguments.)
  _submit_order(otype, symbol, volume, price, sl, tp, deviation) -> {"order": {...}}
  _submit_close(position_id, volume, price)                      -> {"position": {...}}
  _submit_modify_position(position_id, sl, tp)                   -> dict
  _submit_modify_order(ticket, price, sl, tp)                    -> dict
  _submit_cancel_order(ticket)                                   -> dict
"""
import re
import time
import threading


# side keyword -> MT5-style order-type int (shared vocabulary across brokers).
ORDER_TYPE = {"buy": 0, "sell": 1, "buy_limit": 2, "sell_limit": 3,
              "buy_stop": 4, "sell_stop": 5}

KNOWN_CCY = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "ZAR", "SGD",
             "HKD", "CNH", "SEK", "NOK", "DKK", "PLN", "MXN", "TRY", "CZK", "HUF",
             "THB", "INR", "ILS", "RUB", "AED", "SAR"}

# MT5-style close/deal reason -> label (mirrors ExnessTrader._REASON).
_REASON = {0: "manual", 1: "mobile", 2: "web", 3: "expert", 4: "stop_loss",
           5: "take_profit", 6: "stop_out", 7: "rollover", 8: "vmargin", 9: "split"}
_MANUAL_REASONS = {"manual", "mobile", "web", "client"}


class TraderBase:
    ABS_PRICE_BAND = 0.30

    # ── instrument helpers ──────────────────────────────────────────────────────
    def instrument(self, symbol: str) -> dict:
        ins = next((i for i in self.instruments()
                    if str(i.get("symbol", "")).upper() == str(symbol).upper()), None)
        if not ins:
            raise ValueError(f"instrument {symbol} not tradable on this account")
        return ins

    def point_size(self, symbol: str) -> float:
        ins = self.instrument(symbol)
        ts = ins.get("tick_size") or 0
        return ts if ts and ts > 0 else 10 ** (-int(ins["digits"]))

    def account_currency(self) -> str:
        return str(self.meta.get("currency") or "USD").upper()

    def quote_currency(self, symbol: str) -> str:
        ins = self.instrument(symbol)
        for k in ("currency_profit", "profit_currency", "currency"):
            v = ins.get(k)
            if v:
                return str(v).upper()
        sym = str(symbol).upper()
        if len(sym) == 6 and sym[:3] in KNOWN_CCY and sym[3:] in KNOWN_CCY:
            return sym[3:]
        if sym[-3:] in KNOWN_CCY:
            return sym[-3:]
        return "USD"

    def _pair_price(self, base: str, quote: str):
        sym = f"{base}{quote}".upper()
        if any(str(i.get("symbol", "")).upper() == sym for i in self.instruments()):
            try:
                return float(self.price(sym, "bid"))
            except Exception:
                return None
        return None

    def fx_rate(self, frm: str, to: str):
        frm, to = frm.upper(), to.upper()
        if frm == to:
            return 1.0
        direct = self._pair_price(frm, to)
        if direct:
            return direct
        inv = self._pair_price(to, frm)
        if inv:
            return 1.0 / inv
        if "USD" not in (frm, to):
            a, b = self.fx_rate(frm, "USD"), self.fx_rate("USD", to)
            if a and b:
                return a * b
        return None

    # ── margin ──────────────────────────────────────────────────────────────────
    _leverage_cache = None

    def leverage(self) -> int:
        if self._leverage_cache is None:
            self._leverage_cache = 1
            try:
                info = self.account_info() or {}
                lev = ((info.get("settings") or {}).get("leverage")
                       or info.get("leverage") or info.get("marginLeverage"))
                self._leverage_cache = int(lev) or 1
            except Exception:
                self._leverage_cache = 1
        return self._leverage_cache

    def margin_required(self, symbol: str, volume: float, price: float = None) -> float:
        ins = self.instrument(symbol)
        cs = float(ins.get("contract_size") or 0)
        if price is None:
            price = self.price(symbol, "ask")
        return round(volume * cs * price / max(self.leverage(), 1), 2)

    def check_margin(self, symbol: str, volume: float, price: float = None) -> dict:
        required = self.margin_required(symbol, volume, price)
        free = float(self.balance().get("free_margin", 0) or 0)
        return {"ok": free >= required, "required": required,
                "free_margin": round(free, 2), "shortfall": round(max(0.0, required - free), 2)}

    # ── profit / loss calculators ───────────────────────────────────────────────
    def point_value(self, symbol: str, volume: float = 1.0, price: float = None) -> dict:
        ins = self.instrument(symbol)
        cs = float(ins.get("contract_size") or 0)
        pt = self.point_size(symbol)
        sym = str(symbol).upper()
        acct_ccy = self.account_currency()
        quote_ccy = self.quote_currency(symbol)
        rate = self.fx_rate(quote_ccy, acct_ccy)
        exact = rate is not None
        if rate is None:
            rate = 1.0
        return {
            "symbol": sym, "volume": volume, "contract_size": cs,
            "point_size": pt, "digits": int(ins["digits"]),
            "account_currency": acct_ccy, "quote_currency": quote_ccy,
            "quote_rate": round(rate, 8), "exact": exact,
            "money_per_point": round(volume * cs * pt * rate, 6),
            "money_per_price_unit": round(volume * cs * rate, 6),
        }

    def sltp_calc(self, symbol: str, volume: float, side: str,
                  entry: float = None, level: float = None,
                  points: float = None, money: float = None, mode: str = "tp") -> dict:
        is_buy = str(side).lower().startswith("buy")
        if entry is None:
            entry = self.price(symbol, "ask" if is_buy else "bid")
        entry = float(entry)
        pv = self.point_value(symbol, float(volume), entry)
        mpu, pt, digits = pv["money_per_price_unit"], pv["point_size"], pv["digits"]
        if mpu <= 0:
            raise ValueError(f"cannot value {symbol}: contract_size/price missing")

        given = [k for k, v in (("level", level), ("points", points), ("money", money))
                 if v is not None]
        if len(given) != 1:
            raise ValueError("provide exactly one of: money, points, level")

        if level is not None:
            level = float(level)
            signed_move = (level - entry) if is_buy else (entry - level)
            money_signed = signed_move * mpu
            resolved = "tp" if money_signed >= 0 else "sl"
            dist = abs(level - entry)
        else:
            dist = abs(float(points)) * pt if points is not None else abs(float(money)) / mpu
            resolved = mode if mode in ("tp", "sl") else "tp"
            if resolved == "sl":
                level = entry - dist if is_buy else entry + dist
                money_signed = -dist * mpu
            else:
                level = entry + dist if is_buy else entry - dist
                money_signed = dist * mpu

        return {
            "symbol": pv["symbol"], "side": "buy" if is_buy else "sell",
            "mode": resolved, "entry": round(entry, digits), "volume": float(volume),
            "level": round(level, digits), "distance_price": round(dist, digits),
            "distance_points": round(dist / pt, 1),
            "money": round(money_signed, 2), "money_abs": round(abs(money_signed), 2),
            "money_per_point": pv["money_per_point"], "money_per_price_unit": mpu,
            "exact": pv["exact"], "account_currency": pv["account_currency"],
            "quote_currency": pv["quote_currency"],
        }

    def profit_target(self, symbol: str, entry: float, target: float,
                      volume: float, side: str, mode: str = "tp") -> dict:
        is_buy = str(side).lower().startswith("buy")
        pv = self.point_value(symbol, volume, entry)
        mpu = pv["money_per_price_unit"]
        if mpu <= 0:
            raise ValueError(f"cannot value {symbol}: contract_size/price missing")
        target = abs(float(target))
        dist = target / mpu
        digits, pt = pv["digits"], pv["point_size"]
        if mode == "sl":
            level = entry - dist if is_buy else entry + dist
        else:
            level = entry + dist if is_buy else entry - dist
        return {
            "symbol": pv["symbol"], "side": "buy" if is_buy else "sell",
            "mode": mode, "entry": round(entry, digits), "volume": volume,
            "target_money": round(target, 2), "level": round(level, digits),
            "distance_price": round(dist, digits), "distance_points": round(dist / pt, 1),
            "money_per_point": pv["money_per_point"], "money_per_price_unit": mpu,
            "exact": pv["exact"], "account_currency": pv["account_currency"],
            "quote_currency": pv["quote_currency"],
        }

    def basket_target(self, positions, target: float, mode: str = "tp",
                      split: str = "equal") -> dict:
        positions = list(positions)
        n = len(positions)
        if n == 0:
            raise ValueError("no positions given")
        target = abs(float(target))

        def entry_of(p):
            return float(p.get("entry") if p.get("entry") is not None else p.get("open_price"))

        weights = [self.point_value(p["symbol"], float(p["volume"]),
                                    entry_of(p))["money_per_price_unit"] for p in positions]
        wsum = sum(weights) or 1.0
        legs, allocated = [], 0.0
        for p, w in zip(positions, weights):
            share = target / n if split == "equal" else target * (w / wsum)
            leg = self.profit_target(p["symbol"], entry_of(p), share,
                                     float(p["volume"]), p["side"], mode=mode)
            leg["share_money"] = round(share, 2)
            legs.append(leg)
            allocated += share
        return {"mode": mode, "split": split, "positions": n,
                "target_money": round(target, 2), "allocated_money": round(allocated, 2),
                "account_currency": legs[0].get("account_currency") if legs else self.account_currency(),
                "legs": legs}

    def basket_bracket(self, positions, sl_money=None, tp_money=None, split="equal") -> dict:
        """Set an SL and/or TP across a basket to realise a chosen TOTAL P/L at each
        level. sl_money/tp_money are SIGNED account-currency amounts: +100 = a LOCKED
        PROFIT (the level sits on the profit side, so a STOP there still closes in
        profit), −100 = a loss cap. A stop-loss can sit in profit — that's how you
        lock gains. `*_valid` flags whether each level is a legal stop/limit vs the
        live price. split=equal → same money per trade; weighted → same distance."""
        positions = list(positions)
        n = len(positions)
        if n == 0:
            raise ValueError("no positions given")

        def entry_of(p):
            return float(p.get("entry") if p.get("entry") is not None else p.get("open_price"))

        mpus = [self.point_value(p["symbol"], float(p["volume"]), entry_of(p))["money_per_price_unit"]
                for p in positions]
        wsum = sum(mpus) or 1.0
        legs = []
        for p, mpu in zip(positions, mpus):
            sym = p["symbol"]
            e = entry_of(p)
            is_buy = str(p["side"]).lower().startswith("buy")
            digits = int(self.instrument(sym)["digits"])
            cur = self.price(sym, "bid" if is_buy else "ask")     # the exit price

            def level_for(total):
                if total is None or mpu <= 0:
                    return None, None
                share = total / n if split == "equal" else total * (mpu / wsum)
                lvl = e + share / mpu if is_buy else e - share / mpu   # signed distance
                return round(lvl, digits), round(share, 2)

            sl_lvl, sl_share = level_for(sl_money)
            tp_lvl, tp_share = level_for(tp_money)
            legs.append({
                "symbol": sym, "position_id": p.get("position_id"),
                "side": "buy" if is_buy else "sell",
                "entry": round(e, digits), "current_price": round(cur, digits),
                "sl": sl_lvl, "sl_money": sl_share,
                "sl_locks_profit": bool(sl_share is not None and sl_share > 0),
                "sl_valid": None if sl_lvl is None else ((sl_lvl < cur) if is_buy else (sl_lvl > cur)),
                "tp": tp_lvl, "tp_money": tp_share,
                "tp_valid": None if tp_lvl is None else ((tp_lvl > cur) if is_buy else (tp_lvl < cur)),
            })
        return {"bracket": True, "split": split, "positions": n,
                "sl_total": sl_money, "tp_total": tp_money,
                "account_currency": self.account_currency(), "legs": legs}

    def risk_plan(self, symbol: str, side: str, *, entry: float = None,
                  risk_pct: float = None, risk_money: float = None,
                  sl: float = None, sl_points: float = None,
                  tp: float = None, tp_points: float = None, rr: float = None,
                  volume: float = None, basis: str = "equity") -> dict:
        """
        Complete symbol-aware risk/reward engine — one call, no hand maths.
        risk_money = stop_distance · $/price · volume; give any TWO of
        {risk, stop, volume} and it solves the third: SIZE (risk+stop→volume),
        STOP (risk+volume→stop distance), VALIDATE (stop+volume→realised risk).
        Risk = risk_pct (of `basis` equity|balance) or risk_money. Stop = sl (price)
        or sl_points. Optional target: rr (reward:risk) or tp/tp_points. All money in
        the account currency (cross-rated); volume snapped to step/min.
        """
        is_buy = str(side).lower().startswith("buy")
        if entry is None:
            entry = self.price(symbol, "ask" if is_buy else "bid")
        entry = float(entry)

        pv = self.point_value(symbol, 1.0, entry)          # per-1-lot values
        mpu, pt, digits = pv["money_per_price_unit"], pv["point_size"], pv["digits"]
        if mpu <= 0:
            raise ValueError(f"cannot value {symbol}: contract_size/price missing")

        bal = self.balance() or {}
        basis = (basis or "equity").lower()
        basis_amt = float(bal.get("equity" if basis == "equity" else "balance") or 0) or 0.0
        if basis_amt <= 0:
            basis_amt = float(bal.get("balance") or bal.get("equity") or 0) or 0.0

        notes = []

        req_risk = None
        if risk_money is not None:
            req_risk = abs(float(risk_money))
        elif risk_pct is not None:
            if basis_amt <= 0:
                raise ValueError("account balance/equity unavailable — size by risk_money instead of risk_pct")
            req_risk = basis_amt * abs(float(risk_pct)) / 100.0

        sl_dist = None
        sl_price_given = None
        if sl is not None:
            sl_price_given = float(sl)
            sl_dist = abs(entry - sl_price_given)
            if (sl_price_given > entry) if is_buy else (sl_price_given < entry):
                notes.append("sl is on the profit side of entry; used as |entry−sl| distance")
        elif sl_points is not None:
            sl_dist = abs(float(sl_points)) * pt

        have_vol = volume is not None

        if have_vol and sl_dist is not None:                # VALIDATE
            vol, vol_adj = self._normalize_volume(symbol, float(volume))
            act_risk = sl_dist * mpu * vol
            solved = "validate"
        elif have_vol and req_risk is not None:             # STOP (solve distance)
            vol, vol_adj = self._normalize_volume(symbol, float(volume))
            sl_dist = req_risk / (mpu * vol) if (mpu * vol) > 0 else 0.0
            act_risk = req_risk
            solved = "stop"
        elif req_risk is not None and sl_dist is not None:  # SIZE (solve volume)
            loss_per_lot = sl_dist * mpu
            raw_vol = req_risk / loss_per_lot if loss_per_lot > 0 else 0.0
            vol, vol_adj = self._normalize_volume(symbol, raw_vol)
            act_risk = sl_dist * mpu * vol
            solved = "size"
            if abs(act_risk - req_risk) > max(0.01, req_risk * 0.02):
                notes.append(f"volume snapped to {vol} (step/min) — actual risk "
                             f"{round(act_risk, 2)} vs requested {round(req_risk, 2)}")
        else:
            raise ValueError("provide any TWO of: risk (risk_pct|risk_money), "
                             "stop (sl|sl_points), volume")

        sl_price = round(sl_price_given if sl_price_given is not None
                         else (entry - sl_dist if is_buy else entry + sl_dist), digits)

        tp_block, rr_out = None, None
        if tp is not None:
            tp_dist = abs(float(tp) - entry)
            tp_price = round(float(tp), digits)
            if (float(tp) < entry) if is_buy else (float(tp) > entry):
                notes.append("tp is on the loss side of entry")
        elif tp_points is not None:
            tp_dist = abs(float(tp_points)) * pt
            tp_price = round(entry + tp_dist if is_buy else entry - tp_dist, digits)
        elif rr is not None and sl_dist:
            tp_dist = abs(float(rr)) * sl_dist
            tp_price = round(entry + tp_dist if is_buy else entry - tp_dist, digits)
        else:
            tp_dist = None

        if tp_dist is not None:
            reward = tp_dist * mpu * vol
            if tp is None and tp_points is None:
                rr_out = round(abs(float(rr)), 4)
            else:
                rr_out = round(tp_dist / sl_dist, 4) if sl_dist else None
            tp_block = {
                "price": tp_price, "distance_price": round(tp_dist, digits),
                "distance_points": round(tp_dist / pt, 1), "money": round(reward, 2),
                "pct": round(reward / basis_amt * 100, 4) if basis_amt > 0 else None,
            }

        margin_req = self.margin_required(symbol, vol, entry)
        free = float(bal.get("free_margin", 0) or 0)

        return {
            "symbol": pv["symbol"], "side": "buy" if is_buy else "sell",
            "solved_for": solved, "entry": round(entry, digits),
            "account_currency": pv["account_currency"],
            "quote_currency": pv["quote_currency"], "exact": pv["exact"],
            "basis": basis, "basis_amount": round(basis_amt, 2),
            "volume": vol, "volume_adjustment": vol_adj,
            "risk": {
                "money": round(act_risk, 2),
                "pct": round(act_risk / basis_amt * 100, 4) if basis_amt > 0 else None,
                "requested_money": round(req_risk, 2) if req_risk is not None else None,
                "requested_pct": float(risk_pct) if risk_pct is not None else None,
            },
            "sl": {
                "price": sl_price, "distance_price": round(sl_dist, digits),
                "distance_points": round(sl_dist / pt, 1), "money": -round(act_risk, 2),
            },
            "tp": tp_block, "rr": rr_out,
            "money_per_point": round(pv["money_per_point"] * vol, 6),
            "money_per_price_unit": round(mpu * vol, 6),
            "per_lot": {"money_per_point": pv["money_per_point"],
                        "money_per_price_unit": mpu},
            "margin": {"required": margin_req, "free_margin": round(free, 2),
                       "ok": free >= margin_req, "leverage": self.leverage()},
            "notes": notes,
        }

    def auto_sltp(self, symbol: str, side: str, *, style: str = "intraday",
                  entry: float = None, risk_pct: float = None, risk_money: float = None,
                  rr: float = None, basis: str = "equity", sl_mode: str = "structure") -> dict:
        """Smart SL/TP + lot sizing from market structure + ATR + a risk budget.
        `style`: scalp|intraday|swing|position. Delegates to the broker-agnostic
        sltp_engine (uses this trader's canonical candle/instrument/risk_plan API)."""
        import sltp_engine
        return sltp_engine.plan(self, symbol, side, style=style, entry=entry,
                                risk_pct=risk_pct, risk_money=risk_money, rr=rr,
                                basis=basis, sl_mode=sl_mode)

    # ── SL/TP level auto-detection + volume snapping ────────────────────────────
    def _resolve_level(self, value, is_sl: bool, is_buy: bool,
                       entry: float, symbol: str) -> float:
        if not value:
            return 0.0
        v = float(value)
        digits = int(self.instrument(symbol)["digits"])
        pt = self.point_size(symbol)
        lo, hi = entry * (1 - self.ABS_PRICE_BAND), entry * (1 + self.ABS_PRICE_BAND)
        if is_sl:
            correct_side = v < entry if is_buy else v > entry
        else:
            correct_side = v > entry if is_buy else v < entry
        if correct_side and lo <= v <= hi:
            return round(v, digits)
        dist = v * pt
        if is_sl:
            price = entry - dist if is_buy else entry + dist
        else:
            price = entry + dist if is_buy else entry - dist
        return round(price, digits)

    def _normalize_volume(self, symbol: str, volume: float):
        ins = self.instrument(symbol)
        vmin = float(ins.get("volume_min") or 0)
        vmax = float(ins.get("volume_max") or 0)
        vstep = float(ins.get("volume_step") or 0)
        v0 = float(volume)
        v = v0
        if vstep > 0:
            v = round(round(v / vstep) * vstep, 8)
        if vmin and v < vmin:
            v = vmin
        if vmax and v > vmax:
            v = vmax
        adj = None
        if abs(v - v0) > 1e-9:
            adj = {"requested": v0, "used": v, "min_volume": vmin}
        return v, adj

    # ── reads that derive from primitives ───────────────────────────────────────
    def total_profit(self) -> float:
        b = self.balance()
        return round(float(b.get("equity", 0)) - float(b.get("balance", 0)), 2)

    def pnl_summary(self, from_ms, to_ms, instrument=None):
        hist = self.history(from_ms, to_ms, instrument=instrument)
        pnls = [float(h.get("profit", 0) or 0) for h in hist]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        return {
            "trades": len(hist), "gross_profit": round(sum(wins), 2),
            "gross_loss": round(sum(losses), 2), "net_profit": round(sum(pnls), 2),
            "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else 0,
            "swap": round(sum(float(h.get("swap", 0) or 0) for h in hist), 2),
            "commission": round(sum(float(h.get("commission", 0) or 0) for h in hist), 2),
            "fee": round(sum(float(h.get("fee", 0) or 0) for h in hist), 2),
        }

    def closed_trades(self, from_ms, to_ms, instrument=None, limit=50, only=None, reason=None):
        from datetime import datetime, timezone

        def _iso(ms):
            try:
                return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).isoformat()
            except Exception:
                return None

        limit = max(1, min(int(limit or 50), 200))
        rows = []
        for h in self.history(from_ms, to_ms, instrument=instrument):
            p = float(h.get("profit", 0) or 0)
            if only == "profit" and p <= 0:
                continue
            if only == "loss" and p >= 0:
                continue
            rlabel = _REASON.get(int(h.get("reason", -1)), "other")
            if reason:
                if reason == "manual":
                    if rlabel not in _MANUAL_REASONS:
                        continue
                elif rlabel != reason:
                    continue
            rows.append({
                "symbol": h.get("instrument"),
                "side": "buy" if int(h.get("type", 0)) % 2 == 0 else "sell",
                "volume": h.get("volume"), "open_price": h.get("open_price"),
                "close_price": h.get("close_price"), "profit": round(p, 2),
                "close_reason": rlabel, "sl": h.get("sl") or None, "tp": h.get("tp") or None,
                "swap": h.get("swap") or 0, "commission": h.get("commission") or 0,
                "open_at": _iso(h.get("open_time")), "close_at": _iso(h.get("close_time")),
                "close_time": h.get("close_time"), "position_id": h.get("position_id"),
            })
        rows.sort(key=lambda r: r.get("close_time") or 0, reverse=True)
        matched = len(rows)
        return {"trades": rows[:limit], "returned": min(matched, limit),
                "matched": matched, "truncated": matched > limit}

    # ── natural-language command ────────────────────────────────────────────────
    def parse(self, command: str) -> dict:
        text = command.strip()
        low = text.lower()
        side = "buy"
        for s in ("buy", "sell"):
            if re.search(rf"\b{s}\b", low):
                side = s
                break

        def grab(kw):
            m = (re.search(rf"(\d+(?:\.\d+)?)\s*{kw}\b", low)
                 or re.search(rf"\b{kw}\s*[:=]?\s*(\d+(?:\.\d+)?)", low))
            return float(m.group(1)) if m else 0

        sl, tp = grab("sl"), grab("tp")
        tokens = re.findall(r"[A-Za-z]{3,10}", text)
        valid = {str(i.get("symbol", "")).upper() for i in self.instruments()}
        symbol = next((tok.upper() for tok in tokens if tok.upper() in valid), None)
        if not symbol:
            raise ValueError(f"no tradable instrument found in: {command!r}")
        used = {str(sl), str(tp), str(int(sl)), str(int(tp))}
        nums = re.findall(r"\d+(?:\.\d+)?", text)
        volume = next((float(n) for n in nums if n not in used), None)
        if volume is None:
            raise ValueError(f"no volume found in: {command!r}")
        return {"symbol": symbol, "volume": volume, "side": side, "sl": sl, "tp": tp}

    def trade(self, command: str):
        p = self.parse(command)
        return self.place_order(p["symbol"], p["volume"], p["side"], sl=p["sl"], tp=p["tp"])

    # ── order placement (generic prep -> broker _submit_order) ──────────────────
    def place_order(self, symbol: str, volume: float, side: str, price: float = None,
                    sl=0, tp=0, deviation: int = 0, sl_points: int = None,
                    tp_points: int = None, emergency: bool = False):
        otype = ORDER_TYPE[side]
        is_buy = otype % 2 == 0
        if price is None:
            if otype > 1:
                raise ValueError(f"{side} is a pending order — a trigger price is required")
            price = self.price(symbol, "ask" if is_buy else "bid")

        volume, vol_adj = self._normalize_volume(symbol, volume)

        if not emergency and otype <= 1:
            m = self.check_margin(symbol, volume, price)
            if not m["ok"]:
                raise RuntimeError(
                    f"insufficient margin for {volume} {symbol}: need ${m['required']}, "
                    f"free ${m['free_margin']} (short ${m['shortfall']}). "
                    f"Pass emergency=True to override.")

        digits = int(self.instrument(symbol)["digits"])
        pt = self.point_size(symbol)
        price = round(price, digits)
        if sl_points is not None:
            sl = round(price - sl_points * pt if is_buy else price + sl_points * pt, digits)
        else:
            sl = self._resolve_level(sl, True, is_buy, price, symbol)
        if tp_points is not None:
            tp = round(price + tp_points * pt if is_buy else price - tp_points * pt, digits)
        else:
            tp = self._resolve_level(tp, False, is_buy, price, symbol)

        result = self._submit_order(otype, symbol, volume, price, sl, tp, deviation)
        if vol_adj and isinstance(result, dict):
            result["volume_adjusted"] = vol_adj
        return result

    def market_buy(self, symbol, volume, sl=0, tp=0, deviation=0,
                   sl_points=None, tp_points=None, price=None):
        return self.place_order(symbol, volume, "buy", price, sl, tp, deviation,
                                sl_points, tp_points)

    def market_sell(self, symbol, volume, sl=0, tp=0, deviation=0,
                    sl_points=None, tp_points=None, price=None):
        return self.place_order(symbol, volume, "sell", price, sl, tp, deviation,
                                sl_points, tp_points)

    def snipe(self, symbol, volume, side="buy", price=None, sl=0, tp=0,
              sl_points=None, tp_points=None, deviation=1000, emergency=True):
        return self.place_order(symbol, volume, side, price=price, sl=sl, tp=tp,
                                deviation=deviation, sl_points=sl_points,
                                tp_points=tp_points, emergency=emergency)

    def _market_bulk(self, symbol, volume, count, side, sl, tp, sl_points, tp_points,
                     delay_ms, deviation, price, fetch_price_each, emergency=False):
        ref = price
        if ref is None and not fetch_price_each:
            ref = self.price(symbol, "ask" if side == "buy" else "bid")
        if not emergency:
            price_ref = ref if ref is not None else self.price(symbol, "ask" if side == "buy" else "bid")
            per = self.margin_required(symbol, volume, price_ref)
            total = round(per * count, 2)
            free = float(self.balance().get("free_margin", 0) or 0)
            if free < total:
                return {"symbol": symbol, "side": side, "requested": count, "placed": 0,
                        "failed": count, "total_ms": 0, "avg_ms": 0, "price_ref": ref,
                        "delay_ms": delay_ms, "trades": [],
                        "error": (f"insufficient margin for {count}× {volume} {symbol}: "
                                  f"need ${total} (${per} each), free ${round(free, 2)}. "
                                  f"Pass emergency=true to override.")}
        trades, t0 = [], time.perf_counter()
        for i in range(count):
            a = time.perf_counter()
            try:
                r = self.place_order(symbol, volume, side,
                                     price=None if fetch_price_each else ref,
                                     sl=sl, tp=tp, deviation=deviation,
                                     sl_points=sl_points, tp_points=tp_points, emergency=True)
                trades.append({"i": i + 1, "position_id": r["order"]["position_id"],
                               "fill": r["order"].get("price"),
                               "ms": round((time.perf_counter() - a) * 1000, 1), "ok": True})
            except Exception as e:
                trades.append({"i": i + 1, "ok": False, "error": str(e),
                               "ms": round((time.perf_counter() - a) * 1000, 1)})
            if delay_ms and i < count - 1:
                time.sleep(delay_ms / 1000.0)
        total = (time.perf_counter() - t0) * 1000
        oks = [t for t in trades if t.get("ok")]
        return {"symbol": symbol, "side": side, "requested": count, "placed": len(oks),
                "failed": count - len(oks), "total_ms": round(total, 1),
                "avg_ms": round(sum(t["ms"] for t in trades) / len(trades), 1) if trades else 0,
                "price_ref": ref, "delay_ms": delay_ms, "trades": trades}

    def market_buy_bulk(self, symbol, volume, count, sl=0, tp=0, sl_points=None,
                        tp_points=None, delay_ms=0, deviation=0, price=None,
                        fetch_price_each=False, emergency=False):
        return self._market_bulk(symbol, volume, count, "buy", sl, tp, sl_points,
                                 tp_points, delay_ms, deviation, price, fetch_price_each, emergency)

    def market_sell_bulk(self, symbol, volume, count, sl=0, tp=0, sl_points=None,
                         tp_points=None, delay_ms=0, deviation=0, price=None,
                         fetch_price_each=False, emergency=False):
        return self._market_bulk(symbol, volume, count, "sell", sl, tp, sl_points,
                                 tp_points, delay_ms, deviation, price, fetch_price_each, emergency)

    def pending_order(self, symbol, volume, side, price, sl=0, tp=0,
                      sl_points=None, tp_points=None):
        if side not in ("buy_limit", "sell_limit", "buy_stop", "sell_stop"):
            raise ValueError(f"{side} is not a pending order type")
        return self.place_order(symbol, volume, side, price=price, sl=sl, tp=tp,
                                sl_points=sl_points, tp_points=tp_points)

    def buy_limit(self, symbol, volume, price, sl=0, tp=0, **kw):
        return self.pending_order(symbol, volume, "buy_limit", price, sl, tp, **kw)

    def sell_limit(self, symbol, volume, price, sl=0, tp=0, **kw):
        return self.pending_order(symbol, volume, "sell_limit", price, sl, tp, **kw)

    def buy_stop(self, symbol, volume, price, sl=0, tp=0, **kw):
        return self.pending_order(symbol, volume, "buy_stop", price, sl, tp, **kw)

    def sell_stop(self, symbol, volume, price, sl=0, tp=0, **kw):
        return self.pending_order(symbol, volume, "sell_stop", price, sl, tp, **kw)

    def pending_bulk(self, symbol, volume, side, count, price, step_points=0,
                     sl=0, tp=0, sl_points=None, tp_points=None, delay_ms=0):
        if side not in ("buy_limit", "sell_limit", "buy_stop", "sell_stop"):
            raise ValueError(f"{side} is not a pending order type")
        pt = self.point_size(symbol)
        digits = int(self.instrument(symbol)["digits"])
        orders, t0 = [], time.perf_counter()
        for i in range(count):
            p = round(price + i * step_points * pt, digits)
            a = time.perf_counter()
            try:
                r = self.place_order(symbol, volume, side, price=p, sl=sl, tp=tp,
                                     sl_points=sl_points, tp_points=tp_points)
                orders.append({"i": i + 1, "ticket": r["order"]["ticket_id"], "price": p,
                               "ms": round((time.perf_counter() - a) * 1000, 1), "ok": True})
            except Exception as e:
                orders.append({"i": i + 1, "price": p, "ok": False, "error": str(e),
                               "ms": round((time.perf_counter() - a) * 1000, 1)})
            if delay_ms and i < count - 1:
                time.sleep(delay_ms / 1000.0)
        total = (time.perf_counter() - t0) * 1000
        oks = [o for o in orders if o.get("ok")]
        return {"symbol": symbol, "side": side, "requested": count, "placed": len(oks),
                "failed": count - len(oks), "total_ms": round(total, 1),
                "delay_ms": delay_ms, "orders": orders}

    # ── position management (generic -> broker _submit_*) ───────────────────────
    def close_position(self, position_id, volume: float, price: float = None):
        if price is None:
            pos = next((p for p in self.positions()
                        if str(p.get("position_id")) == str(position_id)), None)
            sym = pos.get("instrument") if pos else None
            is_buy = int(pos.get("type", 0)) % 2 == 0 if pos else True
            price = self.price(sym, "bid" if is_buy else "ask") if sym else 0
        return self._submit_close(position_id, volume, price)

    def _filter_positions(self, position_id=None, symbol=None, only=None):
        positions = self.positions()
        if position_id is not None:
            positions = [p for p in positions if str(p.get("position_id")) == str(position_id)]
        if symbol is not None:
            positions = [p for p in positions if (p.get("instrument") or "").upper() == symbol.upper()]
        if only == "profit":
            positions = [p for p in positions if float(p.get("profit", 0) or 0) > 0]
        elif only == "loss":
            positions = [p for p in positions if float(p.get("profit", 0) or 0) < 0]
        return positions

    def close(self, position_id=None, symbol=None, volume=None, only=None):
        results = []
        for p in self._filter_positions(position_id, symbol, only):
            pid = p.get("position_id")
            sym = p.get("instrument")
            is_buy = int(p.get("type", 0)) % 2 == 0
            vol = volume if volume is not None else p.get("volume")
            try:
                price = self.price(sym, "bid" if is_buy else "ask")
                r = self.close_position(pid, vol, price=price)
                results.append({"position_id": pid, "instrument": sym, "volume": vol, "ok": True,
                                "result_code": (r or {}).get("position", {}).get("result_code")})
            except Exception as e:
                results.append({"position_id": pid, "instrument": sym, "ok": False, "error": str(e)})
        return results

    def close_all(self, symbol=None):
        return self.close(symbol=symbol)

    def modify_position(self, position_id, sl: float = 0, tp: float = 0, tsl=None):
        return self._submit_modify_position(position_id, sl, tp)

    def break_even(self, position_id=None, symbol=None, offset_points: int = 0):
        positions = self._filter_positions(position_id, symbol)
        quotes, results = {}, []
        for p in positions:
            pid = p.get("position_id")
            sym = p.get("instrument")
            entry = float(p.get("open_price"))
            tp = p.get("tp", 0) or 0
            is_buy = int(p.get("type", 0)) % 2 == 0
            side = "bid" if is_buy else "ask"
            if (sym, side) not in quotes:
                quotes[(sym, side)] = self.price(sym, side)
            cur = quotes[(sym, side)]
            in_profit = cur > entry if is_buy else cur < entry
            if not in_profit:
                results.append({"position_id": pid, "instrument": sym, "ok": False,
                                "skipped": "in loss — cannot break even"})
                continue
            sl = entry
            if offset_points:
                pt = self.point_size(sym)
                sl = entry + offset_points * pt if is_buy else entry - offset_points * pt
                sl = round(sl, int(self.instrument(sym)["digits"]))
            try:
                self.modify_position(pid, sl=sl, tp=tp)
                results.append({"position_id": pid, "instrument": sym,
                                "sl_set_to": sl, "tp_kept": tp, "ok": True})
            except Exception as e:
                results.append({"position_id": pid, "instrument": sym, "ok": False, "error": str(e)})
        return results

    def remove_levels(self, position_id=None, symbol=None, sl: bool = True, tp: bool = True):
        results = []
        for p in self._filter_positions(position_id, symbol):
            pid = p.get("position_id")
            new_sl = 0 if sl else (p.get("sl", 0) or 0)
            new_tp = 0 if tp else (p.get("tp", 0) or 0)
            try:
                self.modify_position(pid, sl=new_sl, tp=new_tp)
                results.append({"position_id": pid, "instrument": p.get("instrument"),
                                "sl": new_sl, "tp": new_tp,
                                "deleted": [x for x, on in (("sl", sl), ("tp", tp)) if on], "ok": True})
            except Exception as e:
                results.append({"position_id": pid, "instrument": p.get("instrument"),
                                "ok": False, "error": str(e)})
        return results

    def delete_sl_tp(self, position_id=None, symbol=None):
        return self.remove_levels(position_id, symbol, sl=True, tp=True)

    def delete_sl(self, position_id=None, symbol=None):
        return self.remove_levels(position_id, symbol, sl=True, tp=False)

    def delete_tp(self, position_id=None, symbol=None):
        return self.remove_levels(position_id, symbol, sl=False, tp=True)

    # Polling cadence for the profit monitor. Deliberately unhurried: a broker
    # whose API is rate-limited will start refusing a caller that hammers it,
    # and a monitor that gets itself blocked protects nothing at all. Exness
    # overrides this with a WebSocket version because it HAS a tick socket; a
    # broker without one polls, and polls politely.
    LOCK_POLL_MIN_S = 2.0        # never faster than this, however close the trigger
    LOCK_POLL_MAX_S = 30.0       # when profit is nowhere near the threshold
    LOCK_POLL_NEAR = 0.10        # within 10% of the trigger counts as "close"

    def lock_profit_money(self, percent, ref="peak", arm_above=0.0, max_seconds=None,
                          on_update=None, min_interval_s=None, max_interval_s=None,
                          **_ignored):
        """Watch TOTAL floating profit and CLOSE EVERYTHING if it retraces to
        `percent`% of its reference.

          percent=60, ref="peak"  → close all once running profit falls to 60%
                                    of its highest point. ref="start" measures
                                    from the profit at the moment it was armed.

        The cadence ADAPTS to how close the trigger is, because that is the only
        honest way to be both responsive and gentle on a rate-limited API. Far
        from the threshold there is nothing to react to, so it waits; as profit
        approaches the trigger the interval shrinks toward LOCK_POLL_MIN_S. A
        failed poll backs off and retries rather than abandoning a live stop —
        one 429 must not silently leave the position unprotected.

        `poll_ms` and `use_ws` are accepted and ignored, so a caller written for
        the Exness adapter works here unchanged."""
        frac = percent / 100.0 if percent > 1 else float(percent)
        lo = float(min_interval_s or self.LOCK_POLL_MIN_S)
        hi = max(lo, float(max_interval_s or self.LOCK_POLL_MAX_S))

        t0 = time.time()
        state = {"backoff": 0.0, "polls": 0}

        def read():
            """One profit reading, retried patiently. Returns None if the time
            budget ran out while the broker was refusing us.

            Arming goes through here too: a monitor that dies on its first 429
            leaves the position unprotected at exactly the moment the user
            believes it is guarded."""
            while True:
                try:
                    v = self.total_profit()
                    state["polls"] += 1
                    state["backoff"] = 0.0
                    return v
                except Exception:
                    # Doubling up from the slow end: a broker that is refusing
                    # us is left alone, not argued with.
                    state["backoff"] = min(hi, (state["backoff"] * 2) or hi / 2)
                    if max_seconds is not None and \
                            (time.time() - t0) + state["backoff"] >= max_seconds:
                        return None
                    time.sleep(state["backoff"])

        start_profit = read()
        if start_profit is None:
            return {"triggered": False, "armed": False, "peak": None, "mode": "poll",
                    "polls": state["polls"], "elapsed_s": round(time.time() - t0, 1),
                    "error": "could not read the account to arm the monitor"}
        peak = start_profit

        while True:
            cur = read()
            if cur is None:
                return {"triggered": False, "peak": peak, "mode": "poll",
                        "polls": state["polls"], "elapsed_s": round(time.time() - t0, 1),
                        "error": "the broker stopped answering"}

            peak = max(peak, cur)
            base = peak if ref == "peak" else start_profit
            if on_update:
                on_update(cur, peak)

            threshold = round(base * frac, 2)
            if base > arm_above and cur <= threshold:
                res = self.close_all()
                return {"triggered": True,
                        "closed": sum(1 for r in res if r.get("ok")),
                        "profit_at_close": cur, "peak": peak,
                        "reference": base, "threshold": threshold,
                        "mode": "poll", "polls": state["polls"],
                        "elapsed_s": round(time.time() - t0, 1)}

            if max_seconds is not None and (time.time() - t0) >= max_seconds:
                return {"triggered": False, "peak": peak, "mode": "poll",
                        "polls": state["polls"], "elapsed_s": round(time.time() - t0, 1)}

            # How much of the reference sits between here and the trigger. Once
            # that is under LOCK_POLL_NEAR we are in the zone that matters.
            headroom = (cur - threshold) / base if base > 0 else 1.0
            nearness = min(max(headroom / self.LOCK_POLL_NEAR, 0.0), 1.0)
            time.sleep(lo + (hi - lo) * nearness)

    def lock_profit(self, percent, position_id=None, symbol=None):
        frac = percent / 100.0 if percent > 1 else float(percent)
        quotes, results = {}, []
        for p in self._filter_positions(position_id, symbol):
            sym = p.get("instrument")
            is_buy = int(p.get("type", 0)) % 2 == 0
            entry = float(p.get("open_price"))
            tp = p.get("tp", 0) or 0
            side = "bid" if is_buy else "ask"
            if (sym, side) not in quotes:
                quotes[(sym, side)] = self.price(sym, side)
            cur = quotes[(sym, side)]
            profit_dist = (cur - entry) if is_buy else (entry - cur)
            if profit_dist <= 0:
                results.append({"position_id": p.get("position_id"), "instrument": sym,
                                "ok": False, "skipped": "not in profit", "current": cur})
                continue
            digits = int(self.instrument(sym)["digits"])
            new_sl = round(entry + frac * profit_dist, digits) if is_buy \
                else round(entry - frac * profit_dist, digits)
            try:
                self.modify_position(p["position_id"], sl=new_sl, tp=tp)
                results.append({"position_id": p["position_id"], "instrument": sym, "entry": entry,
                                "current": cur, "locked_sl": new_sl, "percent": percent, "ok": True})
            except Exception as e:
                results.append({"position_id": p.get("position_id"), "instrument": sym,
                                "ok": False, "error": str(e)})
        return results

    # ── pending-order management ────────────────────────────────────────────────
    @staticmethod
    def _ticket(o) -> str:
        return str(o.get("ticket_id") or o.get("order_id"))

    def modify_order(self, ticket, price=None, sl=None, tp=None, exp_date: int = 0, tsl=None):
        cur = next((o for o in self.orders() if self._ticket(o) == str(ticket)), None)
        if price is None:
            price = cur.get("price") if cur else None
        if sl is None:
            sl = (cur.get("sl", 0) if cur else 0) or 0
        if tp is None:
            tp = (cur.get("tp", 0) if cur else 0) or 0
        return self._submit_modify_order(ticket, price, sl, tp)

    def cancel_order(self, ticket):
        return self._submit_cancel_order(ticket)

    def cancel_orders(self, ticket=None, symbol=None):
        if ticket is not None:
            return [{"ticket": ticket, **self._safe_cancel(ticket)}]
        results = []
        for o in self.orders():
            if symbol and (o.get("instrument") or "").upper() != symbol.upper():
                continue
            tk = self._ticket(o)
            results.append({"ticket": tk, **self._safe_cancel(tk)})
        return results

    def cancel_all_orders(self, symbol=None):
        return self.cancel_orders(symbol=symbol)

    def _safe_cancel(self, ticket):
        try:
            self._submit_cancel_order(ticket)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── live streaming (polling; same snapshot shape as ExnessTrader.live_stream) ─
    def server(self):
        return {"ok": True, "time": int(time.time() * 1000)}

    def ping(self):
        return self.server()

    def live_stream(self, on_snapshot, stop_event, refresh_s=3.0, debounce_ms=300):
        """Poll positions + account state every `refresh_s` and emit account
        snapshots of the same shape ExnessTrader.live_stream produces.

        TradeLocker has no per-user push socket available to us (the BrandSocket
        needs a broker/brand key we don't have), so this is polling — at 3s it's
        near-real-time (2 cached API calls per poll, ~40/min, well under the limit)
        so closes/opens reflect quickly. Resilience: a lone failed poll does NOT
        flash a "connection error"; we keep the last snapshot and retry with backoff,
        surfacing an error only after several consecutive failures."""
        fails = 0
        while not stop_event.is_set():
            try:
                positions = self.positions()
                b = self.balance()
                rows = []
                for p in positions:
                    is_buy = int(p.get("type", 0)) % 2 == 0
                    pl = float(p.get("profit") or 0)
                    rows.append({"position_id": str(p.get("position_id")),
                                 "symbol": p.get("instrument"),
                                 "side": "buy" if is_buy else "sell",
                                 "volume": float(p.get("volume") or 0),
                                 "profit": round(pl, 2),
                                 "open_price": float(p.get("open_price") or 0),
                                 "sl": p.get("sl"), "tp": p.get("tp")})
                bal = float(b.get("balance") or 0)
                eq = float(b.get("equity") or bal)     # TradeLocker's own equity
                on_snapshot({"account": self.account, "balance": round(bal, 2),
                             "equity": round(eq, 2), "floating_profit": round(eq - bal, 2),
                             "positions": rows, "ts": int(time.time() * 1000)})
                fails = 0
                stop_event.wait(refresh_s)
            except Exception as e:
                fails += 1
                if fails >= 3:                        # only after sustained failure
                    try:
                        on_snapshot({"error": str(e)})
                    except Exception:
                        pass
                stop_event.wait(min(refresh_s * fails, 30))   # back off on failures

    # A chart's live candle, for a broker with no tick socket. 800ms × two calls
    # per symbol was five requests a second for one open chart — enough to get an
    # account rate-limited on its own. One call now (see `quote`), at a cadence a
    # person cannot tell from real time.
    TICK_POLL_MS = 2000

    def quote(self, symbol: str) -> dict:
        """{bid, ask}. The default asks twice; a broker whose API returns both in
        one response should override this, and most do."""
        return {"bid": self.price(symbol, "bid"), "ask": self.price(symbol, "ask")}

    def stream_ticks(self, symbols, on_tick, stop_event, poll_ms=None):
        """Poll live quotes for `symbols` and call on_tick({symbol,bid,ask,ts}).
        Same output the Exness tick WebSocket produces, so the chart is unchanged."""
        syms = [self.instrument(s)["symbol"] for s in symbols]
        wait = (poll_ms or self.TICK_POLL_MS) / 1000.0
        while not stop_event.is_set():
            for sym in syms:
                try:
                    q = self.quote(sym)
                    on_tick({"symbol": sym, "bid": q["bid"], "ask": q["ask"],
                             "ts": int(time.time() * 1000)})
                except Exception:
                    pass
            stop_event.wait(wait)
