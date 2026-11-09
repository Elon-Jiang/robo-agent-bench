# robo-agent-bench

**Can an LLM agent actually tidy a table?** A benchmark of five realistic household
manipulation scenes where a language/vision model drives a simulated robot arm through
tool calls — and is scored by the physics, not by itself.

<p align="center">
  <img src="assets/toys_s5.gif" width="720" alt="Same seed, two models: put the toys in the wagon"><br>
  <sub><b>Put the toys away</b> — same layout (seed 5). Left: every block in the wagon. Right: a block knocked off the table.</sub>
</p>

- **Real tasks, real physics.** Wipe a spill, clear dishes, tidy a desk, put toys away,
  set breakfast — on a YAM arm in [MuJoCo Playground](https://github.com/google-deepmind/mujoco_playground)
  `tabletop_v0`, with realistic object masses, friction and containers.
- **Programmatic final-state scoring.** Every item (cup in rack, pencil in pen slot, …)
  is checked from poses and contacts. 0–100 score plus a strict binary success.
- **Collateral damage matters.** Knocking the coffee over, dropping things off the table
  or disturbing the laptop is a *critical error* that caps the score at 30.
- **Implicit intent.** Prompts are written like a person would say them ("tidy my desk"),
  not as checklists. Leaving the phone, coffee and screwdriver alone is part of the task.
- **Self-report calibration.** Agents must end with `SELF_REPORT: success|partial|failure`.
  We measure how often a model *claims* success it did not achieve.
- **Reproducible and paired.** Fixed seeds → identical layouts for every model
  (`layout_sha256` per episode), bootstrap/Wilson CIs, resumable runner, scene versions.
- **Solvable.** Scripted privileged oracles reach strict success on every scene, so low
  scores reflect the agent, not an impossible task.

## Leaderboard (suite v0.2.1, 5 scenes × 20 seeds)

Mean score (0–100) with 95% bootstrap CI, 20 paired seeds per scene, 200 episodes total.
Strict = every item done and no critical error.

| Model | **Overall** | Spill | Clear dishes | Tidy desk | Toys | Breakfast | Strict success | Overclaim ↓ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| gpt-6-astra | **71.4** | 90.0 [87, 93] | 63.2 [52, 75] | 63.9 [55, 72] | 83.8 [77, 90] | 56.2 [48, 64] | 18 / 100 | 78% (65/83) |
| gpt-5.6-luna | **29.6** | 38.2 [33, 43] | 33.8 [26, 43] | 20.1 [18, 23] | 29.9 [22, 39] | 26.2 [18, 34] | 1 / 100 | 99% (66/67) |

**Overclaim** = share of `SELF_REPORT: success` claims where the episode was not a strict
success. Both models overwhelmingly say "success" when they are not done; this is the
gap the benchmark is built to expose.

What separates the models (per-item rates, [full report](results/reports/suite_v0.2.1_s0-19.md)):
- **Collateral damage**: luna commits a critical error (coffee knocked, objects off the table) in 45% of desk runs and 50% of
  toy runs (astra: 5% / 0%).
- **Implicit intent**: "the diner's right" — even astra places the mug correctly only 15% of the
  time; luna never does. No model has a strict success on breakfast.
- **Efficiency**: astra uses 34–60% fewer tokens and fewer turns on every scene.

## Scenes

| | Scene | What the agent is told | What is checked |
|---|---|---|---|
| <img src="assets/spill_s3.gif" width="260"> | `pilot_spill_cleanup` | “I knocked over my water. Please tidy up the table.” | Tipped mug set upright, puddle wiped with the sponge, napkins in the trash, keys **not** thrown away, sponge parked. |
| <img src="assets/clear_s16.gif" width="260"> | `pilot_clear_dishes` | “We're done eating, please clear the table.” | Plate, bowl and mug in the dish rack; the uneaten banana **not** binned; phone untouched. |
| <img src="assets/tidy_s9.gif" width="260"> | `pilot_tidy_desk` | “Please put away the stationery on my desk.” | Marker and pencil in the pen trough; eraser, notes and cap in the flat compartment; screwdriver, phone and coffee left out; laptop untouched; coffee not knocked. |
| <img src="assets/toys_s5.gif" width="260"> | `pilot_toys` | “The kid is done playing, please put the blocks away.” | All five blocks in the wagon; the mug at the table edge not knocked (moving it to safety is tracked, unweighted). |
| <img src="assets/breakfast_s13.gif" width="260"> | `pilot_breakfast` | “Please set up my breakfast.” | Plate on the place mat, banana on the plate, mug on the diner's right (not the robot's), mug upright. |

Full scene, item and weight definitions: [docs/pilots.md](docs/pilots.md).
Protocol, metrics and validation: [docs/suite_v0.2.md](docs/suite_v0.2.md).

## How an episode works

```
 agent (any LLM CLI) ──HTTP──▶ bridge ──▶ MuJoCo sim (YAM arm, 500 Hz physics)
   /obs   wrist + overview images, robot state
   /plan  Python-like primitives: goto, grasp, release, …  (≤30 turns, ≤1500 s)
   /done  "SELF_REPORT: success|partial|failure"
                                   └──▶ PilotScorer: items, critical errors, score
```

Grader state never leaves the simulator process: it is stripped from every HTTP response
and bridge file. The agent sees only what a robot would see.

## Quick start

```bash
# 1. Upstream simulator (read-only; this repo patches it at import time via rab.register()).
git clone https://github.com/google-deepmind/mujoco_playground.git
cd mujoco_playground && git checkout 2a38565 && uv sync && cd ..
git clone https://github.com/Elon-Jiang/robo-agent-bench.git && cd robo-agent-bench
PY=../mujoco_playground/.venv/bin/python   # Python 3.12, MuJoCo 4.0.1

# 2. Check the scenes are solvable (scripted oracles, no LLM calls).
$PY scripts/oracle_suite.py

# 3. Preview, then run, a paired evaluation (agent = Codex CLI by default).
scripts/eval_suite.sh --models gpt-6-astra gpt-5.6-luna --seeds 0-19 --dry-run
scripts/eval_suite.sh --models gpt-6-astra gpt-5.6-luna --seeds 0-19 --lanes 3 --name my_run

# 4. Report with confidence intervals.
$PY scripts/report.py --inputs outputs/suites/my_run --name my_run
```

Scene assets (Google Scanned Objects, CC-BY 4.0; MuJoCo models) are fetched on first use
from [aloha_sim](https://github.com/google-deepmind/aloha_sim) into `third_party/`.
Every run is resumable: completed model/scene/seed tuples are skipped, interrupted
ones are archived under `incomplete/`.

## Results data

[`results/`](results/) contains every scored episode (`episode.jsonl`), run metadata
(turns, tokens, wall time) and the agent's final message, plus the generated reports.
Rollout videos are not committed (~10 MB each).

## Limitations

- Simulation only, one embodiment (YAM). Primitive-level control (IK `goto`, `grasp`):
  this measures planning, perception and judgment, not low-level motor control.
- Two models so far; small per-scene samples (n = 20) — read the CIs.
- Source-reading restrictions on the agent are prompt rules, not an OS sandbox.
- Scene versions are tracked; `pilot_tidy_desk` is v4 after a contact-solver fix
  (see [docs/suite_v0.2.md](docs/suite_v0.2.md#v021-patch-2026-11-09-tidy_desk-scene-v4-done)).

## License

Apache-2.0 (code). Third-party assets keep their upstream licenses.
