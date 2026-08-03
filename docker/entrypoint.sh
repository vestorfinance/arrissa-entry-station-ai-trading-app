#!/bin/sh
set -e

# Seed the volume from the image, once.
#
# The free modules ship inside the image at /app/modules, but installs have to go
# somewhere writable that survives the container, so MODULES_DIR points at a
# volume. On a first run that volume is empty — so the bundled modules are copied
# across, and only where nothing is already there.
#
# `install_free` would fetch them from the store instead, but that needs the
# network and the store to be up at the exact moment somebody first starts this.
# Copying what is already on disk means a first boot works offline.
mkdir -p "$ENTRYSTATION_MODULES_DIR"
for src in /app/modules/*/; do
    [ -d "$src" ] || continue
    id=$(basename "$src")
    if [ ! -d "$ENTRYSTATION_MODULES_DIR/$id" ]; then
        cp -a "$src" "$ENTRYSTATION_MODULES_DIR/$id"
        echo "[entrypoint] seeded module: $id"
    fi
done

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
