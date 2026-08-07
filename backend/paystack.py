"""
Paystack payments — keys, environment (test/live), the Plan API and transaction
verification.

Keys live in admin_settings (secrets Fernet-encrypted, public keys plaintext since
they're public). The app charges in ZAR (Rands) via the Paystack Inline pop-up in
the browser; verify() confirms each transaction server-side before
billing.apply_success() grants the plan/credits. A single `paystack_mode` flag
(test|live) selects which key set and plan codes are used app-wide.

Docs: https://paystack.com/docs/api/plan/  ·  https://paystack.com/docs/api/subscription/
"""
import requests

import db
import auth

BASE = "https://api.paystack.co"
_COLS = {
    "test": ("paystack_test_secret_enc", "paystack_test_public"),
    "live": ("paystack_live_secret_enc", "paystack_live_public"),
}


def _admin_row():
    with db.connect() as conn:
        return conn.execute("SELECT * FROM admin_settings WHERE id = 1").fetchone()


# ── environment + keys ─────────────────────────────────────────────────────────
def get_mode():
    row = _admin_row()
    return (row.get("paystack_mode") if row else None) or "test"


def set_mode(mode):
    mode = "live" if mode == "live" else "test"
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO admin_settings (id, paystack_mode, updated_at) VALUES (1, %s, now()) "
            "ON CONFLICT (id) DO UPDATE SET paystack_mode = EXCLUDED.paystack_mode, updated_at = now()",
            (mode,))
        conn.commit()
    return mode


def set_keys(mode, secret, public):
    if mode not in _COLS:
        raise ValueError("mode must be test | live")
    sec_col, pub_col = _COLS[mode]
    enc = auth.encrypt(secret) if secret else None
    with db.connect() as conn:
        conn.execute(
            f"INSERT INTO admin_settings (id, {sec_col}, {pub_col}, updated_at) VALUES (1, %s, %s, now()) "
            f"ON CONFLICT (id) DO UPDATE SET {sec_col} = EXCLUDED.{sec_col}, "
            f"{pub_col} = EXCLUDED.{pub_col}, updated_at = now()",
            (enc, public or None))
        conn.commit()


def secret_key(mode=None):
    mode = mode or get_mode()
    sec_col, _ = _COLS[mode]
    row = _admin_row()
    enc = row.get(sec_col) if row else None
    if not enc:
        return None
    try:
        return auth.decrypt(enc)
    except Exception:
        return None


def public_key(mode=None):
    mode = mode or get_mode()
    _, pub_col = _COLS[mode]
    row = _admin_row()
    return (row.get(pub_col) if row else None) or None


def configured(mode=None):
    return bool(secret_key(mode) and public_key(mode))


def _headers(mode=None):
    sk = secret_key(mode)
    if not sk:
        raise RuntimeError("Paystack is not configured for this environment.")
    return {"Authorization": f"Bearer {sk}", "Content-Type": "application/json"}


# ── Plan API + transaction verification ─────────────────────────────────────────
def create_plan(name, amount_zar, interval, mode):
    """Create a Paystack plan in ZAR. `amount` is sent in the minor unit (cents),
    so Rands × 100. interval: 'monthly' | 'annually'. Returns the plan_code."""
    r = requests.post(f"{BASE}/plan", headers=_headers(mode), timeout=30, json={
        "name": name, "amount": int(amount_zar) * 100, "interval": interval, "currency": "ZAR",
    })
    data = r.json()
    if not data.get("status"):
        raise RuntimeError(data.get("message") or "Paystack plan create failed")
    return data["data"]["plan_code"]


def initialize(email, amount_zar, reference, callback_url, metadata=None, mode=None,
               plan=None):
    """Start a transaction and return the URL to send the buyer to.

    ZAR minor units, like the rest of Paystack: Rands x 100. `metadata` rides
    with the transaction and comes back on verify, which is how the instance
    that is buying survives a round trip through a payment page.

    With `plan`, Paystack creates a SUBSCRIPTION rather than taking a single
    payment, and charges again each period without anyone being asked.

    The amount is still sent. Dropping it — on the reasoning that the plan
    already carries one — is refused outright with "Invalid Amount Sent": the
    field is required whether or not a plan is attached, and Paystack takes the
    plan's amount for the subscription regardless. The two agree here because
    both are derived from the same price."""
    body = {
        "email": email, "amount": int(round(float(amount_zar) * 100)),
        "reference": reference, "callback_url": callback_url,
        "currency": "ZAR", "metadata": metadata or {},
    }
    if plan:
        body["plan"] = plan
    r = requests.post(f"{BASE}/transaction/initialize", headers=_headers(mode), timeout=30,
                      json=body)
    data = r.json()
    if not data.get("status"):
        raise RuntimeError(data.get("message") or "Paystack initialize failed")
    return data["data"]        # {authorization_url, access_code, reference}


def verify(reference, mode=None):
    """Verify a transaction. Returns Paystack's `data` dict (status, customer, plan,
    authorization…). Raises on a non-OK API response."""
    r = requests.get(f"{BASE}/transaction/verify/{reference}", headers=_headers(mode), timeout=30)
    data = r.json()
    if not data.get("status"):
        raise RuntimeError(data.get("message") or "Paystack verify failed")
    return data["data"]


def customer_subscriptions(customer_code, mode=None):
    r = requests.get(f"{BASE}/subscription", headers=_headers(mode),
                     params={"customer": customer_code}, timeout=30)
    data = r.json()
    return (data.get("data") or []) if data.get("status") else []


def customer_subscriptions_by_code(subscription_code, mode=None) -> dict:
    """One subscription, fetched for its email_token.

    Disabling needs the code AND a token Paystack issues per subscription, and
    the token is not on anything we stored — so it is read back at the moment it
    is needed rather than kept."""
    r = requests.get(f"{BASE}/subscription/{subscription_code}", headers=_headers(mode),
                     timeout=30)
    data = r.json()
    return (data.get("data") or {}) if data.get("status") else {}


def disable_subscription(code, token, mode=None):
    r = requests.post(f"{BASE}/subscription/disable", headers=_headers(mode), timeout=30,
                      json={"code": code, "token": token})
    return bool(r.json().get("status"))


# ── plan-code mapping (paystack_plans table) ────────────────────────────────────
def plan_code(mode, plan_key, interval):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT plan_code FROM paystack_plans WHERE mode=%s AND plan_key=%s AND interval=%s",
            (mode, plan_key, interval)).fetchone()
    return row["plan_code"] if row else None


def save_plan_code(mode, plan_key, interval, code, amount_zar, name):
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO paystack_plans (mode, plan_key, interval, plan_code, amount_zar, name, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s, now())
               ON CONFLICT (mode, plan_key, interval) DO UPDATE
                 SET plan_code=EXCLUDED.plan_code, amount_zar=EXCLUDED.amount_zar,
                     name=EXCLUDED.name, updated_at=now()""",
            (mode, plan_key, interval, code, amount_zar, name))
        conn.commit()


def list_plan_codes(mode=None):
    with db.connect() as conn:
        if mode:
            rows = conn.execute("SELECT * FROM paystack_plans WHERE mode=%s ORDER BY plan_key, interval",
                                (mode,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM paystack_plans ORDER BY mode, plan_key, interval").fetchall()
    return [dict(r) for r in rows]


def sync_store_plans(mode):
    """Yearly plans for every paid module and bundle, so a purchase RENEWS.

    A module sold as a one-off charge lapses in silence: the buyer keeps what
    they installed, stops getting new versions, and finds out months later when
    something they wanted was published and never arrived. A subscription tells
    them, and charges them, at the moment it matters.

    Paystack cannot delete a plan, so an existing one is updated rather than
    replaced — its plan_code is what any live subscription is attached to, and
    minting a new code would strand every subscriber on the old price.
    """
    import store
    made = []
    products = [(m["id"], m["name"], m.get("price_usd")) for m in store.catalog()]
    products += [(b["id"], b["name"], b.get("price_usd")) for b in store.bundles()]

    for pid, name, usd in products:
        if not usd:                      # free, or bundled and not sold alone
            continue
        import store_api
        cents = round(float(usd) * store_api.USD_ZAR * 100)
        title = f"EntryStation {name} (yearly)"
        existing = plan_code(mode, f"module:{pid}", "annual")
        if existing:
            # The amount can move — a bundle derives its price from what it
            # contains, so publishing a module changes it. Push it to Paystack
            # rather than letting the plan quote last year's number.
            try:
                update_plan(existing, title, cents, mode)
            except Exception as e:
                print(f"[paystack] could not update {pid} plan: {e}", flush=True)
            save_plan_code(mode, f"module:{pid}", "annual", existing, cents // 100, title)
            made.append({"product": pid, "plan_code": existing, "amount_zar": cents / 100,
                         "created": False})
            continue
        code = create_plan_cents(title, cents, "annually", mode)
        save_plan_code(mode, f"module:{pid}", "annual", code, cents // 100, title)
        made.append({"product": pid, "plan_code": code, "amount_zar": cents / 100,
                     "created": True})
    return made


def create_plan_cents(name, amount_cents, interval, mode):
    """Like create_plan, but in cents.

    `create_plan` takes Rands and multiplies by 100, which truncates: $29 is
    R536.50 and would be charged as R536. A module priced in dollars almost
    never lands on a whole Rand, so the store side works in cents throughout."""
    r = requests.post(f"{BASE}/plan", headers=_headers(mode), timeout=30, json={
        "name": name, "amount": int(amount_cents), "interval": interval, "currency": "ZAR",
    })
    data = r.json()
    if not data.get("status"):
        raise RuntimeError(data.get("message") or "Paystack refused the plan")
    return data["data"]["plan_code"]


def update_plan(code, name, amount_cents, mode):
    """Change an existing plan's price. Paystack has no delete, and the code is
    what live subscriptions hang off, so this is the only safe way to reprice."""
    r = requests.put(f"{BASE}/plan/{code}", headers=_headers(mode), timeout=30, json={
        "name": name, "amount": int(amount_cents),
    })
    data = r.json()
    if not data.get("status"):
        raise RuntimeError(data.get("message") or "Paystack refused the update")
    return True


def sync_plans(mode):
    """Create the Paystack plans for every tier × interval in `mode` and store each
    plan_code. Keeps an existing code (Paystack has no plan-delete), only refreshing
    the stored amount/name; creates the plan when we don't yet have a code."""
    import billing
    made = []
    for key, p in billing.PLANS.items():
        for interval, ps_interval, amount in (
            ("monthly", "monthly", p["price_zar"]),
            ("annual", "annually", p["price_zar_annual"] * 12),
        ):
            name = f"EntryStation {p['name']} ({interval})"
            existing = plan_code(mode, key, interval)
            if existing:
                save_plan_code(mode, key, interval, existing, amount, name)
                made.append({"plan": key, "interval": interval, "plan_code": existing,
                             "amount_zar": amount, "created": False})
                continue
            code = create_plan(name, amount, ps_interval, mode)
            save_plan_code(mode, key, interval, code, amount, name)
            made.append({"plan": key, "interval": interval, "plan_code": code,
                         "amount_zar": amount, "created": True})
    return made
