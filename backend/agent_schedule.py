"""
Scheduled analysis agents — the "Trigger on Intervals" node.

An agent normally waits to be called. A flow carrying a `trigger-interval` node
runs itself instead, either on a plain interval ("every 15 minutes") or on a cron
expression ("0 7 * * 1-5" — 07:00 UTC on weekdays).

How it works:
  · The SCHEDULE LIVES IN THE FLOW, not in a separate table the user has to keep
    in step with it. This module reads the node's values; `analysis_schedules`
    holds only what the flow cannot know — when it last ran and what happened.
  · One thread ticks every TICK_S. Any agent that is ACTIVE, has the node, and is
    due gets run through the ordinary engine (`analysis_agent.run_flow`), so a
    scheduled run is the same run a call would make, saved to the same history
    with source="schedule".
  · Every run is metered like any other: the tokens it burns are charged to the
    owner at cost. No subscription or no credits ⇒ the run is skipped, not run
    and billed into a negative balance.

Everything is UTC. Cron is matched to the minute, so an expression fires at most
once per minute, and a run that overruns its own interval simply schedules the
next one from when it finished.
"""
import re
import sys
import threading
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))   # engine modules live at the project root

import db

TICK_S = 20             # how often the loop looks for due agents
MIN_INTERVAL_S = 30     # floor on "every N seconds" — a model call is real money
BOOT_DELAY_S = 100      # let the app finish starting before the first tick
MAX_PER_TICK = 8        # agents run per tick, so one slow flow can't stall the rest

TRIGGER_KINDS = ("trigger-interval", "triggerInterval")
UNIT_SECONDS = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400}

_running = threading.Lock()


def _utc(dt):
    """Any timestamp, read in UTC. Postgres hands back TIMESTAMPTZ in whatever the
    session's TimeZone happens to be, and a cron expression that compared .hour
    against a local-time reading would fire at the wrong hour on any box not set
    to UTC — which is exactly the bug nobody notices until the clocks change."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ── cron ───────────────────────────────────────────────────────────────────────
# A 5-field matcher (minute hour day-of-month month day-of-week), written here
# rather than pulled in as a dependency: the server builds its wheels from source
# on Python 3.14 (see deployment.md §7), and this is forty lines.
_SHORTHAND = {
    "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0", "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}
_FIELD_RANGE = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]   # dow allows 7, normalised to 0
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_DOWS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]


def _field_num(token, idx):
    """One value in a field, accepting names for months and weekdays."""
    t = token.strip().lower()
    if idx == 3 and t[:3] in _MONTHS:
        return _MONTHS.index(t[:3]) + 1
    if idx == 4 and t[:3] in _DOWS:
        return _DOWS.index(t[:3])
    if not re.fullmatch(r"\d{1,2}", t):
        raise ValueError(f"{token!r} is not a number")
    return int(t)


def _field_values(field, idx):
    """The set of values a single cron field matches. Raises ValueError if it
    isn't a field at all — which is what makes an AI-written expression checkable
    before we ever store it."""
    lo, hi = _FIELD_RANGE[idx]
    out = set()
    for part in str(field).split(","):
        part = part.strip()
        if not part:
            raise ValueError("empty field")
        step = 1
        if "/" in part:
            part, _, s = part.partition("/")
            if not re.fullmatch(r"\d{1,2}", s.strip()) or int(s) < 1:
                raise ValueError(f"bad step {s!r}")
            step = int(s)
            part = part.strip() or "*"
        if part == "*":
            a, b = lo, hi
        elif "-" in part[1:]:                       # [1:] so "-5" still fails
            a_s, _, b_s = part.partition("-")
            a, b = _field_num(a_s, idx), _field_num(b_s, idx)
        else:
            a = b = _field_num(part, idx)
        if a > b or a < lo or b > hi:
            raise ValueError(f"{part!r} is outside {lo}-{hi}")
        out.update(range(a, b + 1, step))
    if idx == 4:
        out = {0 if v == 7 else v for v in out}     # both 0 and 7 mean Sunday
    return out


def normalise_cron(expr):
    """The expression as five fields — @daily and friends expanded, so everything
    downstream can index the fields without checking which form it was written in."""
    e = " ".join((expr or "").strip().lower().split())
    return _SHORTHAND.get(e, e)


def parse_cron(expr):
    """['minute set', 'hour set', …] for a valid expression. Raises ValueError with
    a sentence a human can act on."""
    fields = normalise_cron(expr).split()
    if len(fields) != 5:
        raise ValueError(f"a cron expression has 5 fields (minute hour day month weekday) — got {len(fields)}")
    return [_field_values(f, i) for i, f in enumerate(fields)]


def cron_matches(expr, when):
    """Does `when` fall on this expression's minute? `when` is read in UTC, since
    that is what the expression means. Day-of-month and day-of-week are OR'd when
    BOTH are restricted, which is what cron itself does."""
    try:
        fields = normalise_cron(expr).split()
        mins, hours, doms, months, dows = parse_cron(expr)
    except ValueError:
        return False
    when = _utc(when)
    if when.minute not in mins or when.hour not in hours or when.month not in months:
        return False
    dom_set = fields[2] != "*"
    dow_set = fields[4] != "*"
    dom_ok = when.day in doms
    dow_ok = ((when.weekday() + 1) % 7) in dows      # python Mon=0 → cron Sun=0
    if dom_set and dow_set:
        return dom_ok or dow_ok
    return dom_ok and dow_ok


def next_cron_after(expr, after):
    """The first minute strictly after `after` that the expression matches, or
    None if it matches nothing in the next year (e.g. '0 0 30 2 *')."""
    t = _utc(after).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(370 * 24 * 60):
        if cron_matches(expr, t):
            return t
        t += timedelta(minutes=1)
    return None


def describe_cron(expr):
    """A plain reading of an expression, for the canvas and the settings panel."""
    try:
        parse_cron(expr)
    except ValueError as e:
        return f"invalid — {e}"
    e = _SHORTHAND.get((expr or "").strip().lower(), (expr or "").strip())
    m, h, dom, mon, dow = e.split()
    when = "every minute" if m == "*" else (f"at minute {m}" if h == "*" else f"at {h}:{m.zfill(2)} UTC")
    if m.startswith("*/"):
        when = f"every {m[2:]} minutes"
    days = []
    if dow != "*":
        days.append("on " + dow)
    if dom != "*":
        days.append("on day " + dom + " of the month")
    if mon != "*":
        days.append("in month " + mon)
    return " ".join([when] + days)


# ── the schedule, as the flow states it ────────────────────────────────────────
def schedule_node(flow):
    """The flow's interval trigger, or None."""
    for n in (flow or {}).get("nodes") or []:
        kind = (n.get("data") or {}).get("kind") or n.get("type")
        if kind in TRIGGER_KINDS:
            return n
    return None


def read_schedule(flow):
    """What the node asks for, normalised: {mode, seconds|cron, label, request,
    error}. `error` is set (and the schedule ignored) when it can't be honoured."""
    node = schedule_node(flow)
    if not node:
        return None
    v = (node.get("data") or {}).get("values") or {}
    mode = (v.get("mode") or "every").strip().lower()
    request = (v.get("text") or "").strip()

    if mode == "cron":
        expr = (v.get("cron") or "").strip()
        if not expr:
            return {"mode": "cron", "error": "no cron expression set", "request": request}
        try:
            parse_cron(expr)
        except ValueError as e:
            return {"mode": "cron", "cron": expr, "error": str(e), "request": request}
        return {"mode": "cron", "cron": expr, "label": f"cron {expr} · {describe_cron(expr)}",
                "request": request, "error": None}

    unit = (v.get("unit") or "minutes").strip().lower()
    if unit not in UNIT_SECONDS:
        unit = "minutes"
    try:
        every = float(v.get("every") or 0)
    except (TypeError, ValueError):
        every = 0
    seconds = int(every * UNIT_SECONDS[unit])
    if seconds <= 0:
        return {"mode": "every", "error": "no interval set", "request": request}
    floored = max(seconds, MIN_INTERVAL_S)
    n = int(every) if every == int(every) else every
    label = f"every {n} {unit if n != 1 else unit[:-1]}"
    if floored != seconds:
        label += f" (floored to {MIN_INTERVAL_S}s — a model call is real money)"
    return {"mode": "every", "seconds": floored, "label": label, "request": request, "error": None}


# ── state (only what the flow cannot know) ─────────────────────────────────────
def _state(conn, agent_id):
    return conn.execute("SELECT * FROM analysis_schedules WHERE agent_id = %s", (agent_id,)).fetchone()


def _upsert(conn, agent_id, user_id, **fields):
    cols = ["agent_id", "user_id"] + list(fields)
    vals = [agent_id, user_id] + list(fields.values())
    sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in fields) or "updated_at = now()"
    conn.execute(
        f"INSERT INTO analysis_schedules ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
        f"ON CONFLICT (agent_id) DO UPDATE SET {sets}, updated_at = now()",
        vals)


def next_run_for(sched, last_run_at, now=None):
    """When this schedule should next fire, given when it last did."""
    now = _utc(now) or datetime.now(timezone.utc)
    last_run_at = _utc(last_run_at)
    if not sched or sched.get("error"):
        return None
    if sched["mode"] == "cron":
        return next_cron_after(sched["cron"], last_run_at or now - timedelta(minutes=1))
    base = last_run_at or now
    nxt = base + timedelta(seconds=sched["seconds"])
    return nxt if nxt > now else now + timedelta(seconds=sched["seconds"])


def status(agent_id, flow):
    """What the flow page shows: the schedule as written, plus how it has gone."""
    sched = read_schedule(flow)
    if not sched:
        return None
    with db.connect() as conn:
        row = _state(conn, agent_id)
    return {
        "mode": sched["mode"],
        "label": sched.get("label"),
        "error": sched.get("error"),
        "request": sched.get("request"),
        "next_run_at": (row and row["next_run_at"]) or next_run_for(sched, row and row["last_run_at"]),
        "last_run_at": row and row["last_run_at"],
        "last_status": row and row["last_status"],
        "last_error": row and row["last_error"],
        "runs": (row and row["runs"]) or 0,
    }


# ── running ────────────────────────────────────────────────────────────────────
def _due_agents(now):
    """Active agents carrying an interval trigger, with their stored state. The
    flow is JSONB and the node is nested inside an array, so the filtering is done
    here rather than in SQL — there are tens of agents, not millions.

    An INTERVAL is relative — "every 15 minutes" from WHEN? — so the first sight
    of one writes down its anchor and waits. Computing it fresh each tick would
    put the first run 15 minutes after every tick, which is 15 minutes after
    forever: the agent would never run at all. A CRON expression needs no anchor;
    it names its own minutes, so it is left to be recomputed each time."""
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT a.id, a.user_id, a.name, a.description, a.flow,
                      s.spec, s.last_run_at, s.next_run_at
                 FROM analysis_agents a
                 LEFT JOIN analysis_schedules s ON s.agent_id = a.id
                WHERE a.status = 'active'""").fetchall()
    out, arm = [], []
    for r in rows:
        sched = read_schedule(r["flow"])
        if not sched or sched.get("error"):
            continue
        stored = _utc(r["next_run_at"])
        # An edited interval re-anchors from now, so a change takes effect at once
        # instead of after one more run at the old spacing.
        if sched["mode"] == "every" and (stored is None or r["spec"] != sched["label"]):
            arm.append((r["id"], r["user_id"], sched, next_run_for(sched, None, now)))
            continue
        nxt = stored or next_run_for(sched, r["last_run_at"], now)
        if nxt and nxt <= now:
            out.append((dict(r), sched, nxt))
    if arm:
        with db.connect() as conn:
            for agent_id, user_id, sched, nxt in arm:
                _upsert(conn, agent_id, user_id, spec=sched["label"] or "", next_run_at=nxt)
            conn.commit()
    return out


def run_agent_now(row, sched, now=None):
    """Run one scheduled agent: the same engine a call would use, metered the same
    way. Returns a short dict for the log."""
    now = now or datetime.now(timezone.utc)
    import analysis_agent
    import billing
    import user_session
    import main as app_main                       # for the admin provider key

    agent_id, user_id = row["id"], row["user_id"]
    request = sched.get("request") or row.get("description") or f"Run {row['name']}."

    # Metered like every other run — so a lapsed or empty account is skipped
    # rather than run into a negative balance.
    state = billing.get_state(user_id)
    if not state["active"] or state["credits"] <= 0:
        why = "no active subscription" if not state["active"] else "out of credits"
        _finish(agent_id, user_id, sched, now, "skipped", why)
        return {"agent": row["name"], "skipped": why}

    alias = billing.DEFAULT_MODEL
    try:
        alias = app_main._user_analysis_model(user_id) or billing.DEFAULT_MODEL
    except Exception:
        pass
    import ai_keys
    provider, model, key = ai_keys.resolve(user_id, alias)
    ctx = {"_model_label": (alias if ":" not in (alias or "") else None),
           "provider": provider, "api_key": key, "model": model,
           "user_id": user_id, "agent_tools": {}, "last_user": request}

    try:
        with user_session.as_user(user_id):
            res = analysis_agent.run_flow(row["flow"], request, ctx,
                                          agent_id=agent_id, source="schedule")
    except Exception as e:
        _finish(agent_id, user_id, sched, now, "error", str(e)[:300])
        return {"agent": row["name"], "error": str(e)}

    try:
        billing.charge_cost(user_id, res.get("cost_usd") or 0, "analysis-schedule", str(agent_id),
                            provider=res.get("provider"))
    except Exception:
        pass
    err = res.get("error") or res.get("llm_error")
    _finish(agent_id, user_id, sched, now, "error" if err else "ok", err)
    return {"agent": row["name"], "ok": not err, "error": err,
            "cost_usd": res.get("cost_usd")}


def _finish(agent_id, user_id, sched, ran_at, status_txt, error=None):
    nxt = next_run_for(sched, ran_at)
    with db.connect() as conn:
        row = _state(conn, agent_id)
        # A skipped tick is not a run — it must still move the clock forward (or it
        # retries every 20 seconds forever) without pretending the agent ran.
        runs = ((row and row["runs"]) or 0) + (0 if status_txt == "skipped" else 1)
        _upsert(conn, agent_id, user_id, spec=sched.get("label") or "", last_run_at=ran_at,
                next_run_at=nxt, last_status=status_txt,
                last_error=(str(error)[:500] if error else None), runs=runs)
        conn.commit()


def tick(now=None):
    """One pass: run whatever is due. Returns what it did (used by the tests and
    the manual `python agent_schedule.py` run)."""
    now = now or datetime.now(timezone.utc)
    done = []
    for row, sched, _due in _due_agents(now)[:MAX_PER_TICK]:
        try:
            done.append(run_agent_now(row, sched, now))
        except Exception as e:
            print(f"[agent-schedule] {row.get('name')}: {e!r}", flush=True)
    return done


def _loop():
    _time.sleep(BOOT_DELAY_S)
    while True:
        if _running.acquire(blocking=False):
            try:
                tick()
            except Exception as e:
                print(f"[agent-schedule] tick failed: {e!r}", flush=True)
            finally:
                _running.release()
        _time.sleep(TICK_S)


def start():
    threading.Thread(target=_loop, daemon=True, name="agent-schedule").start()


if __name__ == "__main__":          # manual: python agent_schedule.py
    import json
    print(json.dumps(tick(), indent=2, default=str))
