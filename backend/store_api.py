"""
The store's public face — what entrystation.com serves to every instance.

`/api/store/catalog` and `/api/store/download/{id}` are UNAUTHENTICATED by app
login, because the caller is a different machine: a self-hosted instance that has
no account here and never will. What it has is a licence key, and that is what
gates a paid download.

The admin half (`/api/admin/licences`) is owner-only and is how a key gets
issued after someone pays.
"""
from __future__ import annotations

import json as _json
import re as _re

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

import store
from admin_api import require_admin, audit

router = APIRouter(tags=["store"])


@router.get("/api/store/catalog")
def store_catalog(key: str = Query("", description="Licence key, if the caller has one")):
    """Everything on sale, and — when a key is given — which of it that key owns.

    Deliverability is reported separately from availability: a module listed at a
    price we cannot actually ship is a broken promise, and saying so here is
    cheaper than finding out at download time."""
    builds = store.available_versions()
    lic = store.licence(key) if key else None
    out = []
    for m in store.catalog():
        mid = m["id"]
        out.append({
            **m,
            "version": builds.get(mid),
            "deliverable": mid in builds,
            "owned": store.owns(key, mid),
        })
    # What core the STORE is running. An instance compares its own against it,
    # which is the only way a self-hosted box can learn that the app itself has
    # moved on — modules announce their versions through the catalogue, and core
    # had no equivalent, so it could sit a year behind in silence.
    import modules as module_system
    return {"store": "EntryStation", **store.terms(),
            "core": {"version": module_system.CORE_VERSION},
            "bundles": store.bundles(),
            "licence": {"valid": bool(lic), "email": lic["email"] if lic else None,
                        "modules": (lic["modules"] if lic else [])} if key else None,
            "modules": out}


@router.get("/api/store/download/{module_id}")
def store_download(module_id: str, key: str = Query(""),
                   instance: str = Query("", description="the asking instance's domain or id")):
    """The module archive. Free modules are open; a paid one needs a licence that
    covers it, and the refusal says which so the operator knows what to buy.

    A paid key is also checked against the INSTANCE asking. Binding is what stops
    one purchase serving a hundred boxes, and it only means anything if the
    download consults it. An instance that is not the bound one is refused here,
    not later.

    `instance` is optional so an older client keeps working: an unbound key still
    downloads, and binding then happens the first time a client does send one."""
    if not store.owns(key, module_id):
        raise HTTPException(
            402, f"No licence for {module_id}. Buy it at "
                 f"https://entrystation.com/modules/{module_id}, then enter the "
                 f"licence key in Modules → Licence.")
    # Free modules never carry a key, so there is nothing to bind or check.
    if key and instance:
        if not store.bound_to(key, instance):
            raise HTTPException(403, "This licence key is already in use on another "
                                     "installation. Email arrissa.ai@gmail.com to move it.")
        store.bind(key, instance)          # first use claims it; later uses are idempotent
    z = store.zip_for(module_id)
    if z is None:
        raise HTTPException(404, f"{module_id} is listed but has no published build yet.")
    return FileResponse(z, media_type="application/zip", filename=z.name)


# ── what an instance owns ──────────────────────────────────────────────────────
#
# The lookup that replaces pasting a key. An instance asks "what have I bought?"
# and gets an answer, so a purchase reaches the box that made it without anyone
# copying anything out of an email.
#
# The key itself is the difficulty. A hostname is not a secret — anyone can ask
# about anyone's — and handing a key to whoever names the host would be worse
# than the paste it replaces: MAX_REBINDS lets a key MOVE, so a stolen one does
# not merely copy an entitlement, it takes it away from the buyer.
#
# So the key goes only to a caller that has PROVED it is that host, the way an
# ACME HTTP-01 challenge does, in two steps:
#
#   start  — the store mints a CHALLENGE and a CLAIM TOKEN for the host, and
#            returns both. The challenge is meant to be published; the token
#            never leaves the caller.
#   finish — the caller says it has published, and the store fetches
#            https://<host>/api/modules/instance-check to see the challenge
#            there. Only something controlling the host can put it there.
#
# Both halves are needed and that is the whole point. A single step cannot work,
# because whatever the host publishes is by definition public: an attacker could
# read a victim's page and present what they found. The token is what a passer-by
# can never obtain — it was handed to whoever STARTED the claim — and the
# challenge is what a claim-starter can never publish without owning the host.
_CLAIMS: dict = {}                       # token -> {instance, challenge, at}
CLAIM_TTL_S = 300


def _sweep_claims():
    dead = [t for t, c in _CLAIMS.items() if _time.time() - c["at"] > CLAIM_TTL_S]
    for t in dead:
        _CLAIMS.pop(t, None)


@router.post("/api/store/claim/start")
def store_claim_start(instance: str = Query(..., description="the host claiming its purchases")):
    """Begin proving that the caller is `instance`. Publish `challenge`, then finish."""
    inst = _store.normalise_instance(instance)
    if not inst:
        raise HTTPException(400, "Send your instance domain or id.")
    # A generated id proves itself by being known. There is nothing to call back
    # to on a laptop behind NAT, and demanding a routable host there sent every
    # localhost install to type a key by hand — which is the same secret, moved
    # by human hands, for no gain. The id carries 256 bits and is never
    # published, so presenting it is the proof.
    if inst.startswith("es-"):
        _sweep_claims()
        token = _secrets.token_urlsafe(24)
        _CLAIMS[token] = {"instance": inst, "challenge": "", "at": _time.time(),
                          "by_id": True}
        return {"claim_token": token, "challenge": "", "instance": inst,
                "by_id": True, "expires_in": CLAIM_TTL_S}
    if "." not in inst or inst.startswith("localhost"):
        raise HTTPException(400, f"{inst} is not a routable host and is not an instance id, so "
                                 f"there is nowhere to check the claim from here. Update this "
                                 f"instance so it generates an id, or enter the licence key by hand.")
    _sweep_claims()
    token = _secrets.token_urlsafe(24)
    _CLAIMS[token] = {"instance": inst, "challenge": _secrets.token_urlsafe(18),
                      "at": _time.time()}
    return {"claim_token": token, "challenge": _CLAIMS[token]["challenge"],
            "instance": inst, "expires_in": CLAIM_TTL_S}


@router.post("/api/store/claim/finish")
def store_claim_finish(claim_token: str = Query(...)):
    """Check the challenge is live on the host, then hand over what it owns."""
    _sweep_claims()
    claim = _CLAIMS.get(claim_token)
    if not claim:
        raise HTTPException(404, "That claim has expired or was never started. Begin again.")
    host = claim["instance"]
    # Claimed by id: the proof was presenting it, and there is no host to ask.
    if claim.get("by_id"):
        _CLAIMS.pop(claim_token, None)
        lic = _store.licence_for_instance(host)
        if not lic:
            return {"instance": host, "found": False, "modules": [], "licence_key": None,
                    "reason": "Nothing has been bought for this instance yet."}
        return {"instance": host, "found": True, "verified": True, "licence_key": lic["key"],
                "modules": lic["modules"] or [], "email": lic["email"],
                "expires_at": lic["expires_at"], "expired": lic["expired"]}
    try:
        import requests as _rq
        r = _rq.get(f"https://{host}/api/modules/instance-check", timeout=10)
        r.raise_for_status()
        seen = ((r.json() or {}).get("challenge") or "").strip()
    except Exception as e:
        raise HTTPException(400, f"{host} could not be reached to confirm the claim "
                                 f"({type(e).__name__}). It has to be reachable over HTTPS from "
                                 f"the store for this to work; otherwise enter the key by hand.")
    # Constant-time: this comparison decides whether a licence key is handed over.
    if not seen or not _secrets.compare_digest(seen, claim["challenge"]):
        raise HTTPException(403, f"{host} is not serving the challenge that was issued, so this "
                                 f"claim is not proved.")
    _CLAIMS.pop(claim_token, None)                    # one proof, one use

    lic = _store.licence_for_instance(host)
    if not lic:
        return {"instance": host, "found": False, "modules": [], "licence_key": None,
                "reason": "Nothing has been bought for this instance yet."}
    return {"instance": host, "found": True, "verified": True, "licence_key": lic["key"],
            "modules": lic["modules"] or [], "email": lic["email"],
            "expires_at": lic["expires_at"], "expired": lic["expired"]}


@router.get("/api/store/entitlements")
def store_entitlements(instance: str = Query(..., description="the asking instance's domain or id")):
    """What `instance` has bought — never the key, so this is safe to ask openly.

    It is what lets a page say "you own Sentiment, but this box cannot prove it is
    you" instead of showing an unbought module to someone who has paid."""
    inst = _store.normalise_instance(instance)
    if not inst:
        raise HTTPException(400, "Send your instance domain or id.")
    lic = _store.licence_for_instance(inst)
    if not lic:
        return {"instance": inst, "found": False, "modules": [],
                "reason": "Nothing has been bought for this instance yet."}
    return {"instance": inst, "found": True, "modules": lic["modules"] or [],
            "expires_at": lic["expires_at"], "expired": lic["expired"]}


# ── history, handed to a new instance ─────────────────────────────────────────
#
# A module installed today starts with an empty table, and the things these
# modules collect cannot be collected retrospectively: Fed Watch is a running
# series, news is what was published while something was watching. So a fresh
# instance would have the capability and nothing to point it at, and would look
# broken for weeks through no fault of its own.
#
# The store has been gathering all of it anyway. This hands over what it has.
#
# Entitlement is the SAME rule as the download: free modules are open, a paid one
# needs a licence covering it. Data is worth more than the code that fetches it,
# so the two cannot have different answers.
#
# The shape comes from the module's own manifest — `backfill` names the tables
# and the column that says when a row happened — so a module decides what of its
# history is shareable, and core never learns what a fedwatch_snapshot is.
BACKFILL_MAX_ROWS = 20000
_TABLE_OK = _re.compile(r"^[a-z_][a-z0-9_]*$")


@router.get("/api/store/backfill/{module_id}")
def store_backfill(module_id: str, key: str = Query(""), since_days: int = Query(0)):
    """The history this store holds for one module."""
    if not store.owns(key, module_id):
        raise HTTPException(402, f"No licence for {module_id}.")

    import modules as module_system
    path = module_system.MODULES_DIR / module_id
    try:
        spec = (module_system.read_manifest(path).get("backfill") or {})
    except Exception:
        spec = {}
    tables = spec.get("tables") or []
    if not tables:
        return {"module": module_id, "tables": {}, "note": "this module publishes no history"}

    window = since_days or int(spec.get("days") or 0)
    return _collect(tables, window, module_id)


def _collect(tables, window, module_id):
    import db
    out, total = {}, 0
    with db.connect() as conn:
        for t in tables:
            name, since = t.get("table"), t.get("since")
            # The manifest is signed, but a table name reaches SQL as an
            # identifier and cannot be parameterised — so it is checked rather
            # than trusted.
            if not name or not _TABLE_OK.match(name):
                continue
            if since and not _TABLE_OK.match(since):
                continue
            room = max(BACKFILL_MAX_ROWS - total, 0)
            if room == 0:
                break
            if since and window:
                sql = (f"SELECT * FROM {name} WHERE {since} > now() - interval '%s days' "
                       f"ORDER BY {since} DESC LIMIT %s")
                args = (window, room)
            else:
                sql = f"SELECT * FROM {name} LIMIT %s"
                args = (room,)
            try:
                rows = conn.execute(sql, args).fetchall()
            except Exception as e:
                print(f"[store] backfill {module_id}.{name}: {e}", flush=True)
                continue
            # Everything leaves as a STRING. A timestamp, a numeric and a JSONB
            # column each come back as a different Python type, and the receiving
            # instance would have to know which was which to insert them again.
            # Postgres coerces text to the target column type on the way in, so
            # sending text means neither end needs a type map.
            packed = []
            for r in rows:
                packed.append({k: _as_text(v) for k, v in dict(r).items()})
            out[name] = packed
            total += len(packed)
    return {"module": module_id, "rows": total, "tables": out}


def _as_text(v):
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return _json.dumps(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


# ── issuing licences (owner only) ──────────────────────────────────────────────
class IssueBody(BaseModel):
    email: str = ""
    modules: list = []          # module ids, or ["*"] for everything
    note: str = ""
    key: str = ""               # blank ⇒ generate one


@router.get("/api/admin/licences")
def list_licences(user=Depends(require_admin)):
    return {"licences": store.licences(),
            "catalog": [{"id": m["id"], "name": m["name"], "price_usd": m["price_usd"]}
                        for m in store.catalog()]}


@router.post("/api/admin/licences")
def issue_licence(body: IssueBody, user=Depends(require_admin)):
    key = (body.key or "").strip() or store.new_key()
    lic = store.issue(key, body.email, body.modules, body.note)
    audit(user["email"], "licence.issue", "licence", key,
          {"email": body.email, "modules": body.modules})
    return {"licence": lic}


@router.delete("/api/admin/licences/{key}")
def revoke_licence(key: str, user=Depends(require_admin)):
    ok = store.revoke(key)
    audit(user["email"], "licence.revoke", "licence", key)
    return {"revoked": ok}


# ── self-service purchase ──────────────────────────────────────────────────────
#
# A Community operator clicks Buy on their own machine and ends up here, on
# entrystation.com, with their instance identity in the query string. They pay,
# and the licence that comes back is already bound to the instance that asked
# for it — so the key they paste is the key their box may use, and nobody has to
# issue anything by hand.
#
# USD prices, ZAR charges: the catalogue is priced in dollars because that is
# what the world reads, and Paystack settles in Rands.
import os
import secrets as _secrets
import time as _time

import paystack
import store as _store

USD_ZAR = float(os.getenv("ENTRYSTATION_USD_ZAR", "18.5"))
SITE = os.getenv("ENTRYSTATION_SITE", "https://entrystation.com")


def _purchases_table():
    import db
    with db.connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS store_purchases (
                reference   TEXT PRIMARY KEY,
                product     TEXT NOT NULL,
                email       TEXT NOT NULL,
                instance_id TEXT NOT NULL,
                amount_zar  NUMERIC NOT NULL,
                mode        TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'pending',
                licence_key TEXT,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                paid_at     TIMESTAMPTZ
            )""")
        # Where the buyer came FROM. An instance id says WHICH box owns the
        # licence and nothing at all about how to get back to it — the two are
        # different questions, and answering only the first is why a localhost
        # buyer was left staring at raw JSON.
        conn.execute("ALTER TABLE store_purchases ADD COLUMN IF NOT EXISTS return_url TEXT")
        conn.commit()


def _term_end():
    """When a purchase made now runs out. None = perpetual.

    Read from the catalogue's own `billing` rather than hard-coded, so the file
    that advertises the term is the file that sets it — the two cannot drift into
    quoting "/yr" while issuing a licence that never ends.

    Nothing STOPS at expiry: the module keeps running, and only the right to new
    versions lapses. That is `store.can_update`'s rule, not a second one here."""
    from datetime import datetime, timedelta, timezone
    billing = (_store.terms().get("billing") or "yearly").lower()
    days = {"yearly": 365, "annual": 365, "monthly": 31}.get(billing)
    return datetime.now(timezone.utc) + timedelta(days=days) if days else None


def _priced(product: str):
    """What `product` costs in USD, and what it grants. A bundle grants itself —
    store.owns() already expands a bundle into the modules it covers."""
    for m in _store.catalog():
        if m.get("id") == product:
            return (m.get("price_usd"), [product])
    for b in _store.bundles():
        if b.get("id") == product:
            return (b.get("price_usd"), [product])
    return (None, [])


# ── every payment, in one place (owner only) ───────────────────────────────────
#
# Money arrives through two doors — a module bought from the store, and a plan or
# credit top-up bought inside the app — and until now each could only be read in
# its own table. That is fine for reconciling one payment and useless for the
# question anyone actually asks, which is "what came in, and did the buyer get
# what they paid for?".
#
# So both are read into ONE shape. `delivered` is the part worth having: a
# payment whose licence never issued, or whose plan never applied, is the failure
# that matters, and it is invisible in a list of amounts.
@router.get("/api/admin/transactions")
def admin_transactions(limit: int = Query(200, le=1000),
                       kind: str = Query("", description="module | subscription | topup"),
                       user=Depends(require_admin)):
    _purchases_table()
    import db
    rows = []
    with db.connect() as conn:
        for r in conn.execute(
                "SELECT * FROM store_purchases ORDER BY created_at DESC LIMIT %s",
                (limit,)).fetchall():
            rows.append({
                "kind": "module", "reference": r["reference"], "what": r["product"],
                "email": r["email"], "instance": r["instance_id"],
                "amount_zar": float(r["amount_zar"] or 0), "amount_usd": None,
                "mode": r["mode"], "provider": "paystack", "status": r["status"],
                "created_at": r["created_at"], "paid_at": r["paid_at"],
                # A paid module with no key is the exact failure this page exists
                # to surface: charged, and nothing to show for it.
                "delivered": bool(r["licence_key"]), "detail": r["licence_key"] or "",
            })
        # billing_transactions predates the store and may not exist on a fresh
        # Community box, which has no plans to sell at all.
        try:
            for r in conn.execute(
                    "SELECT t.*, u.email FROM billing_transactions t "
                    "JOIN users u ON u.id = t.user_id "
                    "ORDER BY t.created_at DESC LIMIT %s", (limit,)).fetchall():
                rows.append({
                    "kind": r["kind"], "reference": r["reference"],
                    "what": r["plan"] or r["pack"] or r["kind"],
                    "email": r["email"], "instance": None,
                    "amount_zar": float(r["amount_zar"] or 0),
                    "amount_usd": float(r["amount_usd"] or 0),
                    # billing_transactions records the PROVIDER, never which
                    # Paystack environment it went through — so mode is unknown
                    # here rather than "paystack", which would read as live.
                    "mode": None, "provider": r["provider"], "status": r["status"],
                    "created_at": r["created_at"], "paid_at": r["completed_at"],
                    "delivered": r["status"] == "success",
                    "detail": f"{r['interval'] or ''} {r['credits'] or ''}".strip(),
                })
        except Exception as e:
            print(f"[store] transactions: no billing history readable: {e}", flush=True)

    if kind:
        rows = [r for r in rows if r["kind"] == kind]
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    rows = rows[:limit]

    paid = [r for r in rows if r["status"] in ("paid", "success")]
    # Test money is not money. One total covering both would be the single figure
    # on this page nobody could trust, and the first test card run is about to
    # put rows in this table — so they are counted apart from the start.
    return {
        "transactions": rows,
        "totals": {
            "count": len(rows),
            "paid": len(paid),
            "pending": len([r for r in rows if r["status"] == "pending"]),
            "zar": round(sum(r["amount_zar"] or 0 for r in paid if r["mode"] != "test"), 2),
            "zar_test": round(sum(r["amount_zar"] or 0 for r in paid if r["mode"] == "test"), 2),
            # The one number that should always be zero.
            "undelivered": len([r for r in paid if not r["delivered"]]),
        },
    }


def _receipt_page(*, key, product, instance, back, modules, email, expires, reference):
    """What somebody sees the moment their payment goes through.

    Built here rather than in the app because the buyer is not in the app — they
    are on Paystack's return leg, on entrystation.com, and the instance they own
    may not even be reachable from this browser. So it is one self-contained
    page: no stylesheet to fetch, no script to block, nothing that can fail on
    the one screen where a person has just handed over money.

    The key is the thing on it. It is set in a monospace block at a size that
    survives being read aloud down a phone, with a copy button that reports back,
    and it is selectable — a receipt you cannot copy from is a receipt that
    failed. Everything else is secondary and reads as such.

    The instance id is NOT on it. It is the mechanism by which a licence binds to
    a box, and a buyer has no decision to make with it — it travels on the link
    by itself. Printing it on a receipt only invites somebody to reason about how
    the binding works, which is not a conversation a receipt should start.
    """
    from fastapi.responses import HTMLResponse
    import html as _html

    e = _html.escape
    covers = ", ".join(modules or []) or product
    back_btn = (f'<a class="btn primary" href="{e(back)}/modules?licence_key={e(key)}'
                f'&amp;bought={e(product)}">Return to your instance</a>') if back else ""
    return HTMLResponse(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Payment complete · EntryStation</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    padding: 24px; background: #0b0b0c; color: #f4f4f5;
    font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, system-ui, sans-serif;
  }}
  @media (prefers-color-scheme: light) {{ body {{ background: #fff; color: #101010; }} }}
  .card {{ width: 100%; max-width: 560px; }}
  .tick {{
    width: 44px; height: 44px; border-radius: 50%; display: grid; place-items: center;
    background: #16a34a; color: #fff; font-size: 22px; margin-bottom: 18px;
  }}
  h1 {{ margin: 0 0 6px; font-size: 25px; letter-spacing: -0.02em; }}
  .sub {{ margin: 0 0 26px; opacity: .65; font-size: 14px; }}
  .label {{ font-size: 11.5px; text-transform: uppercase; letter-spacing: .09em; opacity: .55; margin-bottom: 7px; }}
  .keyrow {{ display: flex; gap: 8px; align-items: stretch; }}
  .key {{
    flex: 1; min-width: 0; padding: 15px 16px; border-radius: 10px;
    background: #1a1a1d; border: 1px solid #2c2c31;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 19px; letter-spacing: .06em; overflow-wrap: anywhere; user-select: all;
  }}
  @media (prefers-color-scheme: light) {{
    .key {{ background: #f6f6f7; border-color: #e2e2e5; }}
  }}
  .btn {{
    display: inline-flex; align-items: center; justify-content: center; gap: 7px;
    padding: 0 18px; min-height: 46px; border-radius: 10px; border: 1px solid #2c2c31;
    background: transparent; color: inherit; font: inherit; font-weight: 600;
    cursor: pointer; text-decoration: none; white-space: nowrap;
  }}
  @media (prefers-color-scheme: light) {{ .btn {{ border-color: #d6d6da; }} }}
  .btn.primary {{ background: #f4f4f5; color: #0b0b0c; border-color: #f4f4f5; }}
  @media (prefers-color-scheme: light) {{ .btn.primary {{ background: #101010; color: #fff; border-color: #101010; }} }}
  .btn:active {{ transform: translateY(1px); }}
  .actions {{ display: flex; gap: 10px; flex-wrap: wrap; margin: 24px 0 0; }}
  .facts {{ margin: 26px 0 0; padding: 16px 0 0; border-top: 1px solid #232327; font-size: 13.5px; }}
  @media (prefers-color-scheme: light) {{ .facts {{ border-color: #ebebee; }} }}
  .facts div {{ display: flex; gap: 12px; padding: 4px 0; }}
  .facts dt {{ opacity: .55; min-width: 96px; }}
  .facts dd {{ margin: 0; overflow-wrap: anywhere; }}
  .note {{ margin: 20px 0 0; padding: 13px 15px; border-radius: 10px;
           background: #14331f; color: #b7f7cf; font-size: 13.5px; }}
  @media (prefers-color-scheme: light) {{ .note {{ background: #eaf8f0; color: #14532d; }} }}
  code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; }}
</style></head>
<body><div class="card">
  <div class="tick">&check;</div>
  <h1>Payment complete</h1>
  <p class="sub">{e(covers)} is now licensed to this installation.</p>

  <div class="label">Licence key</div>
  <div class="keyrow">
    <div class="key" id="k">{e(key)}</div>
    <button class="btn" id="c">Copy</button>
  </div>

  <div class="note">
    You should not need this. The installation it belongs to checks in with the
    store on its own and activates it — usually within a few minutes. Keep it in
    case you move to another server.
  </div>

  <div class="actions">
    {back_btn}
    <a class="btn" href="https://entrystation.com/modules">Browse modules</a>
  </div>

  <dl class="facts">
    <div><dt>Covers</dt><dd>{e(covers)}</dd></div>
    {f'<div><dt>Updates until</dt><dd>{e(expires)}</dd></div>' if expires else ''}
    <div><dt>Receipt to</dt><dd>{e(email)}</dd></div>
    <div><dt>Reference</dt><dd><code>{e(reference)}</code></dd></div>
  </dl>
</div>
<script>
  document.getElementById('c').onclick = function () {{
    var t = document.getElementById('k').textContent;
    navigator.clipboard.writeText(t).then(function () {{
      var b = document.getElementById('c'); b.textContent = 'Copied';
      setTimeout(function () {{ b.textContent = 'Copy'; }}, 1800);
    }});
  }};
</script>
</body></html>""")


def _safe_return(raw: str) -> str:
    """Where we are willing to send a buyer after they pay.

    A URL supplied by whoever started the checkout and then handed to a browser
    is an open redirect unless something decides what is allowed. Two things are:

    A LOOPBACK address, because it can only ever mean the buyer's own machine.
    Redirecting a victim to their own localhost gains an attacker nothing, and
    refusing it is what left every local install reading raw JSON.

    An https host, because that is the shape a real instance has and the scheme
    a licence key may travel over. Plain http to somebody else's server would
    put the key on the wire in clear.
    """
    from urllib.parse import urlparse
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        u = urlparse(raw)
    except Exception:
        return ""
    host = (u.hostname or "").lower()
    loopback = host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
    if u.scheme == "http" and loopback:
        return raw
    if u.scheme == "https" and host and "." in host:
        return raw
    return ""


@router.get("/api/store/checkout")
def store_checkout(product: str = Query(..., description="module or bundle id"),
                   instance: str = Query(..., description="the buying instance's domain or id"),
                   email: str = Query(..., description="where the key is sent"),
                   return_url: str = Query("", description="where to send the buyer back to")):
    """Begin a purchase. Returns the Paystack URL to send the buyer to."""
    inst = _store.normalise_instance(instance)
    if not inst:
        raise HTTPException(400, "Send your instance domain or id — your Settings page shows it.")
    if "@" not in (email or ""):
        raise HTTPException(400, "A valid email is required — the licence key is sent to it.")

    usd, grants = _priced(product)
    if not grants:
        raise HTTPException(404, f"No module or bundle called {product!r}")
    if not usd:
        raise HTTPException(400, f"{product} is free — install it without paying.")

    mode = paystack.get_mode()
    if not paystack.configured(mode):
        raise HTTPException(503, "Payments are not configured on this server.")

    zar = round(float(usd) * USD_ZAR, 2)
    ref = f"es_{int(_time.time())}_{_secrets.token_hex(5)}"
    _purchases_table()
    import db
    with db.connect() as conn:
        conn.execute("INSERT INTO store_purchases (reference, product, email, instance_id, "
                     "amount_zar, mode, return_url) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                     (ref, product, email.lower().strip(), inst, zar, mode,
                      _safe_return(return_url)))
        conn.commit()

    out = paystack.initialize(
        email=email.lower().strip(), amount_zar=zar, reference=ref,
        callback_url=f"{SITE}/api/store/checkout/verify",
        metadata={"product": product, "instance_id": inst, "grants": grants}, mode=mode)
    return {"authorization_url": out.get("authorization_url"), "reference": ref,
            "product": product, "instance_id": inst, "price_usd": usd, "amount_zar": zar,
            "mode": mode}


@router.get("/api/store/checkout/verify")
def store_checkout_verify(reference: str = Query(...)):
    """Paystack sends the buyer back here. Verify, then issue the key.

    Idempotent on purpose: a buyer refreshing this page, or Paystack retrying,
    must not mint a second licence for one payment — so a purchase already
    marked paid returns the key it already has."""
    _purchases_table()
    import db
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM store_purchases WHERE reference = %s",
                           (reference,)).fetchone()
    if not row:
        raise HTTPException(404, "Unknown payment reference.")
    if row["status"] == "paid" and row["licence_key"]:
        # A refresh, or Paystack retrying. Same page, same key — not a second
        # licence, and not the JSON this used to fall back to.
        back = _safe_return(row.get("return_url") or "")
        if back:
            from fastapi.responses import RedirectResponse
            from urllib.parse import urlencode
            q = urlencode({"licence_key": row["licence_key"], "bought": row["product"]})
            return RedirectResponse(f"{back.rstrip('/')}/modules?{q}", status_code=303)
        return _receipt_page(key=row["licence_key"], product=row["product"],
                             instance=row["instance_id"], back=back, modules=[],
                             email=row["email"], expires="", reference=reference)

    data = paystack.verify(reference, mode=row["mode"])
    if (data.get("status") or "").lower() != "success":
        return {"status": data.get("status") or "failed", "reference": reference,
                "message": "That payment did not complete. Nothing was charged for it."}

    # Trust OUR record of what was bought, not the metadata that came back: the
    # reference we created is the thing we control.
    _, grants = _priced(row["product"])
    # The catalogue quotes "/yr" on every price, so a purchase buys a YEAR. It
    # used to issue a perpetual licence, which is not what the buyer was shown —
    # and lapsing costs them nothing they paid for: `store.can_update` keeps the
    # installed module running for good and ends only the supply of new versions.
    expires = _term_end()
    key, lic = _store.grant(row["instance_id"], row["email"], grants,
                            note=f"paystack {reference} ({row['mode']})", expires_at=expires)
    bound = {"ok": True}

    with db.connect() as conn:
        conn.execute("UPDATE store_purchases SET status='paid', licence_key=%s, paid_at=now() "
                     "WHERE reference=%s", (key, reference))
        conn.commit()
    try:
        import mailer
        app = mailer.app_name()
        covers = ", ".join(lic["modules"] or grants)
        until = str(expires)[:10] if expires else ""
        mailer.send_email(
            row["email"], f"Your {app} licence — {row['product']}",
            f"<p>Thank you for your purchase.</p>"
            f"<p>Your licence key for <strong>{row['product']}</strong> is:</p>"
            f"<p style=\"font-size:18px;letter-spacing:1px\"><strong>{key}</strong></p>"
            # One key per installation, so a second purchase adds to this one
            # rather than replacing it — say what it covers NOW, or the buyer has
            # no way to tell that the thing they bought last month is still on it.
            + (f"<p>It covers <strong>{covers}</strong>.</p>" if covers else "")
            + (f"<p>Updates are included until <strong>{until}</strong>. What you have "
               f"installed keeps working after that — renewing resumes new versions.</p>"
               if until else "")
            + f"<p>It is tied to <strong>{row['instance_id']}</strong>, and that installation "
              f"activates it automatically — you should not need to enter it anywhere. "
              f"Keep it in case you move servers.</p>"
              f"<p>Reference {reference}.</p>")
    except Exception as e:
        # The key is issued and returned regardless — a mail failure must never
        # lose something already paid for. But it is LOGGED now: this used to
        # call mailer.send(), which does not exist, so every confirmation email
        # since the pipeline shipped died silently inside a bare except.
        print(f"[store] licence email to {row['email']} failed: {type(e).__name__}: {e}", flush=True)
    # Send them home, key in hand. Their Module Store reads it from the URL and
    # applies it, so "paid" and "installed" are one continuous motion rather than
    # a copy-paste out of an email.
    #
    # Only ever to the host on OUR purchase row, and only over https. The
    # instance is buyer-supplied, and a redirect that trusted it verbatim would
    # be an open redirect wearing a payment callback as a disguise. An instance
    # that is not a routable host — a generated id, localhost, a bare IP — gets
    # the key on screen instead, because there is nowhere to send them.
    # A page, not a payload. This used to answer a browser with raw JSON whenever
    # it could not redirect — which is every local install — leaving somebody who
    # had just paid to hunt a licence key out of a wall of punctuation.
    #
    # And it does not redirect on its own any more. The return address comes from
    # whoever started the checkout, so sending a browser there automatically is
    # an open redirect with a payment receipt for a disguise; a button the buyer
    # presses is the same journey without that. It also leaves the key on screen
    # long enough to copy, which was the point.
    back = _safe_return(row.get("return_url") or "")
    if not back:
        dest = _store.normalise_instance(row["instance_id"])
        if "." in dest and " " not in dest and not dest.startswith(("es-", "localhost")):
            back = f"https://{dest}"

    # Straight back to the instance that paid, at its Modules page, with the key
    # on the URL. The instance applies it and installs what was bought without
    # anybody pressing anything, so paying and having it are one motion.
    #
    # The address is the one recorded when the CHECKOUT WAS STARTED — never
    # anything supplied at this callback — and it has to be loopback or https to
    # have been recorded at all. So the only person who can aim this redirect is
    # the person who opened and paid for that checkout, at their own machine.
    if back:
        from fastapi.responses import RedirectResponse
        from urllib.parse import urlencode
        q = urlencode({"licence_key": key, "bought": row["product"]})
        return RedirectResponse(f"{back.rstrip('/')}/modules?{q}", status_code=303)

    # Nowhere to go back to — an older purchase, or a checkout begun somewhere
    # that could not say where it was. The key is on screen instead of lost.
    return _receipt_page(key=key, product=row["product"], instance=row["instance_id"],
                         back=back, modules=grants, email=row["email"],
                         expires=str(expires)[:10] if expires else "",
                         reference=reference)
