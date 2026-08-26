"""`range=last-30-minutes` — one way to say "the last N of something".

Before this, asking for a window meant knowing which parameter a given API
happened to expose: news took `hours`, the calendar took `days`, and neither
took weeks or months at all. So "the last three weeks" was `days=21` on one
endpoint and impossible on another.

`last-<N>-<unit>` says it once, and every data API understands it:

    last-30-seconds   last-15-minutes   last-6-hours
    last-3-days       last-2-weeks      last-6-months     last-1-years

Written how people actually type it: singular or plural, hyphens, underscores or
spaces, and `last-1-hour` is the same as `last-1-hours`.

Months and years are approximations — 30 and 365 days — and deliberately so. A
calendar month is not a duration, and someone asking for "the last six months"
of news wants roughly half a year, not an argument about February. Anyone who
needs an exact boundary has `since`/`until`, which mean precisely what they say.
"""
import re
from datetime import timedelta

UNITS = {
    "second": 1,
    "minute": 60,
    "hour": 3600,
    "day": 86400,
    "week": 604800,
    "month": 2592000,        # 30 days
    "year": 31536000,        # 365 days
}

# last-30-minutes / last_30_minutes / "last 30 minutes" / last-1-hour
_RE = re.compile(
    r"^last[\s_-]+(\d+)[\s_-]+(second|minute|hour|day|week|month|year)s?$",
    re.IGNORECASE)

# A cap, so one request cannot ask the database for everything ever recorded.
MAX_SECONDS = 5 * 365 * 86400        # five years


def parse(text) -> timedelta | None:
    """A `last-N-unit` string → timedelta. None for anything else, so callers
    can fall through to their own presets without a special case."""
    if not text:
        return None
    m = _RE.match(str(text).strip())
    if not m:
        return None
    n = int(m.group(1))
    if n <= 0:
        return None
    secs = min(n * UNITS[m.group(2).lower()], MAX_SECONDS)
    return timedelta(seconds=secs)


def describe() -> str:
    """One line for a guide or an error message."""
    return ("last-<N>-<unit> where unit is "
            + ", ".join(f"{u}s" for u in UNITS)
            + " — e.g. last-30-minutes, last-6-hours, last-2-weeks")
