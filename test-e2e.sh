#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
PYCACHE_DIR="$ROOT_DIR/build/pycache"
mkdir -p "$PYCACHE_DIR"

export PYTHONPYCACHEPREFIX="$PYCACHE_DIR"
export PYTHONDONTWRITEBYTECODE=1

cd "$ROOT_DIR"

"$PYTHON_BIN" - <<'PY'
import importlib.metadata
import importlib.util
import sys

if importlib.util.find_spec("openevent.sdk") is None:
    print("missing Python dependency in the current environment: openevent-sdk>=0.3.0", file=sys.stderr)
    sys.exit(2)
try:
    version = importlib.metadata.version("openevent-sdk")
except importlib.metadata.PackageNotFoundError:
    print("missing Python dependency in the current environment: openevent-sdk>=0.3.0", file=sys.stderr)
    sys.exit(2)
parts = tuple(int(part) for part in version.split(".")[:3] if part.isdigit())
if parts < (0, 3, 0):
    print(f"openevent-sdk>=0.3.0 is required, found {version}", file=sys.stderr)
    sys.exit(2)
PY

printf 'cmd e2e tests require a running OpenEvent server and pre-created cmd.v1 channels; see E2E_TEST_CASES.md in the internal repository.\n'
