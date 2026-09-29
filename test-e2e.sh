#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
SERVER_BIN="${OPENEVENT_SERVER_BIN:-}"
E2E_DIR="$ROOT_DIR/build/e2e"

if [ -z "$SERVER_BIN" ] || [ ! -x "$SERVER_BIN" ]; then
  printf 'Set OPENEVENT_SERVER_BIN to an executable openevent_server binary.\n' >&2
  exit 2
fi
SERVER_BIN="$(cd "$(dirname "$SERVER_BIN")" && pwd)/$(basename "$SERVER_BIN")"

export PYTHONDONTWRITEBYTECODE=1
export PIP_NO_CACHE_DIR=1
export PIP_NO_COMPILE=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
# Import the selected environment's installed SDK, never a workspace source tree.
unset PYTHONPATH
cd "$ROOT_DIR"
"$PYTHON_BIN" -B -c 'from importlib.metadata import version; from packaging.version import Version; assert Version(version("openevent-sdk")) >= Version("0.11.1"), "openevent-sdk>=0.11.1 must be installed in PYTHON"'

# build.sh removes old project wheels before building; the installer replaces
# only this project and reuses satisfied dependencies. A failed build stops here.
# Select a separate test environment with PYTHON when desired.
make install PYTHON="$PYTHON_BIN"
rm -rf "$E2E_DIR/tmp" "$E2E_DIR/cases" "$E2E_DIR/cache"
mkdir -p "$E2E_DIR/tmp" "$E2E_DIR/cases"
export TMPDIR="$E2E_DIR/tmp"
export XDG_CACHE_HOME="$E2E_DIR/cache"
export OPENEVENT_SERVER_BIN="$SERVER_BIN"
export OPENEVENT_CMD_E2E_DIR="$E2E_DIR/cases"

"$PYTHON_BIN" -B -m unittest discover -s tests/e2e -v "$@"
printf 'e2e artifacts: %s\n' "$E2E_DIR"
