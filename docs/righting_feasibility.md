# Tipped-mug righting feasibility

Study date: 2026-11-07. Upstream commit: `2a38565151f2985d0c0afcd7e137bf1f1b5f31f4`.

## Decision

**Both arms can right the mug using existing primitives. Recommend YAM for a provisional simulated L2 righting stage; do not enable unrestricted random-pose righting as a reliable SO-101 L2 stage yet.**

The frozen best policies, tested on a fresh seed with at most three attempts per episode, achieved:

| Robot | Retained strategy | Fresh seed 303 | Development seed 202 | Both cohorts |
| --- | --- | ---: | ---: | ---: |
| SO-101 | Non-grasping rim hook | **21/30, 70%** | 17/20, 85% | 38/50, 76% |
| YAM | Top-down rim pull | **30/30, 100%** | 16/20, 80% | 46/50, 92% |

The fresh cohort is the main result; the development cohort was used to choose strategies. Wilson 95% intervals for the fresh cohort are 52.1–83.3% for SO-101 and 88.6–100% for YAM. Thirty YAM successes establish useful feasibility, not a guarantee over all possible resets.

For this recommendation, “reliable enough” means at least 90% observed stable success in the fresh cohort. This is an engineering criterion adopted for the study, not a threshold supplied by the requester. SO-101's 21 successes establish physical possibility; the negative recommendation concerns reliability with the tested primitives and policies, not mathematical impossibility.

**This is a single-mug mechanical feasibility result.** The other mugs, sponge, spill, and preservation of neighboring objects were deliberately absent. Before enabling the complete `spill_wipe` L2, test collateral collisions: successful YAM trials moved the mug by a median 5.4 cm and up to 14.7 cm; SO-101 moved it by a median 4.9 cm and up to 8.5 cm. Neither arm was tested on hardware or with vision-estimated poses.

## Scene and acceptance

The registered task is `right_mug`, with the exact prompt:

> The mug has fallen over. Stand it back upright.

It contains exactly `T.ycb("mug", "025_mug", 0.55)`. The existing asset, collision meshes, friction, mass, robot gains, joint limits, IK, and primitive executor are unchanged. The compiled mug mass is approximately 20.83 g. Its AABB half-extents are `(0.03464, 0.02943, 0.02938)` metres; these include the handle and are not the cylindrical body's radius.

Every reset independently randomizes XY, mouth azimuth, and spin about the mug axis. The initial rotation is `Rz(mouth) @ Ry(pi/2) @ Rz(spin)`, with both angles uniform over `[-pi, pi]`. Collision vertices determine the initial height, leaving 1 mm above the table even when the handle points downward. The mug then settles naturally during the normal robot homing motion.

| Robot | XY sampling rectangle, metres | Reach constraint on initial body center |
| --- | --- | --- |
| SO-101 | X 0.16–0.27; Y −0.13–0.13 | Within 0.22 m of `(0.04, 0)` |
| YAM | X 0.24–0.40; Y −0.14–0.14 | Within 0.42 m of `(0, 0)` |

These are the embodiment's native spawn rectangles and `max_reach - 0.02`, allowing 2 cm for settling motion. They are not a promise that every subsequent finger pose or orientation is reachable. Strategies share identical resets within each robot/seed cohort. Cross-robot samples are not paired exactly: different XY rejection rates consume the RNG differently.

The task reward requires body +Z within 15° of world +Z, no robot–mug contacts, table support, linear speed below 0.02 m/s, and angular speed below 0.2 rad/s. Contact sensors measure release directly: the upstream generic 45 mm pinch-distance shortcut can falsely declare a rim-held mug released.

**Reported success is stricter than an instantaneous reward:** the study runner lets the final plan finish, waits five simulated seconds, and requires the entire final second to satisfy upright/released/supported/stationary criteria. It independently reads the body rotation matrix, physical contacts, and velocities. It does not count a momentarily upright mug or an upside-down mug. All screening starts were on the side; observed pre-plan tilt was approximately 85–87° after settling.

## Strategies and screening results

Seed 101, 10 episodes on SO-101 and 5 on YAM **for every strategy**. The first eight rows permit one oracle plan/attempt. Feedback permits up to 16 short plans; hybrid permits three attempts, so their percentages should not be compared as equal-budget improvements.

| Strategy / `RAB_RIGHT_STRATEGY` | Motion attempted | Budget | SO-101 | YAM |
| --- | --- | ---: | ---: | ---: |
| `body_direct` | Pinch body near base; lift; rotate tool about the mug's horizontal tangent; lower and release | 1 | 1/10, 10% | 1/5, 20% |
| `body_radial` | Body grasp; align wrist in the radial plane; then pitch upright | 1 | 1/10, 10% | 0/5, 0% |
| `body_offset` | Compensate SO-101 fixed-jaw offset; carry outward; release at a reachable 25° tool pitch | 1 | 0/10, 0% | 0/5, 0% |
| `rim_pivot` | Pinch upper lip; short lift; move around an estimated base/table pivot while changing pitch | 1 | 3/10, 30% | 5/5, 100% |
| `rim_lift` | Acquire near upper lip; lift clear; rotate and lower | 1 | 2/10, 20% | 3/5, 60% |
| `rim_hook` | Approach upper lip with jaws at 15% opening; use the finger geometry as a hook along a rising arc; no verified grasp | 1 | 5/10, 50% | 0/5, 0% |
| `rim_pull` | Acquire upper lip; follow the pivot arc while keeping fingers top-down; allow contact-driven rotation in the pads | 1 | 3/10, 30% | 5/5, 100% |
| `handle` | Target the handle using its full world pose; lift, rotate, and lower | 1 | 0/10, 0% | 1/5, 20% |
| `rim_feedback` | Reobserve between 12° pulling increments; release when upright; reacquire after lost contact | 16 | 6/10, 60% | 5/5, 100% |
| `rim_hybrid` | Change contact mode between retries: pull → hook → pivot on SO-101; pull → pivot → lift on YAM | 3 | 8/10, 80% | 5/5, 100% |

These are intended contact strategies, not claims that every commanded grasp was actually achieved. In particular, a purported rim grasp sometimes initially catches a larger part of the body before sliding toward the lip.

Larger development checks prevented selecting a policy from five lucky trials:

| Strategy | Attempts/plans | SO-101, seed 202 | YAM, seed 202 |
| --- | ---: | ---: | ---: |
| Rim hook | 3 | **17/20, 85%** | Not expanded; 0/5 screening |
| Rim pull | 3 | 13/20, 65% | **16/20, 80%** |
| Rim pivot | 3 | 9/20, 45% | 14/20, 70% |
| Rim lift | 3 | Not expanded | 13/20, 65% |
| Rim feedback | 16 | 13/20, 65% | 13/20, 65% |
| Hybrid | 3 | 4/10, 40% | 4/5, 80% |

The default oracle (`best`) therefore resolves to `rim_hook` for SO-101 and `rim_pull` for YAM. Both were frozen before the fresh seed-303 cohort. Extra feedback and hybrid strategies remain available for reproduction but are not the defaults.

## Failure analysis

1. **SO-101 body acquisition has a concrete geometric problem.** Its fixed jaw is about 17 mm from the reported pinch center, whereas the mug body's diameter is roughly 46 mm. A centered top-down command intersects the fixed finger during descent. Recorded descents stop centimetres above the target and can close empty. Offsetting the pinch by 11 mm improved lifting to 9/10 screening episodes, but none finished upright: acquisition alone was not sufficient.

2. **Position tracking does not imply orientation tracking.** Upstream IK prioritizes pinch position over orientation, and the executor's settle gate checks position rather than angular error. SO-101 has five arm joints and cannot freely choose an arbitrary tilted approach azimuth. YAM has six joints but still encounters joint-limit/pose constraints. In screening body-transfer traces, median final tool-orientation errors were approximately 110° for SO-101 `body_direct` and 72° for YAM `body_direct`; the offset variant still had approximately 66° and 30° errors. A separate IK probe also found sub-millimetre position residuals alongside large angular residuals. These are observed tracking/solver limitations, not a proof that no alternative IK branch exists.

3. **Non-prehensile rim contact is the useful route.** SO-101's asymmetric finger can hook and lever the mug without first retaining a full-diameter grasp. YAM's top-down rim pull avoids demanding a precise sideways tool orientation and lets table contact and gravity complete the rotation. Both use only public primitive dictionaries (`grasp`, `goto`, `move_by`, `gripper`, `home`). No welding, object-pose writes during execution, custom torques, added motion primitive, or altered controller was used.

4. **The nine SO-101 holdout failures ended tilted or sideways.** Eight never lifted the body by more than 25 mm; one lifted and subsequently ended tilted. Final tilt angles were 41–85°. Failed episode IDs are 3, 7, 12, 15, 16, 19, 27, 28, and 30. Several commanded arc points also leave the controller's safe workspace, where clipping and poor tracking prevent the intended lever motion. No holdout mug was knocked off the table. With a hook strategy, “no lift” is an outcome descriptor, not a failed grasp detector.

5. **YAM still has known failures outside the fresh cohort.** Four of the 20 development rim-pull episodes failed, including an upside-down result and other side/tilted results. All four happened to have initial spins near the handle-down orientation. That association is not a sufficient failure rule: the fresh cohort passed all 30. It is a reason to retain broad handle randomization and further stress tests.

6. **More feedback was not consistently better.** Small-step feedback sometimes repeatedly missed the lip, retained a poor contact, or reacquired after the mug had moved into a worse pose. Its 20-episode development scores were 65% on both arms despite a much larger turn budget. The fixed arc can also pass through upright and then tip the mug again; final-state scoring is essential.

## Important standard-runner scoring caveat

The unmodified upstream `run.py` latches success with `success = success or reward >= 1.0`. Its terminal console counter is therefore **not the acceptance metric used above**. Even the supported, low-velocity task reward can be true briefly before another part of the plan moves the mug again.

This was directly observed with SO-101 seed 303, episode 3: the standard entry point printed `success=True`; the same primitive sequence under the final-state evaluator ended at **44.0° tilt**. Holdout SO-101 episodes 3, 19, and 28 had a transient success but failed final acceptance. Episode 2 also stopped earlier under latched scoring, while the final-state evaluator required another attempt. The discrepancy changes retry behavior as well as the printed count.

The standard entry point was exercised on 10 SO-101 episodes and 5 YAM episodes, and representative videos were inspected. SO-101 printed **9/10**, versus **8/10** stable successes for the same first ten resets in the independent evaluator. The YAM five-episode console result was 5/5 and agreed with final-state acceptance. Use the study runner below to reproduce the reported stable rates. A future L2 integration should evaluate the final settled state rather than inherit the latched console count.

## Reproduction

Run from the repository root. These commands reproduce the **measured fresh-cohort results**, including all failures, through the unchanged upstream `OracleBackend`, `AgentPolicy`, `PlanExecutor`, IK, and physics. Only unused camera rendering is omitted in this evaluator.

```bash
# SO-101: 21/30 stable successes; default best = rim_hook.
PYTHONDONTWRITEBYTECODE=1 ../mujoco_playground/.venv/bin/python \
  -m scripts.right_mug_study --robot=so101 --strategy=best \
  --episodes=30 --seed=303 --turns=3 \
  --output=/tmp/right_mug_so101_repro.jsonl

# YAM: 30/30 stable successes; default best = rim_pull.
PYTHONDONTWRITEBYTECODE=1 ../mujoco_playground/.venv/bin/python \
  -m scripts.right_mug_study --robot=yam --strategy=best \
  --episodes=30 --seed=303 --turns=3 \
  --output=/tmp/right_mug_yam_repro.jsonl
```

The evaluator refuses to overwrite an existing evidence file; choose a new output path when rerunning. For the requested standard entry point and optional visual inspection:

```bash
PYTHONDONTWRITEBYTECODE=1 RAB_RIGHT_SEED=303 RAB_RIGHT_STRATEGY=rim_pull \
  ../mujoco_playground/.venv/bin/python run.py \
  --mode=sim --robot=yam --agent=oracle --task=right_mug \
  --episodes=5 --max_turns=3 \
  --video=/tmp/right_mug_yam_repro.mp4

# SO-101's retained strategy:
PYTHONDONTWRITEBYTECODE=1 RAB_RIGHT_SEED=303 RAB_RIGHT_STRATEGY=rim_hook \
  ../mujoco_playground/.venv/bin/python run.py \
  --mode=sim --robot=so101 --agent=oracle --task=right_mug \
  --episodes=10 --max_turns=3
```

To reproduce a screening row, select its strategy with `--strategy`, use `--seed=101`, and set `--episodes=10` on SO-101 or `5` on YAM. Set `--turns=1`, except `rim_feedback` uses `16` and `rim_hybrid` uses `3`. No bridge agent or port 8081 was used.

## Evidence and implementation

- [SO-101 fresh-cohort records](../outputs/right_mug/holdout_so101_best_seed303.jsonl) and [YAM fresh-cohort records](../outputs/right_mug/holdout_yam_best_seed303.jsonl): reset poses, complete primitive plans, per-segment observations, final tilt/contact/velocity checks, statuses, and source hash.
- [Aggregated counts and failure IDs](../outputs/right_mug/summary.json), [environment/source manifest](../outputs/right_mug/manifest.json), and [IK probe](../outputs/right_mug/ik_probe.json).
- [SO-101 retained-strategy video](../outputs/right_mug/so101_best_seed303.mp4) and [YAM rim-pull video](../outputs/right_mug/yam_rim_pull.mp4).
- Screening files are `outputs/right_mug/final_<robot>_<strategy>.jsonl`; hybrid screening is `hybrid_<robot>_seed101.jsonl`. Development files are `validation_*_seed202.jsonl` and `hybrid_*_seed202.jsonl`.
- Earlier `v1_*`, `v2_*`, and `v3_*` files are exploratory pilots, **not pooled into the tables**. The initial pilot used a two-second settling window and no 2 cm spawn inset. Source snapshots preserve those pilot versions; the final source snapshot is under [final_source](../outputs/right_mug/final_source/).
- [Task and oracle](../rab/tasks/right_mug.py), [feedback experiment](../rab/tasks/right_mug_feedback.py), [measurement runner](../scripts/right_mug_study.py), and [reward negative controls](../tests/test_right_mug.py). Five tests pass, including inversion, held/airborne mugs, tolerance boundaries, motion, and batched evaluation; formatting checks pass.

The only changed pre-existing source file is `rab/__init__.py`, for registration and read-only cached asset resolution. Upstream was not edited; its pre-existing `uv.lock` modification remains untouched. No dependency installation was required. The environment was macOS ARM64, Python 3.12.13, MuJoCo `4.0.1.dev1015921253`, CPU `cpp` physics, 0.002 s simulation steps, and 0.02 s control steps.
