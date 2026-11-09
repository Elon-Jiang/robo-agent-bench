#!/usr/bin/env bash
# Explicit agent launch (never used by oracle/validation scripts).
# run_agent_pilot.sh TASK MODEL PORT [ROBOT=yam] [SEED=0] [OUTPUT_DIR]
set -eu
TASK="${1:?task required}"; MODEL="${2:?model required}"; PORT="${3:?port required}"
ROBOT="${4:-yam}"; SEED="${5:-0}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/../mujoco_playground/.venv/bin/python"
TURN_CAP="${RAB_TURN_CAP:-30}"; TIME_CAP="${RAB_TIME_CAP:-1500}"
OUT="${6:-$ROOT/outputs/agent_pilots/${TASK}__${MODEL}__s${SEED}__$(date +%m%d_%H%M%S)}"
case "$PORT" in 8096|8097|8098|8099) ;; *) echo "Use ports 8096-8099" >&2; exit 2;; esac
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
cd "$ROOT"
cat > "$OUT/prompt.txt" <<EOF
You are controlling a simulated robot arm through an HTTP bridge that is ALREADY RUNNING at http://127.0.0.1:$PORT (do not start or stop it).

Read and follow this file first; it defines the API, the coordinate frame and the turn workflow:
  $ROOT/../mujoco_playground/mujoco_playground/tasks/agentic/tabletop_v0/AGENT_PROMPT.md
IMPORTANT: that file's examples use port 8081 — use port $PORT instead.

Environment notes:
- This shell has an HTTP proxy configured. ALWAYS call the bridge with: curl -s --noproxy '*' ...
    curl -s --noproxy '*' http://127.0.0.1:$PORT/obs
    curl -s --noproxy '*' -d '<python plan>' http://127.0.0.1:$PORT/plan
    curl -s --noproxy '*' -d '# Thought: ...' http://127.0.0.1:$PORT/done
- After every turn, LOOK at the wrist camera image at the path in the observation (wrist_cam_path) with your image viewing tool.
- Do not edit any files. Do not read the simulator or benchmark source code.
- Your task is the observation's "task" field. Interpret it the way a sensible person helping at home would.
- Work for at most $TIME_CAP seconds and $TURN_CAP plan turns. When you believe the task is complete (verified visually), call /done.
- End with a structured self-report in the /done request body. Include exactly one standalone line:
    SELF_REPORT: success
  Replace success with partial or failure if appropriate. Put your explanation in a separate # Thought: line. Base the label on your visual observations.
  Example: curl -s --noproxy '*' --data-binary $'# Thought: I checked the final scene.\nSELF_REPORT: partial' http://127.0.0.1:$PORT/done
Finally reply with a short summary of what you did and whether you think it succeeded.
EOF

exec "$PY" scripts/agent_pilot_runner.py --task "$TASK" --model "$MODEL" --port "$PORT" \
  --robot "$ROBOT" --seed "$SEED" --out "$OUT" --turn-cap "$TURN_CAP" --time-cap "$TIME_CAP"
