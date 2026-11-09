#!/usr/bin/env bash
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec "$ROOT/../mujoco_playground/.venv/bin/python" scripts/eval_suite.py "$@"
