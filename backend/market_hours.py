"""
When a market is open — one definition, shared.

The FX week turns on New York's 17:00: 21:00 UTC on summer time, 22:00 UTC on
winter time. The hour is DERIVED from the New York clock rather than written
down, so the March and November switches happen on their own and nothing here
needs editing twice a year.

    · every weekday: the rollover hour itself is shut;
    · the week opens Sunday at the hour AFTER rollover and closes Friday at it.

Crypto keeps its own hours — it does not stop — which is the whole reason this
module exists rather than a single boolean: on a Saturday the honest answer for
EURUSD and for BTCUSD are different answers.
"""
from datetime import datetime, timezone

CLOSED_HOURS = 1        # the rollover break, in hours

# Instruments that trade through the weekend. Matched on the resolved symbol, so
# BTCUSD, ETHUSD and anything else crypto the broker names.
_ALWAYS_OPEN_PREFIXES = ("BTC", "ETH", "XRP", "SOL", "ADA", "DOGE", "LTC", "BNB",
                         "DOT", "AVAX", "LINK", "MATIC", "TRX", "SHIB")
_ALWAYS_OPEN_CATEGORIES = {"crypto"}


def rollover_hour_utc(now=None) -> int:
    """The UTC hour the trading day turns over: 21 on New York summer time, 22 on
    winter. Read from the New York clock itself, so DST needs no maintenance."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        offset = now.astimezone(ZoneInfo("America/New_York")).utcoffset()
        return 21 if offset and offset.total_seconds() == -4 * 3600 else 22
    except Exception:
        return 21 if 3 <= now.month <= 10 else 22      # crude, right most of the year


def fx_open(now=None) -> bool:
    """Is the ordinary (non-crypto) market trading right now?"""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    h, wd = now.hour, now.weekday()                    # Monday = 0 … Sunday = 6
    close_h = rollover_hour_utc(now)
    open_h = (close_h + CLOSED_HOURS) % 24

    if wd == 5:                                        # Saturday: shut all day
        return False
    if wd == 4 and h >= close_h:                       # Friday: shut from the close
        return False
    if wd == 6:                                        # Sunday: shut until the open
        return h >= open_h
    return not (close_h <= h < close_h + CLOSED_HOURS)  # the daily rollover break


def always_open(symbol: str = None, category: str = None) -> bool:
    """Does this instrument trade through the weekend?"""
    if category and category.lower() in _ALWAYS_OPEN_CATEGORIES:
        return True
    s = (symbol or "").upper()
    return any(s.startswith(p) for p in _ALWAYS_OPEN_PREFIXES)


def is_open(symbol: str = None, category: str = None, now=None) -> bool:
    """Is THIS instrument tradable right now? Crypto: always. Everything else
    follows the FX week."""
    return True if always_open(symbol, category) else fx_open(now)


def state(now=None) -> dict:
    """The calendar in words, for a caller that has to explain itself."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    close_h = rollover_hour_utc(now)
    live = fx_open(now)
    if live:
        why = "the FX week is open"
    elif now.weekday() in (4, 5, 6):
        why = ("the weekend — FX, metals, indices and energy are shut from Friday's close "
               "until Sunday's open; only crypto trades")
    else:
        why = "the daily rollover hour — everything but crypto is shut for the hour"
    return {
        "fx_open": live,
        "crypto_open": True,
        "note": why,
        "rollover_utc": f"{close_h:02d}:00–{(close_h + CLOSED_HOURS) % 24:02d}:00",
        "season": "summer (New York DST)" if close_h == 21 else "winter",
    }


def split(universe: dict, now=None) -> tuple:
    """{symbol: category} → (tradable, closed). The only thing callers need."""
    tradable, closed = {}, {}
    for sym, cat in (universe or {}).items():
        (tradable if is_open(sym, cat, now) else closed)[sym] = cat
    return tradable, closed
