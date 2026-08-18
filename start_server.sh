#!/usr/bin/env bash
# Factory Box People Counter - start the server (Linux)
set -e
cd "$(dirname "$0")/backend"
exec ../.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000
