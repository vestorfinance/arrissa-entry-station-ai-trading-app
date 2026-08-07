"""Run an agent when something HAPPENS, rather than when a clock says so.

An interval trigger asks "has fifteen minutes passed". This one asks "has
anything arrived worth waking up for" — a Truth Social post, a news story, one
that names an instrument you care about, an economic release about to land, or
one that has just printed. The difference matters most at exactly the moments
that matter most: a schedule that fires at :00 and :15 meets an 08:30 NFP print
either two minutes early or thirteen minutes late.

How it is put together
----------------------
Core cannot import a module, so nothing here knows what Truth Social is. It asks
the registry for a provider by name and does nothing at all when there isn't
one, which is what makes an uninstalled module a missing OPTION rather than a
crash.

Every condition answers the same question — "did something new happen since I
last looked" — and answers it with the item, not just a yes. The item becomes
the request the agent runs on, so the flow analyses THE post that fired it
rather than going and fetching whatever is latest.

Firing twice on one thing is the failure that would make this unusable, so
"new" is decided by a unique key per item and enforced by the database, not by
comparing timestamps. A restart mid-tick, two workers, a clock that steps
backwards: none of them can produce a second run for one post.

Conditions combine with `and` or `or`. `and` means every condition produced
something in the SAME tick — which is the only reading that is both useful and
implementable, since "a new truth AND a new story" has to mean "near enough
together to be about the same thing" or it means nothing at all.
"""
import json
import threading
import time as _time
from datetime import datetime, timedelta, timezone

TICK = 30            # seconds between looks. Fast enough for "30 seconds before".
COOLDOWN = 60        # least time between two runs of one agent, whatever fires.
LOOKBACK_MIN = 30    # how far back a first look reaches, so a new agent is not
                     # instantly buried by everything already in the table.


# ── conditions ────────────────────────────────────────────────────────────────
# Each returns (met, payload). The payload is what the agent will be told.

def _iso(dt) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def _secs(cond) -> int:
    unit = (cond.get("unit") or "minutes").lower()
    mult = {"seconds": 1, "minutes": 60, "hours": 3600}.get(unit, 60)
    try:
        return max(0, int(float(cond.get("amount") or 0))) * mult
    except (TypeError, ValueError):
        return 0


def _impact_ok(item, want) -> bool:
    want = (want or "").strip().lower()
    if not want or want == "any":
        return True
    return str(item.get("impact") or "").lower() == want


def _c_truth(cond, since):
    import registry
    p = registry.get("truth")
    if not p:
        return []
    r = p.query(user=(cond.get("user") or "trump"), hours=6, limit=50,
                impact=(cond.get("impact") or None) if (cond.get("impact") or "any") != "any" else None)
    out = []
    for post in (r.get("posts") or []):
        at = _parse(post.get("datetime"))
        if at and at < since:
            continue
        out.append({"key": f"truth:{post.get('post_id')}",
                    "what": "a new Truth Social post",
                    "text": (post.get("content") or "")[:600],
                    "at": post.get("datetime"), "impact": post.get("impact"),
                    "item": post})
    return out


def _named(item, want: set) -> set:
    """Which of the wanted instruments this story is about.

    The news module tags every story with the instruments it concerns, so this
    reads that judgement rather than searching the text: a story mentioning
    "gold" in passing is not a story about XAUUSD, and the tagger has already
    decided which it is."""
    raw = item.get("instruments")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw.replace("'", '"'))
        except Exception:
            raw = [x.strip(" []'\"") for x in raw.split(",")]
    named = {str(x).upper() for x in (raw or [])}
    return named & want


def _c_news(cond, since):
    """New stories, optionally only those about certain instruments.

    The filter lives on the condition rather than in a separate kind. Choosing
    between "a news story" and "a news story about something" before knowing
    which you want is a decision the builder should not ask for — it is one
    question with an optional answer, and an empty list means every story."""
    import registry
    p = registry.get("news")
    if not p:
        return []
    r = p.query(hours=6, limit=50)
    want = (cond.get("impact") or "any").lower()
    symbols = set(_symbols(cond))
    out = []
    for a in (r.get("articles") or []):
        at = _parse(a.get("time"))
        if at and at < since:
            continue
        if not _impact_ok(a, want):
            continue

        hit = {"key": f"news:{a.get('url') or a.get('title')}",
               "what": "a new market news story",
               "text": (a.get("title") or "")[:400],
               "at": a.get("time"), "impact": a.get("impact"),
               "item": a}

        if symbols:
            # ANY of them, not all: three instruments named on a condition means
            # "wake me for anything touching these", never "a story about all
            # three at once", which almost nothing is.
            named = _named(a, symbols)
            if not named:
                continue
            hit["key"] = "sym" + hit["key"]
            hit["what"] = f"news about {', '.join(sorted(named))}"
            hit["symbols"] = sorted(named)
        out.append(hit)
    return out


def _symbols(cond) -> list:
    return [s.strip().upper() for s in str(cond.get("symbols") or "").split(",") if s.strip()]


def _c_news_symbols(cond, since):
    """The old separate kind, now the same thing under its former name.

    Flows saved before the filter moved onto the news condition still say
    `news_symbols`, and a saved flow must not stop firing because the builder
    was tidied. It refuses an empty list, as it always did: that kind exists to
    filter, and filtering on nothing was never what anyone meant by it."""
    if not _symbols(cond):
        return []
    return _c_news(cond, since)


def _c_before_event(cond, since, now):
    """An economic release is about to land.

    Fires once, when the event enters the lead window. The window is the lead
    time back one tick, not "anything sooner than the lead" — otherwise an event
    30 minutes out would qualify every tick for the next 30 minutes, and the
    dedup key is the only thing that would stop it."""
    import registry
    p = registry.get("calendar")
    if not p:
        return []
    lead = _secs(cond)
    if not lead:
        return []
    try:
        r = p.next_events(currency=(cond.get("currency") or None), limit=40)
    except TypeError:
        r = p.next_events(limit=40)
    out = []
    for e in (r.get("events") or []):
        at = _parse(e.get("time"))
        if not at or not _impact_ok(e, cond.get("impact")):
            continue
        delta = (at - now).total_seconds()
        if 0 <= delta <= lead:
            out.append({"key": f"pre:{e.get('event')}|{e.get('time')}|{lead}",
                        "what": f"{e.get('event')} in {int(delta // 60)} min",
                        "text": f"{e.get('event')} ({e.get('currency')}) at {e.get('time')}",
                        "at": e.get("time"), "impact": e.get("impact"), "item": e})
    return out


def _c_after_event(cond, since, now):
    """An economic release has printed, and the wait is over.

    `released` is the module's own flag for "the actual is in", so this does not
    have to guess from the clock whether the number exists yet."""
    import registry
    p = registry.get("calendar")
    if not p:
        return []
    wait = _secs(cond)
    # BACKWARDS. `hours=N` on the calendar is a window into the FUTURE, so
    # asking it for the last twelve hours returned the next twelve and could
    # never contain something that had already printed. since/until are the
    # explicit pair, and `released=True` is the module's own test for "the
    # actual is in" — a truer signal than reading a field and hoping.
    span = max(wait + TICK * 4, 3600)
    try:
        r = p.query(currency=(cond.get("currency") or None), released=True, limit=60,
                    since=_iso(now - timedelta(seconds=span)), until=_iso(now))
    except TypeError:
        r = p.query(released=True, limit=60,
                    since=_iso(now - timedelta(seconds=span)), until=_iso(now))
    out = []
    for e in (r.get("events") or []):
        if not _impact_ok(e, cond.get("impact")):
            continue
        at = _parse(e.get("time"))
        if not at:
            continue
        ready = at + timedelta(seconds=wait)
        # In the window that opened when the wait elapsed. Bounded, so an event
        # from this morning does not fire an agent created this afternoon.
        if now >= ready and (now - ready).total_seconds() <= max(TICK * 2, 120):
            out.append({"key": f"post:{e.get('event')}|{e.get('time')}|{wait}",
                        "what": f"{e.get('event')} released",
                        "text": (f"{e.get('event')} ({e.get('currency')}) actual "
                                 f"{e.get('actual')}, forecast {e.get('forecast')}, "
                                 f"previous {e.get('previous')}"),
                        "at": e.get("time"), "impact": e.get("impact"), "item": e})
    return out


def evaluate(cond, since, now) -> list:
    kind = (cond.get("kind") or "").strip()
    if kind == "truth":
        return _c_truth(cond, since)
    if kind == "news":
        return _c_news(cond, since)
    if kind == "news_symbols":
        return _c_news_symbols(cond, since)
    if kind == "before_event":
        return _c_before_event(cond, since, now)
    if kind == "after_event":
        return _c_after_event(cond, since, now)
    return []


# ── which sources exist on this instance ──────────────────────────────────────
# The calendar says moderate where the news says medium. One dropdown offering
# "medium" to a calendar condition would have filtered out every event there is
# and looked like a quiet week, so each kind carries its own list.
IMPACTS_NEWS = ["any", "high", "medium", "low"]
IMPACTS_CAL = ["any", "high", "moderate", "low"]

KINDS = [
    {"kind": "truth", "label": "A new Truth Social post", "provider": "truth",
     "impacts": ["any", "high", "low"]},
    {"kind": "news", "label": "A new market news story", "provider": "news",
     "impacts": IMPACTS_NEWS, "symbols": True},
    {"kind": "news_symbols", "label": "News about these instruments (same as above)",
     "provider": "news", "impacts": IMPACTS_NEWS, "symbols": True, "legacy": True},
    {"kind": "before_event", "label": "Before an economic release", "provider": "calendar",
     "impacts": IMPACTS_CAL},
    {"kind": "after_event", "label": "After an economic release", "provider": "calendar",
     "impacts": IMPACTS_CAL},
]


def available() -> list:
    """The conditions this instance can actually offer.

    A condition whose module is not installed is not offered, rather than
    offered and silently never firing — which is the worse of the two, because
    it looks configured."""
    import registry
    return [{**k, "available": bool(registry.get(k["provider"]))} for k in KINDS]


# ── firing ────────────────────────────────────────────────────────────────────
def _seen(agent_id, key) -> bool:
    """True if this agent has already fired on this item.

    The database decides, not a comparison: INSERT and see whether it landed.
    Two workers, a restart mid-tick or a clock that steps backwards cannot
    produce a second run for one post."""
    import db
    with db.connect() as conn:
        row = conn.execute(
            "INSERT INTO agent_trigger_seen (agent_id, item_key) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING RETURNING 1", (agent_id, key)).fetchone()
        conn.commit()
    return row is None


def _trigger_of(flow) -> dict:
    for n in (flow or {}).get("nodes", []):
        if (n.get("data") or {}).get("kind") == "trigger-data":
            return (n["data"].get("values") or {})
    return {}


def _fire(row, hits, log=print):
    """Run the agent on what happened."""
    import agent_schedule
    import analysis_agent
    import billing
    import user_session
    import main as app_main
    import ai_keys

    agent_id, user_id = str(row["id"]), row["user_id"]
    lines = [f"- {h['what']}: {h['text']}" for h in hits]
    request = ("Something you were watching for has happened.\n\n"
               + "\n".join(lines)
               + "\n\nAnalyse it and respond.")

    state = billing.get_state(user_id)
    if not state["active"] or state["credits"] <= 0:
        return None                       # metered like every other run

    alias = billing.DEFAULT_MODEL
    try:
        alias = app_main._user_analysis_model(user_id) or billing.DEFAULT_MODEL
    except Exception:
        pass
    provider, model, key = ai_keys.resolve(user_id, alias)
    ctx = {"_model_label": (alias if ":" not in (alias or "") else None),
           "provider": provider, "api_key": key, "model": model,
           "user_id": user_id, "agent_tools": {}, "last_user": request}

    # What fired it, as variables — so a flow can put {{symbol}} in an API call
    # and have it be the instrument the story was about.
    variables = {"event": hits[0]["what"], "text": hits[0]["text"]}
    syms = [s for h in hits for s in (h.get("symbols") or [])]
    if syms:
        variables["symbol"] = ",".join(sorted(set(syms)))

    with user_session.as_user(user_id):
        res = analysis_agent.run_flow(row["flow"], request, ctx, agent_id=agent_id,
                                      source="data-trigger", variables=variables)
    try:
        billing.charge_cost(user_id, res.get("cost_usd") or 0, "analysis-data-trigger",
                            agent_id, provider=res.get("provider"))
    except Exception:
        pass
    log(f"[data-trigger] {row['name']}: {hits[0]['what']}")
    return res


def run_once(log=print) -> dict:
    """One look. Returns what fired."""
    import db

    now = datetime.now(timezone.utc)
    fired = []
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, user_id, name, flow, last_data_fire FROM analysis_agents "
            "WHERE status = 'active' AND flow IS NOT NULL").fetchall()

    for row in rows:
        vals = _trigger_of(row["flow"])
        conds = vals.get("conditions") or []
        if isinstance(conds, str):
            try:
                conds = json.loads(conds)
            except Exception:
                conds = []
        if not conds:
            continue

        last = row.get("last_data_fire")
        if last and (now - last).total_seconds() < COOLDOWN:
            continue
        since = last or (now - timedelta(minutes=LOOKBACK_MIN))

        try:
            per = [evaluate(c, since, now) for c in conds]
        except Exception as e:
            log(f"[data-trigger] {row['name']}: {e}")
            continue

        combine = (vals.get("combine") or "or").lower()
        if combine == "and":
            # Every condition had to produce something this tick. One key from
            # all of them together, so the pair fires once rather than each part
            # of it firing on its own.
            if not all(per):
                continue
            hits = [p[0] for p in per]
            key = "&".join(sorted(h["key"] for h in hits))
            if _seen(str(row["id"]), key):
                continue
            groups = [hits]
        else:
            groups = [[h] for hs in per for h in hs
                      if not _seen(str(row["id"]), h["key"])]

        for hits in groups[:3]:            # a burst of news is not a burst of runs
            try:
                if _fire(row, hits, log) is not None:
                    fired.append({"agent": row["name"], "what": hits[0]["what"]})
                    with db.connect() as conn:
                        conn.execute("UPDATE analysis_agents SET last_data_fire = now() "
                                     "WHERE id = %s", (row["id"],))
                        conn.commit()
            except Exception as e:
                log(f"[data-trigger] {row['name']} failed: {e}")
            break                          # one run per agent per tick

    return {"fired": fired}


_stop = threading.Event()


def _loop():
    _stop.wait(20)                         # let the app finish coming up
    while not _stop.is_set():
        try:
            run_once()
        except Exception as e:
            print(f"[data-trigger] {e!r}", flush=True)
        if _stop.wait(TICK):
            return


def start():
    threading.Thread(target=_loop, name="data-triggers", daemon=True).start()


def stop():
    _stop.set()
