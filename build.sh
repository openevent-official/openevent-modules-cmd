#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$ROOT_DIR/build"
OUT_DIR="$ROOT_DIR/dist"
PYTHON_BIN="${PYTHON:-python3}"

export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_CACHE_DIR="$BUILD_DIR/pip-cache"
export TMPDIR="$BUILD_DIR/tmp"

cd "$ROOT_DIR"
mode=${1:-build}
if [[ "$mode" == --install || "$mode" == --test ]]; then
  "$PYTHON_BIN" -B scripts/install.py --check-environment
fi

mkdir -p "$OUT_DIR" "$TMPDIR"
rm -f "$OUT_DIR"/openevent_modules_cmd-*.whl
wheel_dir=$(mktemp -d "$TMPDIR/cmd-wheel.XXXXXXXX")
trap 'rm -rf "$wheel_dir"' EXIT

env -u PIP_TARGET -u PIP_PREFIX -u PIP_ROOT -u PIP_USER \
  "$PYTHON_BIN" -B -m pip wheel --no-deps --wheel-dir "$wheel_dir" .

shopt -s nullglob
wheels=("$wheel_dir"/openevent_modules_cmd-*.whl)
if (( ${#wheels[@]} != 1 )); then
  printf 'Expected one Cmd wheel from this build, found %s.\n' "${#wheels[@]}" >&2
  exit 1
fi
wheel=${wheels[0]}
release="$OUT_DIR/$(basename "$wheel")"
mv -- "$wheel" "$release"
printf 'Built %s\n' "$release"
case "$mode" in
  --install) "$PYTHON_BIN" -B scripts/install.py "$release" ;;
  --test) "$PYTHON_BIN" -B scripts/install.py --test-env "$release" ;;
esac
