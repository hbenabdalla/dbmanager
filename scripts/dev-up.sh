#!/usr/bin/env bash
# Local development without containers: PostgreSQL must be running and DBM_DATABASE_URL set.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${DBM_DATABASE_URL:?export DBM_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/dbmanager}"
export DBM_MASTER_KEY="${DBM_MASTER_KEY:-$(python3 -c 'import os,base64;print(base64.b64encode(os.urandom(32)).decode())')}"
export DBM_ENV=development DBM_COOKIE_SECURE=false DBM_LOG_JSON=false
(cd backend && uvicorn app.main:app --reload --port 8000) &
(cd frontend && npm run dev) &
wait
