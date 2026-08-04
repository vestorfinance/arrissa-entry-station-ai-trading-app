"""
The module store — the entrystation.com side of "buy a module, click install".

Two audiences on two different machines, which is why this file exists at all:

  · SELLING (this server, entrystation.com). Publishes a catalogue of modules
    with their prices and a purchase link, and serves a module's ZIP to anyone
    holding a licence that covers it.

  · BUYING (any instance, including a self-hosted Community one). Reads that
    catalogue over HTTPS, merges it with what is installed locally, and asks for
    the ZIP when the operator clicks Install. See `catalog.py`.

The same codebase does both because the same codebase IS both — the cloud
deployment sells, and every deployment buys. Nothing here is edition-specific.

Entitlement is a licence key, not an account. A key is issued once per customer,
lists the modules it covers, and travels with the instance rather than the
person — which is what a self-hoster needs, because their instance has to be
able to fetch an update at 3am without anyone logging in.
"""
from __future__ import annotations

import json
from pathlib import Path

import db

ROOT = Path(__file__).parent.parent
CATALOG_FILE = ROOT / "store" / "catalog.json"
DIST_DIR = ROOT / "dist"                 # where `module_installer.py pack` writes


def _ensure_table():
    with db.connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS store_licences (
                key         TEXT PRIMARY KEY,
                email       TEXT,
                modules     JSONB NOT NULL DEFAULT '[]'::jsonb,
                note        TEXT,
                revoked_at  TIMESTAMPTZ,
                expires_at  TIMESTAMPTZ,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )""")
        conn.execute("ALTER TABLE store_licences ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ")
        # Which INSTANCE this key belongs to. A domain when the buyer has one,
        # otherwise the generated id their Settings page shows them: plenty of
        # self-hosters run on localhost, behind NAT, or on a bare IP, and a key
        # that can only bind to a resolvable domain would lock those buyers out
        # of software they had paid for.
        conn.execute("ALTER TABLE store_licences ADD COLUMN IF NOT EXISTS instance_id TEXT")
        conn.execute("ALTER TABLE store_licences ADD COLUMN IF NOT EXISTS bound_at TIMESTAMPTZ")
        conn.execute("ALTER TABLE store_licences ADD COLUMN IF NOT EXISTS rebinds INT NOT NULL DEFAULT 0")
        # Every move, kept. People do change servers, and the honest reasons look
        # exactly like the dishonest ones until you can see the pattern — so the
        # limit is small, the history is complete, and a buyer who genuinely
        # needs another move can be given one.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS store_licence_binds (
                id          BIGSERIAL PRIMARY KEY,
                key         TEXT NOT NULL,
                instance_id TEXT NOT NULL,
                was         TEXT,
                at          TIMESTAMPTZ NOT NULL DEFAULT now()
            )""")
        conn.commit()


# How many times a key may move to a different instance after its first bind.
MAX_REBINDS = 3


def normalise_instance(raw: str) -> str:
    """One spelling per instance, so the same box never looks like two.

    A domain arrives as a URL, with a scheme, a port, a trailing slash, a
    capital or two, sometimes a www — all the same machine. A generated
    instance id passes through untouched."""
    s = (raw or "").strip().lower()
    if not s:
        return ""
    for pre in ("https://", "http://"):
        if s.startswith(pre):
            s = s[len(pre):]
    s = s.split("/")[0].split("?")[0]
    if s.startswith("www."):
        s = s[4:]
    if ":" in s and not s.startswith("es-"):     # strip a port, keep an id
        s = s.split(":")[0]
    return s.strip(". ")


def _binding_of(key: str) -> tuple:
    """(instance_id, rebinds) as the row actually holds them."""
    _ensure_table()
    with db.connect() as conn:
        row = conn.execute("SELECT instance_id, rebinds FROM store_licences WHERE key = %s",
                           (key.strip(),)).fetchone()
    if not row:
        return ("", 0)
    return (row["instance_id"] or "", int(row["rebinds"] or 0))


def bind(key: str, instance: str) -> dict:
    """Tie a key to the instance using it. Returns {ok, reason, licence}.

    First use binds silently — that is the purchase completing. A LATER use from
    somewhere else is a move: allowed a few times, recorded every time, and
    refused after that with a message that says who to ask rather than a bare
    no."""
    inst = normalise_instance(instance)
    lic = licence(key)
    if not lic:
        return {"ok": False, "reason": "That licence key is not recognised."}
    if not inst:
        return {"ok": False, "reason": "No instance identifier was sent — your Settings page "
                                       "shows the one this installation should use."}

    # Read the binding columns STRAIGHT from the row. licence() projects a
    # published shape for the store API and does not carry them, so asking it
    # returned None every time — which read as "never bound", so every move
    # looked like a first purchase and the limit never once bit.
    cur_inst, rebinds = _binding_of(key)
    current = normalise_instance(cur_inst)
    if current == inst:
        return {"ok": True, "reason": "already bound", "licence": lic}

    if current and rebinds >= MAX_REBINDS:
        return {"ok": False, "reason": (
            f"This key is already in use on {current} and has been moved "
            f"{MAX_REBINDS} times, which is the limit. Email arrissa.ai@gmail.com "
            f"and we will move it for you.")}

    _ensure_table()
    with db.connect() as conn:
        conn.execute(
            "UPDATE store_licences SET instance_id = %s, bound_at = now(), "
            "rebinds = rebinds + %s WHERE key = %s",
            (inst, 1 if current else 0, key.strip()))
        conn.execute("INSERT INTO store_licence_binds (key, instance_id, was) VALUES (%s, %s, %s)",
                     (key.strip(), inst, current or None))
        conn.commit()
    return {"ok": True, "reason": "moved" if current else "bound", "licence": licence(key)}


def bound_to(key: str, instance: str) -> bool:
    """Is this key in use on THIS instance? An unbound key answers yes — it has
    not been claimed yet, and claiming it is what `bind` is for."""
    if not licence(key):
        return False
    current = normalise_instance(_binding_of(key)[0])
    return (not current) or current == normalise_instance(instance)


def licence_for_instance(instance: str) -> dict | None:
    """The licence already bound to this instance, if any.

    This is what makes an instance the unit of entitlement rather than a key.
    Ask it before issuing anything: a second purchase from a box that already
    owns something must EXTEND what it owns, not arrive as a rival key."""
    inst = normalise_instance(instance)
    if not inst:
        return None
    _ensure_table()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT key FROM store_licences WHERE instance_id = %s AND revoked_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1", (inst,)).fetchone()
    return licence(row["key"]) if row else None


def grant(instance: str, email: str, modules: list, note: str = "",
          expires_at=None) -> tuple[str, dict]:
    """Give this INSTANCE these modules, and return (key, licence).

    One instance, one key, however many purchases. Minting a fresh key per
    payment looked harmless while everyone bought once — but an instance can
    hold exactly one key, so a second purchase handed over a key covering only
    the new module and silently locked the first. Buying twice has to leave you
    owning both, so the grants UNION onto the licence the instance already has.

    `expires_at` moves outward only. Renewing one module must not shorten the
    year another still has left."""
    existing = licence_for_instance(instance)
    if not existing:
        key = new_key()
        issue(key, email, modules, note=note, expires_at=expires_at)
        bind(key, instance)
        return key, licence(key)

    key = existing["key"]
    merged = list(existing["modules"] or [])
    for m in modules:
        if m not in merged:
            merged.append(m)
    when = existing.get("expires_at")
    if expires_at and (not when or expires_at > when):
        when = expires_at
    old_note = (existing.get("note") or "").strip()
    issue(key, email or existing.get("email") or "", merged,
          note=(f"{old_note}; {note}".strip("; ") if note else old_note),
          expires_at=when)
    return key, licence(key)


def bind_history(key: str) -> list:
    _ensure_table()
    with db.connect() as conn:
        rows = conn.execute("SELECT instance_id, was, at FROM store_licence_binds "
                            "WHERE key = %s ORDER BY at", (key.strip(),)).fetchall()
    return [dict(r) for r in rows]


# ── the catalogue we publish ───────────────────────────────────────────────────
def _file() -> dict:
    if not CATALOG_FILE.exists():
        return {}
    try:
        return json.loads(CATALOG_FILE.read_text())
    except Exception:
        return {}


def catalog() -> list:
    """Every module offered, priced. Read from disk each time so a price can be
    corrected without a restart."""
    return _file().get("modules", [])


def bundles() -> list:
    """Products that are not modules: buy one, several modules unlock.

    A bundle may state its own `price_usd`, or state a `discount_pct` and let the
    price be DERIVED from what it contains. Derived is better for an all-access
    tier: change any module's price and the bundle cannot end up quoting a
    saving that stopped being true."""
    mods = {m["id"]: m for m in catalog()}
    raw = _file().get("bundles", [])
    priced = {b["id"]: b.get("price_usd") for b in raw}
    out = []
    for b in raw:
        b = dict(b)
        # An all-access bundle contains EVERY paid module, so it derives its
        # membership rather than listing it. A hand-written list is a promise
        # ("every paid module") kept by hand, and the day a module is published
        # the bundle quietly stops containing everything while still saying it
        # does — and undercharges, because the price derives from the list.
        if b.get("all_access"):
            b["includes"] = sorted(
                mid for mid, m in mods.items() if (m.get("price_usd") or 0) > 0)
        # A member may be a module or another bundle — an all-access tier
        # contains the packs as well as the singles, and the full price has to
        # count each product once, at what it actually sells for.
        parts = [mods[i].get("price_usd") if i in mods else priced.get(i)
                 for i in b.get("includes", [])]
        # Only a bundle whose parts are EVERY ONE separately buyable has a "was"
        # price. Unpriced members used to count as zero, which was harmless while
        # none of Market Data's members were sold alone — the sum was 0 and the
        # `or None` below dropped it. The moment one of them got a price of its
        # own the sum became partial instead of empty, and Market Data would have
        # advertised "$19 bought separately" against its own $29. A free member
        # (priced 0) is genuinely buyable and still counts.
        full = sum(parts) if parts and all(p is not None for p in parts) else 0
        b["full_price_usd"] = full or None
        if b.get("price_usd") is None and b.get("discount_pct"):
            b["price_usd"] = round(full * (100 - b["discount_pct"]) / 100)
        if full and b.get("price_usd") is not None and full > b["price_usd"]:
            b["saving_usd"] = full - b["price_usd"]
            b["discount_pct"] = round((full - b["price_usd"]) / full * 100)
        else:
            b.pop("saving_usd", None)
        out.append(b)
    return out


def terms() -> dict:
    """Currency and billing period. Data, not a string in the UI — the day these
    change, nothing needs recompiling."""
    f = _file()
    return {"currency": f.get("currency", "USD"), "billing": f.get("billing", "yearly")}


def available_versions() -> dict:
    """{module_id: version} for every ZIP actually sitting in dist/.

    The catalogue says what is FOR SALE; this says what can be DELIVERED. A
    module listed without a build is a broken promise, so the two are reported
    separately rather than assumed to agree."""
    import versions
    out = {}
    if not DIST_DIR.exists():
        return out
    for z in DIST_DIR.glob("*.zip"):
        mid, _, ver = z.stem.rpartition("-")
        if not mid:
            continue
        # dist/ accumulates every build ever published, so a module has several
        # ZIPs and glob order decides nothing. Take the HIGHEST — otherwise the
        # store's idea of "latest" depends on directory listing order, and can
        # offer an old build to everyone.
        if mid not in out or versions.newer(ver, out[mid]):
            out[mid] = ver
    return out


# ── licences ───────────────────────────────────────────────────────────────────
def licence(key: str) -> dict | None:
    if not key:
        return None
    _ensure_table()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT key, email, modules, note, revoked_at, expires_at, "
            "       (expires_at IS NOT NULL AND expires_at < now()) AS expired "
            "FROM store_licences WHERE key = %s", (key.strip(),)).fetchone()
    if not row or row["revoked_at"]:
        return None
    # An EXPIRED licence is still returned. What you bought keeps working — a
    # subscription that stops your software the day it lapses is a hostage
    # arrangement, not a licence. What lapses is the right to NEW versions, and
    # that is decided by `can_update`, not here.
    return dict(row)


def expired(key: str) -> bool:
    lic = licence(key)
    return bool(lic and lic.get("expired"))


def can_update(key: str, module_id: str) -> tuple[bool, str]:
    """May this licence receive a NEWER build of this module? (ok, why-not)

    Free modules are always updatable — there is no subscription to lapse. A
    paid one needs a licence that covers it AND has not run out; the module
    already installed keeps working either way."""
    if _is_free(module_id):
        return True, ""
    if not owns(key, module_id):
        return False, f"{module_id} is not covered by this licence."
    lic = licence(key) or {}
    if lic.get("expired"):
        when = str(lic.get("expires_at") or "")[:10]
        return False, (f"Your subscription for {module_id} ended{f' on {when}' if when else ''}. "
                       "The installed version keeps working — renew to receive updates.")
    return True, ""


def owns(key: str, module_id: str, _depth: int = 0) -> bool:
    """Does this licence cover this module?

    Three ways it can. A FREE module is owned by everyone, including someone
    with no licence at all — that is what free means, and making a self-hoster
    register to fetch the economic calendar would be a licence server pretending
    to be a store. A licence can name the module. Or it can name the module this
    one is INCLUDED WITH: HMR is not a product, it is part of Exness, and
    charging twice for one thing is how a catalogue starts lying."""
    if _is_free(module_id):
        return True
    lic = licence(key)
    if not lic:
        return False
    mods = lic["modules"] or []
    if "*" in mods or module_id in mods:
        return True
    # A bundle the licence names opens everything it contains. Asked this way
    # round rather than "what does this module belong to", so a bundle can be
    # added — an all-access tier, a seasonal pack — without every module it
    # covers having to name it.
    if _granted_by_bundle(mods, module_id):
        return True
    # HMR comes with Exness, and Exness comes with all-access — so owning the
    # parent has to be asked the same way, not merely looked up in the licence.
    # A licence naming a bundle owns what the bundle's members own.
    parent = _included_with(module_id)
    return bool(parent) and _depth < 4 and owns(key, parent, _depth + 1)


def _granted_by_bundle(owned_ids, module_id, _depth=0) -> bool:
    """Does anything the licence names contain this module, directly or through
    a bundle it contains? An all-access tier holds the packs, and the packs hold
    the modules — so the question has to walk down, not just look one level."""
    if _depth > 3:
        return False
    for b in bundles():
        if b["id"] not in owned_ids:
            continue
        members = b.get("includes") or []
        if module_id in members:
            return True
        if _granted_by_bundle(members, module_id, _depth + 1):
            return True
    return False


def _included_with(module_id: str) -> str | None:
    for m in catalog():
        if m.get("id") == module_id:
            return m.get("included_with")
    return None


def _is_free(module_id: str) -> bool:
    """Priced at zero. A module with NO price is not free — it is bundled, and
    its parent's licence is what opens it."""
    for m in catalog():
        if m.get("id") == module_id:
            return m.get("price_usd") == 0
    return False


def issue(key: str, email: str, modules: list, note: str = "",
          expires_at=None) -> dict:
    """`expires_at` None means perpetual — a one-off purchase rather than a
    subscription. Either way the software keeps running; expiry only ends the
    supply of new versions."""
    from psycopg.types.json import Json
    _ensure_table()
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO store_licences (key, email, modules, note, expires_at)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (key) DO UPDATE SET
                 email = EXCLUDED.email, modules = EXCLUDED.modules,
                 note = EXCLUDED.note, expires_at = EXCLUDED.expires_at,
                 revoked_at = NULL""",
            (key.strip(), (email or "").lower().strip(), Json(list(modules)), note,
             expires_at))
        conn.commit()
    return licence(key)


def revoke(key: str) -> bool:
    _ensure_table()
    with db.connect() as conn:
        n = conn.execute("UPDATE store_licences SET revoked_at = now() "
                         "WHERE key = %s AND revoked_at IS NULL", (key.strip(),)).rowcount
        conn.commit()
    return bool(n)


def licences() -> list:
    _ensure_table()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT key, email, modules, note, revoked_at, created_at "
            "FROM store_licences ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def new_key() -> str:
    """A licence key that is readable aloud — support will have to."""
    import secrets
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # no I/O/0/1
    blocks = ["".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(4)]
    return "ES-" + "-".join(blocks)


def zip_for(module_id: str) -> Path | None:
    ver = available_versions().get(module_id)
    if not ver:
        return None
    p = DIST_DIR / f"{module_id}-{ver}.zip"
    return p if p.is_file() else None
