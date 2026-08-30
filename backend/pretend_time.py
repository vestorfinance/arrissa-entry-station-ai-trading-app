"""Ask any data API for what it would have said at some past moment.

    GET /api/v1/news?pretend_date=2026-08-25&pretend_time=13:12

Both are UTC. The point is replay: run an analysis, a backtest or an agent
against the information that actually existed at a moment, rather than against
today's hindsight. An agent asked "what would you have done" is worthless if it
can see the outcome.

Two rules, and the difference between them is the whole design.

WHAT HAPPENED is hidden if it had not happened yet. A news story published at
14:00 does not exist at 13:12; a Truth Social post made afterwards did not exist
either. These are dropped.

WHAT WAS SCHEDULED is kept, because it WAS known. An economic release due at
15:30 was on the calendar days beforehand, and hiding it would misrepresent the
past as badly as revealing the outcome. So the event stays — but its `actual` is
blanked, because at 13:12 the number had not printed. Forecast and previous
survive; those were known.

Getting that backwards in either direction is the failure mode worth guarding
against: hide the scheduled event and you pretend nobody knew a release was
coming; keep its actual and you leak the future into the past.

Applied as middleware, so it covers every data endpoint including the ones in
modules — core cannot import a module, and would otherwise have to trust each
one to implement this identically.
"""
import contextvars
import re
from datetime import datetime, timezone

_pretend = contextvars.ContextVar("pretend_now", default=None)

# Fields that mean "when this happened / is due". Ordered by how specific they
# are, so a row carrying several is read by the most meaningful one.
# Tried in order, so the most specific name wins for a row carrying several.
# `fetched_at` is here because leaving it out was not a small gap: a fedwatch
# snapshot has no other readable timestamp, so prune() saw no time at all, kept
# every row, and a replay of last Tuesday quietly answered with today.
TIME_FIELDS = ("time", "datetime", "posted_at", "created_at", "published_at",
               "fetched_at", "recorded_at", "observed_at",
               "at", "date", "timestamp", "release_time", "event_time")

# A row carrying any of these is a SCHEDULED thing, not a published one — it was
# on the calendar in advance, so it survives with its outcome removed.
SCHEDULED_MARKERS = ("actual", "forecast", "previous")

# What is unknown before the moment it happens.
OUTCOME_FIELDS = ("actual", "released", "impact_actual", "surprise")


def parse(date_s: str | None, time_s: str | None):
    """`pretend_date=2026-08-25` + `pretend_time=13:12` → an aware UTC datetime.

    Date alone means the very start of that day: asking to be "on the 25th"
    without a time should not quietly hand you the whole 25th, which would
    include the evening's news at nine in the morning.
    """
    if not date_s:
        return None
    date_s = str(date_s).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_s):
        raise ValueError("pretend_date must look like 2026-08-25")
    hh, mm, ss = 0, 0, 0
    if time_s:
        t = str(time_s).strip()
        m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", t)
        if not m:
            raise ValueError("pretend_time must look like 13:12 or 13:12:30")
        hh, mm, ss = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        if hh > 23 or mm > 59 or ss > 59:
            raise ValueError("pretend_time is not a real time of day")
    y, mo, d = (int(x) for x in date_s.split("-"))
    return datetime(y, mo, d, hh, mm, ss, tzinfo=timezone.utc)


def set_now(dt):
    return _pretend.set(dt)


def reset(token):
    try:
        _pretend.reset(token)
    except Exception:
        pass


def get():
    """The pretended moment, or None when we are simply in the present."""
    return _pretend.get()


def active() -> bool:
    return _pretend.get() is not None


def now() -> datetime:
    """What any code that asks the time should use.

    Anything computing a window — "the last six hours", "the next event" —
    should call this rather than datetime.now(), or a replayed request quietly
    measures from today.
    """
    return _pretend.get() or datetime.now(timezone.utc)


# ── reading timestamps out of whatever shape they arrive in ──────────────────
def _as_dt(v):
    """A timestamp in any of the shapes our sources use, or None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        # Epoch, in seconds or milliseconds. 1e11 separates them for any date
        # this side of 1973 in ms and 5138 in seconds.
        try:
            return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, timezone.utc)
        except Exception:
            return None
    if not isinstance(v, str) or len(v) < 8:
        return None
    s = v.strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _row_time(row: dict):
    for f in TIME_FIELDS:
        if f in row:
            d = _as_dt(row.get(f))
            if d:
                return d
    return None


def _is_scheduled(row: dict) -> bool:
    return any(k in row for k in SCHEDULED_MARKERS)


def _blank_outcome(row: dict) -> dict:
    """Strip what had not been announced yet, leaving what was known."""
    out = dict(row)
    for f in OUTCOME_FIELDS:
        if f in out:
            out[f] = False if f == "released" else None
    out["pretend_pending"] = True      # so a reader knows why it is empty
    return out


def prune(node, cutoff):
    """Walk a response and apply the two rules. Returns a rewritten copy.

    Anything without a readable timestamp is left alone: a balance, a config
    block or a status object has no place in the past and dropping it would
    empty out responses that are not time series at all.
    """
    if isinstance(node, dict):
        return {k: prune(v, cutoff) for k, v in node.items()}

    if isinstance(node, list):
        out = []
        for item in node:
            if not isinstance(item, dict):
                out.append(prune(item, cutoff))
                continue
            t = _row_time(item)
            if t is None:
                out.append(prune(item, cutoff))
                continue
            if t <= cutoff:
                out.append(prune(item, cutoff))
            elif _is_scheduled(item):
                # It was on the calendar; the number was not in yet.
                out.append(prune(_blank_outcome(item), cutoff))
            # else: it had not happened. It does not exist at this moment.
        return out

    return node


def cap(since, until):
    """Close an open-ended window at the pretended moment.

    Every one of these sources treats `until=None` as "up to now", which is
    normally safe because nothing is timestamped in the future. Under replay it
    is exactly wrong: the query happily returns today's rows and the response
    filter then deletes all of them, so a perfectly good request comes back
    empty. Capping the window is what makes the source SELECT the right rows in
    the first place, rather than fetching the wrong ones and discarding them.
    """
    p = get()
    if p is None:
        return since, until
    return since, (min(until, p) if until else p)
