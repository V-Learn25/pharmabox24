#!/bin/sh
# Web-service entrypoint for Railway.
#
# Why this script exists:
#   Previously, init_db() ran inside every gunicorn worker on import. Each new
#   worker boot re-ran the Postgres migration sweep + admin sync (~1-3 seconds
#   of synchronous DB work). During Railway's rolling deploys, requests landing
#   on a still-booting worker would either stall or return Railway's 502 page.
#
# What changes:
#   1. We run init_db ONCE in the container, before gunicorn starts, so the
#      schema/admin is guaranteed-current.
#   2. We export SKIP_INIT_DB=1 so when gunicorn imports app.py, it skips the
#      embedded init_db() call. Workers boot in <1s with no DB chatter.
#   3. We add gunicorn flags:
#      --timeout 60         (was 30s default) — generous headroom if a query
#                            ever does run slow, instead of silently killing
#                            the worker mid-request.
#      --graceful-timeout 30 — rolling deploys drain in-flight requests
#                            cleanly instead of cutting them off.
#
# Cold-start cost:
#   Migration runs once per deploy (~1-3s). Web traffic is unaffected because
#   Railway only routes to the container after the healthcheck passes.

set -e

echo "[start.sh] Running init_db (migration + admin sync)..."
python -c "from app import init_db; init_db()"
echo "[start.sh] init_db complete. Booting gunicorn."

export SKIP_INIT_DB=1
exec gunicorn app:app \
    --bind 0.0.0.0:$PORT \
    --workers 2 \
    --timeout 60 \
    --graceful-timeout 30 \
    --access-logfile - \
    --error-logfile -
