#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$ROOT_DIR/build"
OUT_DIR="$ROOT_DIR/dist"
TOOLS_ENV_DIR="$BUILD_DIR/tools-env"
PYCACHE_DIR="$BUILD_DIR/pycache"
TMP_WORK_DIR="$BUILD_DIR/tmp"
PYTHON_BIN="${PYTHON:-python3}"

rm -rf \
  "$PYCACHE_DIR" \
  "$TMP_WORK_DIR" \
  "$BUILD_DIR/build-src" \
  "$BUILD_DIR/test-src" \
  "$BUILD_DIR/deps" \
  "$BUILD_DIR/cache" \
  "$BUILD_DIR/venv"
rm -f "$OUT_DIR"/*.whl "$OUT_DIR"/*.tar.gz
mkdir -p "$OUT_DIR" "$PYCACHE_DIR" "$TMP_WORK_DIR"

export PYTHONPYCACHEPREFIX="$PYCACHE_DIR"
export PYTHONDONTWRITEBYTECODE=1
export PIP_NO_CACHE_DIR=1
export PIP_NO_COMPILE=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
export TMPDIR="$TMP_WORK_DIR"
unset PYTHONPATH PYTHONHOME PIP_TARGET PIP_PREFIX PIP_ROOT PIP_USER

cd "$ROOT_DIR"

"$PYTHON_BIN" -B scripts/prepare_build_env.py

"$TOOLS_ENV_DIR/bin/python" -B -m build --no-isolation --skip-dependency-check --wheel --outdir "$OUT_DIR"

printf 'build artifacts: %s\n' "$OUT_DIR"
