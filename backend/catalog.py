"""
The buying side — what THIS instance knows about modules it could have.

The Modules page is a shop window, and a shop window needs two things this
machine cannot supply on its own: what exists, and what it costs. Both come from
the store over HTTPS, and are merged here with the one thing the store cannot
know — what is already installed and running right now.

Every failure mode ends with a usable page. No network, no licence, a store that
is down: the page still lists what is installed and says plainly why the rest is
missing, because an operator whose instance cannot reach the store still needs to
disable a module.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import auth
import db
import versions

STORE_URL = os.getenv("ENTRYSTATION_STORE", "https://entrystation.com").rstrip("/")
CACHE_TTL_S = 300

_cache = {"at": 0.0, "data": None, "error": None,
          "terms": {"currency": "USD", "billing": "yearly", "bundles": []}}


# ── the instance's licence key ─────────────────────────────────────────────────
_column_ready = False


def _ensure_column():
    """Once per process, not once per read.

    ALTER TABLE takes an ACCESS EXCLUSIVE lock even when it changes nothing, so
    running it on every licence lookup made two ordinary page loads able to
    deadlock against each other — which is exactly what a Modules page that
    loads the catalogue and claims entitlements at the same time does. Observed
    live: `DeadlockDetected ... ALTER TABLE admin_settings`."""
    global _column_ready
    if _column_ready:
        return
    with db.connect() as conn:
        conn.execute("INSERT INTO admin_settings (id) VALUES (1) ON CONFLICT DO NOTHING")
        conn.execute("ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS "
                     "store_licence_enc TEXT")
        # Which free modules this instance has already been given. Not the same
        # question as "what is installed": removing one deletes its record, so
        # without this a module the owner deliberately removed would be handed
        # back on the next restart, for ever.
        conn.execute("ALTER TABLE admin_settings ADD COLUMN IF NOT EXISTS "
                     "seeded_modules TEXT")
        conn.commit()
    _column_ready = True


def licence_key() -> str:
    _ensure_column()
    with db.connect() as conn:
        row = conn.execute("SELECT store_licence_enc FROM admin_settings WHERE id = 1").fetchone()
    if not row or not row["store_licence_enc"]:
        return ""
    try:
        return auth.decrypt(row["store_licence_enc"])
    except Exception:
        return ""


def set_licence_key(raw: str) -> bool:
    _ensure_column()
    enc = auth.encrypt(raw.strip()) if raw and raw.strip() else None
    with db.connect() as conn:
        conn.execute("UPDATE admin_settings SET store_licence_enc = %s, updated_at = now() "
                     "WHERE id = 1", (enc,))
        conn.commit()
    _cache.update(at=0.0, data=None, error=None)      # entitlements just changed
    return bool(enc)


# ── the free modules, already there ────────────────────────────────────────────
#
# A Community box is somebody's own server, and the free modules are not a
# reward for finding them — they are what the app IS without paying anyone. So
# they are installed for the owner rather than displayed to them: a shop window
# is the wrong shape for the things that were never for sale.
#
# Seeded ONCE each, remembered by id. A module the owner then removes stays
# removed — the alternative is software that undoes their decisions every time
# it restarts — while a newly published free module is picked up on the next
# start, because its id has not been seen before.
def _seeded() -> set:
    _ensure_column()
    with db.connect() as conn:
        row = conn.execute("SELECT seeded_modules FROM admin_settings WHERE id = 1").fetchone()
    if not row or not row["seeded_modules"]:
        return set()
    try:
        import json
        return set(json.loads(row["seeded_modules"]))
    except Exception:
        return set()


def _mark_seeded(ids) -> None:
    import json
    _ensure_column()
    with db.connect() as conn:
        conn.execute("UPDATE admin_settings SET seeded_modules = %s WHERE id = 1",
                     (json.dumps(sorted(_seeded() | set(ids))),))
        conn.commit()


def install_free(log=print) -> list:
    """Put every free module the store offers onto this instance. Returns what
    it did, and raises nothing: no network, a store that is down, or one module
    that will not install must never stop the app from starting."""
    import modules as module_system

    try:
        offered, err = remote()
    except Exception as e:
        log(f"[seed] the store could not be read ({e}) — no free modules were added")
        return []
    if err and not offered:
        log(f"[seed] {err} — no free modules were added")
        return []

    already = _seeded()
    done, added = [], []
    for m in offered:
        mid = m.get("id")
        # price 0 is free; price None is BUNDLED, and its parent's licence opens
        # it — handing that out would be giving away something that is sold.
        if not mid or m.get("price_usd") != 0 or mid in already:
            continue
        if (module_system.MODULES_DIR / mid).is_dir():
            added.append(mid)                     # present already: seeded, quietly
            continue
        if not m.get("deliverable"):
            log(f"[seed] {mid} is free but the store has no build of it yet")
            continue
        try:
            import tempfile
            import module_installer
            with tempfile.TemporaryDirectory() as tmp:
                res = module_installer.install(download(mid, tmp))
            done.append(res)
            added.append(mid)
            log(f"[seed] {mid} {res.get('version')} installed — free with this instance")
            # And its history, so a first boot has something to show rather than
            # an empty chart and a week of waiting.
            try:
                got = backfill(mid, log=log)
                if got.get("rows"):
                    log(f"[seed] {mid}: {got['rows']} historical row(s) from the store")
            except Exception as e:
                log(f"[seed] {mid}: history could not be fetched: {e}")
        except Exception as e:
            # Named, not swallowed. A free module that cannot install is a broken
            # promise the owner should hear about, and the app still starts.
            log(f"[seed] {mid} could not be installed: {type(e).__name__}: {e}")

    if added:
        _mark_seeded(added)
    return done


# ── claiming what this box has already bought ──────────────────────────────────
#
# Pasting a key was never a step anyone wanted. The operator pays on
# entrystation.com and comes back here, and this box can simply ASK what it owns
# — the purchase was made in its name, so the answer belongs to it.
#
# It has to prove it is itself first, or the question would be a way of stealing
# other people's keys: the store issues a challenge, this holds it in memory and
# serves it at a public URL, and the store comes back to look. Nothing is
# persisted, because a challenge that outlived the claim would be a credential
# lying around for no reason.
#
# The key stays as the fallback it should always have been: for moving servers,
# and for a box the store cannot reach.
_challenge = {"value": "", "at": 0.0}
CHALLENGE_TTL_S = 300


def published_challenge() -> str:
    """What this instance is currently proving, if anything. Empty when idle —
    there is no standing secret here, only one that exists during a claim."""
    if not _challenge["value"] or time.time() - _challenge["at"] > CHALLENGE_TTL_S:
        return ""
    return _challenge["value"]


def claim(instance: str) -> dict:
    """Fetch and apply whatever `instance` has bought. Returns what happened.

    Never raises for the ordinary failures — an unreachable store, a box with no
    public name, nothing bought yet. Those are things to SAY on the page, and a
    Modules page that failed to load because a licence check failed would be a
    worse page than one with a line of explanation on it."""
    from curl_cffi import requests as creq
    import store as _store

    host = _store.normalise_instance(instance)
    if not host:
        return {"applied": False, "reason": "This instance has no name to claim with."}
    try:
        r = creq.post(f"{STORE_URL}/api/store/claim/start", params={"instance": host},
                      impersonate="chrome", timeout=20)
        if r.status_code == 400:
            return {"applied": False, "reason": (r.json() or {}).get("detail")
                    or f"{host} cannot be verified from the store."}
        r.raise_for_status()
        started = r.json() or {}
        _challenge.update(value=started.get("challenge") or "", at=time.time())
        if not _challenge["value"]:
            return {"applied": False, "reason": "The store issued no challenge."}

        # The store now fetches /api/modules/instance-check on this box. That is a
        # second request arriving while this one is still open, which is fine:
        # these handlers are threaded, so this one waiting does not stop that one
        # being answered.
        r = creq.post(f"{STORE_URL}/api/store/claim/finish",
                      params={"claim_token": started.get("claim_token")},
                      impersonate="chrome", timeout=30)
        if r.status_code in (400, 403, 404):
            return {"applied": False, "reason": (r.json() or {}).get("detail")
                    or "The claim could not be proved."}
        r.raise_for_status()
        out = r.json() or {}
    except Exception as e:
        return {"applied": False,
                "reason": f"Could not reach the store at {STORE_URL}: {e}"}
    finally:
        _challenge.update(value="", at=0.0)          # the proof is spent either way

    key = out.get("licence_key")
    if not key:
        return {"applied": False, "modules": out.get("modules", []),
                "reason": out.get("reason") or "Nothing has been bought for this instance yet."}
    if key == licence_key():
        return {"applied": False, "already": True, "modules": out.get("modules", []),
                "reason": "This instance's licence was already up to date."}
    set_licence_key(key)
    return {"applied": True, "modules": out.get("modules", []),
            "expires_at": out.get("expires_at")}


# ── the store's catalogue ──────────────────────────────────────────────────────
def remote(force=False) -> tuple[list, str | None]:
    """(modules, error). Cached briefly: the shop window is opened often and the
    prices change rarely."""
    now = time.time()
    if not force and _cache["data"] is not None and now - _cache["at"] < CACHE_TTL_S:
        return _cache["data"], _cache["error"]
    try:
        from curl_cffi import requests as creq
        key = licence_key()
        r = creq.get(f"{STORE_URL}/api/store/catalog",
                     params={"key": key} if key else None,
                     impersonate="chrome", timeout=15)
        r.raise_for_status()
        body = r.json() or {}
        mods = body.get("modules", [])
        _cache["terms"] = {"currency": body.get("currency", "USD"),
                           "billing": body.get("billing", "yearly"),
                           "bundles": body.get("bundles", []),
                           "store_core": (body.get("core") or {}).get("version"),
                           "store_build": body.get("core") or {}}
        _cache.update(at=now, data=mods, error=None)
        return mods, None
    except Exception as e:
        err = f"could not reach the module store at {STORE_URL}: {e}"
        # Keep whatever was last fetched. A store outage should cost the page its
        # prices, not its list.
        _cache.update(at=now, error=err)
        return (_cache["data"] or []), err


def _update_gate(module_id: str, offered: bool) -> dict:
    """Whether a newer build may actually be fetched, and if not, why.

    Said in the same breath as the offer: an Update button that fails with 402
    when pressed is worse than one that explains itself before you press it."""
    if not offered:
        return {"can_update": False, "update_blocked": ""}
    import store
    ok, why = store.can_update(licence_key(), module_id)
    return {"can_update": ok, "update_blocked": "" if ok else why}


def view() -> dict:
    """The shop window: everything on offer, merged with what is installed.

    One row per module, whether it came from the store, from disk, or both — a
    module installed from a hand-built ZIP that the store has never heard of is
    still a module, and hiding it would make the page lie about what is running.
    """
    import modules as module_system
    import registry

    offered, error = remote()
    local = {m["id"]: m for m in module_system.status()["modules"]}

    rows = {}
    for m in offered:
        mid = m["id"]
        inst = local.get(mid)
        rows[mid] = {
            **m,
            "installed": bool(inst),
            "status": (inst or {}).get("status"),
            "installed_version": (inst or {}).get("version"),
            "enabled": (inst or {}).get("status") not in ("disabled", None),
            "error": (inst or {}).get("error"),
            "provides": (inst or {}).get("provides", []),
            # STRICTLY newer. This was `!=`, and dist/ held tradelocker-1.0.0
            # while 1.1.0 was installed — so every user was offered a downgrade
            # with "update available" written on it.
            "update_available": bool(inst) and versions.newer(m.get("version"),
                                                              inst.get("version")),
            **_update_gate(mid, bool(inst) and versions.newer(m.get("version"),
                                                              inst.get("version"))),
        }
    for mid, inst in local.items():
        if mid in rows:
            continue
        rows[mid] = {
            "id": mid, "name": inst.get("name") or mid, "icon": None, "group": "other",
            "tagline": "Installed from a file — not listed in the store.",
            "price_usd": None, "purchase_url": None, "requires": [],
            "version": inst.get("version"), "deliverable": False, "owned": True,
            "installed": True, "status": inst.get("status"),
            "installed_version": inst.get("version"),
            "enabled": inst.get("status") not in ("disabled", None),
            "error": inst.get("error"), "provides": inst.get("provides", []),
            "update_available": False,
        }

    order = {"broker": 0, "trading": 1, "analysis": 2, "other": 3}
    out = sorted(rows.values(),
                 key=lambda r: (order.get(r.get("group") or "other", 3),
                                (r.get("price_usd") if r.get("price_usd") is not None else 999),
                                r["name"]))
    # Is CORE itself behind? Modules announce their versions through the
    # catalogue and core had no equivalent, so an instance could sit a year
    # behind without anything ever saying so.
    import modules as module_system
    import build_info
    store_core = _cache["terms"].get("store_core")
    theirs = _cache["terms"].get("store_build") or {}
    mine = build_info.describe()

    # The BUILD decides it, not the version name. CORE_VERSION is a constant a
    # person raises by hand, and twice it shipped unraised — so every instance
    # compared 1.2.0 against 1.2.0 and correctly concluded there was nothing to
    # do while sixteen commits sat published. The stamp is written by the act of
    # publishing, so it cannot be forgotten and cannot disagree with the code.
    #
    # The version name is still consulted, for a store or an instance old enough
    # to carry no stamp: an update must not go unreported just because one end
    # of the conversation predates this.
    core_update = build_info.newer_than(mine, theirs)
    if not core_update and store_core:
        core_update = bool(versions.newer(store_core, module_system.CORE_VERSION))

    return {"store_url": STORE_URL, "has_licence": bool(licence_key()),
            **_cache["terms"], "error": error, "modules": out,
            "core": {**mine, "latest": store_core,
                     "latest_build": theirs.get("date"),
                     "update_available": core_update}}


# ── history that came before this instance existed ────────────────────────────
#
# A module installed today starts with an empty table, and none of what these
# modules collect can be collected retrospectively: Fed Watch is a running
# series, news is what was published while something was watching. So a fresh
# instance has the capability and nothing to point it at, and looks broken for
# weeks through no fault of its own.
#
# The store has been gathering it the whole time. This asks for it.
def backfill(module_id: str, log=print) -> dict:
    """Fill a newly installed module's tables from the store. Never raises."""
    import edition

    # The cloud instance IS the store. Asking itself for its own rows would be a
    # round trip to insert what it already has.
    if not edition.is_community():
        return {"skipped": "this instance is the store"}

    try:
        from curl_cffi import requests as creq
        r = creq.get(f"{STORE_URL}/api/store/backfill/{module_id}",
                     params={"key": licence_key()} if licence_key() else None,
                     impersonate="chrome", timeout=120)
        if r.status_code == 402:
            return {"skipped": "not licensed for this module"}
        r.raise_for_status()
        body = r.json() or {}
    except Exception as e:
        log(f"[backfill] {module_id}: the store could not be reached: {e}")
        return {"error": str(e)}

    tables = body.get("tables") or {}
    if not tables:
        return {"rows": 0, "note": body.get("note") or "the store had nothing to send"}

    import re
    ok = re.compile(r"^[a-z_][a-z0-9_]*$")
    written = 0
    with db.connect() as conn:
        for name, rows in tables.items():
            if not ok.match(name) or not rows:
                continue
            cols = list(rows[0].keys())
            if not all(ok.match(c) for c in cols):
                continue
            collist = ", ".join(cols)
            marks = ", ".join(["%s"] * len(cols))
            # ON CONFLICT DO NOTHING is what makes this safe to run twice, and
            # what stops it overwriting anything this instance gathered itself:
            # the local row always wins.
            sql = (f"INSERT INTO {name} ({collist}) VALUES ({marks}) "
                   f"ON CONFLICT DO NOTHING")
            for row in rows:
                try:
                    conn.execute(sql, tuple(row.get(c) for c in cols))
                    written += 1
                except Exception:
                    # One malformed row must not cost the other 19,999.
                    conn.rollback()
                    continue
        conn.commit()
    log(f"[backfill] {module_id}: {written} row(s) from the store")
    return {"rows": written}


def download(module_id: str, dest_dir) -> Path:
    """Fetch a module's archive from the store into `dest_dir`.

    The bytes are NOT trusted on arrival — they go through the same installer as
    a hand-uploaded ZIP, signature check and all. Downloading is a delivery
    mechanism, not a reason to skip verification."""
    from curl_cffi import requests as creq

    key = licence_key()
    r = creq.get(f"{STORE_URL}/api/store/download/{module_id}",
                 params={"key": key} if key else None,
                 impersonate="chrome", timeout=120)
    if r.status_code == 402:
        raise PermissionError((r.json() or {}).get("detail")
                              or f"no licence for {module_id}")
    r.raise_for_status()
    p = Path(dest_dir) / f"{module_id}.zip"
    p.write_bytes(r.content)
    return p
