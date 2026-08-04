#!/bin/sh
set -e

# Seed the volume from the image, and keep it current.
#
# The bundled modules ship inside the image at /app/modules, but installs have to
# go somewhere writable that survives the container, so MODULES_DIR points at a
# volume. On a first run that volume is empty and they are copied across.
#
# The version comparison is the part that is easy to leave out and then miss for
# months. Copying only when the directory is ABSENT means an updated image never
# reaches a volume that already has the old copy — `git pull && compose up
# --build` would bring new core and a new frontend while every bundled module sat
# frozen at whatever version first seeded, with nothing saying so.
#
# `versions.newer` is the app's own comparison, so the entrypoint and the update
# button cannot disagree about which build is newer. A module the operator
# installed from the store is left alone unless the image genuinely carries a
# newer one.
mkdir -p "$ENTRYSTATION_MODULES_DIR"
python3 - <<'PYEOF'
import json, os, shutil, sys
from pathlib import Path

sys.path.insert(0, "/app/backend")
import versions

dst_root = Path(os.environ["ENTRYSTATION_MODULES_DIR"])
src_root = Path("/app/modules")


def ver(path):
    try:
        return json.loads((path / "module.json").read_text()).get("version")
    except Exception:
        return None


for src in sorted(p for p in src_root.iterdir() if p.is_dir()):
    dst = dst_root / src.name
    have, offered = ver(dst), ver(src)
    if not dst.exists():
        shutil.copytree(src, dst)
        print(f"[entrypoint] seeded module: {src.name} {offered or ''}".rstrip())
    elif offered and have and versions.newer(offered, have):
        shutil.rmtree(dst)
        shutil.copytree(src, dst)
        print(f"[entrypoint] updated module: {src.name} {have} -> {offered}")
PYEOF

# Hand the built frontend to Caddy. It is baked into this image and Caddy runs
# in another container, so the shared volume is the only way across. Copied every
# start, not just the first, or an updated image would keep serving the old
# bundle from a volume that already had files in it.
if [ -d /app/frontend/dist ] && [ -d /web ]; then
    rm -rf /web/* 2>/dev/null || true
    cp -a /app/frontend/dist/. /web/
    echo "[entrypoint] frontend published to the web volume"
fi

# A display for the headed browser. Headless scores worst against reCAPTCHA, so
# the browser is headed and this is what it is headed ON. Started here rather
# than as a second container because it is only ever used by this process.
if [ ! -e /tmp/.X11-unix/X99 ]; then
    Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp >/dev/null 2>&1 &
    echo "[entrypoint] Xvfb started on :99"
fi

# Wait for Postgres. Modules run their own schema.sql at import time, so a
# backend that starts first does not retry — it fails and takes the module with
# it. Cheaper to wait than to explain that.
if [ -n "$DATABASE_URL" ]; then
    printf '[entrypoint] waiting for the database'
    for _ in $(seq 1 60); do
        if python3 -c "
import os, sys, psycopg
try:
    psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=2).close()
except Exception:
    sys.exit(1)
" 2>/dev/null; then
            printf ' ok\n'
            break
        fi
        printf '.'
        sleep 1
    done
fi

cd /app/backend
exec "$@"
