"""
The module system — discover, validate, load.

A module is one capability, whole: its fetcher, its routes, its agent tool, its
flow node, its guide page and its tables, in one directory under `modules/`.
It announces itself through `registry`, which is also how core announces itself,
so a module is not a second-class citizen — it is the same citizen, arriving
later.

    modules/fedwatch/
        module.json          the manifest
        backend/…            python; imported as  entrystation_modules.fedwatch.…
        frontend/guide.json  a declarative guide page
        schema.sql           its own tables, IF NOT EXISTS

WHAT THIS FILE WILL NOT DO

  · It will not import a module whose `requires` are unmet — HMR without Exness
    is a page that 500s, and refusing is kinder than shipping that.
  · It will not let one bad module stop the app. A module that raises on load is
    recorded as failed WITH the reason and skipped; everything else still boots.
  · It will not run a module's schema more than once per version.

Loading happens at startup, on one thread, before the workers start.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import traceback
from pathlib import Path

import db
import registry

# Where installed modules live.
#
# Configurable because a packaged install cannot write into its own image: in a
# container the code is read-only at /app and this points at a mounted volume,
# so a module installed on Tuesday survives Wednesday's `docker compose pull`.
# Left unset it is the directory beside the code, which is what a git checkout
# wants and what every existing install already does.
MODULES_DIR = Path(os.environ.get("ENTRYSTATION_MODULES_DIR")
                   or Path(__file__).parent.parent / "modules")
MANIFEST = "module.json"
# The app's own version, and the ONLY thing that makes the update ribbon fire.
#
# An instance compares this against what the store reports. Ship without raising
# it and every self-hosted box stays silent no matter how much changed — which is
# exactly what happened after a day of work: both ends said 1.0.0, so there was
# nothing to notice.
#
# Raise it in the same commit as the change. See FINDINGS.md.
CORE_VERSION = "1.4.28"

# Loaded this boot: id -> {"manifest", "path", "status", "error"}
_loaded: dict = {}


# ── manifest ───────────────────────────────────────────────────────────────────
REQUIRED_FIELDS = ("id", "name", "version")


def read_manifest(path: Path) -> dict:
    """Parse and check one manifest. Raises ValueError with a sentence a human
    can act on — these are read at install time, where the error is the product."""
    f = path / MANIFEST
    if not f.exists():
        raise ValueError(f"no {MANIFEST} in {path.name}")
    try:
        m = json.loads(f.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"{MANIFEST} is not valid JSON: {e}")
    for field in REQUIRED_FIELDS:
        if not m.get(field):
            raise ValueError(f"{MANIFEST} is missing '{field}'")
    if not str(m["id"]).replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"module id {m['id']!r} must be alphanumeric with - or _")
    if m.get("edition") not in (None, "free", "paid"):
        raise ValueError("edition must be 'free' or 'paid'")
    return m


def _version_ok(have: str, want: str) -> bool:
    """Enough semver for '>=1.0' and '1.2.3'. Deliberately small: a module system
    that needs a full version solver on day one is a module system nobody ships."""
    want = (want or "").strip()
    if not want:
        return True
    op = ">="
    for candidate in (">=", "<=", "==", ">", "<"):
        if want.startswith(candidate):
            op, want = candidate, want[len(candidate):].strip()
            break
    def parts(v):
        out = []
        for piece in str(v).split("."):
            digits = "".join(c for c in piece if c.isdigit())
            out.append(int(digits) if digits else 0)
        return tuple(out + [0, 0])[:3]
    a, b = parts(have), parts(want)
    return {">=": a >= b, "<=": a <= b, "==": a == b, ">": a > b, "<": a < b}[op]


def check_requires(manifest: dict, installed: set) -> str | None:
    """None when the module can run; otherwise why it cannot."""
    req = manifest.get("requires") or {}
    core = req.get("core")
    if core and not _version_ok(CORE_VERSION, core):
        return f"needs core {core}, this is {CORE_VERSION}"
    missing = [m for m in (req.get("modules") or []) if m not in installed]
    if missing:
        return ("needs " + ", ".join(missing) + " — install "
                + ("it" if len(missing) == 1 else "those") + " first")
    return None


# ── the record of what is installed ────────────────────────────────────────────
def _ensure_table():
    with db.connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS modules (
                id           TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                version      TEXT NOT NULL,
                edition      TEXT,
                enabled      BOOLEAN NOT NULL DEFAULT true,
                schema_version TEXT,
                installed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_error   TEXT
            )""")
        conn.commit()


def installed() -> list:
    """Every module the database knows about, whether or not it loaded."""
    try:
        _ensure_table()
        with db.connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM modules ORDER BY id").fetchall()]
    except Exception:
        return []


def record(manifest: dict, *, enabled=True, error=None, schema_version=None):
    _ensure_table()
    with db.connect() as conn:
        conn.execute("""
            INSERT INTO modules (id, name, version, edition, enabled, schema_version, last_error)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (id) DO UPDATE SET
              name = EXCLUDED.name, version = EXCLUDED.version,
              edition = EXCLUDED.edition, enabled = EXCLUDED.enabled,
              schema_version = COALESCE(EXCLUDED.schema_version, modules.schema_version),
              last_error = EXCLUDED.last_error, updated_at = now()""",
            (manifest["id"], manifest["name"], manifest["version"],
             manifest.get("edition"), enabled, schema_version, error))
        conn.commit()


def set_enabled(module_id: str, enabled: bool) -> dict:
    """Switch a module on or off — and actually do it, now.

    Disabling used to only write a flag and ask for a restart. It never needed
    one: every registry a module writes into is a dict or a list read per
    request, including the app's own route table, so removing its entries takes
    effect on the next call. Turning it back on re-imports and re-registers it,
    which is what the boot path does anyway.

    The one thing that can outlive a disable is a background thread whose module
    gave no way to stop it. That is reported rather than papered over."""
    _ensure_table()
    with db.connect() as conn:
        conn.execute("UPDATE modules SET enabled = %s, updated_at = now() WHERE id = %s",
                     (enabled, module_id))
        conn.commit()
    return apply_live(module_id, enabled)


def dependents_of(module_id: str) -> list:
    """Installed modules that declare a requirement on this one."""
    out = []
    for p in discover():
        try:
            m = read_manifest(p)
        except ValueError:
            continue
        if module_id in ((m.get("requires") or {}).get("modules") or {}):
            out.append(m["id"])
    return sorted(out)


def apply_live(module_id: str, enabled: bool) -> dict:
    """Load or unload a module in the running app. Returns what it cost."""
    import registry
    if enabled:
        path = next((p for p in discover()
                     if (p / MANIFEST).exists() and read_manifest(p)["id"] == module_id), None)
        if path is None:
            return {"applied": False, "note": "not on disk"}
        have = {r["id"] for r in installed()
                if r.get("enabled", True) and r["id"] != module_id}
        res = load_one(path, have)
        _loaded[module_id] = res
        if res["status"] != "loaded":
            return {"applied": False, "status": res["status"], "error": res.get("error"),
                    "note": res.get("error") or f"could not load: {res['status']}"}

        # Symmetry with the disable cascade: anything that was switched off only
        # because THIS went away comes back with it. Without this, turning Exness
        # back on would leave HMR — still enabled, still on disk — sitting dark
        # until someone noticed and clicked it too.
        back = []
        enabled_now = {r["id"] for r in installed() if r.get("enabled", True)}
        for d in dependents_of(module_id):
            if d in enabled_now and _loaded.get(d, {}).get("status") != "loaded":
                if apply_live(d, True).get("applied"):
                    back.append(d)
        note = "enabled and serving"
        if back:
            note += f" (also started: {', '.join(back)}, which needed it)"
        return {"applied": True, "status": "loaded", "also_enabled": back,
                "provides": registry.provided_by(module_id), "note": note}

    # Off. Its dependents go first — leaving one running against a module that
    # has gone is worse than switching both off and saying so.
    also = [d for d in dependents_of(module_id)
            if _loaded.get(d, {}).get("status") == "loaded"]
    for d in also:
        registry.forget(d)
        _loaded.pop(d, None)
    lingering = registry.unstoppable_workers(module_id)
    registry.forget(module_id)
    _loaded.pop(module_id, None)
    note = "disabled — its endpoints, tools and pages are gone now"
    if also:
        note += f" (also stopped: {', '.join(also)}, which needed it)"
    if lingering:
        note += (f" · background worker{'s' if len(lingering) > 1 else ''} "
                 f"{', '.join(lingering)} keep running until the next restart")
    return {"applied": True, "status": "disabled", "also_disabled": also,
            "lingering_workers": lingering, "note": note}


def forget_record(module_id: str):
    _ensure_table()
    with db.connect() as conn:
        conn.execute("DELETE FROM modules WHERE id = %s", (module_id,))
        conn.commit()


# ── loading ────────────────────────────────────────────────────────────────────
def _run_schema(path: Path, manifest: dict):
    """A module's own tables. Run once per version — re-running IF NOT EXISTS
    statements is harmless, but a module that ships a migration is not."""
    sql = path / (manifest.get("provides", {}).get("schema") or "schema.sql")
    if not sql.exists():
        return None
    rows = installed()
    already = next((r for r in rows if r["id"] == manifest["id"]), None)
    if already and already.get("schema_version") == manifest["version"]:
        return manifest["version"]
    with db.connect() as conn:
        conn.execute(sql.read_text())
        conn.commit()
    return manifest["version"]


def _import(path: Path, module_id: str, dotted: str):
    """Import `backend/x.py` from a module directory under a namespaced package,
    so two modules may both have a `routes.py` without colliding."""
    file_part, _, attr = dotted.partition(":")
    rel = file_part.replace(".", "/") + ".py"
    target = path / rel
    if not target.exists():
        raise ValueError(f"{dotted} → {rel} does not exist in the module")
    name = f"entrystation_modules.{module_id}.{file_part.replace('/', '.')}"
    if name in sys.modules:
        mod = sys.modules[name]
    else:
        spec = importlib.util.spec_from_file_location(name, target)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        # The module's own directory first, so `import helpers` inside it works.
        sys.path.insert(0, str(target.parent))
        try:
            spec.loader.exec_module(mod)
        finally:
            try:
                sys.path.remove(str(target.parent))
            except ValueError:
                pass
    return getattr(mod, attr) if attr else mod


def load_one(path: Path, installed_ids: set) -> dict:
    """Load one module directory. Never raises: the result says what happened."""
    try:
        manifest = read_manifest(path)
    except ValueError as e:
        return {"id": path.name, "status": "invalid", "error": str(e)}

    mid = manifest["id"]
    why = check_requires(manifest, installed_ids)
    if why:
        # Also stays enabled: install the dependency and this loads next boot.
        record(manifest, enabled=True, error=why)
        return {"id": mid, "manifest": manifest, "status": "unmet", "error": why}

    try:
        schema_version = _run_schema(path, manifest)
        provides = manifest.get("provides") or {}

        # An entry point either registers itself when imported, or exposes a
        # `register(registry, module_id)` we call. Both are honest; the second is
        # easier to read, so it wins when present.
        for key in ("routes", "tool", "node", "worker", "setup"):
            dotted = provides.get(key)
            if not dotted:
                continue
            obj = _import(path, mid, dotted)
            if callable(obj):
                obj(registry, mid)
            elif hasattr(obj, "register"):
                obj.register(registry, mid)

        asset_dir = provides.get("assets")
        if asset_dir:
            d = (path / asset_dir).resolve()
            if d.is_dir() and str(d).startswith(str(path.resolve())):
                registry.assets(d, module=mid)

        guide_file = provides.get("guide")
        if guide_file:
            g = json.loads((path / guide_file).read_text())
            g.setdefault("id", mid)
            registry.guide(g, module=mid)

        for spec in (provides.get("settings") or []):
            registry.setting(spec["key"], spec, module=mid)

        record(manifest, enabled=True, error=None, schema_version=schema_version)
        return {"id": mid, "manifest": manifest, "status": "loaded",
                "provides": registry.provided_by(mid)}
    except Exception as e:
        registry.forget(mid)          # half-registered is worse than not at all
        err = f"{type(e).__name__}: {e}"
        print(f"[modules] {mid} failed to load: {err}\n{traceback.format_exc()}", flush=True)
        # Stays ENABLED. A failure is a status, not a decision — so a module fixed
        # between restarts simply loads, rather than needing someone to remember
        # it was switched off on its behalf.
        record(manifest, enabled=True, error=err)
        return {"id": mid, "manifest": manifest, "status": "failed", "error": err}


def discover() -> list:
    """Module directories on disk, in dependency order where it can be worked out."""
    if not MODULES_DIR.exists():
        return []
    dirs = [p for p in sorted(MODULES_DIR.iterdir())
            if p.is_dir() and (p / MANIFEST).exists()]

    # A module that requires another must load after it. One pass over a handful
    # of modules is enough; a cycle simply keeps its original order and fails the
    # requires-check with a readable message, which is the right outcome anyway.
    ordered, placed = [], set()
    for _ in range(len(dirs) + 1):
        for p in dirs:
            if p in placed:
                continue
            try:
                needs = set((read_manifest(p).get("requires") or {}).get("modules") or [])
            except ValueError:
                needs = set()
            have = {read_manifest(q)["id"] for q in ordered if (q / MANIFEST).exists()}
            if needs <= have:
                ordered.append(p)
                placed.add(p)
    ordered += [p for p in dirs if p not in placed]
    return ordered


def load_all(log=print) -> list:
    """Load every module on disk. Returns one result per module."""
    results, ids = [], set()
    disabled = {r["id"] for r in installed() if not r.get("enabled", True)}
    for path in discover():
        try:
            mid = read_manifest(path)["id"]
        except ValueError:
            mid = path.name
        if mid in disabled:
            results.append({"id": mid, "status": "disabled"})
            continue
        res = load_one(path, ids)
        if res["status"] == "loaded":
            ids.add(res["id"])
        results.append(res)
        _loaded[res["id"]] = res

    ok = [r for r in results if r["status"] == "loaded"]
    bad = [r for r in results if r["status"] in ("failed", "invalid", "unmet")]
    if results:
        log(f"[modules] {len(ok)} loaded"
            + (f", {len(bad)} not: " + "; ".join(f"{r['id']} ({r.get('error')})" for r in bad)
               if bad else ""))
    return results


def loaded() -> dict:
    return dict(_loaded)


def status() -> dict:
    """What is installed, what loaded, and what each one contributed."""
    rows = {r["id"]: r for r in installed()}
    out = []
    for mid, res in _loaded.items():
        row = rows.get(mid, {})
        out.append({
            "id": mid,
            "name": (res.get("manifest") or {}).get("name") or mid,
            "version": (res.get("manifest") or {}).get("version"),
            "edition": (res.get("manifest") or {}).get("edition"),
            "status": res["status"],
            "error": res.get("error") or row.get("last_error"),
            "provides": registry.provided_by(mid),
        })
    # A CLI process never loads modules, so "not loaded" there means "not loaded
    # HERE", not "broken". Saying `not-loaded` to someone running `list` reads as
    # a fault in a module that is working perfectly well inside the app.
    this_process_loads = bool(_loaded)
    for mid, row in rows.items():
        if mid not in _loaded:
            if not row.get("enabled"):
                st = "disabled"
            elif row.get("last_error"):
                st = "failed"
            else:
                st = "not-loaded" if this_process_loads else "installed"
            out.append({"id": mid, "name": row["name"], "version": row["version"],
                        "edition": row.get("edition"), "status": st,
                        "error": row.get("last_error"), "provides": []})
    return {"core_version": CORE_VERSION, "modules_dir": str(MODULES_DIR),
            "count": len(out), "modules": sorted(out, key=lambda m: m["id"])}
