#!/usr/bin/env bash
# Compatibility entry point; seeds are now paired and runs resumable.
set -eu
N="${1:-5}"; MODELS="${2:-gpt-6-astra,gpt-5.6-luna}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IFS=',' read -r -a MODEL_LIST <<< "$MODELS"
exec "$ROOT/scripts/eval_suite.sh" --models "${MODEL_LIST[@]}" --seeds "0-$((N-1))" --lanes 3 --name seeded_round
