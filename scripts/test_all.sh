#!/usr/bin/env bash
# Single unified test entrypoint for AWS-SHOPPING (shopper unittest + backend pytest).
# Runs serially in one process chain to avoid CPU/RAM spikes.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== [1/2] shopper unittest suite (tests/) ==="
python3 -m unittest discover -s tests -t .

echo ""
echo "=== [2/2] backend pytest suite (backend/tests/) ==="
if [ -x "$ROOT/backend/.venv/bin/pytest" ]; then
  (cd "$ROOT/backend" && .venv/bin/pytest -q)
elif command -v uv >/dev/null 2>&1; then
  uv --directory "$ROOT/backend" run pytest -q
else
  echo "ERROR: neither backend/.venv/bin/pytest nor uv found" >&2
  exit 1
fi

echo ""
echo "=== ALL TESTS PASSED ==="
