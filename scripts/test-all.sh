#!/usr/bin/env bash
# Lint, type-check and test backend and frontend. API tests need DBM_TEST_DATABASE_URL (a scratch database).
set -euo pipefail
cd "$(dirname "$0")/.."
(cd backend && ruff check app tests && pytest -q)
(cd frontend && npm run typecheck && npm test && npm run build)
