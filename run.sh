#!/usr/bin/env bash
# ローカルでデモアプリを起動する: ./run.sh → http://localhost:8000
cd "$(dirname "$0")"
exec .venv/bin/uvicorn main:app --app-dir app --port "${PORT:-8000}"
