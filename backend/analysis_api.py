"""
Analysis API — run one of your analysis agents from an API key and get back a
machine-readable trade signal instead of prose.

    GET /api/analysis?api_key=…&analysis_agent_id=…&message=Analyse BTCUSD for a scalper
    → {"signals": {"symbol": "BTCUSD", "direction": "BUY", "quality": 3,
                   "sl": 56356, "tp": 63773}, …}

Same path, both prefixes: `/api/analysis` (short, documented) and
`/api/v1/analysis` (consistent with the rest of the programmatic API).

Auth: `api_key` (as everywhere in /api/v1) AND the Elite plan — the programmatic
API is the Developer surface. The run is metered exactly like a chat/test run:
the flow's real token cost is debited from the caller's credits.
"""
import secrets
import time

from fastapi import APIRouter, HTTPException, Query

import billing
import db
from trading_api import api_user

router = APIRouter(prefix="/api/v1", tags=["analysis"])
alias = APIRouter(prefix="/api", tags=["analysis"])       # the short documented URL


# MT5 truncates an order comment at 31 characters, so the whole tag has to fit in
# 31: "<SYMBOL>_<id>". 12 id characters leaves room for even a long symbol name.
# The alphabet drops 0/O and 1/I — these get read off a terminal by humans.
MT5_COMMENT_MAX = 31
_ID_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_ID_LENGTH = 12


def new_analysis_id():
    """A fresh id for ONE analysis. Shared answers keep the first one's id — it
    identifies the analysis, not the caller."""
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(_ID_LENGTH))


def trade_comment(symbol, analysis_id):
    """`XAUUSD_K7M2PQXT4B1V`, clipped to what MT5 will actually store."""
    tag = f"{(symbol or '').upper()}_{analysis_id}"
    return tag[:MT5_COMMENT_MAX]


def scope_symbol(message):
    """The instrument named in the request — the fallback when the signal itself
    carries no symbol (a no-trade answer still gets a tagged comment)."""
    import analysis_cache
    return analysis_cache.scope(message)[0] or ""


def _elite(user_id):
    """The programmatic API is Elite-only on the cloud, and an AI run needs credits.

    Neither applies where nothing is sold: a self-hosted operator is already
    paying for the machine and the AI keys, and there is no Elite plan on their
    box to upgrade to."""
    import edition
    state = billing.get_state(user_id)
    if not edition.metered():
        return state
    if not state["developer"]:
        raise HTTPException(403, "The Analysis API is an Elite feature. "
                                 "Upgrade to Elite to run analysis agents programmatically.")
    if state["credits"] <= 0:
        raise HTTPException(402, "Out of credits — top up or upgrade your plan.")
    return state


@router.get("/analysis/agents")
@alias.get("/analysis/agents")
def analysis_agents(api_key: str = Query(...)):
    """Your analysis agents and their IDs — the id to pass as `analysis_agent_id`."""
    u = api_user(api_key)
    _elite(u["user_id"])
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT id, user_id, name, description, status, flow, is_public, updated_at
                 FROM analysis_agents WHERE user_id = %s OR is_public
             ORDER BY (user_id = %s) DESC, updated_at DESC""",
            (u["user_id"], u["user_id"])).fetchall()
    return {"agents": [{
        "analysis_agent_id": str(r["id"]), "name": r["name"],
        "description": r["description"], "status": r["status"],
        "nodes": len((r["flow"] or {}).get("nodes") or []),
        # published by an admin: runnable by anyone, owned and edited by them
        "public": bool(r["is_public"]),
        "mine": str(r["user_id"]) == str(u["user_id"]),
        "updated_at": r["updated_at"],
    } for r in rows]}


@router.get("/analysis")
@alias.get("/analysis")
def analysis(
    api_key: str = Query(...),
    analysis_agent_id: str = Query(None, description="The agent to run (see /api/analysis/agents)"),
    agent_id: str = Query(None, description="Alias for analysis_agent_id"),
    message: str = Query(None, description="What to analyse, in plain language"),
    request: str = Query(None, description="Alias for message"),
    model: str = Query(None, description="Branded model alias (arrissa-chat | arrissa-pro)"),
    include: str = Query("", description="trace = include every node's full result"),
):
    """Run an analysis agent and return its verdict as a signal.

    There are only three answers: no trade, a trade to take now, or an order to
    rest at a level. `signals.direction` is BUY | SELL | NONE and
    `signals.order_type` is NONE | MARKET | BUY_STOP | SELL_STOP | BUY_LIMIT |
    SELL_LIMIT — a conditional entry ("short on a confirmed break of 64,527")
    comes back as a pending order with `entry` at the trigger, never as a market
    fill. `quality` is the agent's own confidence out of 5 (5 = ripe to trade).
    `sl`/`tp` are PRICES — exact engine numbers when the flow has a
    risk-management node, otherwise the levels stated in the analysis. `analysis`
    carries the full text the agent wrote.
    """
    u = api_user(api_key)
    uid = u["user_id"]
    _elite(uid)

    aid = (analysis_agent_id or agent_id or "").strip()
    msg = (message or request or "").strip()
    if not aid:
        raise HTTPException(400, "Missing analysis_agent_id — list yours at /api/analysis/agents")
    if not msg:
        raise HTTPException(400, "Missing message — say what to analyse, e.g. "
                                 "message=Analyse BTCUSD for a scalper to enter immediately")

    with db.connect() as conn:
        try:
            # your own agent, or one an admin published for everyone
            row = conn.execute(
                "SELECT id, name, flow, is_public FROM analysis_agents "
                "WHERE id = %s AND (user_id = %s OR is_public)",
                (aid, uid)).fetchone()
        except Exception:      # a malformed uuid must read as "not found", not a 500
            row = None
    if not row:
        raise HTTPException(404, "Analysis agent not found")
    if not (row["flow"] or {}).get("nodes"):
        raise HTTPException(400, f"“{row['name']}” has no flow yet — build it on the canvas first.")

    # The user's own chat model (branded) resolved to the real provider/model + the
    # app's key — same path as chat and the canvas Test run.
    import main
    import ai_keys
    alias_name = model or main._user_analysis_model(uid) or billing.DEFAULT_MODEL
    provider, real_model, key = ai_keys.resolve(uid, alias_name)

    import analysis_agent, analysis_cache

    def run_and_price():
        """One real analysis. Returns (payload, cost_usd)."""
        analysis_id = new_analysis_id()
        ctx = {"provider": provider, "api_key": key, "model": real_model,
               "user_id": uid, "agent_tools": {}, "last_user": msg,
               # Their words for it: the branded tier when it is one, the real
               # model only when they chose it themselves.
               "_model_label": (alias_name if ":" not in (alias_name or "") else None),
               "_analysis_id": analysis_id}      # stored on the run, for tracing back
        # api_user() already bound this user's broker session + active account, so the
        # account-dependent nodes (market-data, risk-management, hmr) work as in chat.
        res = analysis_agent.run_flow(row["flow"], msg, ctx, agent_id=str(row["id"]), source="api")
        sig = analysis_agent.extract_signal(res, ctx, msg)
        # Meter the run + the extraction call as one charge. ctx usage covers every LLM
        # call made here, so re-pricing it is the whole bill — EXCEPT on a cache hit,
        # where the flow didn't call the LLM at all and run_flow already priced the hit;
        # then ctx holds only the extraction call, so add them.
        usage, usage_model = ctx.get("_usage"), ctx.get("_usage_model") or real_model
        usage_cost = billing.cost_of(usage, usage_model) if usage else 0.0
        spend = (res.get("cost_usd") or 0) + usage_cost if res.get("cached") \
            else (usage_cost or res.get("cost_usd") or 0)
        sig["analysis_id"] = analysis_id
        sig["comment"] = trade_comment(sig.get("symbol") or scope_symbol(msg), analysis_id)
        return {
            "agent": {"analysis_agent_id": str(row["id"]), "name": row["name"]},
            "analysis_id": analysis_id,
            "request": msg,
            "signals": sig,
            "analysis": res.get("response"),
            "error": res.get("error") or res.get("llm_error"),
            "trace": res.get("trace"),
        }, spend

    # Same user, agent, instrument and style inside the window ⇒ ONE analysis. The
    # first caller runs it; everyone else waits on that run or takes its stored
    # result, and pays the admin-set fraction instead of full price.
    role, entry = analysis_cache.acquire(uid, str(row["id"]), msg)
    if role == "wait":
        shared = analysis_cache.wait(entry)
        role, entry = ("cached", shared) if shared else ("run", None)

    if role == "cached":
        payload = dict(entry.payload)
        cost = (entry.cost_usd or 0) * analysis_cache.charge_fraction()
        shared_from = entry.finished_at
    else:
        try:
            payload, cost = run_and_price()
        except Exception:
            analysis_cache.fail(entry)
            raise
        analysis_cache.finish(entry, payload, cost)
        shared_from = None

    credits = 0
    try:
        credits = billing.charge_cost(uid, cost, "analysis-api-shared" if shared_from else "analysis-api",
                                      provider=provider)
    except Exception:
        pass

    out = dict(payload)
    out["cached"] = shared_from is not None
    out["credits_used"] = credits
    if shared_from is not None:
        out["shared_analysis_age_s"] = round(time.time() - shared_from, 1)
    if "trace" not in (include or "").lower():
        out.pop("trace", None)
    return out


# ── submit and poll ────────────────────────────────────────────────────────────
#
# A real analysis runs a whole agent — a dozen model calls on a reasoning model —
# and takes two to four minutes. That is longer than anything in front of it is
# willing to wait: Cloudflare gives up at its own edge limit and answers the
# caller with an HTML error page, which no client-side timeout can raise, because
# the request is killed at the edge and never reaches us. The EA, waiting 360s as
# instructed, got "HTTP 524 after 125.2s" and a page of markup where its JSON
# should have been.
#
# Cloudflare's own advice on that error page is this: "Use status polling of
# large HTTP processes to avoid this error."
#
# So: start returns an id at once, result answers immediately every time — either
# "still running" or the finished analysis. Neither request is ever slow, so
# nothing in the path has anything to time out. It also unblocks the EA, whose
# WebRequest is synchronous: it was frozen for the whole analysis, managing no
# positions, and now it is not.
import threading

_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()
_JOB_TTL_S = 1800          # a finished result is collectable for 30 minutes


def _job_sweep():
    """Drop what nobody came back for. Called on every start; there is no timer,
    and a dict that only grows is a leak with extra steps."""
    cutoff = time.time() - _JOB_TTL_S
    for k in [k for k, v in _JOBS.items() if v.get("at", 0) < cutoff]:
        _JOBS.pop(k, None)


@router.get("/analysis/start")
@alias.get("/analysis/start")
def analysis_start(
    api_key: str = Query(...),
    analysis_agent_id: str = Query(None, description="The agent to run (see /api/analysis/agents)"),
    agent_id: str = Query(None, description="Alias for analysis_agent_id"),
    message: str = Query(None, description="What to analyse, in plain language"),
    request: str = Query(None, description="Alias for message"),
    model: str = Query(None, description="Branded model alias (arrissa-chat | arrissa-pro)"),
    include: str = Query("", description="trace = include every node's full result"),
):
    """Begin an analysis and return immediately with an id to poll.

    Same parameters as /api/analysis. Returns {analysis_id, status:"running"}
    within milliseconds; collect the result from /api/analysis/result?id=…

    The work is identical — the same agent, the same sharing window, the same
    metering — it simply is not held open across the network while it runs.
    """
    u = api_user(api_key)
    uid = u["user_id"]
    _elite(uid)                       # refuse here, while someone is listening

    aid = (analysis_agent_id or agent_id or "").strip()
    msg = (message or request or "").strip()
    if not aid:
        raise HTTPException(400, "Missing analysis_agent_id — list yours at /api/analysis/agents")
    if not msg:
        raise HTTPException(400, "Missing message — say what to analyse, e.g. "
                                 "message=Analyse BTCUSD for a scalper to enter immediately")

    job = new_analysis_id()
    with _JOBS_LOCK:
        _job_sweep()
        _JOBS[job] = {"status": "running", "user_id": uid, "at": time.time(),
                      "started": time.time(), "symbol": scope_symbol(msg)}

    def work():
        try:
            out = analysis(api_key=api_key, analysis_agent_id=aid, agent_id=None,
                           message=msg, request=None, model=model, include=include)
            done = {"status": "done", "result": out}
        except HTTPException as e:
            # A refusal is an ANSWER, and the poller must be able to read it —
            # letting the job stay "running" forever would turn a 402 into a hang.
            done = {"status": "error", "http_status": e.status_code, "error": str(e.detail)}
        except Exception as e:
            done = {"status": "error", "http_status": 500, "error": f"{type(e).__name__}: {e}"}
        with _JOBS_LOCK:
            prev = _JOBS.get(job) or {}
            done.update({"user_id": prev.get("user_id"), "at": time.time(),
                         "started": prev.get("started"), "symbol": prev.get("symbol"),
                         "took_s": round(time.time() - (prev.get("started") or time.time()), 1)})
            _JOBS[job] = done

    threading.Thread(target=work, daemon=True, name=f"analysis-{job}").start()
    return {"analysis_id": job, "status": "running", "symbol": scope_symbol(msg),
            "poll": f"/api/analysis/result?api_key=…&id={job}",
            "note": "Poll every few seconds. Typical runs finish in 2-4 minutes."}


@router.get("/analysis/result")
@alias.get("/analysis/result")
def analysis_result(api_key: str = Query(...), id: str = Query(..., description="analysis_id from /analysis/start")):
    """Collect a started analysis. Answers at once, always.

    status is "running", "done" (with `result`) or "error" (with `error`). A
    finished result stays collectable for 30 minutes, so a poller that misses a
    beat does not lose the run it paid for.
    """
    u = api_user(api_key)
    with _JOBS_LOCK:
        job = _JOBS.get((id or "").strip())
        job = dict(job) if job else None

    if not job:
        raise HTTPException(404, "No such analysis id — it may have expired (results are kept "
                                 "30 minutes) or never started.")
    # Someone else's run is not yours to read, even with a valid key of your own.
    if job.get("user_id") != u["user_id"]:
        raise HTTPException(404, "No such analysis id")

    if job["status"] == "running":
        return {"status": "running", "analysis_id": id, "symbol": job.get("symbol"),
                "waiting_s": round(time.time() - (job.get("started") or time.time()), 1)}
    if job["status"] == "error":
        return {"status": "error", "analysis_id": id, "symbol": job.get("symbol"),
                "error": job.get("error"), "http_status": job.get("http_status")}

    out = dict(job.get("result") or {})
    out["status"] = "done"
    out["analysis_id"] = out.get("analysis_id") or id
    out["took_s"] = job.get("took_s")
    return out


# ── Daily Market Scan (the app's own agent, run once a day at 00:00 UTC) ────────
def _scan_out(row, include=""):
    """A stored scan as the API returns it — the bulky per-symbol measurements and
    macro context only when asked for."""
    inc = (include or "").lower()
    out = {
        "scan_date": str(row["scan_date"]),
        "status": row["status"],
        "error": row["error"],
        "summary": row["summary"],
        "picks": row["picks"] or [],
        "symbols_scanned": row["universe_count"],
        "model": row["model"],
        "generated_at": row["created_at"],
    }
    if "features" in inc:
        out["features"] = row.get("features")
    if "macro" in inc:
        out["macro"] = row.get("macro")
    return out


@router.get("/daily-scan")
@alias.get("/daily-scan")
def daily_scan(
    api_key: str = Query(...),
    date: str = Query(None, description="YYYY-MM-DD — defaults to the most recent scan"),
    days: int = Query(0, description="Return the last N scans instead of one"),
    include: str = Query("", description="features and/or macro — the full audit trail"),
):
    """The day's tradeable symbols, when to trade them and at what price.

    A system job scans the whole universe (major + minor FX, US30 / NASDAQ /
    US500 / DE30, BTCUSD / ETHUSD) every day at 00:00 UTC and stores the result,
    so this endpoint is an instant read of what was already decided — each pick
    carries direction, order_type, entry/sl/tp and `windows_utc`, the UTC windows
    it can be traded in.
    """
    u = api_user(api_key)
    _elite(u["user_id"])
    import daily_scan as scan

    if days:
        return {"scans": [_scan_out(r) for r in scan.history(days)]}
    row = scan.get(date or None)
    if not row:
        raise HTTPException(404, "No scan stored yet — the first one runs at "
                                 f"{scan.SCAN_HOUR_UTC:02d}:00 UTC.")
    return _scan_out(row, include)


@router.get("/daily-scan/status")
@alias.get("/daily-scan/status")
def daily_scan_status(api_key: str = Query(...)):
    """When the scan runs, when it runs next, and how the last one went."""
    u = api_user(api_key)
    _elite(u["user_id"])
    import daily_scan as scan
    return scan.status()


# ── Daily Watch List (the system analysis agent) ───────────────────────────────
def _watch_out(row, include=""):
    """A stored run in the shape callers asked for: symbol → times + prices. The
    agent's full write-up per instrument is several KB each, so it only rides
    along on request (include=assessment)."""
    keep_assessment = "assessment" in (include or "").lower()
    symbols = {
        sym: (v if keep_assessment else {k: x for k, x in v.items() if k != "assessment"})
        for sym, v in (row["symbols"] or {}).items()
    }
    # Why the list is the length it is. A weekend list of one crypto symbol looks
    # broken next to a weekday list of eight, and without this a caller — or an EA
    # polling it — has no way to tell "nothing qualified" from "most of the market
    # is shut".
    funnel = row.get("funnel") or {}
    hours = funnel.get("market_hours") or {}
    closed = funnel.get("closed") or []
    return {
        "today_watch_list": {
            "date": str(row["run_date"]),
            "run_utc": row["run_slot"],
            "status": row["status"],
            "error": row["error"],
            "instruments_considered": row["considered"],
            "watching": len(symbols),
            "market_hours": {
                "fx_open": hours.get("fx_open"),
                "note": hours.get("note"),
                "instruments_closed": len(closed),
            } if hours else None,
            "symbols": symbols,
            "built_at": row["created_at"],
        }
    }


@router.get("/watch_list_daily")
@alias.get("/watch_list_daily")
def watch_list_daily(
    api_key: str = Query(...),
    date: str = Query(None, description="YYYY-MM-DD — defaults to the most recent run"),
    slot: str = Query(None, description="Which run of that day, e.g. 00:00 or 06:00"),
    days: int = Query(0, description="Return the last N days of runs instead of one"),
    include: str = Query("", description="assessment = the agent's full write-up per instrument"),
):
    """What to WATCH today — instruments, the UTC times, and the price levels.

    Built twice a day by the app's own system analysis agent, which studies each
    instrument in the universe on its own against every data source in the app
    (candles, high-impact news, today's calendar, retail sentiment, bond yields,
    Fed odds, political posts, HMR). This is a watch list, not a signal service:
    it says what to keep an eye on and when, never what to buy or sell.
    """
    u = api_user(api_key)
    _elite(u["user_id"])
    import watchlist

    if days:
        return {"runs": [_watch_out(r)["today_watch_list"] for r in watchlist.history(days)]}
    row = watchlist.get(date or None, slot or None)
    if not row:
        raise HTTPException(404, "No watch list built yet — the next one runs at "
                                 + ", ".join(watchlist.status()["schedule_utc"]) + " UTC.")
    return _watch_out(row, include)


@router.get("/watch_list_daily/status")
@alias.get("/watch_list_daily/status")
def watch_list_status(api_key: str = Query(...)):
    """The schedule, the next run, the last run, and where to edit the agent."""
    u = api_user(api_key)
    _elite(u["user_id"])
    import watchlist
    return watchlist.status()
