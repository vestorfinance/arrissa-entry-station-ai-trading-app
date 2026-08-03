"""
Artificial Sentiment — who controls this market, reconstructed from its candles.

Myfxbook reports how ITS OWN users are positioned: one number per symbol, for the
symbols it happens to cover, behind a daily quota. This answers the same question
from the only evidence that is always available — OHLCV and market structure — so
it works on any instrument, on any timeframe, as often as you like.

THE MODEL (see "ARTIFICIAL SENTIMENT PLAN.md" for the full reasoning)

Every completed swing is a battle, and each battle TRANSFERS control energy
between bulls and bears. Energy is never created, only moved, so Bull + Bear is
always 100 and the output is a percentage without a fudge at the end.

What earns energy is not the price move alone but how it was won:

    strength = body_dominance × volume_factor × momentum × (1 − rejection)

and wicks are read AGAINST the body — a big upper wick on heavy volume means
buyers were met and absorbed, so it credits BEARS however green the candle is.

Liquidity events matter most. A sweep below a swing low that reverses does not
mean "lower" — it means weak longs were flushed and stronger buyers replaced
them at a better price, which is bullish. That single idea is what separates this
from counting green candles.

Alongside the percentage we keep an inventory map (price bucket → estimated long
and short units). It never leaves this module as a table, because a price ladder
answers no question a trader asks; it exists to produce the three numbers that
do: average long entry, average short entry, and how much of each side is
TRAPPED — underwater and therefore future forced flow.

The payload is MEASUREMENTS ONLY — percentages, prices, counts. No bias label,
no strength word, no event log, no written reasoning: "58% bull" is a number and
"moderately bullish" is a reading of it, and the caller does its own reading. A
label in the payload is just a second source of truth that can disagree with the
first.

IT IS A MODEL, NOT A MEASUREMENT. Every response says so in `method`.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

# ── tuning ─────────────────────────────────────────────────────────────────────
DEFAULT_COUNT = 200          # candles to reconstruct from
MIN_CANDLES = 40             # below this there is no structure to read
SWING_ATR_MULT = 1.5         # ZigZag threshold = this × ATR, so it scales per symbol
ATR_PERIOD = 14
HALF_LIFE_FRAC = 0.25        # a swing's weight halves every quarter-window of age
                             # (energy model only — inventory ages by survival)

# How much energy each event type may transfer at full strength. Sweeps dominate
# deliberately: a liquidity event tells you more about positioning than a big
# candle does, because it is where positions were forcibly closed.
ENERGY = {
    "impulse": 18.0,
    "sweep": 22.0,
    "break_of_structure": 14.0,
    "absorption": 10.0,
}
# Profit taking: an extended impulse gives back this fraction of what it earned,
# reached at PROFIT_TAKE_BARS. Its own holders start banking, which is why strong
# trends bleed into the pullback.
PROFIT_TAKE_MAX = 0.30
PROFIT_TAKE_BARS = 200


# Said in every response rather than buried in documentation.
METHOD = ("Derived from price structure only (swings, liquidity sweeps, volume and wick "
          "absorption) — NOT broker positioning data. It estimates who controls the market "
          "and where they are positioned; it cannot observe real accounts.")


# ── candle features ────────────────────────────────────────────────────────────
def _feat(c: dict) -> dict:
    """One candle, read as a contest rather than four numbers."""
    o, h, l, cl = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
    rng = max(h - l, 1e-12)
    body = cl - o
    upper = h - max(o, cl)
    lower = min(o, cl) - l
    vol = c.get("volume")
    vol = float(vol) if vol not in (None, "") else None
    return {
        "o": o, "h": h, "l": l, "c": cl,
        "range": rng,
        "body": body,
        "body_abs": abs(body),
        "body_dominance": abs(body) / rng,        # closed where it ran?
        "upper_wick": upper,
        "lower_wick": lower,
        "upper_frac": upper / rng,
        "lower_frac": lower / rng,
        "close_pos": (cl - l) / rng,              # 1 = closed on the high
        "bull": body > 0,
        "volume": vol,
    }


def _atr(feats: list, period: int = ATR_PERIOD) -> float:
    """Average true range over the features, with the previous close included so
    gaps count as the movement they are."""
    if not feats:
        return 0.0
    trs = []
    for i, f in enumerate(feats):
        if i == 0:
            trs.append(f["range"])
        else:
            prev_c = feats[i - 1]["c"]
            trs.append(max(f["h"] - f["l"], abs(f["h"] - prev_c), abs(f["l"] - prev_c)))
    tail = trs[-period:] if len(trs) >= period else trs
    return sum(tail) / len(tail) if tail else 0.0


def _volume_factor(feats: list, lo: int, hi: int) -> float:
    """This swing's volume against the window's average, capped so one freak bar
    cannot dominate. 1.0 when the broker reports no volume at all — the model then
    runs on structure alone, which is stated in the output rather than hidden."""
    vols = [f["volume"] for f in feats if f["volume"] is not None]
    if not vols:
        return 1.0
    avg = sum(vols) / len(vols)
    if avg <= 0:
        return 1.0
    seg = [f["volume"] for f in feats[lo:hi + 1] if f["volume"] is not None]
    if not seg:
        return 1.0
    return max(0.4, min((sum(seg) / len(seg)) / avg, 2.5))


# ── swings ─────────────────────────────────────────────────────────────────────
def _swings(feats: list, atr: float) -> list:
    """ZigZag: a new leg starts when price retraces more than the threshold from
    the running extreme. The threshold is ATR-scaled, so gold and EURUSD both get
    sensible swings without per-symbol settings."""
    if len(feats) < 3 or atr <= 0:
        return []
    thresh = atr * SWING_ATR_MULT

    legs = []
    direction = 0                      # +1 up, -1 down, 0 = not yet established
    piv_i, piv_p = 0, feats[0]["c"]    # the last confirmed pivot
    ext_i, ext_p = 0, feats[0]["c"]    # the running extreme since it

    def emit(a_i, b_i, a_p, b_p, d, is_open=False):
        legs.append({"dir": d, "start_i": a_i, "end_i": b_i,
                     "start": a_p, "end": b_p, "open": is_open})

    for i in range(1, len(feats)):
        h, l = feats[i]["h"], feats[i]["l"]

        # Until price has travelled a threshold either way there is no direction to
        # extend — seeding it from the first real move, rather than tracking both at
        # once, is what keeps the running extreme from being overwritten each bar.
        if direction == 0:
            if h - piv_p > thresh:
                direction, ext_i, ext_p = 1, i, h
            elif piv_p - l > thresh:
                direction, ext_i, ext_p = -1, i, l
            continue

        if direction > 0:
            if h > ext_p:                       # the up leg extends
                ext_i, ext_p = i, h
            elif ext_p - l > thresh:            # retraced enough: that high was a pivot
                emit(piv_i, ext_i, piv_p, ext_p, 1)
                piv_i, piv_p = ext_i, ext_p
                direction, ext_i, ext_p = -1, i, l
        else:
            if l < ext_p:
                ext_i, ext_p = i, l
            elif h - ext_p > thresh:
                emit(piv_i, ext_i, piv_p, ext_p, -1)
                piv_i, piv_p = ext_i, ext_p
                direction, ext_i, ext_p = 1, i, h

    # The leg still in progress — unconfirmed, but it is exactly where current
    # positioning lives, so it counts.
    if ext_i > piv_i:
        emit(piv_i, ext_i, piv_p, ext_p, direction or (1 if ext_p >= piv_p else -1),
             is_open=True)

    for leg in legs:
        lo, hi = leg["start_i"], leg["end_i"]
        seg = feats[lo:hi + 1] or [feats[lo]]
        leg["bars"] = max(1, hi - lo)
        leg["move"] = abs(leg["end"] - leg["start"])
        leg["momentum"] = leg["move"] / leg["bars"] / max(atr, 1e-12)
        leg["body_dominance"] = sum(s["body_dominance"] for s in seg) / len(seg)
        # Wick volume WITH and AGAINST the leg: rejection is the share of effort
        # that was absorbed by the other side.
        against = sum(s["upper_frac"] if leg["dir"] > 0 else s["lower_frac"] for s in seg)
        leg["rejection"] = min(against / len(seg), 0.9)
        leg["vol_factor"] = _volume_factor(feats, lo, hi)
        # How much ground the leg took, in ATRs: five is an ordinary leg, so that
        # scores 1.0. Territory is the point — a slow grind that travels a long way
        # is control just as much as a fast one, and scoring on momentum alone
        # rated a 70-bar trend leg below a single sharp candle.
        leg["size_factor"] = min(leg["move"] / max(atr * 5.0, 1e-12), 2.0)
        # Momentum still counts, but as a modifier around 1.0 rather than a factor
        # that punishes anything patient.
        pace = 0.75 + min(leg["momentum"], 1.5) * 0.35
        leg["strength"] = (leg["body_dominance"] * leg["vol_factor"] * pace
                           * leg["size_factor"] * (1.0 - leg["rejection"]))
    return legs


# ── liquidity events ───────────────────────────────────────────────────────────
def _classify(legs: list, feats: list) -> list:
    """What each swing DID to the structure before it: swept it and reversed,
    broke it and held, or broke it and failed."""
    events = []
    for n, leg in enumerate(legs):
        prior_highs = [l["end"] for l in legs[:n] if l["dir"] > 0]
        prior_lows = [l["end"] for l in legs[:n] if l["dir"] < 0]
        kind, note = "impulse", None

        if leg["dir"] < 0 and prior_lows:
            ref = min(prior_lows[-3:])
            if leg["end"] < ref:
                nxt = legs[n + 1] if n + 1 < len(legs) else None
                if nxt and nxt["dir"] > 0 and nxt["end"] > ref:
                    kind = "sweep_low"      # took the low, came straight back
                    note = (f"swept the {_r(ref)} low and reversed — weak longs flushed, "
                            "stronger buyers replaced them")
                else:
                    kind = "break_down"
                    note = f"broke the {_r(ref)} low and held below it"
        elif leg["dir"] > 0 and prior_highs:
            ref = max(prior_highs[-3:])
            if leg["end"] > ref:
                nxt = legs[n + 1] if n + 1 < len(legs) else None
                if nxt and nxt["dir"] < 0 and nxt["end"] < ref:
                    kind = "sweep_high"
                    note = (f"swept the {_r(ref)} high and reversed — breakout buyers "
                            "trapped, sellers absorbed them")
                else:
                    kind = "break_up"
                    note = f"broke the {_r(ref)} high and held above it"

        # A leg that is mostly wick against itself is absorption, whatever it closed.
        if kind == "impulse" and leg["rejection"] > 0.45:
            kind = "absorption"
            note = ("pushed and was absorbed — most of the effort came back as wick, "
                    "so the other side was waiting")

        leg["event"] = kind
        leg["note"] = note
        events.append(kind)
    return events


def _r(x, digits=None):
    """Round for prose without dragging float noise into a sentence."""
    if x is None:
        return None
    if digits is not None:
        return round(x, digits)
    return round(x, 5) if abs(x) < 100 else round(x, 2)


# ── the battle ─────────────────────────────────────────────────────────────────
def _weight(n: int, total: int) -> float:
    """Recency: a swing's say halves every HALF_LIFE_FRAC of the window, because
    positioning from the far end of it has mostly been closed out."""
    if total <= 1:
        return 1.0
    age = (total - 1 - n) / (total - 1)
    return 0.5 ** (age / max(HALF_LIFE_FRAC, 1e-9))


def _battle(legs: list) -> tuple:
    """Run the swings and transfer energy. Returns (bull, bear, log).

    The log records which battle moved what; it is not returned to callers — the
    output is measurements only — but it is the first thing to print when a
    reading looks wrong."""
    bull = bear = 50.0
    log = []

    for n, leg in enumerate(legs):
        w = _weight(n, len(legs))
        s = min(leg["strength"], 2.0)
        up = leg["dir"] > 0
        kind = leg["event"]

        if kind in ("sweep_low", "sweep_high"):
            # The sweep's beneficiary is the side that was NOT flushed.
            to_bull = kind == "sweep_low"
            amount = ENERGY["sweep"] * w * max(0.4, min(s, 1.5))
        elif kind in ("break_up", "break_down"):
            to_bull = kind == "break_up"
            amount = ENERGY["break_of_structure"] * w * max(0.3, min(s, 1.5))
        elif kind == "absorption":
            to_bull = not up          # the absorbed side loses; the absorber gains
            amount = ENERGY["absorption"] * w * max(0.3, min(s, 1.5))
        else:
            to_bull = up
            amount = ENERGY["impulse"] * w * max(0.2, min(s, 1.5))

        # Profit taking: an extended leg gives some back — its own holders start
        # banking, which is why strong trends bleed into the pullback. A FRACTION of
        # what the leg earned, never a flat subtraction: taking a fixed 6 points off
        # every long leg cancelled the impulse it was meant to trim.
        if kind == "impulse" and leg["bars"] >= 8:
            amount *= 1.0 - min(PROFIT_TAKE_MAX, leg["bars"] / PROFIT_TAKE_BARS)

        amount = max(amount, 0.0)
        # Zero-sum transfer, clamped so neither side can go negative.
        if to_bull:
            amount = min(amount, bear)
            bull, bear = bull + amount, bear - amount
        else:
            amount = min(amount, bull)
            bear, bull = bear + amount, bull - amount

        if amount >= 0.5:
            log.append({
                "type": kind,
                "side": "bull" if to_bull else "bear",
                "energy": round(amount, 2),
                "price": _r(leg["end"]),
                "bars": leg["bars"],
                "note": leg["note"] or _default_note(kind, up, leg),
            })
    return bull, bear, log


def _default_note(kind, up, leg):
    if kind == "impulse":
        return (f"{'bullish' if up else 'bearish'} impulse of {_r(leg['move'])} over "
                f"{leg['bars']} bars, {int(leg['body_dominance'] * 100)}% body")
    return kind.replace("_", " ")


# ── position survival ──────────────────────────────────────────────────────────
# A position does not live forever, and it does not vanish the instant price
# touches a level either. Each swing creates a COHORT of synthetic positions, and
# every later candle erodes the probability that cohort is still open:
#
#     P(open) = P(not taken profit) × P(not stopped) × P(not liquidated) × P(not timed out)
#
# Nothing here assumes one exit style. Retail does not all use 2:1, or one
# leverage, or any stop at all — so each factor is a WEIGHTED MIX over a
# distribution, and the survival curves are deliberately gentle: at the take
# profit a fifth of the cohort is still in, because some traders let winners run,
# and past the stop a few percent survive, because some widen stops or ignore them.
#
# Liquidation is modelled apart from the stop, because it is a different event:
# the broker closes the trade for want of margin, often BEFORE the trader's own
# stop. That is what removes weak hands on a sweep, and why a sweep flips
# positioning so violently.

# Reward targets, as a share of the cohort. (R multiple, weight)
RR_MIX = ((1.0, 0.25), (1.5, 0.40), (2.0, 0.25), (3.0, 0.10))
# Leverage, as a share of the cohort. (leverage, weight)
LEVERAGE_MIX = ((30, 0.40), (100, 0.40), (500, 0.20))
STOP_ATR = 1.0               # a retail stop, in ATRs at entry
LIQ_MARGIN_USE = 0.35        # fraction of equity behind a position, for the
                             # liquidation distance: entry / leverage / this
TIME_HALF_LIFE_BARS = 60     # P(not timed out) halves every this many candles

# Survival against progress toward an exit. x = distance travelled ÷ distance to
# the level; y = share of the cohort still open. Piecewise linear between points.
TP_CURVE = ((0.0, 1.00), (0.50, 0.92), (0.75, 0.68), (1.00, 0.20), (1.60, 0.06))
SL_CURVE = ((0.0, 1.00), (0.20, 0.95), (0.50, 0.75), (0.90, 0.45), (1.00, 0.08), (1.50, 0.02))
LIQ_CURVE = ((0.0, 1.00), (0.80, 0.90), (1.00, 0.05), (1.20, 0.00))


def _interp(curve, x: float) -> float:
    """Piecewise-linear lookup, flat outside the ends."""
    if x <= curve[0][0]:
        return curve[0][1]
    if x >= curve[-1][0]:
        return curve[-1][1]
    for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
        if x <= x1:
            t = (x - x0) / max(x1 - x0, 1e-12)
            return y0 + t * (y1 - y0)
    return curve[-1][1]


def _excursion(feats: list, born: int, entry: float, is_long: bool) -> tuple:
    """How far price has gone FOR and AGAINST a cohort since it was opened."""
    seg = feats[born:] or feats[-1:]
    hi = max(f["h"] for f in seg)
    lo = min(f["l"] for f in seg)
    if is_long:
        return max(hi - entry, 0.0), max(entry - lo, 0.0)      # favourable, adverse
    return max(entry - lo, 0.0), max(hi - entry, 0.0)


def _survival(entry: float, is_long: bool, atr: float, mfe: float, mae: float,
              age: int) -> tuple:
    """P(still open) for a cohort, and the share of it that was liquidated.

    Liquidation is returned separately because it is the interesting one: it is
    forced flow that has ALREADY happened, and it is what a sweep does to the
    losing side."""
    stop = max(STOP_ATR * atr, 1e-12)

    p_not_tp = sum(w * _interp(TP_CURVE, mfe / max(stop * r, 1e-12)) for r, w in RR_MIX)
    p_not_sl = _interp(SL_CURVE, mae / stop)

    p_not_liq, liquidated = 0.0, 0.0
    for lev, w in LEVERAGE_MIX:
        # How far price can go against a leveraged position before the broker
        # closes it. Crude by necessity — OHLCV cannot show anyone's leverage —
        # which is exactly why it is a DISTRIBUTION rather than one number.
        liq_dist = max(entry / lev / max(LIQ_MARGIN_USE, 1e-9), 1e-12)
        alive = _interp(LIQ_CURVE, mae / liq_dist)
        p_not_liq += w * alive
        liquidated += w * (1.0 - alive)

    p_time = 0.5 ** (max(age, 0) / max(TIME_HALF_LIFE_BARS, 1))
    return p_not_tp * p_not_sl * p_not_liq * p_time, liquidated


def _positions(legs: list, feats: list, atr: float) -> dict:
    """Every cohort the window created, aged forward to now.

    Positions are created BY THE CANDLE, not by the swing. Creating them only at
    swing ends meant a long clean trend produced almost no counter-trend
    inventory, so the losing side was annihilated and the read saturated at 99/1
    — which is no reading at all, since every trend then looks identical and
    Myfxbook's real numbers live between 20 and 80.

    Every candle makes both sides, split by what the candle actually shows:

        buyers  ∝ bullish body + LOWER wick   (selling that was absorbed)
        sellers ∝ bearish body + UPPER wick   (buying that was absorbed)

    which is the wick-reading from the plan doing real work — a green candle with
    a long upper shadow creates sellers, not just buyers. Volume scales the whole
    candle's contribution, and structure adds to it: a candle at a swing extreme
    is where positions actually cluster.

    The old sweep-flush and flat age-decay are gone. A position now leaves because
    price reached somewhere that would make it leave, which is the only thing
    OHLCV can honestly tell us."""
    price = feats[-1]["c"]
    last = len(feats) - 1
    vols = [f["volume"] for f in feats if f["volume"] is not None]
    avg_vol = (sum(vols) / len(vols)) if vols else None
    # Candles at a swing extreme carry extra weight: that is where stops rest and
    # where fresh positions are opened on the turn.
    pivots = {leg["end_i"] for leg in legs} | {leg["start_i"] for leg in legs}

    longs, shorts = [], []
    for i, f in enumerate(feats):
        rng = f["range"]
        if rng <= 0:
            continue
        vol_w = 1.0
        if avg_vol and f["volume"] is not None and avg_vol > 0:
            vol_w = max(0.3, min(f["volume"] / avg_vol, 3.0))
        pivot_w = 1.6 if i in pivots else 1.0
        scale = vol_w * pivot_w * 10.0

        long_u = (max(f["body"], 0.0) + f["lower_wick"]) / rng * scale
        short_u = (max(-f["body"], 0.0) + f["upper_wick"]) / rng * scale
        for units, is_long, book in ((long_u, True, longs), (short_u, False, shorts)):
            if units <= 1e-9:
                continue
            entry = f["c"]
            mfe, mae = _excursion(feats, i, entry, is_long)
            p_open, liq = _survival(entry, is_long, atr, mfe, mae, last - i)
            book.append({"entry": entry, "units": units, "open": units * p_open,
                         "liquidated": units * liq})

    def side(book, underwater_above):
        opened = sum(r["open"] for r in book)
        created = sum(r["units"] for r in book) or 1.0
        liq_pct = round(sum(r["liquidated"] for r in book) / created * 100, 1)
        if opened <= 0:
            return {"open": 0.0, "avg": None, "trapped": 0.0, "liquidated": liq_pct}
        avg = sum(r["entry"] * r["open"] for r in book) / opened
        under = sum(r["open"] for r in book
                    if (r["entry"] > price if underwater_above else r["entry"] < price))
        return {"open": opened, "avg": round(avg, 5),
                "trapped": round(under / opened * 100, 1), "liquidated": liq_pct}

    # Longs are underwater when they bought ABOVE the price; shorts when they sold
    # BELOW it. Verified against live data — the sign is the easiest thing here to
    # get backwards and the hardest to notice.
    return {"long": side(longs, True), "short": side(shorts, False)}


def _confidence(bulls_pct: float, energy_bulls: float, log: list, legs: int) -> tuple:
    """How much the evidence agrees with itself, 0–100, and the counts behind it.

    Two windows can both read 54% bull: one where every signal points the same way,
    one where they fight. Without this they look identical, and they are not.

      · agreement — do the surviving positions and the control battle say the
        same thing? They are computed from different things, so when they line up
        that is real corroboration.
      · decisiveness — how one-sided the battles were.
      · depth — how much structure there was to read at all.
    """
    bull_e = sum(e["energy"] for e in log if e["side"] == "bull")
    bear_e = sum(e["energy"] for e in log if e["side"] == "bear")
    total_e = bull_e + bear_e

    agree = max(0.0, 1.0 - abs(bulls_pct - energy_bulls) / 50.0)
    decisive = abs(bull_e - bear_e) / total_e if total_e > 0 else 0.0
    depth = min(legs / 12.0, 1.0)
    score = 100.0 * (0.50 * agree + 0.30 * decisive + 0.20 * depth)
    counts = {
        "swing_battles": len(log),
        "bull_events": sum(1 for e in log if e["side"] == "bull"),
        "bear_events": sum(1 for e in log if e["side"] == "bear"),
        "liquidity_sweeps": sum(1 for e in log if e["type"].startswith("sweep")),
        "breaks_of_structure": sum(1 for e in log if e["type"].startswith("break")),
        "absorptions": sum(1 for e in log if e["type"] == "absorption"),
    }
    return round(max(0.0, min(score, 100.0)), 1), counts


# ── the read ───────────────────────────────────────────────────────────────────
def analyse(candles: list, symbol: str = None, timeframe: str = None) -> dict:
    """Reconstruct positioning from a candle series (oldest first)."""
    if not candles or len(candles) < MIN_CANDLES:
        return {"error": f"need at least {MIN_CANDLES} candles to read structure; "
                         f"got {len(candles or [])}"}

    feats = [_feat(c) for c in candles]
    atr = _atr(feats)
    legs = _swings(feats, atr)
    price = feats[-1]["c"]
    has_volume = any(f["volume"] for f in feats)

    if len(legs) < 2:
        # No swing travelled a full threshold: the market is ranging, and that is
        # an ANSWER — neither side is in control — not a failure to compute one.
        return {
            "symbol": symbol, "timeframe": timeframe, "candles": len(candles),
            "bulls_percent": 50.0, "bears_percent": 50.0, "confidence_percent": 0.0,
            "current_price": _r(price),
            "average_long_entry": None, "average_short_entry": None,
            "trapped_longs_percent": 0.0, "trapped_shorts_percent": 0.0,
            "liquidated_longs_percent": 0.0, "liquidated_shorts_percent": 0.0,
            "open_long_units": 0.0, "open_short_units": 0.0,
            "swings": len(legs), "atr": _r(atr), "volume_used": has_volume,
            "evidence": {}, "from": candles[0].get("time"), "to": candles[-1].get("time"),
            "computed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "method": METHOD,
        }

    _classify(legs, feats)
    bull_energy, bear_energy, log = _battle(legs)
    energy_bulls = bull_energy / max(bull_energy + bear_energy, 1e-9) * 100

    pos = _positions(legs, feats, atr)
    live = pos["long"]["open"] + pos["short"]["open"]
    # The headline is SURVIVING POSITIONING — how much of each side is estimated
    # to still be in the market — not who won the battles. The battles feed the
    # confidence instead, where a second, independent estimate belongs.
    bulls_pct = round(pos["long"]["open"] / live * 100, 1) if live > 0 else 50.0
    bears_pct = round(100 - bulls_pct, 1)
    conf, evidence = _confidence(bulls_pct, energy_bulls, log, len(legs))

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": len(candles),
        "bulls_percent": bulls_pct,
        "bears_percent": bears_pct,
        "confidence_percent": conf,
        "current_price": _r(price),
        "average_long_entry": pos["long"]["avg"],
        "average_short_entry": pos["short"]["avg"],
        "trapped_longs_percent": pos["long"]["trapped"],
        "trapped_shorts_percent": pos["short"]["trapped"],
        # Already gone, not merely underwater: the share of each side's created
        # inventory the model thinks was force-closed for margin.
        "liquidated_longs_percent": pos["long"]["liquidated"],
        "liquidated_shorts_percent": pos["short"]["liquidated"],
        "open_long_units": round(pos["long"]["open"], 1),
        "open_short_units": round(pos["short"]["open"], 1),
        "swings": len(legs),
        "atr": _r(atr),
        "volume_used": has_volume,
        "evidence": evidence,
        "from": candles[0].get("time"),
        "to": candles[-1].get("time"),
        "computed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "method": METHOD,
    }


# ── the callable surface ───────────────────────────────────────────────────────
def read(symbol: str, timeframe: str = "M15", count: int = DEFAULT_COUNT,
         account=None) -> dict:
    """Fetch candles and read them. The only I/O in this module."""
    import market
    n = max(MIN_CANDLES, min(int(count or DEFAULT_COUNT), 1000))
    series = market.candles(symbol, timeframe=timeframe, count=n, account=account)
    res = analyse(series.get("candles") or [], symbol=series.get("symbol"),
                  timeframe=series.get("timeframe"))
    if res.get("error"):
        res.update({"symbol": series.get("symbol"), "timeframe": series.get("timeframe")})
    return res


def compare(symbol: str, timeframe: str = "M15", count: int = DEFAULT_COUNT,
            account=None) -> dict:
    """The modelled read beside Myfxbook's real retail one, where it exists.

    The disagreement is the point: Myfxbook is a sample of RETAIL, this is the
    whole market's footprint. Retail heavily long against a bearish footprint is
    the classic trapped-retail setup, and neither number says that alone."""
    out = read(symbol, timeframe=timeframe, count=count, account=account)
    if out.get("error"):
        return out
    # Retail comes from a module. Without it this still answers in full — the
    # comparison is a bonus, not a dependency.
    import registry
    retail = registry.get("sentiment")
    if retail is None:
        return out
    try:
        r = retail.one(symbol)
        if r and not r.get("error"):
            out["retail"] = {
                "source": "myfxbook",
                "long_percent": r.get("long_percent"),
                "short_percent": r.get("short_percent"),
                "age_seconds": r.get("age_seconds"),
            }
            # The gap as a signed number: positive = the footprint is more bullish
            # than the crowd, negative = the crowd is. What that MEANS is the
            # caller's call, so no sentence is attached to it.
            out["retail_gap"] = round((out["bulls_percent"] or 0) - (r.get("long_percent") or 0), 1)
    except Exception as e:
        out["retail"] = {"error": f"retail sentiment unavailable: {e}"}
    return out
