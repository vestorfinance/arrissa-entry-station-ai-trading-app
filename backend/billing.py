"""
Plans, credits and (simulated) Paystack billing.

The product is subscription-metered: every plan includes ALL features and differs
only by a monthly **credit** allowance. Credits meter AI usage (chat, analysis,
voice); plain reads / order placement are free. The one feature gate is Developer
mode + the programmatic API → Elite only. There is **no free tier**: an account
with no active subscription is view-only with a 0 credit balance.

Payments are NOT wired to a live provider yet. `checkout()` creates a pending
transaction and `simulate()` completes it as success|declined — standing in for
the Paystack callback we'll add later. Everything else (plans, the credit ledger,
gating, metering) is real.

User-facing model names are branded and mapped internally here, so we never expose
which provider/model actually runs or what it costs us:

    arrissa-chat  →  deepseek-v4-flash  (fast, cheap, the default)      1× credits
    arrissa-pro   →  deepseek-v4-pro    (sharper reasoning)             ~3× credits

    BOTH house tiers run on DeepSeek. OpenAI is for VOICE only (Whisper) and is
    not a model tier — pointing a tier at it put every arrissa-pro run on a key
    bought for transcription, which is how "openai API quota exhausted" came back
    as a no-trade signal to a live EA.
"""
import secrets
from datetime import datetime, timedelta, timezone

import db
import edition

# ── Plans ─────────────────────────────────────────────────────────────────────
# price_usd = monthly; price_annual_usd = the per-month rate when billed yearly
# (20% off). credits = monthly allowance. limits: None = unlimited. developer =
# whether Developer mode + the programmatic API/keys are unlocked (Elite only).
PLANS = {
    "trader": {
        "key": "trader", "name": "Trader", "order": 1,
        "price_usd": 49, "price_annual_usd": 39, "credits": 25000,
        "price_zar": 899, "price_zar_annual": 699,
        "developer": False,
        "limits": {"accounts": 3, "agents": 10, "monitors": 5,
                   "monitor_min_interval_min": 15, "scheduled": 25, "history_days": 365},
        "blurb": "Everyday trading with Arrissa, with every feature and generous limits.",
    },
    "pro": {
        "key": "pro", "name": "Pro", "order": 2,
        "price_usd": 99, "price_annual_usd": 79, "credits": 60000,
        "price_zar": 1799, "price_zar_annual": 1399,
        "developer": False,
        "limits": {"accounts": 10, "agents": 30, "monitors": 15,
                   "monitor_min_interval_min": 5, "scheduled": 100, "history_days": 730},
        "blurb": "Serious, active traders who lean on automation.",
    },
    "max": {
        "key": "max", "name": "Max", "order": 3,
        "price_usd": 199, "price_annual_usd": 159, "credits": 160000,
        "price_zar": 3599, "price_zar_annual": 2799,
        "developer": False,
        "limits": {"accounts": 25, "agents": 100, "monitors": 40,
                   "monitor_min_interval_min": 2, "scheduled": 300, "history_days": None},
        "blurb": "Always-on intelligence and heavy analysis.",
    },
    "elite": {
        "key": "elite", "name": "Elite", "order": 4,
        "price_usd": 399, "price_annual_usd": 319, "credits": 320000,
        "price_zar": 6999, "price_zar_annual": 5499,
        "developer": True,
        "limits": {"accounts": None, "agents": None, "monitors": None,
                   "monitor_min_interval_min": 1, "scheduled": None, "history_days": None},
        "blurb": "Run it like a desk, with Developer mode and the full programmatic API.",
    },
}

# One-off credit packs (also simulated Paystack). Priced above the in-plan rate so
# upgrading is always the better value, while still clearing margin.
CREDIT_PACKS = {
    "boost": {"key": "boost", "name": "Boost", "credits": 10000, "price_usd": 15, "price_zar": 249},
    "power": {"key": "power", "name": "Power", "credits": 30000, "price_usd": 39, "price_zar": 649},
    "bulk":  {"key": "bulk",  "name": "Bulk",  "credits": 100000, "price_usd": 119, "price_zar": 1999},
}

# The two branded tiers the user picks. These are the ONLY models this app names.
#
# What each one RUNS ON is an admin setting, not a constant: a provider retires a
# model with a few weeks' notice, and the answer to that should be a field in
# Settings, not a release. The values below are only the defaults for an instance
# that has never been told otherwise.
MODELS = {
    "arrissa-chat": {"key": "arrissa-chat", "name": "arrissa-chat",
                     "tagline": "Fast & efficient — the everyday default",
                     "provider": "deepseek", "model": "deepseek-v4-flash"},
    "arrissa-pro":  {"key": "arrissa-pro", "name": "arrissa-pro",
                     "tagline": "Sharper reasoning for complex analysis",
                     "provider": "deepseek", "model": "deepseek-v4-pro"},
}

_tier_cache: tuple = (0.0, None)


def tiers() -> dict:
    """{'arrissa-chat': (provider, model), …} — the admin's mapping, or the
    defaults. Memoised 10s, like the other admin-tunable numbers."""
    global _tier_cache
    import time as _t
    now = _t.time()
    if _tier_cache[0] > now and _tier_cache[1]:
        return _tier_cache[1]
    out = {k: (m["provider"], m["model"]) for k, m in MODELS.items()}
    try:
        with db.connect() as conn:
            row = conn.execute("SELECT tier_chat_model, tier_pro_model FROM admin_settings "
                               "WHERE id = 1").fetchone()
        for key, col in (("arrissa-chat", "tier_chat_model"), ("arrissa-pro", "tier_pro_model")):
            raw = (row or {}).get(col)
            if raw and ":" in raw:
                p, m = str(raw).split(":", 1)
                if p.strip() and m.strip():
                    out[key] = (p.strip(), m.strip())
    except Exception:
        pass
    _tier_cache = (now + 10, out)
    return out


def public_model(provider, model) -> str | None:
    """The branded name for a house model — 'arrissa-chat' — or None if it isn't one.

    The mask used to depend on whoever started the run remembering to pass a
    label down, and three of the six callers did not, so the run history printed
    the house model beside a run the user had picked "arrissa-pro" for. Reading
    the answer back off the tier table instead means no caller can leak it: the
    model names itself, and a tier the admin re-points renames with it.

    Only the HOUSE models are masked. A user on their own DeepSeek key picked
    DeepSeek and pays for DeepSeek, so they are shown DeepSeek — the caller
    checks that before asking."""
    if not provider or not model:
        return None
    for tier, (p, m) in tiers().items():
        if p == provider and m == model:
            return tier
    return None


def save_tiers(chat=None, pro=None) -> dict:
    """Each value is 'provider:model'. Blank restores the built-in default."""
    global _tier_cache
    import ai_keys
    vals = {}
    for name, raw in (("tier_chat_model", chat), ("tier_pro_model", pro)):
        if raw is None:
            continue
        raw = str(raw).strip()
        if raw:
            if ":" not in raw:
                raise ValueError(f"{name} must look like provider:model")
            p = raw.split(":", 1)[0].strip()
            if p not in ai_keys.PROVIDERS:
                raise ValueError(f"unknown provider {p!r} — one of {', '.join(ai_keys.PROVIDERS)}")
        vals[name] = raw or None
    if vals:
        cols = ", ".join(vals)
        with db.connect() as conn:
            conn.execute(
                f"INSERT INTO admin_settings (id, {cols}) VALUES (1, "
                + ", ".join(["%s"] * len(vals)) + ") ON CONFLICT (id) DO UPDATE SET "
                + ", ".join(f"{c} = EXCLUDED.{c}" for c in vals),
                tuple(vals.values()))
            conn.commit()
    _tier_cache = (0.0, None)
    return {k: f"{p}:{m}" for k, (p, m) in tiers().items()}

# Flat fallback rates (used only when a call reports no token usage, and for voice
# where Whisper is billed per-minute, not per-token).
ACTION_CREDITS = {
    "chat":     {"arrissa-chat": 10, "arrissa-pro": 30},
    "analysis": {"arrissa-chat": 12, "arrissa-pro": 40},
    "voice":    {"arrissa-chat": 15, "arrissa-pro": 15},
}

# ── real-token metering ─────────────────────────────────────────────────────────
# 1 credit = this much of our actual model spend, so credits debited == our cost —
# the user bears the true cost, we don't absorb it.
CREDIT_USD = 0.0004

# USD per 1,000,000 tokens: in = cache-miss input, in_cache = cache-hit input, out.
LLM_PRICING = {
    "deepseek-chat":     {"in": 0.14,  "in_cache": 0.0028,   "out": 0.28},
    "deepseek-v4-flash": {"in": 0.14,  "in_cache": 0.0028,   "out": 0.28},
    "deepseek-v4-pro":   {"in": 0.435, "in_cache": 0.003625, "out": 0.87},
    "deepseek-reasoner": {"in": 0.435, "in_cache": 0.003625, "out": 0.87},
    "gpt-4.1-mini":      {"in": 0.40,  "in_cache": 0.10,     "out": 1.60},
    "gpt-4o-mini":       {"in": 0.15,  "in_cache": 0.075,    "out": 0.60},
}


def price_for(model):
    m = (model or "").lower()
    return next((p for k, p in LLM_PRICING.items() if k in m), None)


def cost_of(usage, model):
    """USD cost of accumulated usage {in, out, cache_hit}. 0.0 for an unpriced model
    or empty usage."""
    p = price_for(model)
    if not p or not usage:
        return 0.0
    in_tok = int(usage.get("in", 0) or 0)
    hit = min(int(usage.get("cache_hit", 0) or 0), in_tok)
    miss = in_tok - hit
    out_tok = int(usage.get("out", 0) or 0)
    return miss / 1e6 * p["in"] + hit / 1e6 * p["in_cache"] + out_tok / 1e6 * p["out"]


def credits_of_cost(cost_usd):
    import math
    return int(math.ceil(max(0.0, float(cost_usd or 0)) / CREDIT_USD))


def _now():
    return datetime.now(timezone.utc)


# ── model branding / credit costs ──────────────────────────────────────────────
def model_tier(provider, model):
    """Map a raw (provider, model) — or an arrissa-* alias — to a branded tier
    used for credit pricing. DeepSeek is the cheap arrissa-chat; everything else
    (OpenAI/Anthropic) is the premium arrissa-pro."""
    p = (provider or "").lower()
    m = (model or "").lower()
    if m in MODELS:
        return m
    if p == "deepseek" or "deepseek" in m or m == "arrissa-chat":
        return "arrissa-chat"
    return "arrissa-pro"


def credits_for(action, provider=None, model=None):
    tier = model_tier(provider, model)
    return ACTION_CREDITS.get(action, {}).get(tier, ACTION_CREDITS.get(action, {}).get("arrissa-chat", 0))


DEFAULT_MODEL = "arrissa-chat"


def resolve_model(alias):
    """Map a branded alias ('arrissa-chat'/'arrissa-pro') — or any legacy
    'provider:model'/bare value — to the REAL (provider, model) we run under the
    hood. Unknown input falls back to the default branded tier. Users never see
    or send the real provider/model; this is the only place the mapping lives."""
    key = alias if alias in MODELS else None
    if key is None:
        if alias and ":" in alias:          # legacy "provider:model" → map to a branded tier
            prov, mdl = alias.split(":", 1)
            key = model_tier(prov, mdl)
        else:                                # empty / unknown → the default branded model
            key = DEFAULT_MODEL
    return tiers()[key]


def catalog():
    """Everything the pricing UI needs to render."""
    return {
        "plans": [PLANS[k] for k in sorted(PLANS, key=lambda k: PLANS[k]["order"])],
        "packs": list(CREDIT_PACKS.values()),
        "models": list(MODELS.values()),
        "action_credits": ACTION_CREDITS,
    }


# ── credit ledger ──────────────────────────────────────────────────────────────
def balance(user_id, conn=None):
    def _q(c):
        row = c.execute(
            "SELECT COALESCE(SUM(delta), 0) AS bal FROM credit_ledger WHERE user_id = %s",
            (user_id,),
        ).fetchone()
        return int(row["bal"] or 0)
    if conn is not None:
        return _q(conn)
    with db.connect() as c:
        return _q(c)


def _ledger(conn, user_id, delta, reason, ref=None):
    conn.execute(
        "INSERT INTO credit_ledger (user_id, delta, reason, ref) VALUES (%s, %s, %s, %s)",
        (user_id, int(delta), reason, ref),
    )


def charge(user_id, credits, reason, ref=None):
    """Debit `credits` from the user. Returns True on success, False if the balance
    is insufficient (nothing is debited)."""
    credits = int(credits)
    if credits <= 0:
        return True
    with db.connect() as conn:
        bal = balance(user_id, conn)
        if bal < credits:
            return False
        _ledger(conn, user_id, -credits, reason, ref)
        conn.commit()
    return True


def debit(user_id, credits, reason, ref=None):
    """Unconditionally debit `credits` (may take the balance below 0). Used for
    real-token metering AFTER an action — the tokens were already spent, so we must
    record the cost even if it slightly overshoots; the next request is then gated."""
    credits = int(credits)
    if credits <= 0:
        return
    with db.connect() as conn:
        _ledger(conn, user_id, -credits, reason, ref)
        conn.commit()


def charge_cost(user_id, cost_usd, reason, ref=None, provider=None):
    """Debit credits for a model run. Returns the credits charged.

    Normally that is our real token cost. When the run went out on the USER's OWN
    key we spent nothing on tokens, so the charge becomes a MARKUP on what those
    tokens would have cost us — the model is theirs, the analysis engine, the
    data, the accounts and the scheduling around it are still ours, and the
    operator sets the percentage in admin settings.

    `provider` is what makes that knowable. Without it the charge falls back to
    the full cost, which is the safe direction: we would rather bill a BYOK user
    as though they were on our key than hand out free runs by accident."""
    import ai_keys
    factor = 1.0
    if provider and ai_keys.on_own_key(user_id, provider):
        factor = ai_keys.byok_policy()["markup_pct"] / 100.0
    credits = credits_of_cost(cost_usd * factor)
    debit(user_id, credits, reason, ref)
    return credits


# ── subscription state ─────────────────────────────────────────────────────────
def _ensure_row(conn, user_id):
    conn.execute(
        "INSERT INTO user_billing (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
        (user_id,),
    )


def get_state(user_id):
    """The full billing state the frontend uses for the meter, the lapsed gate and
    the Developer-mode gate."""
    with db.connect() as conn:
        _ensure_row(conn, user_id)
        conn.commit()
        row = conn.execute(
            "SELECT plan, status, interval, renews_at, started_at FROM user_billing WHERE user_id = %s",
            (user_id,),
        ).fetchone()
        bal = balance(user_id, conn)
    plan_key = row["plan"] if row else None
    plan = PLANS.get(plan_key)
    active = bool(plan_key and row["status"] == "active")
    # An edition that sells nothing has no tiers, so there is nothing to be above.
    # Every plan-shaped answer is YES on a self-hosted box: the operator is paying
    # for the machine and the AI keys already, and telling them to "upgrade to
    # Elite" is a locked door with no key sold anywhere.
    unmetered = not edition.metered()
    return {
        "plan": plan_key,
        "plan_name": plan["name"] if plan else None,
        "status": row["status"] if row else "inactive",
        "active": unmetered or active,
        "interval": row["interval"] if row else "monthly",
        "renews_at": row["renews_at"] if row else None,
        "credits": bal,
        "monthly_credits": plan["credits"] if plan else 0,
        "developer": unmetered or bool(plan and plan["developer"] and active),
        "limits": plan["limits"] if plan else {},
    }


def is_active(user_id):
    return get_state(user_id)["active"]


def is_developer(user_id):
    return get_state(user_id)["developer"]


# ── checkout → (Paystack pop-up) → verify/apply ────────────────────────────────
def _ref():
    return "es_" + secrets.token_hex(12)


def checkout(user_id, *, plan=None, pack=None, interval="monthly"):
    """Create a PENDING transaction and return what the Paystack pop-up needs. The
    charge happens in the browser (Paystack Inline); verify() confirms it server-side
    and apply_success() grants the plan/credits. Amounts are in ZAR (Rands)."""
    if plan:
        if plan not in PLANS:
            raise ValueError("Unknown plan")
        p = PLANS[plan]
        amount_zar = p["price_zar_annual"] * 12 if interval == "annual" else p["price_zar"]
        kind, credits = "subscription", p["credits"]
    elif pack:
        if pack not in CREDIT_PACKS:
            raise ValueError("Unknown credit pack")
        pk = CREDIT_PACKS[pack]
        amount_zar, kind, credits, interval = pk["price_zar"], "topup", pk["credits"], None
    else:
        raise ValueError("Nothing to check out")

    ref = _ref()
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO billing_transactions
                 (user_id, reference, kind, plan, pack, interval, amount_zar, credits, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending')""",
            (user_id, ref, kind, plan, pack, interval, amount_zar, credits),
        )
        conn.commit()
    return {"reference": ref, "kind": kind, "plan": plan, "pack": pack,
            "interval": interval, "amount_zar": int(amount_zar), "credits": credits}


def apply_success(user_id, reference, paystack_ids=None):
    """Mark a pending transaction paid and apply the plan/credits. Idempotent: a tx
    already 'success' just returns the current state. paystack_ids (optional):
    {customer_code, subscription_code, email_token} — persisted for cancellation."""
    pid = paystack_ids or {}
    with db.connect() as conn:
        tx = conn.execute(
            "SELECT * FROM billing_transactions WHERE reference = %s AND user_id = %s",
            (reference, user_id),
        ).fetchone()
        if not tx:
            raise ValueError("Unknown transaction")
        if tx["status"] == "success":
            return get_state(user_id)
        if tx["status"] != "pending":
            raise ValueError(f"Transaction already {tx['status']}")

        if tx["kind"] == "subscription":
            plan = PLANS[tx["plan"]]
            interval = tx["interval"] or "monthly"
            renews = _now() + (timedelta(days=365) if interval == "annual" else timedelta(days=30))
            _ensure_row(conn, user_id)
            conn.execute(
                """UPDATE user_billing
                     SET plan = %s, status = 'active', interval = %s, renews_at = %s,
                         started_at = COALESCE(started_at, now()),
                         paystack_customer_code     = COALESCE(%s, paystack_customer_code),
                         paystack_subscription_code = COALESCE(%s, paystack_subscription_code),
                         paystack_email_token       = COALESCE(%s, paystack_email_token),
                         updated_at = now()
                   WHERE user_id = %s""",
                (plan["key"], interval, renews, pid.get("customer_code"),
                 pid.get("subscription_code"), pid.get("email_token"), user_id),
            )
            cur = balance(user_id, conn)
            _ledger(conn, user_id, plan["credits"] - cur, "subscription", reference)
        else:  # topup
            _ledger(conn, user_id, int(tx["credits"]), "topup", reference)

        conn.execute(
            "UPDATE billing_transactions SET status = 'success', completed_at = now() WHERE id = %s",
            (tx["id"],),
        )
        conn.commit()
    return get_state(user_id)


def renew_subscription(customer_code=None, subscription_code=None, reference=None):
    """A Paystack renewal landed. Move the period on and restore the credits.

    Nothing did this. `renews_at` was written once, at the moment of purchase,
    and never again — so Paystack charged the card every cycle while the app
    went on believing the plan ended on the original date. The customer pays and
    is locked out, which is the worst way for this to fail and the last way
    anybody notices.

    Matched on the subscription first, then the customer: a subscription code is
    exact, and a customer may hold more than one."""
    with db.connect() as conn:
        row = None
        if subscription_code:
            row = conn.execute(
                "SELECT user_id, plan, interval FROM user_billing "
                "WHERE paystack_subscription_code = %s", (subscription_code,)).fetchone()
        if not row and customer_code:
            row = conn.execute(
                "SELECT user_id, plan, interval FROM user_billing "
                "WHERE paystack_customer_code = %s", (customer_code,)).fetchone()
        if not row or not row["plan"]:
            return None

        user_id, key = row["user_id"], row["plan"]
        interval = row["interval"] or "monthly"
        plan = PLANS.get(key)
        if not plan:
            return None

        # From NOW, not from the old date. A renewal that lands late — a retry,
        # a webhook delayed — should still give a full period rather than a
        # short one measured from a date already passed.
        renews = _now() + (timedelta(days=365) if interval == "annual" else timedelta(days=30))
        conn.execute(
            "UPDATE user_billing SET status = 'active', renews_at = %s, updated_at = now() "
            "WHERE user_id = %s", (renews, user_id))
        # Back UP to the plan's allowance, not plus it: a subscription buys a
        # period's worth, and adding to an unspent balance would compound for
        # somebody who simply did not use it.
        cur = balance(user_id, conn)
        if plan["credits"] > cur:
            _ledger(conn, user_id, plan["credits"] - cur, "renewal", reference or "renewal")
        conn.commit()
    return {"user_id": user_id, "plan": key, "interval": interval, "renews_at": renews,
            "credits": plan["credits"]}


def mark_declined(user_id, reference):
    with db.connect() as conn:
        conn.execute(
            "UPDATE billing_transactions SET status = 'declined', completed_at = now() "
            "WHERE reference = %s AND user_id = %s AND status = 'pending'",
            (reference, user_id),
        )
        conn.commit()


def paystack_ids(user_id):
    """The stored Paystack subscription/customer identifiers (for cancellation)."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT paystack_subscription_code, paystack_email_token, paystack_customer_code "
            "FROM user_billing WHERE user_id = %s", (user_id,)).fetchone()
    return dict(row) if row else {}


def simulate(user_id, reference, outcome):
    """Dev-only fallback when no live Paystack is configured: complete a pending tx."""
    if str(outcome).lower() in ("success", "ok", "paid"):
        return {"status": "success", "reference": reference, "state": apply_success(user_id, reference)}
    mark_declined(user_id, reference)
    return {"status": "declined", "reference": reference}


def cancel(user_id):
    """Cancel/downgrade to the unsubscribed state immediately: no plan, 0 credits,
    view-only. (Demo behaviour — a real cancel would run to period end.)"""
    with db.connect() as conn:
        _ensure_row(conn, user_id)
        cur = balance(user_id, conn)
        if cur:
            _ledger(conn, user_id, -cur, "cancel", None)
        conn.execute(
            """UPDATE user_billing
                 SET plan = NULL, status = 'inactive', renews_at = NULL,
                     paystack_subscription_code = NULL, paystack_email_token = NULL, updated_at = now()
               WHERE user_id = %s""",
            (user_id,),
        )
        conn.commit()
    return get_state(user_id)


def ledger(user_id, limit=50):
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT delta, reason, ref, created_at FROM credit_ledger WHERE user_id = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (user_id, limit),
        ).fetchall()
    return [{"delta": r["delta"], "reason": r["reason"], "ref": r["ref"],
             "created_at": r["created_at"]} for r in rows]
