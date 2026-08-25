#!/bin/sh
# Single-container entrypoint for platforms that only run one process/port
# per service (e.g. Render's free web service tier). Runs the toy backend
# in the background on the fixed internal port the gateway already defaults
# to (GATEWAY_BACKEND_URL=http://localhost:9000), then execs the gateway in
# the foreground bound to the platform-assigned $PORT so it receives signals
# correctly as PID 1.
#
# docker-compose.yml does not use this: it overrides the Dockerfile CMD with
# an explicit `command:` per service, running gateway and backend as
# separate containers instead.
set -e
uvicorn backend.slow_backend:app --host 0.0.0.0 --port 9000 &
exec uvicorn gateway.app:app --host 0.0.0.0 --port "${PORT:-8080}"
