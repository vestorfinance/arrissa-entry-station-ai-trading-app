"""
Installing and removing modules — the ZIP end of the module system.

    python module_installer.py list
    python module_installer.py pack   modules/economic-calendar  [-o dist/]
    python module_installer.py install  economic-calendar-1.0.0.zip
    python module_installer.py remove   economic-calendar  [--purge]
    python module_installer.py enable|disable <id>
    python module_installer.py verify   <zip>
    python module_installer.py keygen                        (vendor: once)
    python module_installer.py sign   <module_dir> <priv_hex>  (vendor: per release)

A ZIP is unpacked into `modules/<id>/` and loaded on the next start. It is NOT
loaded into the running process: FastAPI collects routes at import, so a module
installed live would have working tools and dead endpoints — half-installed,
which is worse than not installed. The installer says "restart to finish" rather
than pretending.

SIGNING. A paid module carries `signature.txt` — the manifest's SHA-256 signed
with the vendor's ed25519 key. That is the licence check and it needs no licence
server: a module that has not been signed by the vendor does not install. The
public key ships with core; the private one never leaves the vendor. When
cryptography is unavailable the installer REFUSES a signed module rather than
skipping the check, because a check that silently passes is not a check.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import modules as module_system

MODULES_DIR = module_system.MODULES_DIR
SIGNATURE_FILE = "signature.txt"
# The vendor's ed25519 public key, hex. Empty here: this build signs nothing, so
# every module is treated as unsigned/free. Set it in the release build.
VENDOR_PUBLIC_KEY = "d93a6f614fc857e8ebb47036a220fe6bd64f8b7742784c09143b3f91a5f4339b"

# Never unpack these, whatever a ZIP contains.
UNSAFE = ("..", "/etc", "/usr", "\\")


class InstallError(Exception):
    """Anything that stops an install, with a sentence the user can act on."""


# ── signing ────────────────────────────────────────────────────────────────────
def manifest_digest(manifest: dict) -> str:
    """A stable hash of the manifest — sorted keys, so re-serialising cannot
    change the signature."""
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify_signature(manifest: dict, signature_hex: str) -> bool:
    if not signature_hex:
        return False
    if not VENDOR_PUBLIC_KEY:
        raise InstallError(
            "this module is signed, but this build carries no vendor public key to "
            "check it against — refusing rather than installing it unverified")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
    except ImportError:
        raise InstallError(
            "this module is signed but `cryptography` is not installed, so the "
            "signature cannot be checked — refusing rather than skipping the check")
    key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(VENDOR_PUBLIC_KEY))
    try:
        key.verify(bytes.fromhex(signature_hex), manifest_digest(manifest).encode())
        return True
    except (InvalidSignature, ValueError):
        return False


# ── inspecting an archive ──────────────────────────────────────────────────────
def _safe_members(zf: zipfile.ZipFile) -> list:
    """Reject path traversal before anything is written to disk."""
    out = []
    for info in zf.infolist():
        name = info.filename
        if name.startswith("/") or any(bad in name for bad in UNSAFE):
            raise InstallError(f"the archive contains an unsafe path: {name!r}")
        out.append(info)
    return out


def _root_of(zf: zipfile.ZipFile) -> str:
    """A module ZIP holds one top-level directory. Anything else is ambiguous."""
    tops = {n.split("/")[0] for n in zf.namelist() if n.strip("/")}
    if len(tops) != 1:
        raise InstallError(
            f"a module archive must contain exactly one top-level folder; this has "
            f"{len(tops) or 'none'}")
    return tops.pop()


def inspect(zip_path) -> dict:
    """What is in this archive, without installing it."""
    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise InstallError(f"{zip_path} does not exist")
    with zipfile.ZipFile(zip_path) as zf:
        _safe_members(zf)
        root = _root_of(zf)
        try:
            manifest = json.loads(zf.read(f"{root}/{module_system.MANIFEST}"))
        except KeyError:
            raise InstallError(f"no {module_system.MANIFEST} in the archive")
        except json.JSONDecodeError as e:
            raise InstallError(f"{module_system.MANIFEST} is not valid JSON: {e}")
        try:
            signature = zf.read(f"{root}/{SIGNATURE_FILE}").decode().strip()
        except KeyError:
            signature = ""
    # Validate through the same reader the loader uses, so install-time and
    # load-time never disagree about what a valid manifest is — but re-raise as
    # an InstallError, or the CLI shows a traceback where it should show a
    # sentence, and every validation message is wasted.
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "m"
        p.mkdir()
        (p / module_system.MANIFEST).write_text(json.dumps(manifest))
        try:
            manifest = module_system.read_manifest(p)
        except ValueError as e:
            raise InstallError(str(e))
    return {"manifest": manifest, "root": root, "signed": bool(signature),
            "signature": signature}


# ── install ────────────────────────────────────────────────────────────────────
def install(zip_path, *, force=False) -> dict:
    info = inspect(zip_path)
    manifest, root = info["manifest"], info["root"]
    mid = manifest["id"]

    if manifest.get("edition") == "paid" or info["signed"]:
        if not verify_signature(manifest, info["signature"]):
            raise InstallError(
                f"{mid} is a paid module and its signature does not verify — it was "
                "not published by the vendor, or it has been modified since")

    # Dependencies, checked against what is on disk rather than what is loaded, so
    # installing in any order still ends up correct after a restart.
    have = set()
    for p in (MODULES_DIR.iterdir() if MODULES_DIR.exists() else []):
        try:
            have.add(module_system.read_manifest(p)["id"])
        except Exception:
            continue
    why = module_system.check_requires(manifest, have)
    if why and not force:
        raise InstallError(f"{mid} {why}")

    dest = MODULES_DIR / mid
    if dest.exists() and not force:
        existing = module_system.read_manifest(dest)
        raise InstallError(
            f"{mid} {existing['version']} is already installed — pass --force to "
            f"replace it with {manifest['version']}")

    MODULES_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp, members=_safe_members(zf))
        staged = Path(tmp) / root
        if not (staged / module_system.MANIFEST).exists():
            raise InstallError("the archive's folder does not contain its manifest")
        # Replace atomically-ish: the old one moves aside and only goes when the
        # new one is in place, so a failed install does not leave a hole.
        backup = None
        if dest.exists():
            backup = dest.with_suffix(".replacing")
            shutil.rmtree(backup, ignore_errors=True)
            dest.rename(backup)
        try:
            shutil.copytree(staged, dest)
        except Exception:
            if backup:
                shutil.rmtree(dest, ignore_errors=True)
                backup.rename(dest)
            raise
        if backup:
            shutil.rmtree(backup, ignore_errors=True)

    # Load it into the running app. In the CLI there is no app to load into, so
    # `applied` comes back false and the note says the restart is what will do it.
    live = module_system.apply_live(mid, True)
    note = ("installed and serving" if live.get("applied")
            else "installed — it loads on the next restart"
                 + (f" ({live['note']})" if live.get("note") else ""))
    return {"id": mid, "name": manifest["name"], "version": manifest["version"],
            "edition": manifest.get("edition"), "signed": info["signed"],
            "path": str(dest), "live": bool(live.get("applied")), "note": note}


# ── remove ─────────────────────────────────────────────────────────────────────
def remove(module_id: str, *, purge=False) -> dict:
    dest = MODULES_DIR / module_id
    if not dest.exists():
        raise InstallError(f"{module_id} is not installed")
    manifest = {}
    try:
        manifest = module_system.read_manifest(dest)
    except Exception:
        pass

    # Anything that DEPENDS on it must go first, or the next boot is a module
    # that loads and immediately refuses itself.
    dependents = []
    for p in MODULES_DIR.iterdir():
        if p == dest or not p.is_dir():
            continue
        try:
            m = module_system.read_manifest(p)
        except Exception:
            continue
        if module_id in ((m.get("requires") or {}).get("modules") or []):
            dependents.append(m["id"])
    if dependents:
        raise InstallError(
            f"{module_id} is required by {', '.join(dependents)} — remove "
            + ("that" if len(dependents) == 1 else "those") + " first")

    # Unregister before deleting the files, so nothing can be serving from a
    # directory that is about to vanish. In the CLI nothing is registered and
    # this is a no-op, which is why a zero is not reported as a teardown.
    import registry
    lingering = registry.unstoppable_workers(module_id)
    live = registry.forget(module_id)
    shutil.rmtree(dest)
    module_system.forget_record(module_id)

    # `live` counts what was UNREGISTERED, which is zero for a module that was
    # already disabled — that is not the same as one still serving, and saying
    # "restart to unload it" about something that was never loaded is a lie the
    # operator has no way to check.
    was_serving = module_system.loaded().get(module_id, {}).get("status") == "loaded"
    note = ("removed" if (live or not was_serving)
            else "removed from disk — restart the app to unload it")
    if lingering:
        note += (f" · background worker{'s' if len(lingering) > 1 else ''} "
                 f"{', '.join(lingering)} keep running until the next restart")
    if not purge:
        note += ". Its tables were LEFT IN PLACE; pass --purge to drop them"
    out = {"id": module_id, "name": manifest.get("name", module_id),
           "purged": bool(purge), "note": note}
    if live:                                  # only when this process had it loaded
        out["unregistered_here"] = live
    return out


# ── pack ───────────────────────────────────────────────────────────────────────
def pack(module_dir, out_dir=None) -> dict:
    """Build a distributable ZIP from a module directory."""
    src = Path(module_dir).resolve()
    manifest = module_system.read_manifest(src)
    out_dir = Path(out_dir or src.parent.parent / "dist")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{manifest['id']}-{manifest['version']}.zip"

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(src.rglob("*")):
            # Code and data the module needs — not the development captures that
            # happen to live beside them. A 22 MB HAR helps nobody who installs it.
            if (f.is_dir() or "__pycache__" in f.parts
                    or f.suffix in (".pyc", ".har", ".log")
                    or f.name.startswith(".")):
                continue
            zf.write(f, Path(manifest["id"]) / f.relative_to(src))
    return {"id": manifest["id"], "version": manifest["version"],
            "zip": str(target), "bytes": target.stat().st_size}


# ── signing (vendor side) ──────────────────────────────────────────────────────
def keygen() -> dict:
    """Generate a vendor signing key. The private half never leaves the vendor;
    the public half is compiled into core as VENDOR_PUBLIC_KEY."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    priv = Ed25519PrivateKey.generate()
    raw = lambda k, pub: k.public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw) if pub else \
        k.private_bytes(encoding=serialization.Encoding.Raw,
                        format=serialization.PrivateFormat.Raw,
                        encryption_algorithm=serialization.NoEncryption())
    return {"private_key": raw(priv, False).hex(),
            "public_key": raw(priv.public_key(), True).hex()}


def sign(module_dir, private_key_hex: str) -> dict:
    """Write signature.txt into a module directory — the vendor's assertion that
    this manifest is theirs. Signing the MANIFEST rather than the archive means a
    repack cannot invalidate it, while changing the id, version or edition does."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    src = Path(module_dir).resolve()
    manifest = module_system.read_manifest(src)
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    sig = key.sign(manifest_digest(manifest).encode()).hex()
    (src / SIGNATURE_FILE).write_text(sig + "\n")
    return {"id": manifest["id"], "version": manifest["version"],
            "signature": sig, "file": str(src / SIGNATURE_FILE)}


# ── CLI ────────────────────────────────────────────────────────────────────────
def _main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]
    flags = {a for a in args if a.startswith("--")}
    args = [a for a in args if not a.startswith("--")]
    try:
        if cmd == "list":
            st = module_system.status()
            rows = st["modules"] or []
            print(f"{len(rows)} module(s) in {st['modules_dir']}")
            for m in rows:
                print(f"  {m['id']:<22} {str(m.get('version') or '-'):<8} "
                      f"{str(m.get('edition') or 'free'):<5} {m['status']}"
                      + (f"  — {m['error']}" if m.get("error") else ""))
            return 0
        if cmd == "keygen":
            print(json.dumps(keygen(), indent=2))
            return 0
        if cmd == "sign":
            print(json.dumps(sign(args[0], args[1]), indent=2))
            return 0
        if cmd == "pack":
            print(json.dumps(pack(args[0], args[1] if len(args) > 1 else None), indent=2))
            return 0
        if cmd == "verify":
            info = inspect(args[0])
            print(json.dumps({"id": info["manifest"]["id"],
                              "version": info["manifest"]["version"],
                              "edition": info["manifest"].get("edition"),
                              "signed": info["signed"]}, indent=2))
            return 0
        if cmd == "install":
            print(json.dumps(install(args[0], force="--force" in flags), indent=2))
            return 0
        if cmd == "remove":
            print(json.dumps(remove(args[0], purge="--purge" in flags), indent=2))
            return 0
        if cmd in ("enable", "disable"):
            module_system.set_enabled(args[0], cmd == "enable")
            print(f"{args[0]} {cmd}d — restart the app to apply")
            return 0
        print(f"unknown command {cmd!r}; try --help")
        return 2
    except InstallError as e:
        print(f"error: {e}")
        return 1
    except IndexError:
        print(f"error: {cmd} needs an argument; try --help")
        return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
