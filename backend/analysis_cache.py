"""
One analysis per instrument+style per window — everyone else rides along.

A hundred clients asking "Analyse XAUUSD for a scalper" in the same minute is one
question, not a hundred. The first caller in a window RUNS the agent; everyone
who asks while it is still running WAITS on that same run and gets its answer;
anyone who asks after it finished, but still inside the window, gets the stored
answer. Only the first caller pays full price — the rest are charged a fraction,
because they consumed a result rather than tokens.

    t=0.0s   client A  → runs the agent (~20s), pays full
    t=0.4s   client B  → waits on A's run, gets A's answer, pays the fraction
    t=21.0s  client C  → window still open, gets A's stored answer, pays the fraction
    t=61.0s  client D  → window closed, runs a fresh agent, pays full

The window and the fraction are admin settings (Admin → Settings → Analysis API).

Scope: the key is (user, agent, INSTRUMENT, STYLE) whenever both can be read out
of the request, so "Analyse XAUUSD for a scalper to enter immediately" and
"analyse XAUUSD, scalp" share one run. When no instrument is recognisable it
falls back to the normalised request text, which never over-shares.

NOTE: the registry is per process. With one uvicorn worker (how this app runs)
that is the whole app; behind several workers each would keep its own.
"""
import hashlib
import re
import threading
import time

DEFAULT_WINDOW_SECONDS = 60
DEFAULT_CACHED_CHARGE_PCT = 50
MAX_WAIT_SECONDS = 180          # a waiter never hangs longer than this

_lock = threading.Lock()
_entries = {}                   # key -> _Entry
_settings_cache = (0.0, None)   # (expires_at, dict) — one DB read per 10s, not per request


class _Entry:
    """One analysis: the run itself, then its result for the rest of the window."""
    __slots__ = ("event", "payload", "cost_usd", "failed", "created_at", "finished_at")

    def __init__(self):
        self.event = threading.Event()
        self.payload = None
        self.cost_usd = 0.0
        self.failed = False
        self.created_at = time.time()
        self.finished_at = None


# ── settings ───────────────────────────────────────────────────────────────────
def settings():
    """{'window_seconds', 'cached_charge_pct', 'enabled'} — admin-set, memoised 10s."""
    global _settings_cache
    now = time.time()
    if _settings_cache[0] > now and _settings_cache[1]:
        return _settings_cache[1]
    out = {"window_seconds": DEFAULT_WINDOW_SECONDS,
           "cached_charge_pct": DEFAULT_CACHED_CHARGE_PCT,
           "enabled": True}
    try:
        import db
        with db.connect() as conn:
            row = conn.execute(
                "SELECT analysis_window_seconds, analysis_cached_charge_pct, analysis_share_enabled "
                "FROM admin_settings WHERE id = 1").fetchone()
        if row:
            if row["analysis_window_seconds"] is not None:
                out["window_seconds"] = max(0, int(row["analysis_window_seconds"]))
            if row["analysis_cached_charge_pct"] is not None:
                out["cached_charge_pct"] = max(0, min(100, int(row["analysis_cached_charge_pct"])))
            if row["analysis_share_enabled"] is not None:
                out["enabled"] = bool(row["analysis_share_enabled"])
    except Exception:
        pass
    _settings_cache = (now + 10, out)
    return out


def save_settings(window_seconds=None, cached_charge_pct=None, enabled=None):
    global _settings_cache
    import db
    cur = settings()
    w = cur["window_seconds"] if window_seconds is None else int(window_seconds)
    p = cur["cached_charge_pct"] if cached_charge_pct is None else int(cached_charge_pct)
    e = cur["enabled"] if enabled is None else bool(enabled)
    if not (0 <= w <= 3600):
        raise ValueError("window must be between 0 and 3600 seconds")
    if not (0 <= p <= 100):
        raise ValueError("the cached charge must be between 0 and 100 percent")
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO admin_settings (id, analysis_window_seconds, analysis_cached_charge_pct,
                                           analysis_share_enabled)
               VALUES (1, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE SET
                 analysis_window_seconds    = EXCLUDED.analysis_window_seconds,
                 analysis_cached_charge_pct = EXCLUDED.analysis_cached_charge_pct,
                 analysis_share_enabled     = EXCLUDED.analysis_share_enabled""",
            (w, p, e))
        conn.commit()
    _settings_cache = (0.0, None)
    return settings()


# ── what counts as "the same question" ─────────────────────────────────────────
_STYLE_WORDS = {
    "scalp": "scalp", "scalper": "scalp", "scalping": "scalp",
    "intraday": "intraday", "daytrade": "intraday", "daytrader": "intraday",
    "swing": "swing", "swinger": "swing",
    "position": "position", "investor": "position", "longterm": "position",
}
# uppercase words that are never instruments
_NOT_SYMBOLS = {"API", "AI", "USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD",
                "BUY", "SELL", "SL", "TP", "RR", "EA", "UTC", "AND", "FOR", "THE"}
_SYMBOL_RE = re.compile(r"\b([A-Z][A-Z0-9]{2,9})\b")
_ALIAS = {"gold": "XAUUSD", "silver": "XAGUSD", "bitcoin": "BTCUSD", "btc": "BTCUSD",
          "ethereum": "ETHUSD", "eth": "ETHUSD", "nasdaq": "USTEC", "dax": "DE30",
          "dow": "US30", "oil": "USOIL", "cable": "GBPUSD"}


def scope(message):
    """(instrument, style) read out of the request — either may be None."""
    text = message or ""
    instrument = None
    for m in _SYMBOL_RE.finditer(text):                    # a written symbol wins
        word = m.group(1)
        if word not in _NOT_SYMBOLS:
            instrument = word
            break
    lowered = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    words = lowered.split()
    if instrument is None:
        for w in words:
            if w in _ALIAS:
                instrument = _ALIAS[w]
                break
    style = None
    for w in words:
        if w in _STYLE_WORDS:
            style = _STYLE_WORDS[w]
            break
    return instrument, style


def key_for(user_id, agent_id, message):
    """The sharing key. Same user, same agent, same instrument, same style ⇒ one run.
    Without a recognisable instrument it degrades to the exact question asked."""
    instrument, style = scope(message)
    if instrument:
        basis = f"{user_id}|{agent_id}|{instrument}|{style or '-'}"
    else:
        basis = f"{user_id}|{agent_id}|text:{' '.join((message or '').lower().split())}"
    return hashlib.sha256(basis.encode()).hexdigest()


# ── the coalescing gate ────────────────────────────────────────────────────────
def _prune(now, window):
    for k, e in list(_entries.items()):
        if e.finished_at and now - e.finished_at > window:
            _entries.pop(k, None)
        elif not e.finished_at and now - e.created_at > MAX_WAIT_SECONDS:
            _entries.pop(k, None)          # a leader that died


def acquire(user_id, agent_id, message):
    """Decide this caller's role.

    Returns (role, entry):
      "run"    — you are the first: run the analysis, then call finish()/fail()
      "wait"   — someone is running it: call wait(entry)
      "cached" — it already ran inside the window: entry.payload is the answer
    """
    cfg = settings()
    window = cfg["window_seconds"]
    if not cfg["enabled"] or window <= 0:
        return "run", None                  # sharing switched off: everyone runs

    k = key_for(user_id, agent_id, message)
    now = time.time()
    with _lock:
        _prune(now, window)
        e = _entries.get(k)
        if e is not None:
            if e.finished_at is None:
                return "wait", e                                  # in flight
            if not e.failed and now - e.finished_at <= window:
                return "cached", e                                # still fresh
            _entries.pop(k, None)                                 # stale or failed
        e = _Entry()
        _entries[k] = e
        return "run", e


def wait(entry):
    """Block until the leader finishes. Returns its entry, or None if it failed
    or took too long — the caller then runs the analysis itself."""
    if entry is None:
        return None
    remaining = MAX_WAIT_SECONDS - (time.time() - entry.created_at)
    if remaining <= 0 or not entry.event.wait(timeout=remaining):
        return None
    return None if (entry.failed or entry.payload is None) else entry


def finish(entry, payload, cost_usd):
    """The leader's result — released to every waiter and kept for the window."""
    if entry is None:
        return
    entry.payload = payload
    entry.cost_usd = float(cost_usd or 0.0)
    entry.finished_at = time.time()
    entry.event.set()


def fail(entry):
    """The leader blew up: wake the waiters so they run their own analysis."""
    if entry is None:
        return
    entry.failed = True
    entry.finished_at = time.time()
    entry.event.set()
    with _lock:
        for k, v in list(_entries.items()):
            if v is entry:
                _entries.pop(k, None)


def charge_fraction():
    return settings()["cached_charge_pct"] / 100.0


def stats():
    now = time.time()
    with _lock:
        running = sum(1 for e in _entries.values() if e.finished_at is None)
        return {"tracked": len(_entries), "running": running,
                "fresh": sum(1 for e in _entries.values()
                             if e.finished_at and now - e.finished_at <= settings()["window_seconds"])}
