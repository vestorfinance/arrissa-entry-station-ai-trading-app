"""
Module manager API — install, remove, enable, and the guides modules contribute.

Two audiences, two access levels:

  · `/api/modules/guides` is open to any signed-in user. It is how the frontend
    learns which pages and nav entries exist, and a user who cannot see them
    cannot use the modules that are installed.

  · everything that CHANGES anything is OWNER-ONLY, and deliberately so. A module
    is arbitrary code running in this process. On a self-hosted box the operator
    and the user are the same person, so that is their business. In a hosted,
    multi-tenant deployment it never can be — a tenant-uploaded module would run
    beside other tenants' data — which is why installation is gated on the
    console's own admin check rather than on a plan or a role.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from fastapi.responses import FileResponse

import modules as module_system
import module_installer
import registry
from admin_api import _current_user, require_admin, audit

router = APIRouter(prefix="/api/modules", tags=["modules"])

MAX_ZIP_BYTES = 25 * 1024 * 1024      # a module is code and a guide, not a dataset


# ── reading ────────────────────────────────────────────────────────────────────
@router.get("")
def list_modules(user=Depends(require_admin)):
    """Everything installed, what it provides, and whether it actually loaded."""
    return module_system.status()


@router.get("/guides")
def module_guides(user=Depends(_current_user)):
    """The declarative guide pages installed modules contribute.

    This is what lets a ZIP add a page to a frontend bundle that was built
    without it: the page is DATA, rendered by a component core already ships."""
    return {"guides": registry.guides()}


@router.get("/palette")
def module_palette(user=Depends(_current_user)):
    """Flow-canvas palette entries from installed modules, so the builder offers
    a module's node without the bundle knowing it exists."""
    return {"nodes": registry.palette()}


@router.get("/{module_id}/asset/{name:path}")
def module_asset(module_id: str, name: str):
    """A static file from a module's own assets directory — a broker logo, an
    image in its guide.

    UNAUTHENTICATED, deliberately. An `<img src>` cannot carry a bearer token, so
    gating this would mean no module could ever show a picture. What it can serve
    is one resolved directory per module, and a path that escapes it is refused
    rather than merely discouraged."""
    root = registry.asset_dir(module_id)
    if root is None:
        raise HTTPException(404, "no assets")
    target = (root / name).resolve()
    if not str(target).startswith(str(root)) or not target.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(target, headers={"Cache-Control": "public, max-age=86400"})


# ── changing ───────────────────────────────────────────────────────────────────
@router.post("/install")
async def install_module(file: UploadFile = File(...), force: bool = False,
                         user=Depends(require_admin)):
    """Install from an uploaded ZIP. The module loads on the next restart."""
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(400, "A module is a .zip archive.")
    body = await file.read()
    if len(body) > MAX_ZIP_BYTES:
        raise HTTPException(413, f"That archive is {len(body) // 1024 // 1024} MB; "
                                 f"the limit is {MAX_ZIP_BYTES // 1024 // 1024} MB.")
    with tempfile.TemporaryDirectory() as tmp:
        z = Path(tmp) / (file.filename or "module.zip")
        z.write_bytes(body)
        try:
            res = module_installer.install(z, force=force)
        except module_installer.InstallError as e:
            # The installer's refusals are written to be read by a person, so they
            # go through to the UI intact rather than becoming "install failed".
            raise HTTPException(400, str(e))
    audit(user["email"], "module.install", "module", res["id"],
          {"version": res["version"], "edition": res.get("edition")})
    return res


# ── the shop window ────────────────────────────────────────────────────────────
@router.get("/catalog")
def catalog_view(user=Depends(require_admin)):
    """Everything on offer merged with everything installed — one row per module."""
    import catalog
    return catalog.view()


class LicenceBody(BaseModel):
    key: str = ""


@router.post("/licence")
def set_licence(body: LicenceBody, user=Depends(require_admin)):
    """The licence key bought on entrystation.com. It entitles this INSTANCE, not
    a person: a self-hosted box has to be able to fetch an update with nobody
    logged in.

    The fallback path now, not the main one — `/claim` is how a purchase normally
    arrives. This is for moving to a new server, and for a box the store cannot
    reach to verify."""
    import catalog
    has = catalog.set_licence_key(body.key)
    audit(user["email"], "module.licence", "licence", "instance", {"set": has})
    return {"has_licence": has, **catalog.view()}


@router.get("/instance-check")
def instance_check(request: Request):
    """The challenge this box is currently proving, for the store to come and read.

    UNAUTHENTICATED of necessity: the caller is entrystation.com, which has no
    account here — that is the whole point of it being an outside check. It
    discloses nothing. Between claims it is an empty string, and during one it is
    a random value the store itself has just issued and is about to throw away.
    Serving it proves only that whoever started the claim controls this host,
    which is exactly the fact in question."""
    import catalog
    return {"instance": (request.headers.get("host") or "").split(":")[0],
            "challenge": catalog.published_challenge()}


class ClaimBody(BaseModel):
    instance: str = ""          # blank ⇒ the host this request arrived on


@router.post("/claim")
def claim_entitlements(body: ClaimBody, request: Request, user=Depends(require_admin)):
    """Ask the store what this instance has bought, and apply it.

    This is what makes a purchase arrive on its own. The operator pays on
    entrystation.com; this box proves it is the box that paid and collects the
    entitlement. Nobody types a key.

    The instance defaults to the host this request came in on rather than
    anything stored, because that is the name the buyer's browser used and so the
    name the purchase was made against."""
    import catalog
    host = (body.instance or request.headers.get("host") or "").split(":")[0]
    out = catalog.claim(host)
    if out.get("applied"):
        audit(user["email"], "module.claim", "licence", host,
              {"modules": out.get("modules", [])})
    # The page's own `modules` are the catalogue rows, and the claim's are the ids
    # it just unlocked. Merging the two dicts blind let one silently overwrite the
    # other, so what was claimed is named apart from what is on offer.
    return {**catalog.view(),
            "applied": bool(out.get("applied")), "already": bool(out.get("already")),
            "reason": out.get("reason"), "claimed": out.get("modules", []),
            "licence_expires_at": out.get("expires_at")}


@router.post("/install-remote/{module_id}")
def install_remote(module_id: str, force: bool = False, user=Depends(require_admin)):
    """Buy-then-click: fetch the module from the store and install it live.

    The download is not trusted for being a download — it goes through the same
    installer as an uploaded file, signature check included."""
    import catalog
    with tempfile.TemporaryDirectory() as tmp:
        try:
            z = catalog.download(module_id, tmp)
        except PermissionError as e:
            raise HTTPException(402, str(e))
        except Exception as e:
            raise HTTPException(502, f"Could not download {module_id}: {e}")
        try:
            res = module_installer.install(z, force=force)
        except module_installer.InstallError as e:
            raise HTTPException(400, str(e))
    audit(user["email"], "module.install_remote", "module", module_id,
          {"version": res.get("version")})
    # The history that existed before this instance did. A module installed today
    # starts with an empty table and cannot collect the past, so it would look
    # broken for weeks; the store has been gathering it the whole time.
    try:
        res["backfill"] = catalog.backfill(module_id)
    except Exception as e:
        res["backfill"] = {"error": str(e)}
    return res


@router.post("/{module_id}/update")
def update_module(module_id: str, user=Depends(require_admin)):
    """Fetch and install a NEWER build of a module already installed.

    Deliberately not just install-remote with a different name: it refuses to
    run unless the store is actually offering something newer, and unless the
    licence still entitles this instance to it. A module whose subscription has
    lapsed keeps running exactly as it is — what stops is the supply of new
    versions.

    The schema comes with it. `_run_schema` is gated on the module's VERSION, so
    a release that ships new tables applies them on the way in, once."""
    import catalog, store, versions

    row = next((m for m in catalog.view()["modules"] if m["id"] == module_id), None)
    if not row:
        raise HTTPException(404, f"{module_id} is not in the store")
    if not row.get("installed"):
        raise HTTPException(409, f"{module_id} is not installed — install it first")
    if not versions.newer(row.get("version"), row.get("installed_version")):
        raise HTTPException(409,
            f"{module_id} is already at {row.get('installed_version')}; "
            f"the store offers {row.get('version') or 'nothing newer'}")
    ok, why = store.can_update(catalog.licence_key(), module_id)
    if not ok:
        raise HTTPException(402, why)

    before = row.get("installed_version")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            z = catalog.download(module_id, tmp)
        except PermissionError as e:
            raise HTTPException(402, str(e))
        except Exception as e:
            raise HTTPException(502, f"Could not download {module_id}: {e}")
        try:
            res = module_installer.install(z, force=True)
        except module_installer.InstallError as e:
            raise HTTPException(400, str(e))
    audit(user["email"], "module.update", "module", module_id,
          {"from": before, "to": res.get("version")})
    return {**res, "updated_from": before}


@router.get("/updates")
def updates(user=Depends(_current_user)):
    """Everything with a newer version waiting, in one answer.

    One call so the UI can badge a count without asking about each module, and
    so "you have updates" and "here is why you cannot take one" arrive together
    rather than as a surprise at the moment of pressing the button."""
    import catalog
    view = catalog.view()
    rows = [m for m in view["modules"] if m.get("update_available")]
    core = view.get("core") or {}
    return {
        "core": core,
        # The ribbon counts BOTH, because a user does not think in terms of
        # "core" and "modules" — they think the app has an update or it does not.
        "count": len(rows) + (1 if core.get("update_available") else 0),
        "blocked": sum(1 for m in rows if not m.get("can_update")),
        "modules": [{"id": m["id"], "name": m["name"],
                     "from": m.get("installed_version"), "to": m.get("version"),
                     "can_update": m.get("can_update", False),
                     "blocked": m.get("update_blocked") or ""} for m in rows],
    }


@router.delete("/{module_id}")
def remove_module(module_id: str, purge: bool = False, user=Depends(require_admin)):
    try:
        res = module_installer.remove(module_id, purge=purge)
    except module_installer.InstallError as e:
        raise HTTPException(400, str(e))
    audit(user["email"], "module.remove", "module", module_id, {"purge": purge})
    return res


@router.post("/{module_id}/enable")
def enable_module(module_id: str, user=Depends(require_admin)):
    """Switch a module on. It loads and starts serving immediately."""
    res = module_system.set_enabled(module_id, True)
    audit(user["email"], "module.enable", "module", module_id, {"applied": res.get("applied")})
    return {"id": module_id, "enabled": True, **res}


@router.post("/{module_id}/disable")
def disable_module(module_id: str, user=Depends(require_admin)):
    """Switch a module off — immediately. Its files and tables stay, so turning
    it back on loses nothing."""
    res = module_system.set_enabled(module_id, False)
    audit(user["email"], "module.disable", "module", module_id,
          {"also_disabled": res.get("also_disabled")})
    return {"id": module_id, "enabled": False, **res}
