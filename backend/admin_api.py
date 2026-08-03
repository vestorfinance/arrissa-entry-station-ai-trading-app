"""
Admin backend — owner-only console API (mounted at /api/admin).

Self-contained auth (its own bearer → user dependency) so it doesn't import from
main.py (which imports this router). Access = one of the hardcoded super-owners
(DEMO_ALLOWED) OR a row in the `admins` table. Every mutation is written to
`admin_audit_log`. See ADMIN-BACKEND-PLAN.md.
"""
import threading
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pydantic import BaseModel

import auth
import db
import billing
import registry

router = APIRouter(prefix="/api/admin")

# Unremovable super-owners. The single answer to "is this person an owner" —
# core asks `_is_admin`, which also honours the delegated `admins` table.
SUPER_OWNERS = {"davidrichchild@gmail.com", "egracemedia@gmail.com"}


# ── auth ─────────────────────────────────────────────────────────────────────────
from deps import current_user as _current_user   # noqa: E402


def _is_admin(email):
    import edition
    if edition.everyone_is_owner():
        return True
    if email in SUPER_OWNERS:
        return True
    try:
        with db.connect() as conn:
            return bool(conn.execute("SELECT 1 FROM admins WHERE email = %s", (email,)).fetchone())
    except Exception:
        return False


def require_console(user=Depends(_current_user)):
    """The admin CONSOLE — managing other people. It does not exist on a
    single-user instance, so its routes are not merely hidden, they are gone."""
    import edition
    if not edition.has_admin_console():
        raise HTTPException(404, "This is a single-user installation; it has no admin area.")
    return require_admin(user)


def require_admin(user=Depends(_current_user)):
    if not _is_admin(user["email"]):
        raise HTTPException(403, "Admin only.")
    return user


def audit(email, action, target_type=None, target_id=None, meta=None):
    from psycopg.types.json import Json
    try:
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO admin_audit_log (admin_email, action, target_type, target_id, meta) "
                "VALUES (%s, %s, %s, %s, %s)",
                (email, action, target_type, target_id, Json(meta or {})))
            conn.commit()
    except Exception:
        pass


# ── overview / health ─────────────────────────────────────────────────────────────
@router.get("/overview")
def overview(user=Depends(require_console)):
    with db.connect() as conn:
        one = lambda s: conn.execute(s).fetchone()
        users_total = one("SELECT count(*) c FROM users")["c"]
        new7 = one("SELECT count(*) c FROM users WHERE created_at > now()-interval '7 days'")["c"]
        new30 = one("SELECT count(*) c FROM users WHERE created_at > now()-interval '30 days'")["c"]
        active7 = one("SELECT count(DISTINCT user_id) c FROM chats WHERE updated_at > now()-interval '7 days'")["c"]

        by_plan, mrr, subs_active = {}, 0.0, 0
        for r in conn.execute("SELECT plan, interval, count(*) c FROM user_billing "
                              "WHERE status='active' AND plan IS NOT NULL GROUP BY plan, interval").fetchall():
            subs_active += r["c"]
            by_plan[r["plan"]] = by_plan.get(r["plan"], 0) + r["c"]
            p = billing.PLANS.get(r["plan"])
            if p:
                mrr += (p["price_zar_annual"] if r["interval"] == "annual" else p["price_zar"]) * r["c"]

        spent_today = -one("SELECT COALESCE(sum(delta),0) s FROM credit_ledger "
                           "WHERE delta<0 AND created_at::date = now()::date")["s"]
        spent_mtd = -one("SELECT COALESCE(sum(delta),0) s FROM credit_ledger "
                         "WHERE delta<0 AND created_at >= date_trunc('month', now())")["s"]
        rev_mtd = one("SELECT COALESCE(sum(amount_zar),0) s FROM billing_transactions "
                      "WHERE status='success' AND completed_at >= date_trunc('month', now())")["s"]
        txns = {r["status"]: r["c"] for r in
                conn.execute("SELECT status, count(*) c FROM billing_transactions GROUP BY status").fetchall()}
        try:
            pending_signups = one("SELECT count(*) c FROM signups WHERE NOT verified")["c"]
        except Exception:
            pending_signups = 0

    spent_mtd = int(spent_mtd)
    return {
        "users": {"total": users_total, "new_7d": new7, "new_30d": new30, "active_7d": active7,
                  "pending_signups": pending_signups},
        "subscriptions": {"active": subs_active, "by_plan": by_plan, "mrr_zar": round(mrr, 2)},
        "credits": {"spent_today": int(spent_today), "spent_mtd": spent_mtd},
        "cost": {"llm_mtd_usd": round(spent_mtd * billing.CREDIT_USD, 2)},
        "revenue": {"mtd_zar": float(rev_mtd)},
        "transactions": txns,
    }


@router.get("/system/health")
def system_health(user=Depends(require_console)):
    """True freshness = when each fetcher LAST PULLED (its heartbeat), not when the
    newest row happened to change. The fetchers track last_fetch_at in-process (same
    uvicorn), so we read that directly; truth/hmr stamp a per-poll timestamp we read."""
    now = datetime.now(timezone.utc)

    def age_of(v):
        if not v:
            return None
        if isinstance(v, str):
            try:
                v = datetime.fromisoformat(v.replace("Z", "+00:00"))
            except Exception:
                return None
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return (now - v).total_seconds()

    out = []
    # Fetchers exposing a real last_fetch_at heartbeat + their own health verdict.
    # Module-provided ones are asked for by name, so the panel simply omits a
    # source whose module is not installed instead of failing to render at all.
    import registry
    sources = []
    for name in ("sentiment", "fedwatch", "calendar", "news", "bonds", "truth"):
        mod = registry.get(name)
        if mod is not None:
            sources.append((name, mod))
    for name, mod in sources:
        try:
            st = mod.status()
            # Take the heartbeat from whichever key the module offers. They did
            # not all use the same one — the calendar reported `last_listing_at`
            # and was rendered as "no pull" for a fetcher that had never missed
            # one. Reading the module's own word beats making it wrong.
            last = next((st[k] for k in ("last_fetch_at", "last_poll_at", "last_listing_at",
                                         "last_run_at", "last_seen_at") if st.get(k)), None)
            a = st.get("age_seconds")
            if a is None:
                a = age_of(last)
            # `running` absent is not `running: False`. A module that answers at
            # all is answering FROM its own process, so silence on that key means
            # it never said, not that it is dead.
            running = st.get("running", True)
            out.append({"source": name, "running": running,
                        "fresh": bool(st.get("healthy", a is not None)),
                        "age_seconds": int(a) if a is not None else None,
                        "last": last, "error": st.get("last_error")})
        except AttributeError:
            # No status() at all — as Truth Social had. It is installed and its
            # thread may be running perfectly; what it cannot do is say so.
            out.append({"source": name, "running": None, "fresh": False,
                        "age_seconds": None, "last": None,
                        "error": "this module does not report a heartbeat"})
        except Exception as e:
            out.append({"source": name, "running": False, "fresh": False, "age_seconds": None, "last": None, "error": str(e)[:120]})

    # hmr writes a per-poll timestamp, so max() IS the heartbeat. Only asked for
    # when the module is installed — its table does not exist otherwise, and a
    # panel row saying a capability you never bought is "not fresh" is noise.
    if registry.has("hmr"):
        with db.connect() as conn:
            try:
                t = conn.execute("SELECT max(fetched_at) t FROM hmr_meta").fetchone()["t"]
            except Exception:
                t = None
            a = age_of(t)
            out.append({"source": "hmr", "running": t is not None,
                        "fresh": bool(a is not None and a <= 3 * 3600),
                        "age_seconds": int(a) if a is not None else None, "last": t, "error": None})
    return {"sources": out, "db": True}


# ── users ──────────────────────────────────────────────────────────────────────────
@router.get("/users")
def list_users(user=Depends(require_console), q: str = "", plan: str = "",
               limit: int = Query(50, le=200), offset: int = 0):
    where, params = [], []
    if q:
        where.append("(u.email ILIKE %s OR u.first_name ILIKE %s OR u.last_name ILIKE %s)")
        params += [f"%{q}%"] * 3
    if plan:
        where.append("b.plan = %s")
        params.append(plan)
    wsql = ("WHERE " + " AND ".join(where)) if where else ""
    with db.connect() as conn:
        rows = conn.execute(
            f"""SELECT u.id, u.email, u.first_name, u.last_name, u.created_at, u.status,
                       b.plan, b.status AS bstatus,
                       COALESCE((SELECT sum(delta) FROM credit_ledger WHERE user_id = u.id), 0) AS credits
                FROM users u LEFT JOIN user_billing b ON b.user_id = u.id
                {wsql} ORDER BY u.created_at DESC LIMIT %s OFFSET %s""",
            (*params, limit, offset)).fetchall()
        total = conn.execute(
            f"SELECT count(*) c FROM users u LEFT JOIN user_billing b ON b.user_id = u.id {wsql}",
            params).fetchone()["c"]
    return {"users": [{**dict(r), "id": str(r["id"]), "credits": int(r["credits"])} for r in rows],
            "total": total}


@router.get("/users/{uid}")
def get_user(uid: str, user=Depends(require_console)):
    with db.connect() as conn:
        u = conn.execute("SELECT id, email, first_name, last_name, phone, country, created_at, status "
                         "FROM users WHERE id = %s", (uid,)).fetchone()
        if not u:
            raise HTTPException(404, "User not found")
        ledger = conn.execute("SELECT delta, reason, ref, created_at FROM credit_ledger "
                              "WHERE user_id = %s ORDER BY created_at DESC LIMIT 50", (uid,)).fetchall()
        chats = conn.execute("SELECT count(*) c FROM chats WHERE user_id = %s", (uid,)).fetchone()["c"]
        runs = conn.execute("SELECT count(*) c, COALESCE(sum(cost_usd),0) cost FROM analysis_runs "
                            "WHERE user_id = %s", (uid,)).fetchone()
        # The Exness table belongs to the Exness module; ask only if it is installed.
        exness = None
        if registry.has("broker:exness"):
            try:
                exness = conn.execute("SELECT exness_email, connected_at FROM exness_user_sessions "
                                      "WHERE user_id = %s", (uid,)).fetchone()
            except Exception:
                exness = None
    return {
        "user": {**dict(u), "id": str(u["id"])},
        "billing": billing.get_state(uid),
        "ledger": [dict(l) for l in ledger],
        "usage": {"chats": chats, "runs": runs["c"], "analysis_cost_usd": float(runs["cost"] or 0)},
        "exness": dict(exness) if exness else None,
    }


class CreditsBody(BaseModel):
    amount: int         # + grant, - deduct
    note: str = ""


@router.post("/users/{uid}/credits")
def adjust_credits(uid: str, body: CreditsBody, user=Depends(require_console)):
    if body.amount == 0:
        raise HTTPException(400, "amount must be non-zero")
    with db.connect() as conn:
        if not conn.execute("SELECT 1 FROM users WHERE id = %s", (uid,)).fetchone():
            raise HTTPException(404, "User not found")
        conn.execute("INSERT INTO credit_ledger (user_id, delta, reason, ref) VALUES (%s, %s, 'admin', %s)",
                     (uid, int(body.amount), (body.note or "admin adjustment")[:200]))
        conn.commit()
    audit(user["email"], "credits.adjust", "user", uid, {"amount": body.amount, "note": body.note})
    return {"ok": True, "balance": billing.balance(uid)}


class PlanBody(BaseModel):
    plan: str = ""          # trader|pro|max|elite ; "" = cancel/downgrade
    interval: str = "monthly"


@router.post("/users/{uid}/plan")
def set_plan(uid: str, body: PlanBody, user=Depends(require_console)):
    with db.connect() as conn:
        if not conn.execute("SELECT 1 FROM users WHERE id = %s", (uid,)).fetchone():
            raise HTTPException(404, "User not found")
    if not body.plan:
        billing.cancel(uid)
        audit(user["email"], "plan.set", "user", uid, {"plan": None})
        return {"ok": True, "billing": billing.get_state(uid)}
    if body.plan not in billing.PLANS:
        raise HTTPException(400, "unknown plan")
    p = billing.PLANS[body.plan]
    renews = datetime.now(timezone.utc) + (timedelta(days=365) if body.interval == "annual" else timedelta(days=30))
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO user_billing (user_id, plan, status, interval, renews_at, started_at, updated_at)
               VALUES (%s, %s, 'active', %s, %s, now(), now())
               ON CONFLICT (user_id) DO UPDATE SET plan=EXCLUDED.plan, status='active',
                 interval=EXCLUDED.interval, renews_at=EXCLUDED.renews_at, updated_at=now()""",
            (uid, body.plan, body.interval, renews))
        cur = billing.balance(uid, conn)
        conn.execute("INSERT INTO credit_ledger (user_id, delta, reason, ref) VALUES (%s, %s, 'admin', %s)",
                     (uid, p["credits"] - cur, "admin comp plan"))
        conn.commit()
    audit(user["email"], "plan.set", "user", uid, {"plan": body.plan, "interval": body.interval})
    return {"ok": True, "billing": billing.get_state(uid)}


class SuspendBody(BaseModel):
    suspended: bool


@router.post("/users/{uid}/suspend")
def suspend_user(uid: str, body: SuspendBody, user=Depends(require_console)):
    with db.connect() as conn:
        row = conn.execute("UPDATE users SET status = %s WHERE id = %s RETURNING email",
                           ("suspended" if body.suspended else "active", uid)).fetchone()
        conn.commit()
    if not row:
        raise HTTPException(404, "User not found")
    audit(user["email"], "user.suspend" if body.suspended else "user.unsuspend", "user", uid, {})
    return {"ok": True, "status": "suspended" if body.suspended else "active"}


# ── audit ──────────────────────────────────────────────────────────────────────────
@router.get("/audit")
def audit_log(user=Depends(require_console), limit: int = Query(100, le=500)):
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT admin_email, action, target_type, target_id, meta, created_at "
            "FROM admin_audit_log ORDER BY created_at DESC LIMIT %s", (limit,)).fetchall()
    return {"log": [dict(r) for r in rows]}


# ── settings (consolidated — everything the admin manages) ──────────────────────
_AI_COLS = {"deepseek": "deepseek_key_enc", "openai": "openai_key_enc", "anthropic": "anthropic_key_enc"}


@router.get("/settings")
def get_settings(user=Depends(require_console)):
    import paystack
    with db.connect() as conn:
        row = conn.execute(
            """SELECT app_name, registrations_open, signup_invite_code,
                      openai_key_enc, deepseek_key_enc, anthropic_key_enc, tradelocker_dev_key_enc,
                      smtp_host, smtp_port, smtp_user, smtp_pass_enc, smtp_from
               FROM admin_settings WHERE id = 1""").fetchone() or {}
        admins = conn.execute("SELECT email, role, created_at FROM admins ORDER BY created_at").fetchall()
    invite = row.get("signup_invite_code") or None
    return {
        "app_name": row.get("app_name") or "EntryStation",
        "registrations_open": row.get("registrations_open"),
        "invite": {"code": invite, "path": f"/signup?invite={invite}" if invite else None},
        "ai": {p: {"has_key": bool(row.get(c))} for p, c in _AI_COLS.items()},
        "tradelocker": {"has_key": bool(row.get("tradelocker_dev_key_enc"))},
        "smtp": {"host": row.get("smtp_host"), "port": row.get("smtp_port"), "user": row.get("smtp_user"),
                 "mail_from": row.get("smtp_from"), "has_pass": bool(row.get("smtp_pass_enc"))},
        "paystack": {"mode": paystack.get_mode(),
                     "test": {"has_secret": bool(paystack.secret_key("test")), "public": paystack.public_key("test")},
                     "live": {"has_secret": bool(paystack.secret_key("live")), "public": paystack.public_key("live")},
                     "plans": paystack.list_plan_codes()},
        "super_owners": sorted(SUPER_OWNERS),
        "admins": [{"email": a["email"], "role": a["role"], "created_at": a["created_at"]} for a in admins],
    }


class AIKeyBody(BaseModel):
    provider: str
    key: str = ""


@router.post("/ai-keys")
def set_ai_key(body: AIKeyBody, user=Depends(require_console)):
    col = _AI_COLS.get(body.provider)
    if not col:
        raise HTTPException(400, "unknown provider")
    enc = auth.encrypt(body.key.strip()) if body.key.strip() else None
    with db.connect() as conn:
        conn.execute(f"INSERT INTO admin_settings (id, {col}, updated_at) VALUES (1, %s, now()) "
                     f"ON CONFLICT (id) DO UPDATE SET {col} = EXCLUDED.{col}, updated_at = now()", (enc,))
        conn.commit()
    audit(user["email"], "ai_key.set", "setting", body.provider, {"cleared": not enc})
    return {"ok": True, "has_key": bool(enc)}


class RegBody(BaseModel):
    open: bool


@router.post("/registrations")
def set_registrations(body: RegBody, user=Depends(require_console)):
    with db.connect() as conn:
        conn.execute("INSERT INTO admin_settings (id, registrations_open, updated_at) VALUES (1, %s, now()) "
                     "ON CONFLICT (id) DO UPDATE SET registrations_open = EXCLUDED.registrations_open, updated_at = now()",
                     (body.open,))
        conn.commit()
    audit(user["email"], "registrations.set", "setting", None, {"open": body.open})
    return {"ok": True, "open": body.open}


class BrandBody(BaseModel):
    app_name: str


@router.post("/branding")
def set_branding(body: BrandBody, user=Depends(require_console)):
    name = (body.app_name or "").strip()[:40] or "EntryStation"
    with db.connect() as conn:
        conn.execute("INSERT INTO admin_settings (id, app_name, updated_at) VALUES (1, %s, now()) "
                     "ON CONFLICT (id) DO UPDATE SET app_name = EXCLUDED.app_name, updated_at = now()", (name,))
        conn.commit()
    audit(user["email"], "branding.set", "setting", None, {"app_name": name})
    return {"ok": True, "app_name": name}


class SmtpBody(BaseModel):
    host: str = ""
    port: int = 587
    user: str = ""
    password: str = ""      # blank = keep existing
    mail_from: str = ""


@router.post("/smtp")
def set_smtp(body: SmtpBody, user=Depends(require_console)):
    sets = ["smtp_host = %s", "smtp_port = %s", "smtp_user = %s", "smtp_from = %s"]
    params = [body.host or None, body.port, body.user or None, body.mail_from or None]
    if body.password.strip():
        sets.append("smtp_pass_enc = %s")
        params.append(auth.encrypt(body.password.strip()))
    with db.connect() as conn:
        conn.execute("INSERT INTO admin_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
        conn.execute(f"UPDATE admin_settings SET {', '.join(sets)}, updated_at = now() WHERE id = 1", params)
        conn.commit()
    audit(user["email"], "smtp.set", "setting", None, {})
    return {"ok": True}


class AdminBody(BaseModel):
    email: str
    role: str = "admin"


@router.post("/admins")
def add_admin(body: AdminBody, user=Depends(require_console)):
    email = body.email.strip().lower()
    with db.connect() as conn:
        u = conn.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()
        if not u:
            raise HTTPException(400, "No user with that email — they must sign up first.")
        conn.execute("INSERT INTO admins (user_id, email, role, added_by) VALUES (%s, %s, %s, %s) "
                     "ON CONFLICT (user_id) DO UPDATE SET role = EXCLUDED.role",
                     (u["id"], email, body.role, user["email"]))
        conn.commit()
    audit(user["email"], "admin.add", "user", email, {"role": body.role})
    return {"ok": True}


@router.delete("/admins/{email}")
def remove_admin(email: str, user=Depends(require_console)):
    email = email.strip().lower()
    if email in SUPER_OWNERS:
        raise HTTPException(400, "Super-owners can't be removed.")
    with db.connect() as conn:
        conn.execute("DELETE FROM admins WHERE email = %s", (email,))
        conn.commit()
    audit(user["email"], "admin.remove", "user", email, {})
    return {"ok": True}


# ── Analysis API requests: one analysis shared across a window ─────────────────
class AnalysisApiBody(BaseModel):
    window_seconds: int = None       # how long one analysis is shared for
    cached_charge_pct: int = None    # what a shared answer costs, vs a fresh run
    enabled: bool = None


@router.get("/analysis-api")
def analysis_api_settings(user=Depends(require_console)):
    import analysis_cache
    return {**analysis_cache.settings(), "live": analysis_cache.stats()}


@router.put("/analysis-api")
def set_analysis_api_settings(body: AnalysisApiBody, user=Depends(require_console)):
    """Inside the window, the same user+agent+instrument+style is analysed ONCE:
    the first caller runs it, everyone else waits on that run or takes its result
    and is charged `cached_charge_pct` of what it cost."""
    import analysis_cache
    try:
        out = analysis_cache.save_settings(body.window_seconds, body.cached_charge_pct, body.enabled)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit(user["email"], "analysis_api.settings", "setting", "analysis_window", out)
    return {**out, "live": analysis_cache.stats()}


# ── Bring your own key, on a metered edition ──────────────────────────────────
class ByokBody(BaseModel):
    enabled: bool = None       # may a paying user run on their OWN provider key?
    markup_pct: int = None     # what we charge, as a % of what those tokens would have cost us


@router.get("/byok")
def byok_settings(user=Depends(require_console)):
    import ai_keys, edition
    return {**ai_keys.byok_policy(), "metered": edition.metered()}


@router.put("/byok")
def set_byok_settings(body: ByokBody, user=Depends(require_console)):
    """When a user connects their own OpenAI/Anthropic/… key, their requests go
    out on it and we spend nothing on tokens. `markup_pct` is what we charge
    instead — a percentage of what those tokens WOULD have cost us, debited from
    their credits as usual. 40% means a run that would have cost us $0.010 costs
    them the credit equivalent of $0.004.

    Set it to 0 to let a user's own key run free, or above 100 to charge more
    than the tokens would have cost. Turning `enabled` off ignores connected keys
    entirely and puts everyone back on the app's."""
    import ai_keys
    try:
        out = ai_keys.save_byok_policy(body.enabled, body.markup_pct)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit(user["email"], "byok.settings", "setting", "byok", out)
    return out


# ── Which real model each branded tier runs on ────────────────────────────────
class TiersBody(BaseModel):
    chat: str = None    # 'provider:model' for arrissa-chat; blank restores the default
    pro: str = None     # 'provider:model' for arrissa-pro


@router.get("/model-tiers")
def model_tiers(user=Depends(require_console)):
    import billing
    return {"tiers": {k: f"{p}:{m}" for k, (p, m) in billing.tiers().items()},
            "defaults": {k: f"{v['provider']}:{v['model']}" for k, v in billing.MODELS.items()}}


@router.put("/model-tiers")
def set_model_tiers(body: TiersBody, user=Depends(require_console)):
    """arrissa-chat and arrissa-pro are the only models this app names. WHICH
    real model each one runs on is set here, so a provider retiring a model is a
    field to edit rather than a release to cut."""
    import billing
    try:
        out = billing.save_tiers(body.chat, body.pro)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit(user["email"], "model_tiers.set", "setting", "model_tiers", out)
    return {"tiers": out}


# ── Daily Watch List (system agent): schedule + manual run ─────────────────────
class WatchScheduleBody(BaseModel):
    hours: str = ""          # UTC hours, e.g. "0,6" (or "00:00, 06:00")


@router.get("/watch-list")
def watch_list_admin(user=Depends(require_console)):
    """Schedule, last run and where the system agent can be edited."""
    import watchlist
    return watchlist.status()


@router.put("/watch-list/schedule")
def set_watch_schedule(body: WatchScheduleBody, user=Depends(require_console)):
    """Change WHEN the watch list is built (UTC hours). Takes effect within a
    minute — the worker re-reads the schedule on every tick, no restart needed."""
    import watchlist
    try:
        hours = watchlist.set_hours(body.hours)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit(user["email"], "watchlist.schedule", "setting", "watchlist_hours_utc", {"hours": hours})
    return watchlist.status()


@router.post("/watch-list/run")
def run_watch_list(user=Depends(require_console)):
    """Build the watch list NOW. Runs the system agent over every instrument, so
    it takes a few minutes; the result replaces the current slot's row."""
    import watchlist
    if watchlist._running.locked():
        raise HTTPException(409, "A watch-list run is already in progress.")
    audit(user["email"], "watchlist.run", "agent", watchlist.AGENT_ID, {})
    threading.Thread(target=lambda: watchlist.run_once(force=True),
                     daemon=True, name="watchlist-manual").start()
    return {"started": True, **watchlist.status()}


# helper used by main.py's chat gate
def is_suspended(user_id):
    try:
        with db.connect() as conn:
            row = conn.execute("SELECT status FROM users WHERE id = %s", (user_id,)).fetchone()
        return bool(row and row["status"] == "suspended")
    except Exception:
        return False
