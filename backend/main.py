"""
Arrissa Exness API — backend
FastAPI + Postgres. Auth (login, change password) and API-key management.

Run:  uvicorn main:app --reload --port 8000
"""
from fastapi import FastAPI, Depends, HTTPException, Header, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, constr

import asyncio
import json
import threading
import time as _time

import trading_api as _trading_api

from sse_starlette.sse import EventSourceResponse

import auth
import db
import registry
import edition
import ai_keys
import agent
import billing
import paystack
from trading_api import router as trading_router, start_scheduler

app = FastAPI(title="ArrissaTrade")
app.include_router(trading_router)

from admin_api import router as admin_router
app.include_router(admin_router)

# Analysis API (api_key + Elite): run an analysis agent, get a trade signal back.
# Registered on both /api/v1/analysis and the short /api/analysis.
from analysis_api import router as analysis_router, alias as analysis_alias
app.include_router(analysis_router)
app.include_router(analysis_alias)


# When the Exness JWT/refresh token has expired, return a clean JSON 401 the UI can
# handle ("reconnect your account") instead of a raw 500 HTML page that breaks
# `response.json()` on the frontend.
from fastapi.responses import JSONResponse
import brokers as _brokers


@app.exception_handler(_brokers.SessionExpired)
def _session_expired_handler(request, exc):
    return JSONResponse(
        status_code=401,
        content={"detail": "Your broker session has expired. Please reconnect your account.",
                 "code": "exness_session_expired"},
    )


# The same courtesy for a failure the BROKER raises. An upstream HTTP error is
# not an application crash, and answering one with FastAPI's default 500 sends
# the browser a plain-text "Internal Server Error" — which is where
# `Unexpected token 'I', "Internal S"... is not valid JSON` comes from. The
# status the broker actually gave is worth more than a stack trace.
try:
    from curl_cffi.requests.exceptions import HTTPError as _UpstreamHTTPError
except Exception:                                   # curl_cffi absent — nothing to catch
    _UpstreamHTTPError = None

@app.exception_handler(_brokers.AccountRefused)
def _account_refused_handler(request, exc):
    # Deliberately NOT 401: the session is fine, so "reconnect your account"
    # would send the user round a loop that cannot help them.
    return JSONResponse(
        status_code=502,
        content={"detail": str(exc), "code": "broker_account_refused"})


@app.exception_handler(_brokers.NoBroker)
def _no_broker_handler(request, exc):
    return JSONResponse(status_code=503,
                        content={"detail": str(exc), "code": "no_broker_installed"})


@app.exception_handler(_brokers.NoAccount)
def _no_account_handler(request, exc):
    # 409, not 500: nothing is broken, the app is simply being asked to trade
    # before an account exists. Every fresh install passes through this state,
    # and a stack trace is a poor way to say "connect an account first".
    return JSONResponse(status_code=409,
                        content={"detail": str(exc), "code": "no_active_account"})

if _UpstreamHTTPError is not None:
    @app.exception_handler(_UpstreamHTTPError)
    def _upstream_error_handler(request, exc):
        status = getattr(getattr(exc, "response", None), "status_code", 0) or 0
        if status in (401, 403):
            return JSONResponse(
                status_code=401,
                content={"detail": "Your broker rejected this request's session. "
                                   "Please reconnect your account.",
                         "code": "exness_session_expired"})
        return JSONResponse(
            status_code=502,
            content={"detail": f"The broker's API answered HTTP {status or 'error'}. "
                               "Nothing was changed on your account — try again.",
                     "code": "broker_upstream_error", "upstream_status": status})


@app.on_event("startup")
def _startup():
    try:
        db.init_schema()   # idempotent (IF NOT EXISTS) — ensures billing tables exist
    except Exception:
        pass
    # Modules were loaded at import time (see below); their workers start here,
    # once the app is actually coming up. After this, a module enabled later
    # starts its own worker the moment it registers it.
    registry.start_workers()
    # Take the updates this instance is entitled to without being asked. Nothing
    # ran the update path before, so a self-hosted box sat on a fix until
    # somebody opened the store and noticed the badge. Self-hosted only; the
    # entitlement rule is store.can_update's, not a second one.
    try:
        import auto_update
        auto_update.start()
    except Exception as _e:
        print(f"[auto-update] not started: {_e!r}", flush=True)
    # Agents that wake on an EVENT rather than on a clock: a new post, a story
    # about an instrument, an economic release about to land or just printed.
    try:
        import data_triggers
        data_triggers.start()
    except Exception as _e:
        print(f"[data-trigger] not started: {_e!r}", flush=True)
    # The same happenings, delivered to a screen instead of to an agent: a toast
    # with a sound when something market-moving lands.
    try:
        import market_alerts
        market_alerts.start()
    except Exception as _e:
        print(f"[market-alerts] not started: {_e!r}", flush=True)
    try:
        import risk_gate
        risk_gate.seed()        # the Risk Settings system agent
    except Exception as _e:
        print(f"[risk-gate] not seeded: {_e!r}", flush=True)
    start_scheduler()  # background thread that executes scheduled orders/actions
    try:
        import daily_scan
        daily_scan.start()      # no-AI market scan, once a day at 00:00 UTC
    except Exception:
        pass
    try:
        import watchlist
        watchlist.seed()        # the system analysis agent ships with the app
        watchlist.start()       # …and builds the watch list twice a day (00:00 + 06:00 UTC)
    except Exception:
        pass
    try:
        import agent_schedule
        agent_schedule.start()  # agents carrying a "Trigger on Intervals" node run themselves
    except Exception:
        pass

# Modules load HERE, at import time, not in the startup event: FastAPI collects
# routes when this module is imported, so a router registered any later is never
# mounted — the endpoints simply 404 with no error anywhere. Their workers start
# in _startup; only the loading has to happen this early.
# Core's tables FIRST. A module's schema.sql references them — every one of
# them has a user_id — and modules load at import time while core's schema used
# to run at startup. On an existing database the order never showed; on a FRESH
# one, which is every self-hosted install, the first module to load died on
# `relation "users" does not exist`.
try:
    db.init_schema()
except Exception as _e:
    print(f"[db] schema init failed: {_e!r}", flush=True)

# The free modules, before anything is loaded. It has to be here rather than in
# _startup: modules are discovered from disk at import time, so a module seeded
# any later would sit on disk doing nothing until the next restart — which, on a
# first boot, is exactly when the owner is looking at the page wondering where it
# is. Community only; the cloud install ships with everything already.
try:
    import edition
    if edition.is_community():
        import catalog as _catalog
        _catalog.install_free()
except Exception as _e:                     # a seeding failure must never stop the app
    print(f"[seed] free modules could not be installed: {_e!r}", flush=True)

try:
    import modules as module_system
    module_system.load_all()
except Exception as _e:                     # one bad module must never stop the app
    print(f"[modules] the module system itself failed: {_e!r}", flush=True)

# Hand the live app over. This mounts everything registered so far AND lets a
# module mounted later — enabled from the Modules page — serve immediately,
# because Starlette matches against a plain list of routes it is happy to have
# changed underneath it.
registry.bind_app(app)

# The module manager itself, mounted after the modules so its own routes exist
# whatever happened to them.
try:
    from store_api import router as store_router     # entrystation.com's own shop
    app.include_router(store_router)
except Exception as _e:
    print(f"[store] unavailable: {_e!r}", flush=True)

try:
    from modules_api import router as modules_router
    app.include_router(modules_router)
except Exception as _e:
    print(f"[modules] manager API unavailable: {_e!r}", flush=True)

@app.middleware("http")
async def _count_inflight(request, call_next):
    """What a restart would interrupt.

    The auto-updater has to restart to apply a module — modules load at import
    time — and doing that mid-analysis loses the run. This is the only thing
    that knows whether anything is in flight."""
    try:
        import auto_update
    except Exception:
        return await call_next(request)
    auto_update.note_request(1)
    try:
        return await call_next(request)
    finally:
        auto_update.note_request(-1)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── request/response models ────────────────────────────────────────────────────
class LoginBody(BaseModel):
    email: EmailStr
    password: str


class PasswordBody(BaseModel):
    current_password: str
    new_password: constr(min_length=8)


class KeyBody(BaseModel):
    name: constr(min_length=1, max_length=60)


# ── auth dependency ────────────────────────────────────────────────────────────
# Defined in `deps` so a module can depend on it without importing this file,
# which is the file that imports the modules.
from deps import current_user   # noqa: E402


# ── auth routes ────────────────────────────────────────────────────────────────
@app.post("/api/auth/login")
def login(body: LoginBody):
    with db.connect() as conn:
        user = conn.execute(
            "SELECT id, email, password_hash FROM users WHERE email = %s",
            (body.email.lower(),),
        ).fetchone()
    if not user or not auth.verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Invalid email or password")
    token = auth.make_token(user["id"], user["email"])
    return {"token": token, "user": {"email": user["email"]}}


@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    b = billing.get_state(user["id"])
    caps = edition.capabilities(user["email"], b)
    return {"email": user["email"], "first_name": user.get("first_name"),
            "last_name": user.get("last_name"), "created_at": user["created_at"],
            "admin": caps["admin"],
            "edition": caps["edition"],
            # What this user may actually reach. The frontend used to work this
            # out from the billing state, which is a CLOUD question and left a
            # Community owner locked out of their own software.
            "capabilities": caps,
            "billing": b}


# ── signup (email → verify → profile) ──────────────────────────────────────────
import random
from datetime import datetime, timezone


class SignupStart(BaseModel):
    email: EmailStr
    invite: str = ""


class SignupVerify(BaseModel):
    email: EmailStr
    code: str
    invite: str = ""


class SignupComplete(BaseModel):
    email: EmailStr
    first_name: constr(min_length=1, max_length=60)
    last_name: constr(min_length=1, max_length=60)
    phone: str = ""
    country: str = ""
    password: constr(min_length=8)
    # Broker fields are OPTIONAL to core and consumed by whichever broker module
    # is installed. With none installed, signup is just a signup.
    exness_email: str = ""         # the Exness account email (may differ from the signup email)
    exness_password: str = ""      # used once to connect the Exness account, never stored
    invite: str = ""
    # Refused server-side, not merely unchecked in the browser. A gate that only
    # exists in the page is a gate that is not there at all.
    accept_terms: bool = False
    terms_version: str = ""


# Invite-only for now — the signup flow is kept but rejected server-side. Set to True
# to re-open public registration. While closed, a valid private invite code
# (/signup?invite=CODE) bypasses the block — that's the ONLY way in.
REGISTRATIONS_ENABLED = False
_INVITE_MSG = ("Registration is invite-only right now. Contact arrissa.ai@gmail.com or "
               "WhatsApp +27 73 271 6360 to request access.")


def _invite_code():
    with db.connect() as conn:
        row = conn.execute("SELECT signup_invite_code FROM admin_settings WHERE id = 1").fetchone()
    return (row["signup_invite_code"] if row else None) or None


def _registrations_open():
    """Admin-toggleable (admin_settings.registrations_open); falls back to the code
    constant when unset."""
    try:
        with db.connect() as conn:
            row = conn.execute("SELECT registrations_open FROM admin_settings WHERE id = 1").fetchone()
        if row and row["registrations_open"] is not None:
            return bool(row["registrations_open"])
    except Exception:
        pass
    return REGISTRATIONS_ENABLED


def _signup_allowed(invite=""):
    """Registration is permitted when it's open, or the caller carries the private
    invite code. Comparison is constant-time-ish and requires a code to be set.

    A Community instance is SINGLE-USER: the operator and the user are the same
    person. The first account may be created — someone has to be able to get in
    — and after that registration is closed, whatever any setting says. A
    self-hoster who exposes their box to the internet should not discover that
    it was accepting sign-ups."""
    if edition.multi_tenant() is False:
        return not _has_any_user()
    if _registrations_open():
        return True
    code = _invite_code()
    return bool(code) and (invite or "").strip() == code


def _has_any_user() -> bool:
    try:
        with db.connect() as conn:
            return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None
    except Exception:
        return True          # cannot tell → assume taken, never open by accident


@app.get("/api/signup/invite")
def check_invite(code: str = ""):
    """Public: is registration reachable (open, or this invite code is valid)?
    The signup page calls this to decide whether to show the form."""
    return {"valid": _signup_allowed(code), "open": _registrations_open()}


# Read-only, for the Test button. Anything that changes an account is refused:
# somebody checking whether their parameters are right has not asked to close a
# position, and a preview that could is not a preview.
TRADE_READONLY = {
    "list_accounts", "search_symbols", "list_symbols", "symbol_info", "price",
    "account_stats", "positions", "orders", "history", "closed_trades",
    "calc_sltp", "risk_plan",
}


class ParamsPreview(BaseModel):
    kind: str
    api_params: str = ""
    variables: dict = {}


@app.post("/api/analysis/preview-params")
def preview_params(body: ParamsPreview, user=Depends(current_user)):
    """Run this node's call with these parameters, and return what came back.

    The same handler the flow uses, so what is shown is what the node will
    actually receive rather than an approximation of it. api_params is
    explicit, so no model is consulted and nothing is charged.

    Anything that would change an account is refused."""
    import analysis_agent as eng
    import registry
    import user_session
    import json as _json
    import time as _time

    kind = (body.kind or "").strip()
    handler = eng._DATA.get(kind) or registry.node_handler(kind)
    if not handler:
        raise HTTPException(404, f"no node called {kind!r}")

    params = (body.api_params or "").strip()
    if not params:
        raise HTTPException(400, "Nothing to test — write the parameters first.")

    if kind in ("trade-actions", "tradeActions"):
        stated = eng._explicit_params({"api_params": params}, body.variables or {}) or {}
        tool = str(stated.get("tool") or "")
        if not tool:
            raise HTTPException(400, "Testing this node needs a fixed tool= call. Without one "
                                     "it would decide for itself what to do.")
        if tool not in TRADE_READONLY:
            raise HTTPException(400, f"{tool} changes the account, so it is not run from here. "
                                     f"Testing is limited to calls that only read.")

    ctx = {"user_id": user["id"], "agent_tools": {}}
    context = {"request": "(parameter test)", "vars": body.variables or {}}

    t0 = _time.time()
    try:
        with user_session.as_user(user["id"]):
            out = handler("", context, ctx, {"api_params": params})
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}")

    # Trimmed: a news call can return two hundred rows, and the point is to see
    # the SHAPE and whether the filters bit.
    text = _json.dumps(out, default=str, indent=2)
    return {"ok": True, "ms": int((_time.time() - t0) * 1000),
            "truncated": len(text) > 12000, "result": text[:12000],
            "count": (out.get("count") if isinstance(out, dict) else None)}


class ChartSnapshot(BaseModel):
    png: str                       # data URL or bare base64
    symbol: str = ""
    timeframe: str = ""
    drawings: int = 0


def _store_snapshot(user_id, body: "ChartSnapshot") -> bytes:
    """Decode the PNG and keep it as the one picture this user is looking at."""
    import base64
    raw = body.png or ""
    if "," in raw[:64]:
        raw = raw.split(",", 1)[1]         # strip a data: URL prefix
    try:
        blob = base64.b64decode(raw)
    except Exception:
        raise HTTPException(400, "that is not an image")
    if len(blob) > 6_000_000:
        raise HTTPException(413, "chart image too large")

    with db.connect() as conn:
        conn.execute(
            "INSERT INTO chart_snapshots (user_id, symbol, timeframe, drawings, png, at) "
            "VALUES (%s,%s,%s,%s,%s, now()) ON CONFLICT (user_id) DO UPDATE SET "
            "symbol=EXCLUDED.symbol, timeframe=EXCLUDED.timeframe, "
            "drawings=EXCLUDED.drawings, png=EXCLUDED.png, at=now()",
            (user_id, body.symbol[:32], body.timeframe[:16], int(body.drawings or 0), blob))
        conn.commit()
    return blob


@app.post("/api/chart/snapshot")
def put_chart_snapshot(body: ChartSnapshot, user=Depends(current_user)):
    """The browser handing over what is on screen, drawings and all.

    Kept as one row per user and replaced each time, because the question is
    always "what am I looking at now" — a history of screenshots would be a
    growing table nobody reads."""
    return {"ok": True, "bytes": len(_store_snapshot(user["id"], body))}


class ChartAnalyse(ChartSnapshot):
    question: str = ""


@app.post("/api/chart/analyse")
def analyse_chart_now(body: ChartAnalyse, user=Depends(current_user)):
    """Look at THIS chart. No agent, no tool choice, no ambiguity.

    The button exists because "analyse the chart" is a sentence the assistant
    can reasonably read as a request for market analysis, and it did — it ran
    the analysis agent on the instrument instead of looking at the picture.
    A button cannot be misread: it carries the chart it is attached to, and
    goes straight to a model that can see. The chat tool is left exactly as it
    was, for when somebody does ask in words."""
    import chart_vision
    png = _store_snapshot(user["id"], body)
    out = chart_vision.analyse(user["id"], png, body.question or "")
    if out.get("error"):
        raise HTTPException(400, out["error"])
    return {**out, "symbol": body.symbol, "timeframe": body.timeframe,
            "drawings": int(body.drawings or 0)}


@app.get("/api/market-alerts")
def get_market_alerts(since: str = "", limit: int = 30, user=Depends(current_user)):
    """What has happened since this client last looked.

    The watermark is the CLIENT's — it sends back the `now` from its previous
    call — so a browser opened an hour later still receives what it missed
    rather than only what arrives while it happens to be watching."""
    import market_alerts
    return market_alerts.since(since or None, limit)


@app.get("/api/truth/latest")
def truth_latest(hours: int = 24, impact: str = "high", limit: int = 5,
                 user=Depends(current_user)):
    """The most recent Truth Social posts, market-moving ones by default.

    Through the registry, because Truth Social is a module and core may not
    import one. 404 rather than an empty list when it is absent, so the caller
    can tell "not installed" from "nothing to say"."""
    import registry
    p = registry.get("truth")
    if not p:
        raise HTTPException(404, "The Truth Social module is not installed here.")
    imp = None if str(impact).lower() in ("any", "all", "") else impact
    res = p.query(user="trump", hours=hours, limit=limit, impact=imp)
    if isinstance(res, dict) and res.get("error"):
        raise HTTPException(400, res["error"])
    return res


@app.get("/api/calendar/day")
def calendar_day(since: str = "", until: str = "", impact: str = "high",
                 user=Depends(current_user)):
    """A day's economic releases, for the navbar calendar.

    The window arrives as an explicit since/until from the browser rather than a
    date: a "day" starts and ends in the reader's own timezone, and the server
    guessing which one that is would put the wrong events either side of
    midnight. Reached through the registry, because the calendar is a module and
    core may not import one."""
    import registry
    p = registry.get("calendar")
    if not p:
        raise HTTPException(404, "The Economic Calendar module is not installed here.")
    if not since or not until:
        raise HTTPException(400, "since and until are required")
    # High impact by default. The full list is mostly releases nobody trades, and
    # a calendar you have to scan past is one you stop opening. `impact=any`
    # turns the filter off for anyone who does want everything.
    imp = None if str(impact).lower() in ("any", "all", "") else impact
    res = p.query(since=since, until=until, impact=imp, limit=300, order="asc")
    if isinstance(res, dict) and res.get("error"):
        raise HTTPException(400, res["error"])
    return res


@app.get("/api/market-alerts/feed")
def market_alerts_feed(limit: int = 40, user=Depends(current_user)):
    """What the bell shows: outstanding alerts and the unread count. Served from
    the database, so history survives a closed browser — the worker keeps
    running whether or not anybody is watching."""
    import market_alerts
    return market_alerts.feed(user["id"], limit)


@app.post("/api/market-alerts/seen")
def market_alerts_seen(user=Depends(current_user)):
    import market_alerts
    return market_alerts.mark_seen(user["id"])


@app.post("/api/market-alerts/dismiss")
def market_alerts_dismiss(key: str = "", user=Depends(current_user)):
    import market_alerts
    return market_alerts.dismiss(user["id"], key or None)


@app.get("/api/market-alerts/status")
def market_alerts_status(user=Depends(current_user)):
    import market_alerts
    return market_alerts.status()


@app.get("/api/trigger-sources")
def trigger_sources(user=Depends(current_user)):
    """Which data conditions this instance can offer.

    A condition whose module is not installed is not offered, rather than
    offered and silently never firing."""
    import data_triggers
    return {"kinds": data_triggers.available()}


@app.get("/api/notifications")
def notifications(user=Depends(current_user)):
    """Everything outstanding, so nothing has to be discovered.

    A half-configured install is silent: Sentiment with no Myfxbook connection
    returns nothing and says nothing, and the Exness module with no account
    behind it looks exactly like one that works until a trade is attempted."""
    import notifications as _notes
    # Who may act on an update. On Community the operator and the user are the
    # same person; on the hosted service it is an admin. `current_user` carries
    # no admin flag, so it is asked rather than assumed.
    operator = False
    try:
        import edition
        from admin_api import _is_admin
        operator = edition.is_community() or _is_admin(user.get("email"))
    except Exception:
        pass
    try:
        return _notes.for_user(user["id"], is_operator=operator)
    except Exception as e:
        # A bell that breaks the page it hangs on is worse than an empty bell.
        print(f"[notifications] {e!r}", flush=True)
        return {"items": [], "count": 0, "blocked": 0}


@app.get("/api/licence")
def licence_text():
    """The software licence, from the file that actually governs the software.

    Read from LICENSE.md rather than copied into the page, because a licence
    that exists in two places is a licence that will eventually say two things,
    and the one somebody agreed to would be whichever they happened to read."""
    from pathlib import Path
    p = Path(__file__).parent.parent / "LICENSE.md"
    try:
        return {"text": p.read_text(encoding="utf-8")}
    except Exception as e:
        return {"text": "", "error": str(e)}


@app.get("/api/app-config")
def app_config():
    """Public config for the frontend: branding, edition, and what we sell.

    The homepage is served to people with no account, so everything it quotes has
    to be readable without one. `/api/billing/catalog` needs a user, which is
    right for the billing page and useless for a shop window — so the plans ride
    here instead, priced from the same `billing.PLANS` the checkout charges from.
    A page cannot then advertise a price nothing takes.

    Community sends no plans at all, because it sells none: nobody is billed on
    their own server, and a tier list there would be an advert for a product the
    operator has already declined."""
    import mailer
    out = {"app_name": mailer.app_name(), "edition": edition.NAME,
           # Nobody has an account yet. On a Community box that means this is a
           # fresh install, and the first thing anyone needs is to CREATE the
           # account — not sign in to one that does not exist. Sending them to a
           # login form first asks for a password nobody has set, and the way
           # out of it is a link at the bottom of the page.
           #
           # Cloud never reports this: entrystation.com has accounts, and one
           # deleted database should not turn the front door into a sign-up.
           "setup": edition.is_community() and not _has_any_user()}

    # What can be connected, for the homepage's logo strip. Taken from the same
    # TYPES the Connections page renders, so the two can never disagree about
    # what the app supports — the strip was hand-written and had already drifted,
    # listing Groq in words because whoever wrote it had no logo to hand while
    # /logos/groq.webp was sitting in the build the whole time.
    conns = []
    # Brokers first: they are the connection the whole product hangs off, and a
    # strip that opened with an AI provider would put the model layer ahead of
    # the account being traded. Each names and pictures ITSELF — core does not
    # know what a broker looks like, the module does — so an installed broker
    # appears here without core learning anything about it.
    try:
        import brokers
        for bid, p in brokers.providers().items():
            logo = getattr(p, "logo", None)
            conns.append({
                "kind": bid,
                "name": getattr(p, "name", bid.title()),
                "group": "broker",
                "logo": f"/api/modules/{bid}/asset/{logo}" if logo else None,
                "mark": None, "tone": "amber",
            })
    except Exception as e:
        print(f"[app-config] brokers unavailable: {e}", flush=True)

    try:
        import connections
        conns += [
            {"kind": t["kind"], "name": t["name"], "group": t.get("group"),
             "logo": t.get("logo"), "mark": t.get("mark"), "tone": t.get("tone")}
            for t in connections.types()
        ]
    except Exception as e:
        print(f"[app-config] connection types unavailable: {e}", flush=True)
    out["connections"] = conns
    if edition.metered():
        import billing
        # `limits` rides along because it IS what the tiers differ by. Without it
        # the page could only quote a price and a credit count, so every plan
        # looked like the same product at four prices — the accounts, agents,
        # monitors and history a tier actually buys are the answer to "what do I
        # get", and they were sitting right here unsent.
        out["plans"] = [
            {k: p[k] for k in ("key", "name", "price_usd", "price_annual_usd",
                               "credits", "developer", "blurb", "limits")}
            for p in sorted(billing.PLANS.values(), key=lambda x: x["order"])
        ]
    return out


def _signup_refusal():
    if not edition.multi_tenant():
        return ("This is a single-user installation and its account already "
                "exists. Log in with it, or run create_user.py on the server.")
    return _INVITE_MSG


@app.post("/api/signup/start")
def signup_start(body: SignupStart):
    if not _signup_allowed(body.invite):
        raise HTTPException(403, _signup_refusal())
    email = body.email.lower()
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM users WHERE email = %s", (email,)).fetchone():
            raise HTTPException(400, "An account with this email already exists. Please log in.")
    with db.connect() as conn:
        code = f"{random.randint(0, 999999):06d}"
        conn.execute(
            """INSERT INTO signups (email, code, verified, attempts, created_at, expires_at)
               VALUES (%s, %s, false, 0, now(), now() + interval '15 minutes')
               ON CONFLICT (email) DO UPDATE SET
                 code = EXCLUDED.code, verified = false, attempts = 0,
                 created_at = now(), expires_at = now() + interval '15 minutes'""",
            (email, code),
        )
        conn.commit()
    try:
        import mailer
        mailer.send_verification(email, code)
    except Exception as e:
        # A Community box has no SMTP and is not expected to — so the ONE signup
        # it will ever accept would be the one that could never complete. When
        # nobody can be emailed, the code is returned to the caller instead: the
        # person asking is the only person there, and the alternative is an
        # instance nobody can get into.
        if not edition.multi_tenant():
            return {"ok": True, "emailed": False, "code": code,
                    "note": "No mail server is configured, so the code is shown "
                            "here. This only happens on a single-user install."}
        raise HTTPException(500, f"Could not send the verification email: {e}")
    return {"ok": True, "emailed": True}


@app.post("/api/signup/verify")
def signup_verify(body: SignupVerify):
    if not _signup_allowed(body.invite):
        raise HTTPException(403, _signup_refusal())
    email = body.email.lower()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT code, expires_at, attempts FROM signups WHERE email = %s", (email,)
        ).fetchone()
        if not row:
            raise HTTPException(400, "Start the signup first.")
        if row["attempts"] >= 8:
            raise HTTPException(429, "Too many attempts — request a new code.")
        if row["expires_at"] < datetime.now(timezone.utc):
            raise HTTPException(400, "That code has expired — request a new one.")
        if body.code.strip() != row["code"]:
            conn.execute("UPDATE signups SET attempts = attempts + 1 WHERE email = %s", (email,))
            conn.commit()
            raise HTTPException(400, "Invalid code.")
        conn.execute("UPDATE signups SET verified = true WHERE email = %s", (email,))
        conn.commit()
    return {"ok": True}


@app.post("/api/signup/complete")
def signup_complete(body: SignupComplete):
    if not _signup_allowed(body.invite):
        raise HTTPException(403, _signup_refusal())
    # Checked here and not only in the browser. A gate that exists only in the
    # page is not a gate: anything can post this endpoint directly, and the
    # record of consent has to be the thing that created the account.
    if not body.accept_terms:
        raise HTTPException(400, "Please accept the Terms of Use and Privacy Policy to continue.")
    email = body.email.lower()
    with db.connect() as conn:
        s = conn.execute("SELECT verified FROM signups WHERE email = %s", (email,)).fetchone()
        if not s or not s["verified"]:
            raise HTTPException(400, "Please verify your email first.")
        if conn.execute("SELECT 1 FROM users WHERE email = %s", (email,)).fetchone():
            raise HTTPException(400, "An account with this email already exists. Please log in.")

    # Connect the broker FIRST — its check is the expensive, failable one, and
    # doing it before the INSERT means a failed connect never leaves half an
    # account behind. Core does not know what the check IS; the broker module
    # does. With no broker installed there is simply nothing to connect.
    import brokers
    connects = []
    for bid, p in brokers.providers().items():
        fn = getattr(p, "signup_precheck", None)
        if fn:
            connects.append((bid, p, fn(body.model_dump())))

    with db.connect() as conn:
        row = conn.execute(
            """INSERT INTO users (email, password_hash, first_name, last_name, phone, country,
                                  terms_accepted_at, terms_version)
               VALUES (%s, %s, %s, %s, %s, %s, now(), %s) RETURNING id, email""",
            (email, auth.hash_password(body.password), body.first_name.strip(),
             body.last_name.strip(), body.phone.strip(), body.country.strip().upper(),
             (body.terms_version or "").strip() or None),
        ).fetchone()
        for _bid, p, blob in connects:
            p.signup_attach(conn, row["id"], blob)
        # auto-issue a default API key so the user can use the API immediately
        raw = auth.generate_api_key()
        prefix, last_four = auth.key_display(raw)
        conn.execute(
            "INSERT INTO api_keys (user_id, name, key_prefix, last_four, key_hash, key_plain) "
            "VALUES (%s, 'Default', %s, %s, %s, %s)",
            (row["id"], prefix, last_four, auth.hash_key(raw), raw),
        )
        conn.execute("DELETE FROM signups WHERE email = %s", (email,))
        conn.commit()
    for _bid, p, _blob in connects:
        try:
            getattr(p, "after_connect", lambda _u: None)(row["id"])
        except Exception:
            pass
    _seed_default_agents(row["id"])
    token = auth.make_token(row["id"], row["email"])
    return {"token": token, "user": {"email": row["email"]}}


def _seed_default_agents(user_id):
    """Give a user the default analysis agent(s) if they don't already have them.
    Runs on registration and as a backfill for existing users."""
    from pathlib import Path as _Path
    from psycopg.types.json import Json
    tpl_dir = _Path(__file__).parent.parent / "templates"
    for tpl_file in ("master-analysis-agent.json",):
        try:
            tpl = json.loads((tpl_dir / tpl_file).read_text())
            with db.connect() as conn:
                if conn.execute("SELECT 1 FROM analysis_agents WHERE user_id = %s AND name = %s",
                                (user_id, tpl["name"])).fetchone():
                    continue
                conn.execute(
                    "INSERT INTO analysis_agents (user_id, name, description, status, flow) "
                    "VALUES (%s, %s, %s, 'active', %s)",
                    (user_id, tpl["name"], tpl.get("description", ""), Json(tpl["flow"])))
                conn.commit()
        except Exception:
            pass


@app.post("/api/me/password")
def change_password(body: PasswordBody, user: dict = Depends(current_user)):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE id = %s", (user["id"],)
        ).fetchone()
        if not auth.verify_password(body.current_password, row["password_hash"]):
            raise HTTPException(400, "Current password is incorrect")
        conn.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (auth.hash_password(body.new_password), user["id"]),
        )
        conn.commit()
    return {"ok": True}


# ── API-key routes ─────────────────────────────────────────────────────────────
def _ensure_default_key(user_id):
    """Every user holds an API key from the moment they exist.

    Signup issues one, but a user created any other way — `create_user.py` on a
    self-hosted box, most obviously — had none, and the API keys panel greeted
    them with an empty list and a button, for a key the app was always going to
    give them anyway. Issued on first look instead."""
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM api_keys WHERE user_id = %s AND revoked_at IS NULL",
                        (user_id,)).fetchone():
            return
        raw = auth.generate_api_key()
        prefix, last_four = auth.key_display(raw)
        conn.execute(
            "INSERT INTO api_keys (user_id, name, key_prefix, last_four, key_hash, key_plain) "
            "VALUES (%s, 'Default', %s, %s, %s, %s)",
            (user_id, prefix, last_four, auth.hash_key(raw), raw))
        conn.commit()


@app.get("/api/keys")
def list_keys(user: dict = Depends(current_user)):
    _ensure_default_key(user["id"])
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT id, name, key_prefix, last_four, created_at, last_used_at, revoked_at
               FROM api_keys WHERE user_id = %s ORDER BY created_at DESC""",
            (user["id"],),
        ).fetchall()
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "masked": f'{r["key_prefix"]}…{r["last_four"]}',
            "created_at": r["created_at"],
            "last_used_at": r["last_used_at"],
            "revoked": r["revoked_at"] is not None,
        }
        for r in rows
    ]


@app.post("/api/keys")
def create_key(body: KeyBody, user: dict = Depends(current_user)):
    # The programmatic API is the Developer surface → Elite only, where plans
    # exist at all. is_developer() is already true on an unmetered edition.
    if not billing.is_developer(user["id"]):
        raise HTTPException(403, "API keys are an Elite feature. Upgrade to Elite to use the programmatic API.")
    raw = auth.generate_api_key()
    prefix, last_four = auth.key_display(raw)
    with db.connect() as conn:
        row = conn.execute(
            """INSERT INTO api_keys (user_id, name, key_prefix, last_four, key_hash, key_plain)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, created_at""",
            (user["id"], body.name, prefix, last_four, auth.hash_key(raw), raw),
        ).fetchone()
        conn.commit()
    # The full key is returned exactly once, here.
    return {
        "id": str(row["id"]),
        "name": body.name,
        "key": raw,
        "masked": f"{prefix}…{last_four}",
        "created_at": row["created_at"],
    }


@app.delete("/api/keys/{key_id}")
def revoke_key(key_id: str, user: dict = Depends(current_user)):
    with db.connect() as conn:
        row = conn.execute(
            """UPDATE api_keys SET revoked_at = now()
               WHERE id = %s AND user_id = %s AND revoked_at IS NULL
               RETURNING id""",
            (key_id, user["id"]),
        ).fetchone()
        conn.commit()
    if not row:
        raise HTTPException(404, "Key not found or already revoked")
    return {"ok": True}


@app.get("/api/keys/primary")
def primary_key(user: dict = Depends(current_user)):
    """The oldest active API key's full value — used by the API guide to
    pre-fill runnable example URLs. Issues one if the user somehow has none, so
    a guide's Run buttons work the first time rather than after a detour."""
    _ensure_default_key(user["id"])
    with db.connect() as conn:
        row = conn.execute(
            """SELECT key_plain FROM api_keys
               WHERE user_id = %s AND revoked_at IS NULL AND key_plain IS NOT NULL
               ORDER BY created_at ASC LIMIT 1""",
            (user["id"],),
        ).fetchone()
    return {"api_key": row["key_plain"] if row else None}


def _ensure_settings_row(conn, user_id):
    conn.execute(
        "INSERT INTO exness_settings (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
        (user_id,),
    )


class ActiveAccountBody(BaseModel):
    broker: str = ""      # blank ⇒ the only installed broker
    account: int


@app.get("/api/brokers")
def list_brokers(user: dict = Depends(current_user)):
    """Every installed broker, with the name and logo IT supplies.

    Core does not know what a broker is called or what it looks like — the
    module does, so it says. A broker without a logo simply has none, and the UI
    falls back to its initial."""
    import brokers
    out = []
    for bid, p in brokers.providers().items():
        logo = getattr(p, "logo", None)
        out.append({
            "id": bid,
            "name": getattr(p, "name", bid.title()),
            "logo": f"/api/modules/{bid}/asset/{logo}" if logo else None,
        })
    return {"brokers": out}


# ── unified accounts (all brokers) + broker-aware active switch ─────────────────
@app.get("/api/accounts")
def list_all_accounts(user: dict = Depends(current_user)):
    """Every account across every installed broker, for the Accounts page.

    Core does not know what any broker's accounts look like; each broker module
    answers for itself through `accounts_view`, and its answer is filed under its
    own id. A broker that is not installed contributes no key at all, which is
    how the page knows not to offer it."""
    import brokers
    uid = user["id"]
    with db.connect() as conn:
        s = conn.execute(
            "SELECT active_account, active_broker FROM exness_settings WHERE user_id = %s",
            (uid,),
        ).fetchone()
    out = {"active": {"broker": (s["active_broker"] if s else None) or brokers.default_broker(),
                      "account": (s["active_account"] if s else None)},
           "brokers": []}
    for bid, p in brokers.providers().items():
        out["brokers"].append(bid)
        fn = getattr(p, "accounts_view", None)
        if not fn:
            continue
        try:
            out[bid] = fn(uid)
        except Exception as e:
            out[bid] = {"connected": False, "accounts": [], "error": str(e)}
        # null means "never chosen", which the UI shows as everything ticked —
        # a user who has not opened this page has not opted out of anything.
        out[bid]["available"] = brokers.available(uid, bid)

    return out


# ── operator settings, where the operator IS the user ─────────────────────────
# Two of the admin console's settings are not about managing OTHER PEOPLE — they
# are about how this instance behaves — so on a single-user box they belong in
# that person's own Settings rather than behind a door marked Admin. The rest of
# the console (users, plans, credits, who may register) has nothing to manage
# there and is gone.
class InstanceSettings(BaseModel):
    app_name: str | None = None
    analysis_window_seconds: int | None = None
    analysis_sharing: bool | None = None
    watch_list_hours: str | None = None


def _require_self_operated():
    if edition.has_admin_console():
        raise HTTPException(404, "Use the admin area on this deployment.")


@app.get("/api/instance/settings")
def get_instance_settings(user: dict = Depends(current_user)):
    _require_self_operated()
    import analysis_cache, watchlist, mailer
    return {"app_name": mailer.app_name(),
            "analysis": {**analysis_cache.settings(), "live": analysis_cache.stats()},
            "watch_list": watchlist.status()}


@app.put("/api/instance/settings")
def set_instance_settings(body: InstanceSettings, user: dict = Depends(current_user)):
    _require_self_operated()
    import analysis_cache, watchlist, mailer
    if body.app_name is not None and body.app_name.strip() != mailer.app_name():
        # This endpoint is Community-only, and there the name is fixed: renaming
        # is the first step of white-labelling, which is what the licence exists
        # to prevent. Refused rather than quietly dropped, so nobody is told
        # "Saved" about a change that did not happen. The real line is in
        # `mailer.app_name`; this only stops a write that would look like it
        # worked until the next page load.
        raise HTTPException(403, f"The app name is fixed to {mailer.app_name()} on this "
                                 f"edition and cannot be changed.")
    if body.analysis_window_seconds is not None or body.analysis_sharing is not None:
        cur = analysis_cache.settings()
        try:
            # The shared-answer PRICE is not offered: nobody is billed here, so a
            # percentage of nothing is a setting that cannot mean anything.
            analysis_cache.save_settings(
                cur["window_seconds"] if body.analysis_window_seconds is None
                else body.analysis_window_seconds,
                cur.get("cached_charge_pct", 0),
                cur["enabled"] if body.analysis_sharing is None else body.analysis_sharing)
        except ValueError as e:
            raise HTTPException(400, str(e))
    if body.watch_list_hours is not None:
        try:
            watchlist.set_hours(body.watch_list_hours)
        except ValueError as e:
            raise HTTPException(400, str(e))
    return get_instance_settings(user)


@app.post("/api/instance/watch-list/run")
def run_watch_list_now(user: dict = Depends(current_user)):
    _require_self_operated()
    import watchlist
    if watchlist._running.locked():
        raise HTTPException(409, "A watch-list run is already in progress.")
    threading.Thread(target=lambda: watchlist.run_once(force=True),
                     daemon=True, name="watchlist-manual").start()
    return {"started": True, **watchlist.status()}


# ── connections ────────────────────────────────────────────────────────────────
class ConnectionBody(BaseModel):
    kind: str = ""
    name: str = ""
    config: dict = {}
    enabled: bool | None = None


@app.get("/api/connections")
def list_connections(user: dict = Depends(current_user)):
    """What can be connected, and what this user has connected."""
    import connections
    return {"types": connections.types(), "connections": connections.listing(user["id"])}


@app.post("/api/connections")
def create_connection(body: ConnectionBody, user: dict = Depends(current_user)):
    import connections
    try:
        c = connections.create(user["id"], body.kind, body.name, body.config)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"connection": c, **list_connections(user)}


@app.put("/api/connections/{cid}")
def update_connection(cid: str, body: ConnectionBody, user: dict = Depends(current_user)):
    import connections
    c = connections.update(user["id"], cid, name=body.name or None,
                           config=body.config, enabled=body.enabled)
    if c is None:
        raise HTTPException(404, "No such connection.")
    return {"connection": c, **list_connections(user)}


@app.delete("/api/connections/{cid}")
def delete_connection(cid: str, user: dict = Depends(current_user)):
    import connections
    if not connections.delete(user["id"], cid):
        raise HTTPException(404, "No such connection.")
    return list_connections(user)


# ── the chat agent's prompt ────────────────────────────────────────────────────
class AgentPromptBody(BaseModel):
    instructions: str | None = None


@app.get("/api/agent/prompt")
def get_agent_prompt(user: dict = Depends(current_user)):
    """What the agent is told, and what the user has added to it.

    `built_in` is returned so the page can show what is being extended. Editing
    blind is how someone writes an instruction the built-in prompt already
    contradicts, and then wonders why it is ignored."""
    import agent
    s = agent.user_prompt_settings(user["id"])
    built_in = agent.system_prompt([], "", None, None)
    return {"instructions": s["instructions"],
            "built_in": built_in, "built_in_chars": len(built_in)}


@app.put("/api/agent/prompt")
def set_agent_prompt(body: AgentPromptBody, user: dict = Depends(current_user)):
    """Save the user's own standing instructions.

    Not validated beyond a length cap: it is a prompt, and there is no useful way
    to check one except by using it."""
    sets, args = [], []
    if body.instructions is not None:
        sets.append("agent_instructions = %s"); args.append(body.instructions.strip()[:8000] or None)
    if sets:
        with db.connect() as conn:
            conn.execute("INSERT INTO user_prefs (user_id) VALUES (%s) "
                         "ON CONFLICT (user_id) DO NOTHING", (user["id"],))
            conn.execute(f"UPDATE user_prefs SET {', '.join(sets)}, updated_at = now() "
                         f"WHERE user_id = %s", (*args, user["id"]))
            conn.commit()
    return get_agent_prompt(user)


class AvailableBody(BaseModel):
    broker: str
    accounts: list = []


@app.post("/api/accounts/available")
def set_available_accounts(body: AvailableBody, user: dict = Depends(current_user)):
    """Which accounts this app may touch on a broker.

    Separate from the ACTIVE account on purpose: available is "you may use this
    one", active is "use this one now". A user with five accounts at a broker
    should be able to keep four of them out of reach of an app that places
    trades."""
    import brokers
    if brokers.get(body.broker) is None:
        raise HTTPException(400, f"No {body.broker} broker is installed.")
    picks = brokers.set_available(user["id"], body.broker, body.accounts)
    return {"broker": body.broker, "available": picks}


@app.post("/api/accounts/active")
def set_active_account_unified(body: ActiveAccountBody, user: dict = Depends(current_user)):
    """Set the active account for ANY installed broker. Selecting one flips the
    whole app to that broker's protocol."""
    import brokers
    broker = (body.broker or "").lower() or (brokers.default_broker() or "")
    p = brokers.get(broker)
    if p is None:
        have = ", ".join(sorted(brokers.providers())) or "none"
        raise HTTPException(400, f"No {broker or 'broker'} is installed (installed: {have}).")
    owns = getattr(p, "owns_account", None)
    if owns and not owns(user["id"], body.account):
        raise HTTPException(404, f"That {broker} account isn't connected.")
    if not brokers.is_available(user["id"], broker, body.account):
        raise HTTPException(409, "That account is not available to this app. Tick it "
                                 "on the Accounts page first.")
    with db.connect() as conn:
        _ensure_settings_row(conn, user["id"])
        conn.execute(
            "UPDATE exness_settings SET active_account = %s, active_broker = %s, "
            "updated_at = now() WHERE user_id = %s",
            (body.account, broker, user["id"]),
        )
        conn.commit()
    return {"ok": True, "broker": broker, "active_account": body.account}



# ── AI model settings ──────────────────────────────────────────────────────────
_PROVIDERS = ai_keys.PROVIDERS


class AIProviderKey(BaseModel):
    provider: str
    key: str = ""   # blank = clear the stored key


class AIModels(BaseModel):
    selected: list = []   # [{provider, model}]


class AgentChat(BaseModel):
    model: str = ""              # branded alias (arrissa-chat / arrissa-pro); empty → default
    provider: str = ""          # legacy/ignored — the app resolves the provider itself
    messages: list = []          # [{role, content}]
    accounts: list = []          # account numbers the chat operates on


def _user_chat_model(user_id):
    """The branded model alias the user last picked in chat (user_prefs.chat_model)."""
    with db.connect() as conn:
        row = conn.execute("SELECT chat_model FROM user_prefs WHERE user_id = %s", (user_id,)).fetchone()
    return (row["chat_model"] if row else None) or None


def _user_analysis_model(user_id):
    """The model AGENTS run on — a separate choice from the chat model.

    They used to be the same field, so switching the chat to something fast and
    chatty switched every analysis agent to it as well, including the scheduled
    ones and the ones an EA polls. That is how a rate-limited model picked for a
    conversation ended up returning quality-0 no-trade signals to a live trading
    robot. Falls back to the chat model, then the default tier, so an account
    that has never set one behaves exactly as before."""
    with db.connect() as conn:
        row = conn.execute("SELECT analysis_model FROM user_prefs WHERE user_id = %s",
                           (user_id,)).fetchone()
    return ((row["analysis_model"] if row else None) or None) or _user_chat_model(user_id)


# ── AI metering ────────────────────────────────────────────────────────────────
# Every model call the app makes on a user's behalf is gated the same way BEFORE
# (they must be subscribed and hold credits) and metered the same way AFTER (on
# the tokens actually burned, at our real cost). One pair of helpers, so a new
# AI-backed endpoint bills like the old ones instead of quietly running for free.
def _require_credits(user_id, why="Subscribe to use AI features."):
    # Nobody is billed on a Community instance — the operator is paying for the
    # box and the AI keys already. Asking them to subscribe to their own
    # software is the one thing this gate must never do.
    if not edition.metered():
        return billing.get_state(user_id)
    state = billing.get_state(user_id)
    if not state["active"]:
        raise HTTPException(402, why)
    if state["credits"] <= 0:
        raise HTTPException(402, "Out of credits — top up or upgrade your plan.")
    return state


def _meter(user_id, res, reason, ref=None):
    """Charge what `res` reports it cost. Returns the credits debited (0 when the
    call reported no usage — an unpriced model, or no key at all)."""
    try:
        return billing.charge_cost(user_id, (res or {}).get("cost_usd") or 0, reason,
                                   str(ref) if ref else None,
                                   provider=(res or {}).get("provider"))
    except Exception:
        return 0


@app.get("/api/ai/config")
def ai_config(user: dict = Depends(current_user)):
    """What the model picker may offer.

    Cloud: the branded tiers, whose real provider is never exposed. Community:
    the models the user chose from their own providers."""
    return ai_keys.config(user["id"])


class ProviderKey(BaseModel):
    provider: str
    key: str = ""        # blank clears it


class ModelChoice(BaseModel):
    models: list = []    # [{provider, model}]


def _require_byok():
    """May this user manage keys and models of their own?

    This asked `edition.byok()`, which is community-only — so on the cloud the
    endpoint that LISTS a provider's models answered 403. A user could connect
    DeepSeek on the Connections page, be shown no models to choose from, and have
    no way to reach what they had just connected. The operator's switch decides
    it now, in either edition."""
    if not ai_keys.may_bring_own():
        raise HTTPException(403, "This deployment runs on its own AI keys.")


@app.post("/api/ai/provider")
def set_ai_provider_key(body: ProviderKey, user: dict = Depends(current_user)):
    """Store one of the user's own provider keys. Community only — on the cloud
    the app runs on its own keys and a user key would be spending money nobody
    agreed to."""
    _require_byok()
    if body.provider not in ai_keys.PROVIDERS:
        raise HTTPException(400, f"provider must be one of {', '.join(ai_keys.PROVIDERS)}")
    has = ai_keys.set_user_key(user["id"], body.provider, body.key)
    return {"provider": body.provider, "has_key": has, **ai_keys.config(user["id"])}


@app.get("/api/ai/models")
def list_ai_models(provider: str, user: dict = Depends(current_user)):
    """Every model that key can actually reach, asked of the provider RIGHT NOW.

    Not a hardcoded list: providers ship models weekly, and a compiled-in list is
    wrong by the next release."""
    _require_byok()
    return ai_keys.list_models(user["id"], provider)


@app.post("/api/ai/models")
def choose_ai_models(body: ModelChoice, user: dict = Depends(current_user)):
    """Which of them to offer in the model picker."""
    _require_byok()
    ai_keys.set_selected(user["id"], body.models)
    return ai_keys.config(user["id"])


@app.post("/api/agent/chat")
def agent_chat(body: AgentChat, user: dict = Depends(current_user)):
    # Resolve the branded model the user picked (arrissa-chat / arrissa-pro) to the
    # …to a real provider/model and the key it should run on: the app's on cloud,
    # the user's own on a Community box.
    alias = body.model or billing.DEFAULT_MODEL
    provider, model, key = ai_keys.resolve(user["id"], alias)

    # Billing gate — active subscription + a positive credit balance. Metering is
    # done AFTER the turn from REAL token usage (we can't know the cost up front),
    # so a balance at/above 0 is enough to start; the turn may dip it slightly below.
    import admin_api
    if admin_api.is_suspended(user["id"]):
        raise HTTPException(403, "Your account is suspended. Contact support.")
    _require_credits(user["id"], "Subscribe to start chatting with Arrissa.")
    if not key:
        raise HTTPException(503, f"{alias} is temporarily unavailable — the operator hasn't configured it yet.")

    import user_session
    with user_session.as_user(user["id"]):     # this user's own Exness accounts only
        accounts = agent._accounts_context()
    # restrict the agent to EXACTLY the accounts the user picked for this chat.
    # (Don't fall back to "all" if a pick isn't in the list — e.g. an archived
    # account — or the agent would silently act on the wrong account.)
    if body.accounts and isinstance(accounts, list):
        by_num = {a.get("account"): a for a in accounts}
        accounts = [by_num.get(int(n), {"account": int(n)}) for n in body.accounts]

    memory = agent.read_memory(user["id"])
    uid = user["id"]
    last_user = next((m.get("content", "") for m in reversed(body.messages)
                      if m.get("role") == "user"), "")

    meter = {}   # accumulates the whole turn's real token usage (chat + analysis runs)

    def gen():
        reply = []
        try:
            for ev in agent.run_agent(provider, model, key, body.messages, accounts, memory, uid, meter=meter):
                if ev.get("type") == "text":
                    reply.append(ev.get("text", ""))
                yield {"data": json.dumps(ev, default=str)}
        except Exception as e:
            yield {"data": json.dumps({"type": "error", "error": str(e)})}
        # Meter the REAL spend for the whole turn — the main chat calls (metered by
        # token) PLUS every analysis-agent run it triggered (their real cost, or 20%
        # for a 5s cache hit, rolled into extra_cost_usd) — and debit our true cost.
        try:
            cost = billing.cost_of(meter, model) + float(meter.get("extra_cost_usd", 0))
            credits = billing.charge_cost(uid, cost, "chat", provider=provider)
            yield {"data": json.dumps({"type": "usage", "credits": credits,
                                       "cost_usd": round(cost, 6),
                                       "cache_hits": int(meter.get("cache_hits", 0))})}
        except Exception:
            pass
        # automatic post-turn memory: save any new durable facts about the user
        try:
            for note in agent.extract_memory(provider, key, last_user, "".join(reply), memory, uid, model):
                yield {"data": json.dumps({"type": "memory", "note": note})}
        except Exception:
            pass

    return EventSourceResponse(gen())


# ── admin settings + Whisper voice transcription ───────────────────────────────
def _admin_openai_key():
    with db.connect() as conn:
        row = conn.execute("SELECT openai_key_enc FROM admin_settings WHERE id = 1").fetchone()
    if row and row["openai_key_enc"]:
        try:
            return auth.decrypt(row["openai_key_enc"])
        except Exception:
            return None
    return None


def _openai_key_for(user_id):
    """The OpenAI key used for Whisper — the app's on cloud, the user's own on a
    Community box, where there is no app key to fall back to."""
    return ai_keys.key_for(user_id, "openai")


class AdminKeyBody(BaseModel):
    key: str = ""   # blank = clear


@app.get("/api/admin/openai-key")
def get_admin_openai_key(user: dict = Depends(current_user)):
    return {"has_key": bool(_admin_openai_key())}


@app.post("/api/admin/openai-key")
def set_admin_openai_key(body: AdminKeyBody, user: dict = Depends(current_user)):
    enc = auth.encrypt(body.key) if body.key else None
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO admin_settings (id, openai_key_enc, updated_at) VALUES (1, %s, now()) "
            "ON CONFLICT (id) DO UPDATE SET openai_key_enc = EXCLUDED.openai_key_enc, updated_at = now()",
            (enc,),
        )
        conn.commit()
    return {"ok": True, "has_key": bool(enc)}


# ── admin: TradeLocker developer/partner API key (app-level, one key) ───────────
def _require_admin(user: dict):
    """Only admins (hardcoded super-owners OR the admins table) may read/write
    app-level credentials/settings."""
    import admin_api
    if not admin_api._is_admin(user["email"]):
        raise HTTPException(403, "Admin only.")



@app.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...), user: dict = Depends(current_user)):
    """Transcribe recorded audio to text with OpenAI Whisper (voice chat input)."""
    # Billing gate + metering (voice is a metered AI action) — on a metered
    # edition only.
    if edition.metered():
        if not billing.is_active(user["id"]):
            raise HTTPException(402, "Subscribe to use voice input.")
        if not billing.charge(user["id"], billing.credits_for("voice"), "voice", None):
            raise HTTPException(402, "Out of credits — top up or upgrade to keep using voice.")
    key = _openai_key_for(user["id"])
    if not key:
        raise HTTPException(400, "No OpenAI API key configured for voice input.")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty audio.")
    from openai import OpenAI
    client = OpenAI(api_key=key)
    try:
        tr = client.audio.transcriptions.create(
            model="whisper-1",
            file=(file.filename or "audio.webm", data, file.content_type or "audio/webm"))
        return {"text": (tr.text or "").strip()}
    except Exception as e:
        raise HTTPException(400, f"Transcription failed: {e}")


# ── billing: plans, credits, (simulated) Paystack ──────────────────────────────
class CheckoutBody(BaseModel):
    plan: str = ""            # subscription: trader|pro|max|elite
    pack: str = ""            # topup: boost|power|bulk
    interval: str = "monthly"  # monthly | annual


class SimulateBody(BaseModel):
    reference: str
    outcome: str = "success"   # success | declined


def _billing_applies():
    """Refuse the whole billing surface where nobody is billed.

    Hiding the page is a courtesy; refusing the endpoints is the actual rule. A
    Community instance has no plans to sell, and an endpoint that would happily
    take a checkout on one is a bug waiting for someone to find it."""
    if not edition.metered():
        raise HTTPException(404, "This deployment has no plans or billing.")


@app.get("/api/billing/catalog")
def billing_catalog(user: dict = Depends(current_user)):
    """Plans, credit packs, branded models and per-action credit costs."""
    _billing_applies()
    return billing.catalog()


@app.get("/api/billing/me")
def billing_me(user: dict = Depends(current_user)):
    _billing_applies()
    return billing.get_state(user["id"])


@app.get("/api/billing/ledger")
def billing_ledger(user: dict = Depends(current_user)):
    _billing_applies()
    return billing.ledger(user["id"])


class VerifyBody(BaseModel):
    reference: str


@app.post("/api/billing/checkout")
def billing_checkout(body: CheckoutBody, user: dict = Depends(current_user)):
    """Create a PENDING transaction and return everything the Paystack Inline pop-up
    needs (public key, reference, email, plan_code or amount, mode). If Paystack has
    no keys for the current mode we fall back to the simulated flow."""
    _billing_applies()
    try:
        tx = billing.checkout(user["id"], plan=body.plan or None,
                              pack=body.pack or None, interval=body.interval)
    except ValueError as e:
        raise HTTPException(400, str(e))

    mode = paystack.get_mode()
    tx["mode"] = mode
    tx["email"] = user["email"]
    if not paystack.configured(mode):
        tx["provider"] = "simulated"     # no keys → the UI shows the simulate buttons
        return tx
    tx["provider"] = "paystack"
    tx["public_key"] = paystack.public_key(mode)
    if tx["kind"] == "subscription":
        code = paystack.plan_code(mode, tx["plan"], tx["interval"])
        if not code:
            raise HTTPException(400, f"Paystack plans aren't set up for {mode} yet — run 'Create plans' in admin.")
        tx["plan_code"] = code
    tx["amount_kobo"] = int(tx["amount_zar"]) * 100   # Paystack minor unit (cents)
    return tx


@app.post("/api/billing/verify")
def billing_verify(body: VerifyBody, user: dict = Depends(current_user)):
    """Verify a Paystack transaction server-side and, on success, apply the
    plan/credits. Called by the pop-up's success callback."""
    _billing_applies()
    mode = paystack.get_mode()
    try:
        data = paystack.verify(body.reference, mode)
    except Exception as e:
        raise HTTPException(400, f"Could not verify payment: {e}")
    if (data.get("status") or "").lower() != "success":
        billing.mark_declined(user["id"], body.reference)
        return {"status": "declined", "reference": body.reference}
    # capture Paystack identifiers for later cancellation
    cust = (data.get("customer") or {}).get("customer_code")
    ids = {"customer_code": cust,
           "subscription_code": data.get("subscription_code"),
           "email_token": data.get("email_token")}
    # a plan transaction creates the subscription async — pull its code if present
    if cust and not ids["subscription_code"]:
        try:
            subs = paystack.customer_subscriptions(cust, mode)
            if subs:
                ids["subscription_code"] = subs[0].get("subscription_code")
                ids["email_token"] = subs[0].get("email_token") or ids["email_token"]
        except Exception:
            pass
    try:
        state = billing.apply_success(user["id"], body.reference, ids)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": "success", "reference": body.reference, "state": state}


@app.post("/api/billing/simulate")
def billing_simulate(body: SimulateBody, user: dict = Depends(current_user)):
    """Dev-only fallback (used when Paystack has no keys for the current mode):
    complete a pending transaction as success|declined."""
    _billing_applies()
    try:
        return billing.simulate(user["id"], body.reference, body.outcome)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/billing/cancel")
def billing_cancel(user: dict = Depends(current_user)):
    _billing_applies()
    # best-effort: disable the recurring subscription on Paystack, then downgrade locally
    ids = billing.paystack_ids(user["id"])
    code, token = ids.get("paystack_subscription_code"), ids.get("paystack_email_token")
    if code and token and paystack.configured():
        try:
            paystack.disable_subscription(code, token)
        except Exception:
            pass
    return billing.cancel(user["id"])


# ── admin: Paystack keys, environment (test/live), plan sync ────────────────────
class PaystackKeys(BaseModel):
    mode: str                 # test | live
    secret: str = ""
    public: str = ""


class PaystackMode(BaseModel):
    mode: str                 # test | live


@app.get("/api/admin/paystack")
def get_admin_paystack(user: dict = Depends(current_user)):
    _require_admin(user)
    return {
        "mode": paystack.get_mode(),
        "test": {"has_secret": bool(paystack.secret_key("test")), "public": paystack.public_key("test")},
        "live": {"has_secret": bool(paystack.secret_key("live")), "public": paystack.public_key("live")},
        "plans": paystack.list_plan_codes(),
    }


@app.post("/api/admin/paystack/keys")
def set_admin_paystack_keys(body: PaystackKeys, user: dict = Depends(current_user)):
    _require_admin(user)
    if body.mode not in ("test", "live"):
        raise HTTPException(400, "mode must be test | live")
    paystack.set_keys(body.mode, body.secret.strip(), body.public.strip())
    return {"ok": True}


@app.post("/api/admin/paystack/mode")
def set_admin_paystack_mode(body: PaystackMode, user: dict = Depends(current_user)):
    _require_admin(user)
    return {"ok": True, "mode": paystack.set_mode(body.mode)}


@app.post("/api/admin/paystack/sync-plans")
def sync_admin_paystack_plans(body: PaystackMode, user: dict = Depends(current_user)):
    _require_admin(user)
    if body.mode not in ("test", "live"):
        raise HTTPException(400, "mode must be test | live")
    if not paystack.configured(body.mode):
        raise HTTPException(400, f"No Paystack keys set for {body.mode}.")
    try:
        made = paystack.sync_plans(body.mode)
    except Exception as e:
        raise HTTPException(400, f"Plan sync failed: {e}")
    # The store's products too, in the same action. Two buttons for "make the
    # plans" is one button somebody forgets, and the failure is silent: the
    # store falls back to a single charge and nothing renews.
    store_made = []
    try:
        store_made = paystack.sync_store_plans(body.mode)
    except Exception as e:
        print(f"[paystack] store plan sync failed: {e}", flush=True)
    return {"ok": True, "mode": body.mode, "plans": made, "store_plans": store_made}


# ── admin: private invite link (registration bypass) ────────────────────────────
def _set_invite_code(code):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO admin_settings (id, signup_invite_code, updated_at) VALUES (1, %s, now()) "
            "ON CONFLICT (id) DO UPDATE SET signup_invite_code = EXCLUDED.signup_invite_code, updated_at = now()",
            (code,))
        conn.commit()
    return code


@app.get("/api/admin/invite")
def get_admin_invite(user: dict = Depends(current_user)):
    _require_admin(user)
    code = _invite_code()
    if not code:                       # seed one the first time it's viewed
        import secrets as _s
        code = _set_invite_code(_s.token_urlsafe(12))
    return {"code": code, "path": f"/signup?invite={code}", "registrations_open": _registrations_open()}


@app.post("/api/admin/invite/rotate")
def rotate_admin_invite(user: dict = Depends(current_user)):
    _require_admin(user)
    import secrets as _s
    code = _set_invite_code(_s.token_urlsafe(12))
    return {"code": code, "path": f"/signup?invite={code}"}


# ── chat history ─────────────────────────────────────────────────────────────────
class ChatCreate(BaseModel):
    title: str = "New chat"
    messages: list = []


class ChatUpdate(BaseModel):
    title: str | None = None
    messages: list | None = None


@app.get("/api/chats")
def list_chats(user: dict = Depends(current_user)):
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, title, updated_at FROM chats WHERE user_id = %s ORDER BY updated_at DESC",
            (user["id"],),
        ).fetchall()
    return [{"id": str(r["id"]), "title": r["title"], "updated_at": r["updated_at"]} for r in rows]


@app.post("/api/chats")
def create_chat(body: ChatCreate, user: dict = Depends(current_user)):
    from psycopg.types.json import Json
    import chat_title
    # The sidebar name is read out of the ANSWER — no model call — rather than
    # copied from the first sentence typed. What the client sent stays as the
    # fallback for anything the parser cannot name.
    title = (body.title or "").strip()[:120] or "New chat"
    suggested = chat_title.suggest(body.messages)
    if suggested:
        title = suggested
    with db.connect() as conn:
        row = conn.execute(
            "INSERT INTO chats (user_id, title, messages) VALUES (%s, %s, %s) RETURNING id, title",
            (user["id"], title, Json(body.messages)),
        ).fetchone()
        conn.commit()
    return {"id": str(row["id"]), "title": row["title"]}


@app.get("/api/chats/{chat_id}")
def get_chat(chat_id: str, user: dict = Depends(current_user)):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id, title, messages FROM chats WHERE id = %s AND user_id = %s",
            (chat_id, user["id"]),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Chat not found")
    return {"id": str(row["id"]), "title": row["title"], "messages": row["messages"]}


@app.put("/api/chats/{chat_id}")
def update_chat(chat_id: str, body: ChatUpdate, user: dict = Depends(current_user)):
    from psycopg.types.json import Json
    sets, params = [], []
    if body.title is not None:
        sets.append("title = %s"); params.append(body.title[:120] or "New chat")
    if body.messages is not None:
        sets.append("messages = %s"); params.append(Json(body.messages))
    if not sets:
        return {"ok": True}
    sets.append("updated_at = now()")
    params += [chat_id, user["id"]]
    with db.connect() as conn:
        row = conn.execute(
            f"UPDATE chats SET {', '.join(sets)} WHERE id = %s AND user_id = %s RETURNING id",
            params,
        ).fetchone()
        conn.commit()
    if not row:
        raise HTTPException(404, "Chat not found")
    return {"ok": True}


@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: str, user: dict = Depends(current_user)):
    with db.connect() as conn:
        conn.execute("DELETE FROM chats WHERE id = %s AND user_id = %s", (chat_id, user["id"]))
        conn.commit()
    return {"ok": True}


# ── per-user memory (MEMORY.md) ────────────────────────────────────────────────
class MemoryBody(BaseModel):
    content: str = ""


@app.get("/api/memory")
def get_memory(user: dict = Depends(current_user)):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT content, updated_at FROM user_memory WHERE user_id = %s", (user["id"],)
        ).fetchone()
    return {"content": (row["content"] if row else "") or "", "updated_at": row["updated_at"] if row else None}


@app.put("/api/memory")
def set_memory(body: MemoryBody, user: dict = Depends(current_user)):
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO user_memory (user_id, content, updated_at) VALUES (%s, %s, now())
               ON CONFLICT (user_id) DO UPDATE SET content = EXCLUDED.content, updated_at = now()""",
            (user["id"], body.content),
        )
        conn.commit()
    return {"ok": True}


# ── per-user chat preferences (persisted selection) ────────────────────────────
class PrefsBody(BaseModel):
    chat_model: str | None = None
    chat_accounts: list | None = None
    # Default risk management (used by the SL/TP engine when a request omits its own).
    default_risk_pct: float | None = None
    default_risk_money: float | None = None
    risk_basis: str | None = None            # equity | balance
    default_trade_style: str | None = None   # scalp | intraday | swing | position
    analysis_model: str | None = None        # the model AGENTS run on; "" = follow the chat model


@app.get("/api/prefs")
def get_prefs(user: dict = Depends(current_user)):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT chat_model, chat_accounts, default_risk_pct, default_risk_money, "
            "risk_basis, default_trade_style, analysis_model FROM user_prefs "
            "WHERE user_id = %s", (user["id"],)
        ).fetchone()
    return {"chat_model": row["chat_model"] if row else None,
            # Empty means "follow the chat model", which is what it did before
            # this was a separate field — so an account that never sets it is
            # unchanged.
            "analysis_model": (row["analysis_model"] if row else None) or "",
            "chat_accounts": (row["chat_accounts"] if row else []) or [],
            "default_risk_pct": row["default_risk_pct"] if row else None,
            "default_risk_money": row["default_risk_money"] if row else None,
            "risk_basis": (row["risk_basis"] if row else None) or "equity",
            "default_trade_style": (row["default_trade_style"] if row else None) or "intraday"}


@app.put("/api/prefs")
def set_prefs(body: PrefsBody, user: dict = Depends(current_user)):
    from psycopg.types.json import Json
    with db.connect() as conn:
        conn.execute("INSERT INTO user_prefs (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
                     (user["id"],))
        if body.chat_model is not None:
            conn.execute("UPDATE user_prefs SET chat_model = %s, updated_at = now() WHERE user_id = %s",
                         (body.chat_model, user["id"]))
        if body.analysis_model is not None:
            conn.execute("UPDATE user_prefs SET analysis_model = %s, updated_at = now() "
                         "WHERE user_id = %s",
                         (body.analysis_model.strip() or None, user["id"]))
        if body.chat_accounts is not None:
            conn.execute("UPDATE user_prefs SET chat_accounts = %s, updated_at = now() WHERE user_id = %s",
                         (Json(body.chat_accounts), user["id"]))
        if body.default_risk_pct is not None:
            conn.execute("UPDATE user_prefs SET default_risk_pct = %s, updated_at = now() WHERE user_id = %s",
                         (body.default_risk_pct, user["id"]))
        if body.default_risk_money is not None:
            conn.execute("UPDATE user_prefs SET default_risk_money = %s, updated_at = now() WHERE user_id = %s",
                         (body.default_risk_money, user["id"]))
        if body.risk_basis is not None:
            conn.execute("UPDATE user_prefs SET risk_basis = %s, updated_at = now() WHERE user_id = %s",
                         (body.risk_basis, user["id"]))
        if body.default_trade_style is not None:
            conn.execute("UPDATE user_prefs SET default_trade_style = %s, updated_at = now() WHERE user_id = %s",
                         (body.default_trade_style, user["id"]))
        conn.commit()
    return {"ok": True}


# ── per-user risk parameters (profile-wide + per-account overrides) ─────────────
class RiskSettingsBody(BaseModel):
    account: str | None = None          # '' / None = profile-wide default; else account number/id
    risk_pct: float | None = None       # risk % per trade
    reward_rr: float | None = None      # reward : risk per trade (2 = 2R)
    max_dd_day: float | None = None     # max drawdown per day, %
    max_dd_week: float | None = None    # per week, %
    max_dd_month: float | None = None   # per month, %
    trading_hours: list | None = None   # [{"start":"HH:MM","end":"HH:MM"}, …]
    trading_tz: str | None = None       # IANA tz the hours are stated in
    risk_basis: str | None = None       # equity | balance
    trade_style: str | None = None      # scalp | intraday | swing | position


def _risk_row(r):
    if not r:
        return None
    return {k: r[k] for k in ("account", "risk_pct", "reward_rr", "max_dd_day", "max_dd_week",
                              "max_dd_month", "trading_tz", "risk_basis", "trade_style")} | \
           {"trading_hours": r["trading_hours"] or []}


def _clean_hours(hours):
    """Validate/normalise [{start,end}] as 'HH:MM'. Raises 400 on bad shape."""
    if not hours:
        return []
    out = []
    for w in hours:
        try:
            s, e = str(w["start"]).strip(), str(w["end"]).strip()
            for x in (s, e):
                hh, mm = x.split(":")
                assert 0 <= int(hh) < 24 and 0 <= int(mm) < 60
            out.append({"start": s, "end": e})
        except Exception:
            raise HTTPException(400, "trading_hours must be a list of {start:'HH:MM', end:'HH:MM'}")
    return out


class TradeCheck(BaseModel):
    symbol: str
    side: str                      # buy | sell
    volume: float
    # An account number arrives as a NUMBER from the panel and as a string from
    # anything that stores it as text. Declaring it `str` alone rejected the
    # first with "Input should be a valid string", which reads as a bug in the
    # trade rather than in the type — so it accepts both and is normalised once.
    account: str | int = ""
    sl: float | None = None
    tp: float | None = None

    @property
    def acct(self) -> str:
        return str(self.account or "")


class TradeAdvise(BaseModel):
    ctx: dict
    message: str = ""


class TradeGo(TradeCheck):
    override: bool = False         # they saw the warning and chose to proceed


@app.post("/api/trade/precheck")
def trade_precheck(body: TradeCheck, user: dict = Depends(current_user)):
    """Does this trade fit the trader's own rules? Deterministic and cheap —
    no model runs here, because the common answer is yes and that answer has to
    arrive at the speed of a button."""
    import risk_gate
    import user_session
    # Bound to THIS user's broker session. There is no global middleware doing
    # it — every endpoint that touches a broker binds explicitly — and without
    # it the call falls through to whatever session happens to be ambient.
    with user_session.as_user(user["id"]):
        return risk_gate.check(user["id"], body.acct, body.symbol, body.side,
                               body.volume, sl=body.sl, tp=body.tp)


@app.post("/api/trade/advise")
def trade_advise(body: TradeAdvise, user: dict = Depends(current_user)):
    """The Risk Settings agent's reply — only reached once the check already
    found something, or when the trader answers back in the modal."""
    import risk_gate
    return risk_gate.advise(user["id"], body.ctx or {}, body.message or "")


@app.post("/api/trade/execute")
def trade_execute(body: TradeGo, user: dict = Depends(current_user)):
    """Place it. Re-checks server-side unless the trader explicitly overrode.

    The frontend already asked, but a check that only exists in the browser is
    not a check — this endpoint is reachable on its own, and `override` has to
    be a deliberate flag rather than the absence of one."""
    import risk_gate
    import trading_api
    import user_session
    with user_session.as_user(user["id"]):
        if not body.override:
            gate = risk_gate.check(user["id"], body.acct, body.symbol, body.side,
                                   body.volume, sl=body.sl, tp=body.tp)
            if not gate["ok"]:
                raise HTTPException(409, "; ".join(i["title"] for i in gate["issues"]
                                                   if i["severity"] == "block"))
        t = trading_api.trader(account=body.acct) if body.acct else trading_api.trader()
        res = t.place_order(body.symbol, float(body.volume), body.side,
                            sl=body.sl or 0, tp=body.tp or 0)
    return {"ok": True, "result": res}


@app.get("/api/risk-settings")
def get_risk_settings(user: dict = Depends(current_user)):
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM risk_settings WHERE user_id = %s", (user["id"],)).fetchall()
    return {"profile": next((_risk_row(r) for r in rows if r["account"] == ""), None),
            "accounts": [_risk_row(r) for r in rows if r["account"] != ""]}


@app.put("/api/risk-settings")
def put_risk_settings(body: RiskSettingsBody, user: dict = Depends(current_user)):
    from psycopg.types.json import Json
    acct = (body.account or "").strip()
    hours = _clean_hours(body.trading_hours)
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO risk_settings (user_id, account, risk_pct, reward_rr, max_dd_day,
                 max_dd_week, max_dd_month, trading_hours, trading_tz, risk_basis, trade_style, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
               ON CONFLICT (user_id, account) DO UPDATE SET
                 risk_pct=EXCLUDED.risk_pct, reward_rr=EXCLUDED.reward_rr, max_dd_day=EXCLUDED.max_dd_day,
                 max_dd_week=EXCLUDED.max_dd_week, max_dd_month=EXCLUDED.max_dd_month,
                 trading_hours=EXCLUDED.trading_hours, trading_tz=EXCLUDED.trading_tz,
                 risk_basis=EXCLUDED.risk_basis, trade_style=EXCLUDED.trade_style, updated_at=now()""",
            (user["id"], acct, body.risk_pct, body.reward_rr, body.max_dd_day, body.max_dd_week,
             body.max_dd_month, Json(hours), (body.trading_tz or "UTC"),
             (body.risk_basis or "equity"), body.trade_style))
        conn.commit()
    return {"ok": True, "account": acct}


@app.delete("/api/risk-settings")
def delete_risk_settings(account: str = "", user: dict = Depends(current_user)):
    """Remove a scope's settings. account='' clears the profile default; an account
    number removes just that override (it then inherits the profile again)."""
    with db.connect() as conn:
        conn.execute("DELETE FROM risk_settings WHERE user_id = %s AND account = %s",
                     (user["id"], (account or "").strip()))
        conn.commit()
    return {"ok": True}



# ── analysis agents (flow graphs the chat agent can call as tools) ─────────────
class AgentCreate(BaseModel):
    name: constr(min_length=1, max_length=80)
    description: str = ""


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None                 # draft | active | paused
    flow: dict | None = None                  # {nodes, edges}
    is_public: bool | None = None             # admin only: let every user run it


class AgentTestRun(BaseModel):
    request: str = ""
    provider: str = ""
    model: str = ""
    # What the trigger declares this agent needs. A test that could not supply
    # them would fail on the required check before reaching a single node, which
    # is a correct refusal and a useless way to try a flow out.
    variables: dict = {}


def _ser_agent(r, full=False, me=None):
    out = {"id": str(r["id"]), "name": r["name"], "description": r["description"],
           "status": r["status"], "created_at": r["created_at"], "updated_at": r["updated_at"],
           "public": bool(r.get("is_public")), "system": bool(r.get("is_system"))}
    if me is not None and "user_id" in r:
        out["mine"] = str(r["user_id"]) == str(me)      # only the owner may edit it
    if full:
        out["flow"] = r["flow"] or {"nodes": [], "edges": []}
    else:
        out["nodes"] = len((r["flow"] or {}).get("nodes", []))
    return out


@app.get("/api/analysis-agents")
def list_analysis_agents(user: dict = Depends(current_user)):
    """The user's own agents, plus any an admin has made public. A public agent
    is runnable by everyone but editable only by its owner."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, user_id, name, description, status, flow, is_public, is_system, "
            "       created_at, updated_at "
            "FROM analysis_agents WHERE user_id = %s OR is_public "
            "ORDER BY (user_id = %s) DESC, updated_at DESC",
            (user["id"], user["id"]),
        ).fetchall()
    return [_ser_agent(r, me=user["id"]) for r in rows]


@app.post("/api/analysis-agents")
def create_analysis_agent(body: AgentCreate, user: dict = Depends(current_user)):
    with db.connect() as conn:
        row = conn.execute(
            "INSERT INTO analysis_agents (user_id, name, description) VALUES (%s, %s, %s) "
            "RETURNING id, name, description, status, flow, created_at, updated_at",
            (user["id"], body.name.strip(), (body.description or "").strip()),
        ).fetchone()
        conn.commit()
    return _ser_agent(row, full=True)


@app.get("/api/analysis-agents/{agent_id}")
def get_analysis_agent(agent_id: str, user: dict = Depends(current_user)):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id, user_id, name, description, status, flow, is_public, is_system, "
            "       created_at, updated_at "
            "FROM analysis_agents WHERE id = %s AND (user_id = %s OR is_public)",
            (agent_id, user["id"]),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Agent not found")
    return _ser_agent(row, full=True, me=user["id"])


@app.put("/api/analysis-agents/{agent_id}")
def update_analysis_agent(agent_id: str, body: AgentUpdate, user: dict = Depends(current_user)):
    from psycopg.types.json import Json
    sets, params = [], []
    if body.name is not None:
        sets.append("name = %s"); params.append(body.name.strip()[:80] or "Untitled")
    if body.description is not None:
        sets.append("description = %s"); params.append(body.description.strip())
    if body.status is not None:
        if body.status not in ("draft", "active", "paused"):
            raise HTTPException(400, "status must be draft | active | paused")
        sets.append("status = %s"); params.append(body.status)
    if body.flow is not None:
        sets.append("flow = %s"); params.append(Json(body.flow))
    if body.is_public is not None:
        # making an agent public is an admin decision — and only over one they own
        import admin_api
        if not admin_api._is_admin(user["email"]):
            raise HTTPException(403, "Only an admin can make an agent public.")
        sets.append("is_public = %s"); params.append(bool(body.is_public))
    if not sets:
        return {"ok": True}
    sets.append("updated_at = now()")
    params += [agent_id, user["id"]]
    with db.connect() as conn:
        row = conn.execute(
            f"UPDATE analysis_agents SET {', '.join(sets)} WHERE id = %s AND user_id = %s "
            "RETURNING id, user_id, name, description, status, flow, is_public, is_system, "
            "          created_at, updated_at",
            params,
        ).fetchone()
        conn.commit()
    if not row:
        raise HTTPException(404, "Agent not found")
    return _ser_agent(row, full=True, me=user["id"])


@app.delete("/api/analysis-agents/{agent_id}")
def delete_analysis_agent(agent_id: str, user: dict = Depends(current_user)):
    """System agents cannot be deleted.

    They are part of the app rather than something the user built: the watch
    list and the risk gate are seeded, re-seeded on upgrade, and other features
    call them by a fixed id. Deleting one does not remove a feature, it breaks
    it — and it would come back on the next boot anyway, which is a worse
    experience than being told no."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT name, is_system FROM analysis_agents WHERE id = %s AND user_id = %s",
            (agent_id, user["id"])).fetchone()
        if row and row["is_system"]:
            raise HTTPException(
                409, f"“{row['name']}” is a system agent and cannot be deleted. "
                     f"You can disable it, or edit what it does.")
        conn.execute("DELETE FROM analysis_agents WHERE id = %s AND user_id = %s",
                     (agent_id, user["id"]))
        conn.commit()
    return {"ok": True}


@app.get("/api/analysis-agents/{agent_id}/export")
def export_analysis_agent(agent_id: str, user: dict = Depends(current_user)):
    """A portable JSON snapshot of an agent (name + description + flow) that can be
    re-imported here or shared."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT name, description, flow FROM analysis_agents WHERE id = %s AND user_id = %s",
            (agent_id, user["id"]),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Agent not found")
    return {"arrissa_analysis_agent": 1, "name": row["name"],
            "description": row["description"], "flow": row["flow"] or {"nodes": [], "edges": []}}


class AgentImport(BaseModel):
    name: str | None = None
    description: str | None = None
    flow: dict = {}


@app.post("/api/analysis-agents/import")
def import_analysis_agent(body: AgentImport, user: dict = Depends(current_user)):
    """Create a new agent from an exported JSON payload. The flow must be a
    {nodes, edges} graph. Imported agents always start as drafts."""
    flow = body.flow or {}
    if not isinstance(flow.get("nodes"), list) or not isinstance(flow.get("edges"), list):
        raise HTTPException(400, "invalid flow — expected {nodes: [], edges: []}")
    from psycopg.types.json import Json
    name = (body.name or "Imported agent").strip()[:80] or "Imported agent"
    with db.connect() as conn:
        row = conn.execute(
            "INSERT INTO analysis_agents (user_id, name, description, status, flow) "
            "VALUES (%s, %s, %s, 'draft', %s) "
            "RETURNING id, name, description, status, flow, created_at, updated_at",
            (user["id"], name, (body.description or "").strip(), Json(flow)),
        ).fetchone()
        conn.commit()
    return _ser_agent(row, full=True)


class AgentBuild(BaseModel):
    instruction: str = ""
    provider: str = ""
    model: str = ""


@app.post("/api/analysis-agents/{agent_id}/build")
def build_analysis_agent(agent_id: str, body: AgentBuild, user: dict = Depends(current_user)):
    """Author/modify a flow from a plain-language brief using the user's AI model.
    Returns the new flow (nodes+edges) plus a suggested name/description. The
    caller (flow page) applies it to the canvas; autosave persists it."""
    if not (body.instruction or "").strip():
        raise HTTPException(400, "Describe the agent you want to build.")
    with db.connect() as conn:
        row = conn.execute(
            "SELECT name, flow FROM analysis_agents WHERE id = %s AND user_id = %s",
            (agent_id, user["id"]),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Agent not found")

    # Building is a model call, so it is gated and metered like every other one.
    _require_credits(user["id"], "Subscribe to build agents with AI.")

    alias = body.model or _user_analysis_model(user["id"]) or billing.DEFAULT_MODEL
    provider, model, key = ai_keys.resolve(user["id"], alias)

    import analysis_agent
    ctx = {"_model_label": (alias if ":" not in (alias or "") else None),
           "provider": provider, "api_key": key, "model": model, "user_id": user["id"]}
    res = analysis_agent.build_flow(body.instruction, row["flow"] or {}, ctx)
    # Charged on the TOKENS THE CALL ACTUALLY BURNED, before the error check: a
    # refusal or an unparseable reply still cost us the round trip, and billing
    # only the successful half would be us absorbing the rest.
    charged = _meter(user["id"], res, "agent-build", agent_id)
    if res.get("error"):
        raise HTTPException(400, res["error"])
    return {"flow": res["flow"], "name": res.get("name"),
            "description": res.get("description"), "note": res.get("note"),
            "used_model": (alias if key else None),
            "usage": res.get("usage"), "cost_usd": res.get("cost_usd"),
            "credits_charged": charged}


class CronSuggest(BaseModel):
    brief: str = ""
    model: str = ""


@app.post("/api/cron/suggest")
def suggest_cron(body: CronSuggest, user: dict = Depends(current_user)):
    """Plain language in ('every weekday at 7am, before London opens'), a checked
    cron expression out ('0 7 * * 1-5'). Used by the Trigger on Intervals node.
    A model call, so it is charged on the tokens it burns like anything else."""
    if not (body.brief or "").strip():
        raise HTTPException(400, "Describe when you want it to run.")
    _require_credits(user["id"], "Subscribe to write schedules with AI.")

    alias = body.model or _user_analysis_model(user["id"]) or billing.DEFAULT_MODEL
    provider, model, key = ai_keys.resolve(user["id"], alias)

    import analysis_agent
    ctx = {"_model_label": (alias if ":" not in (alias or "") else None),
           "provider": provider, "api_key": key, "model": model, "user_id": user["id"]}
    res = analysis_agent.suggest_cron(body.brief, ctx)
    charged = _meter(user["id"], res, "cron-suggest")
    if res.get("error"):
        raise HTTPException(400, res["error"])
    return {"cron": res["cron"], "explanation": res.get("explanation"),
            "reads_as": res.get("reads_as"), "used_model": (alias if key else None),
            "usage": res.get("usage"), "cost_usd": res.get("cost_usd"),
            "credits_charged": charged}


@app.get("/api/analysis-agents/{agent_id}/schedule")
def get_agent_schedule(agent_id: str, user: dict = Depends(current_user)):
    """The agent's own schedule as its flow states it, plus when it last ran and
    when it is next due. null when the flow has no interval trigger."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT status, flow FROM analysis_agents WHERE id = %s AND user_id = %s",
            (agent_id, user["id"])).fetchone()
    if not row:
        raise HTTPException(404, "Agent not found")
    import agent_schedule
    st = agent_schedule.status(agent_id, row["flow"])
    if st:
        # A schedule on a draft or paused agent is written down but not running —
        # say so here rather than let it look live and silently never fire.
        st["running"] = row["status"] == "active"
    return {"schedule": st}


@app.post("/api/analysis-agents/{agent_id}/test-run")
def test_run_analysis_agent(agent_id: str, body: AgentTestRun, user: dict = Depends(current_user)):
    """Run a flow once, from the canvas, so the user can see what it produces.
    Uses the given provider/model (or the first selected AI model) for the
    reasoning nodes."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT name, flow FROM analysis_agents WHERE id = %s AND user_id = %s",
            (agent_id, user["id"]),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Agent not found")

    # Billing gate — active subscription + positive balance (metered after by real cost).
    _require_credits(user["id"], "Subscribe to run analysis agents.")

    # The model the user picked in chat (branded alias) — Test matches real chat
    # behaviour. Resolved to the real provider/model + the app's own key.
    alias = body.model or _user_analysis_model(user["id"]) or billing.DEFAULT_MODEL
    provider, model, key = ai_keys.resolve(user["id"], alias)

    import analysis_agent, user_session
    ctx = {"_model_label": (alias if ":" not in (alias or "") else None),
           "provider": provider, "api_key": key, "model": model,
           "user_id": user["id"], "agent_tools": {}, "last_user": body.request}
    # Bind the user's Exness session + active account so account-dependent nodes
    # (market-data, hmr, risk-management) work in Test exactly as they do in chat.
    with user_session.as_user(user["id"]):
        res = analysis_agent.run_flow(row["flow"], body.request, ctx, agent_id=agent_id,
                                      source="test", variables=body.variables)
    # Meter the real token cost of this run (a 5s cache hit already priced at 20%).
    _meter(user["id"], res, "analysis-cache" if res.get("cached") else "analysis", agent_id)
    return _strip_usage(
        {"agent": row["name"], "used_model": (alias if key else None),
         "cached": bool(res.get("cached")),
         "response": res.get("response"), "trace": res.get("trace"),
         "error": res.get("error"), "llm_error": res.get("llm_error"),
         "usage": res.get("usage"), "usage_model": res.get("usage_model"),
         "cost_usd": res.get("cost_usd")}, _sees_usage(user))


@app.post("/api/analysis-agents/{agent_id}/test-run/stream")
def test_run_analysis_agent_stream(agent_id: str, body: AgentTestRun,
                                   user: dict = Depends(current_user)):
    """The same test run, narrated as it happens.

    A flow talks to a model once per node and an Octo body calls a dozen tools,
    so a real run is one to three minutes. Answering only at the end meant a
    spinner for all of it — a working run and a wedged one looked the same — and
    when the proxy gave up first the reply was its HTML error page, which the
    client tried to parse as JSON ("Unexpected token '<'"). Streaming fixes both:
    the caller sees each node start and finish, and bytes flow from the first
    second, so no proxy in the path decides the request has stalled.

    The run itself is blocking, so it goes on its own thread and posts events to
    a queue this generator drains. The final `done` event carries exactly what
    the non-streaming endpoint returns, so the client renders one shape either way.
    """
    from fastapi.responses import StreamingResponse
    import queue as _queue, threading as _threading

    with db.connect() as conn:
        row = conn.execute(
            "SELECT name, flow FROM analysis_agents WHERE id = %s AND user_id = %s",
            (agent_id, user["id"])).fetchone()
    if not row:
        raise HTTPException(404, "Agent not found")
    _require_credits(user["id"], "Subscribe to run analysis agents.")

    alias = body.model or _user_analysis_model(user["id"]) or billing.DEFAULT_MODEL
    provider, model, key = ai_keys.resolve(user["id"], alias)
    show_usage = _sees_usage(user)          # resolved on THIS thread, while the request is live

    events: "_queue.Queue" = _queue.Queue()

    def emit(ev: dict):
        """Per-step tokens and cost are the same operator numbers as the total, so
        they leave by the same door. Stripped here rather than in the engine: the
        engine should not have to know who is watching."""
        if not show_usage:
            ev.pop("usage", None)
            ev.pop("cost", None)
        events.put(ev)

    def work():
        import analysis_agent, user_session
        ctx = {"_model_label": (alias if ":" not in (alias or "") else None),
               "provider": provider, "api_key": key, "model": model,
               "user_id": user["id"], "agent_tools": {}, "last_user": body.request,
               "_progress": emit}
        try:
            with user_session.as_user(user["id"]):
                res = analysis_agent.run_flow(row["flow"], body.request, ctx,
                                              variables=body.variables,
                                              agent_id=agent_id, source="test")
            _meter(user["id"], res, "analysis-cache" if res.get("cached") else "analysis", agent_id)
            events.put({"type": "done", "result": _strip_usage(
                {"agent": row["name"], "used_model": (alias if key else None),
                 "cached": bool(res.get("cached")),
                 "response": res.get("response"), "trace": res.get("trace"),
                 "error": res.get("error"), "llm_error": res.get("llm_error"),
                 "usage": res.get("usage"), "usage_model": res.get("usage_model"),
                 "cost_usd": res.get("cost_usd")}, show_usage)})
        except Exception as e:
            # The failure IS the news here — say where it happened rather than
            # letting the stream stop and look like a hang.
            events.put({"type": "failed", "message": f"{type(e).__name__}: {e}"})
        finally:
            events.put(None)

    _threading.Thread(target=work, daemon=True).start()

    def gen():
        yield "retry: 10000\n\n"          # flush headers immediately; nothing has run yet
        while True:
            try:
                ev = events.get(timeout=10)
            except _queue.Empty:
                yield ": still working\n\n"     # a comment keeps the connection warm
                continue
            if ev is None:
                break
            yield f"data: {json.dumps(ev, default=str)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",        # nginx would otherwise hold the whole stream
    })


@app.get("/api/analysis-agents/{agent_id}/runs")
def list_analysis_runs(agent_id: str, user: dict = Depends(current_user), limit: int = 25):
    """Recent executions of this agent (newest first) — summaries for the history list."""
    with db.connect() as conn:
        if not conn.execute("SELECT 1 FROM analysis_agents WHERE id = %s AND user_id = %s",
                            (agent_id, user["id"])).fetchone():
            raise HTTPException(404, "Agent not found")
        rows = conn.execute(
            """SELECT id, request, response, steps, status, error, source, created_at,
                      tokens_in, tokens_out, tokens_cache_hit, llm_calls, usage_model, cost_usd
               FROM analysis_runs WHERE agent_id = %s ORDER BY created_at DESC LIMIT %s""",
            (agent_id, max(1, min(int(limit or 25), 100)))).fetchall()
    ok = _sees_usage(user)
    return {"runs": [_strip_usage(dict(r), ok) for r in rows]}


# Token counts, call counts, cost and the model behind a run are OPERATOR
# numbers. They describe how the product is built — how many model calls one
# analysis takes, what it costs to serve — and none of that is the user's to
# read. Hiding the row in the page is not enough on its own: the response still
# crosses the wire, and anyone who opens the network tab has it. So it is
# removed here, and the page's own check is the second lock rather than the only
# one.
_USAGE_FIELDS = ("tokens_in", "tokens_out", "tokens_cache_hit", "llm_calls",
                 "usage_model", "cost_usd", "usage", "used_model", "provider")


def _sees_usage(user: dict) -> bool:
    """OWNER, not admin. `admin` is a cloud console flag and is false by design in
    Community — gating on it would have hidden these numbers from someone running
    the software on their own machine, with their own keys, paying their own model
    bills. Owner covers both: the Community owner and the cloud operator.

    Resolved ONCE per request. It reads the billing state, which writes a row on
    first touch, so asking it per result row turned one history page into 25 round
    trips."""
    return bool(edition.capabilities(user["email"], billing.get_state(user["id"]))["owner"])


def _strip_usage(row: dict, allowed: bool) -> dict:
    return row if allowed else {k: v for k, v in row.items() if k not in _USAGE_FIELDS}


@app.get("/api/analysis-agents/{agent_id}/runs/{run_id}")
def get_analysis_run(agent_id: str, run_id: str, user: dict = Depends(current_user)):
    """One execution's FULL trace — every node's reasoning, result and opinion."""
    with db.connect() as conn:
        row = conn.execute(
            """SELECT r.* FROM analysis_runs r JOIN analysis_agents a ON a.id = r.agent_id
               WHERE r.id = %s AND r.agent_id = %s AND a.user_id = %s""",
            (run_id, agent_id, user["id"])).fetchone()
    if not row:
        raise HTTPException(404, "Run not found")
    return _strip_usage(dict(row), _sees_usage(user))


# ── position actions from the live panel (bearer auth, explicit account) ───────
class PositionAction(BaseModel):
    account: int
    position_id: str | None = None      # None ⇒ act on ALL open positions


@app.post("/api/positions/close")
def close_positions(body: PositionAction, user: dict = Depends(current_user)):
    """Close one position (position_id) or ALL of the account's open positions."""
    import user_session
    try:
        with user_session.as_user(user["id"]):     # act on THIS user's own account only
            res = _trading_api.trader(body.account).close(position_id=body.position_id)
        return {"ok": True, "result": res}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/positions/break-even")
def break_even_positions(body: PositionAction, user: dict = Depends(current_user)):
    """Move SL to entry on one position, or all of the account's winners."""
    import user_session
    try:
        with user_session.as_user(user["id"]):
            res = _trading_api.trader(body.account).break_even(position_id=body.position_id)
        return {"ok": True, "result": res}
    except Exception as e:
        raise HTTPException(400, str(e))


class LevelChange(BaseModel):
    account: int
    position_id: str
    sl: float | None = None
    tp: float | None = None


@app.post("/api/positions/levels")
def set_position_levels(body: LevelChange, user: dict = Depends(current_user)):
    """Move a live position's stop or target to an exact price.

    Reached by DRAGGING the SL or TP line on the chart, which is the most direct
    expression of the intent there is: the line is where the level will be, and
    letting go is the instruction. Only the level that moved is sent — a null
    means "leave that one alone", not "clear it"."""
    import user_session
    if body.sl is None and body.tp is None:
        raise HTTPException(400, "nothing to change")
    try:
        with user_session.as_user(user["id"]):
            t = _trading_api.trader(body.account)
            # The broker's modify takes BOTH levels and writes both, so the one
            # that did not move has to be sent back unchanged. Passing None or 0
            # for it would clear it — dragging a target would silently remove the
            # stop, which is the worst possible outcome for this gesture.
            cur = next((p for p in (t.positions() or [])
                        if str(p.get("position_id")) == str(body.position_id)), None)
            if not cur:
                raise HTTPException(404, "that position is no longer open")
            sl = body.sl if body.sl is not None else (cur.get("sl") or 0)
            tp = body.tp if body.tp is not None else (cur.get("tp") or 0)
            res = t.modify_position(body.position_id, sl=sl, tp=tp)
        return {"ok": True, "result": res, "sl": sl, "tp": tp}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))


# ── live positions WebSocket (real-time, tick-driven) ──────────────────────────
_LIVE_STREAMS = {}   # (user_id, account) -> stop Event; ensures one poller per account


@app.websocket("/ws/positions/{account}")
async def ws_positions(websocket: WebSocket, account: int):
    # browsers can't set headers on a WS handshake → auth via ?token=
    token = websocket.query_params.get("token", "")
    try:
        claims = auth.decode_token(token)
    except Exception:
        await websocket.close(code=4401)
        return
    uid = claims.get("sub")
    await websocket.accept()
    loop = asyncio.get_running_loop()
    stop = threading.Event()

    # One poller per (user, account): a second connection (another tab, a reconnect)
    # stops the previous one, so we never double the API request rate — critical for
    # TradeLocker's tight per-route limits (GET_POSITIONS ~1/s).
    key = (uid, account)
    prev = _LIVE_STREAMS.get(key)
    if prev is not None:
        prev.set()
    _LIVE_STREAMS[key] = stop

    def push(snapshot):
        try:
            asyncio.run_coroutine_threadsafe(websocket.send_json(snapshot), loop)
        except Exception:
            stop.set()

    def worker():
        import user_session
        try:
            with user_session.as_user(uid):     # stream THIS user's own account only
                _trading_api.trader(account).live_stream(push, stop)
        except Exception as e:
            try:
                asyncio.run_coroutine_threadsafe(websocket.send_json({"error": str(e)}), loop)
            except Exception:
                pass

    threading.Thread(target=worker, daemon=True).start()
    try:
        while True:
            await websocket.receive_text()   # detect client disconnect
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        stop.set()
        if _LIVE_STREAMS.get(key) is stop:
            _LIVE_STREAMS.pop(key, None)


# ── chart refresh for the chat (bearer auth, unlike the /api/v1 key routes) ────
@app.get("/api/market/chart")
def chat_chart(symbol: str, timeframe: str = "M15", count: int = 150,
               account: int = None, user: dict = Depends(current_user)):
    """Re-read a chart the chat is already showing. Called when a chart scrolls
    back into view, so it catches up on whatever moved while it was paused."""
    import market
    import user_session
    try:
        with user_session.as_user(user["id"]):     # draw only THIS user's trades
            return market.chart(symbol, timeframe=timeframe, count=count, account=account)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── live tick WebSocket (drives the chat's chart) ──────────────────────────────
@app.websocket("/ws/ticks/{symbol}")
async def ws_ticks(websocket: WebSocket, symbol: str):
    """Forward Exness's tick stream for one instrument to the browser, so a chart
    can update its live candle without polling. Auth via ?token= (browsers can't
    set headers on a WS handshake)."""
    token = websocket.query_params.get("token", "")
    try:
        claims = auth.decode_token(token)
    except Exception:
        await websocket.close(code=4401)
        return
    uid = claims.get("sub")
    await websocket.accept()

    import market
    import user_session
    try:
        with user_session.as_user(uid):          # resolve against THIS user's instruments
            sym = market.resolve_symbol(symbol)
    except Exception as e:
        await websocket.send_json({"error": str(e)})
        await websocket.close()
        return

    loop = asyncio.get_running_loop()
    stop = threading.Event()

    def on_tick(d):
        try:
            asyncio.run_coroutine_threadsafe(websocket.send_json(d), loop)
        except Exception:
            stop.set()

    def worker():
        tokens = user_session.bind(uid)          # this user's session + active broker/account
        try:
            # broker-agnostic: ExnessTrader streams off its tick WS, TradeLocker
            # polls quotes — both emit {symbol, bid, ask, ts}.
            _trading_api.trader().stream_ticks([sym], on_tick, stop)
        except Exception as e:
            try:
                asyncio.run_coroutine_threadsafe(websocket.send_json({"error": str(e)}), loop)
            except Exception:
                pass
        finally:
            user_session.reset(tokens)

    threading.Thread(target=worker, daemon=True).start()
    try:
        while True:
            await websocket.receive_text()   # detect client disconnect
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        stop.set()


@app.get("/api/health")
def health():
    return {"ok": True}
