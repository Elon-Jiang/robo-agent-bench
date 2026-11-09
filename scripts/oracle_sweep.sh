#!/usr/bin/env bash
# Run the scripted oracle on every tabletop_v0 task to check which tasks are
# reliable on this machine. Usage: scripts/oracle_sweep.sh [episodes]
set -u
EPISODES="${1:-5}"
PG="$HOME/Documents/ai/AIProject/mujoco_playground"
OUT="$(cd "$(dirname "$0")/.." && pwd)/outputs/oracle_sweep"
mkdir -p "$OUT"
TASKS="cube_in_bowl bar_in_bowl banana_in_bowl cubes_in_bowl marker_on_plate stack3 peg_in_socket cap_fixed_marker usb_in_port"

cd "$PG" || exit 1
printf "task\tsuccess\tepisodes\twall_s\texit\n" > "$OUT/summary.tsv"
for t in $TASKS; do
  start=$(date +%s)
  .venv/bin/python -m mujoco_playground.tasks.agentic.tabletop_v0.run \
    --mode=sim --robot=so101 --agent=oracle --task="$t" --episodes="$EPISODES" \
    > "$OUT/$t.log" 2>&1
  rc=$?
  wall=$(( $(date +%s) - start ))
  ok=$(grep -cE "^episode [0-9]+: success=True" "$OUT/$t.log")
  n=$(grep -cE "^episode [0-9]+: success=" "$OUT/$t.log")
  printf "%s\t%s\t%s\t%s\t%s\n" "$t" "$ok" "$n" "$wall" "$rc" | tee -a "$OUT/summary.tsv"
done
