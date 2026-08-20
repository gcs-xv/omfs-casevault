#!/usr/bin/env bash
set -euo pipefail
mkdir -p data cache
if [[ ! -x ".venv/bin/python" ]]; then
  echo "Virtual environment not found. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi
.venv/bin/python scripts/init_db.py
.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 &
api_pid=$!
trap 'kill "$api_pid" 2>/dev/null || true' EXIT INT TERM
.venv/bin/streamlit run frontend/app.py --server.address 127.0.0.1 --server.port 8501
