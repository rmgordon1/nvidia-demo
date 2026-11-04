#!/usr/bin/env bash
# Start the ITSM RAG server on the host. Binds to the docker bridge address by default so the
# webapp's nginx container can reach it, while staying off the venue network.
set -euo pipefail
cd "$(dirname "$0")"
export HF_HOME="$PWD/.cache/hf" HF_HUB_CACHE=/opt/workshop/models/hf/hub HF_HUB_OFFLINE=1 TRANSFORMERS_VERBOSITY=error
HOST="${ITSM_HOST:-172.17.0.1}"
PORT="${ITSM_PORT:-8030}"
exec /opt/workshop/venv/bin/python3 -m uvicorn app.main:app --host "$HOST" --port "$PORT" --log-level info
