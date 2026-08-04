# EntryStation, in a container.
#
# Based on Playwright's own image rather than a plain python one, and that single
# choice removes most of the pain this app has caused on bare servers: the
# browsers are already there, so are every one of the t64 runtime libs, and the
# "Playwright does not support ubuntu26.04" refusal never happens because the
# base is a distribution Playwright supports.
#
# It also fixes a whole class of bug by construction. The three variables that
# the Exness login needs — DISPLAY, EXNESS_HEADLESS, PLAYWRIGHT_BROWSERS_PATH —
# are ENV lines here, baked into the image. On a bare server they live on a
# systemd unit and can be forgotten per-service, which is exactly what happened
# to the Community instance: Xvfb running, Edge installed, and the login still
# dead because its own unit had never been told.

# ── the one version number ────────────────────────────────────────────────────
# The Playwright package and the Playwright IMAGE each ship their own browser
# builds, and they must be the same version. They were not: the image said
# v1.49.0 while requirements.txt pinned 1.60.0, so pip's Playwright went looking
# for chromium-1223 in an image that had never heard of it — and it failed at
# LOGIN time, in front of a user, rather than at build time.
#
# One number now, and the build asserts it below. Change it here and in
# backend/requirements.txt together, or the image refuses to be built.
ARG PW_VERSION=1.60.0

FROM node:20-slim AS web

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


FROM mcr.microsoft.com/playwright/python:v${PW_VERSION}-jammy
ARG PW_VERSION

# Real Microsoft Edge. The warm profile is an Edge profile and the login asks for
# `channel="msedge"`, so the bundled Chromium is a fallback rather than the plan.
# Edge is published for amd64 only, which is why the compose file pins the
# platform: on arm64 this layer is skipped and the fallback is what runs.
RUN set -eux; \
    if [ "$(dpkg --print-architecture)" = "amd64" ]; then \
      curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-edge.gpg; \
      echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-edge.gpg] \
https://packages.microsoft.com/repos/edge stable main" > /etc/apt/sources.list.d/microsoft-edge.list; \
      apt-get update -qq && apt-get install -y --no-install-recommends microsoft-edge-stable xvfb; \
    else \
      apt-get update -qq && apt-get install -y --no-install-recommends xvfb; \
    fi; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt \
 && got="$(playwright --version | awk '{print $NF}')" \
 && if [ "$got" != "$PW_VERSION" ]; then \
      echo ""; \
      echo "  Playwright mismatch, refusing to build:"; \
      echo "    base image ships browsers for  $PW_VERSION"; \
      echo "    requirements.txt installs      $got"; \
      echo ""; \
      echo "  They must match. Set ARG PW_VERSION=$got at the top of the"; \
      echo "  Dockerfile, or pin playwright==$PW_VERSION in requirements.txt."; \
      echo ""; \
      exit 1; \
    fi \
 && echo "playwright $got matches the base image"

# Proof, at build time, that a browser can actually be launched. Every failure
# this container has had so far only showed up when somebody tried to log in.
RUN python -c "\
from playwright.sync_api import sync_playwright; \
p = sync_playwright().start(); \
b = p.chromium.launch(args=['--no-sandbox']); \
print('chromium ok:', b.version); \
b.close(); p.stop()"

COPY backend/   backend/
COPY templates/ templates/
COPY modules/   modules/
COPY --from=web /build/dist frontend/dist

# Everything that must OUTLIVE the container. Each of these is written at
# runtime and each was, until now, written into the source tree — which in an
# image means it is lost the moment the image is replaced.
ENV ENTRYSTATION_MODULES_DIR=/data/modules \
    EXNESS_PROFILE_DIR=/data/edge-profile \
    EXNESS_SESSION_CACHE=/data/session_cache.json

# The browser, decided here so no operator has to know.
ENV DISPLAY=:99 \
    EXNESS_HEADLESS=0 \
    EXNESS_NO_SANDBOX=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    HOME=/app

ENV ENTRYSTATION_EDITION=community

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
