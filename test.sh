#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$ROOT_DIR/build"
TEST_TMP_DIR="$BUILD_DIR/test-tmp"
PYTHON_BIN="${PYTHON:-python3}"

rm -rf "$TEST_TMP_DIR"
mkdir -p "$TEST_TMP_DIR"
export PYTHONDONTWRITEBYTECODE=1
export PIP_NO_CACHE_DIR=1
export PIP_NO_COMPILE=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
export TMPDIR="$TEST_TMP_DIR"
export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

cd "$ROOT_DIR"
"$PYTHON_BIN" -c 'from openevent.cmd_sdk.dependencies import require_sdk; require_sdk()'
"$PYTHON_BIN" -m unittest discover -s tests "$@"
printf 'test artifacts: %s\n' "$BUILD_DIR"
