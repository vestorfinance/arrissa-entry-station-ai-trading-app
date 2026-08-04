"""Take the module updates this instance is entitled to, without being asked.

Almost all of this existed already. `catalog.view()` says what is newer,
`store.can_update` says whether this licence may have it, and the update
endpoint does the work. What was missing was anything that RUNS it: an instance
sat on a fix until somebody opened the store and happened to notice the badge.

Entitlement is not re-decided here. A lapsed subscription is refused by
`store.can_update`, exactly as it is refused when the button is pressed — one
rule, in one place, so the automatic path and the manual one can never disagree
about what somebody is owed. What lapses is the supply of NEW versions; what is
installed keeps running, which is the same promise the store page makes.

Self-hosted only. The hosted service is deployed by rsync, and a store that
installed from itself would be updating the directory it publishes from.

Applying an update means restarting, and that is not squeamishness: modules load
at import time, so a downloaded module is inert until the process comes back.
Both supervisors we ship under restart it (`Restart=always`, and compose's
`unless-stopped`), so exiting IS the way to apply it. It waits for the instance
to be idle first — a restart in the middle of somebody's analysis loses the run.
"""
import os
import threading
import time

# Every six hours. An update is not urgent enough to hammer the store for, and
# the check costs a request whether or not anything has changed.
INTERVAL = 6 * 3600
# Not on boot. A fresh start has schema migrations, module loading and the
# fetchers all going at once, and a download on top of that is how a small
# machine falls over on the first boot somebody is watching.
FIRST_DELAY = 300
# How quiet is quiet enough to restart, and how long to wait for it before
# giving up and leaving it for the next tick.
IDLE_SECONDS = 90
IDLE_WAIT = 3600

_stop = threading.Event()
_inflight = 0
_inflight_lock = threading.Lock()
_last_request = 0.0


def note_request(delta: int) -> None:
    """Called around every HTTP request, so `_idle()` knows what it would cut off."""
    global _inflight, _last_request
    with _inflight_lock:
        _inflight += delta
        if delta < 0:
            _last_request = time.time()


def _idle() -> bool:
    with _inflight_lock:
        return _inflight <= 0 and (time.time() - _last_request) > IDLE_SECONDS


def enabled() -> bool:
    """On unless someone turned it off. Off by default would mean the instances
    that most need a fix — the unattended ones — are the ones that never get it.

    The environment wins over the database so an operator who wants nothing
    touching their box can say so once, in the file they already keep."""
    if (os.getenv("ENTRYSTATION_AUTO_UPDATE") or "").strip().lower() in ("0", "false", "no", "off"):
        return False
    try:
        import db
        with db.connect() as conn:
            row = conn.execute("SELECT auto_update FROM admin_settings WHERE id = 1").fetchone()
        if row and row["auto_update"] is not None:
            return bool(row["auto_update"])
    except Exception:
        pass
    return True


def run_once(log=print) -> dict:
    """Install every update this instance is entitled to. Returns what it took."""
    import catalog
    import module_installer
    import tempfile

    took, refused, failed = [], [], []
    try:
        view = catalog.view()
    except Exception as e:
        log(f"[auto-update] could not read the catalogue: {e}")
        return {"took": [], "refused": [], "failed": [], "error": str(e)}

    for m in view.get("modules", []):
        if not m.get("update_available"):
            continue
        if not m.get("can_update"):
            # Not an error and not worth a warning every six hours — it is the
            # subscription doing what a subscription does.
            refused.append({"id": m["id"], "why": m.get("update_blocked") or "not entitled"})
            continue
        before = m.get("installed_version")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                z = catalog.download(m["id"], tmp)
                res = module_installer.install(z, force=True)
            took.append({"id": m["id"], "from": before, "to": res.get("version")})
            log(f"[auto-update] {m['id']} {before} -> {res.get('version')}")
        except Exception as e:
            # One module failing must not stop the others. A broken build is
            # exactly when the rest of the fixes matter.
            failed.append({"id": m["id"], "error": str(e)})
            log(f"[auto-update] {m['id']} failed: {e}")

    if refused:
        log(f"[auto-update] {len(refused)} update(s) held back: "
            + ", ".join(r["id"] for r in refused))
    return {"took": took, "refused": refused, "failed": failed}


def apply_when_idle(log=print) -> None:
    """Restart, so the code that was just downloaded is the code that is running.

    Waits for the instance to go quiet. If it never does, the update stays on
    disk and the next tick tries again — six hours late is better than dropping
    the request somebody is waiting on."""
    waited = 0
    while not _idle():
        if _stop.wait(15) or waited >= IDLE_WAIT:
            log("[auto-update] still busy — the update is on disk and applies on the next restart")
            return
        waited += 15
    log("[auto-update] restarting to apply the update")
    # A clean exit. The supervisor brings it straight back with the new modules
    # loaded; os._exit skips the shutdown handlers that would otherwise try to
    # stop workers we are about to abandon anyway.
    os._exit(0)


def _loop():
    if _stop.wait(FIRST_DELAY):
        return
    while not _stop.is_set():
        try:
            if enabled():
                res = run_once()
                if res.get("took"):
                    apply_when_idle()
        except Exception as e:
            print(f"[auto-update] {e!r}", flush=True)
        if _stop.wait(INTERVAL):
            return


def start():
    import edition
    if not edition.is_community():
        return
    threading.Thread(target=_loop, name="auto-update", daemon=True).start()


def stop():
    _stop.set()
