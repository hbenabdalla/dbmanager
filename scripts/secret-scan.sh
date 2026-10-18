#!/usr/bin/env sh
# Scan the repository (working tree and full git history) for committed secrets with gitleaks,
# using the project configuration in .gitleaks.toml. Exits non-zero when a leak is found.
#   scripts/secret-scan.sh
# Uses a local gitleaks binary when installed, otherwise the official container image.
set -eu
cd "$(dirname "$0")/.."
if command -v gitleaks >/dev/null 2>&1; then
  gitleaks git --config .gitleaks.toml --redact --no-banner .
  gitleaks dir --config .gitleaks.toml --redact --no-banner .
elif command -v docker >/dev/null 2>&1; then
  docker run --rm -v "$(pwd)":/repo -w /repo zricethezav/gitleaks:v8.24.2 git --config .gitleaks.toml --redact --no-banner .
  docker run --rm -v "$(pwd)":/repo -w /repo zricethezav/gitleaks:v8.24.2 dir --config .gitleaks.toml --redact --no-banner .
else
  echo "install gitleaks (https://github.com/gitleaks/gitleaks) or Docker" >&2
  exit 2
fi
echo "no secrets found"
