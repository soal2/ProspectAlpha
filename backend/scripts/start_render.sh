#!/usr/bin/env bash
set -euo pipefail

if [[ "${DATABASE_URL:-}" == postgresql://* ]]; then
  export DATABASE_URL="postgresql+psycopg://${DATABASE_URL#postgresql://}"
fi

if [[ -n "${QDRANT_HOSTPORT:-}" ]]; then
  export QDRANT_URL="http://${QDRANT_HOSTPORT}"
fi

mkdir -p "${AGENT_RUNTIME_DATA_DIR:?AGENT_RUNTIME_DATA_DIR is required}"

python -c 'from riskagent.database import init_reference_data; init_reference_data()'

prospect-alpha-worker &
worker_pid=$!

uvicorn riskagent.api.main:app --host 0.0.0.0 --port "${PORT:-8000}" &
api_pid=$!

shutdown() {
  trap - INT TERM
  kill -TERM "$api_pid" "$worker_pid" 2>/dev/null || true
  wait "$api_pid" 2>/dev/null || true
  wait "$worker_pid" 2>/dev/null || true
}

trap shutdown INT TERM

set +e
wait -n "$api_pid" "$worker_pid"
status=$?
set -e

shutdown
exit "$status"
