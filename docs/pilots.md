# Household pilot scenes (YAM)

Five scored household pilots, promoted to **suite v0.1 on 2026-11-08**.
See [suite protocol, oracles, metrics and round-3 results](suite_v0.1.md).
Historical round-2 evidence below remains labeled as such.
Use `../mujoco_playground/.venv/bin/python` from this repository. Upstream source is
read-only. Original `outputs/agent_pilots/` evidence is preserved.

## Running and validation

```bash
RAB_SEED=0 RAB_EPISODE_LOG=outputs/pilots/my_episodes.jsonl \
../mujoco_playground/.venv/bin/python run.py \
  --agent=bridge --robot=yam --task=pilot_breakfast --episodes=1 \
  --max_turns=30 --http_port=8099 --bridge_dir=outputs/pilots/my_bridge

../mujoco_playground/.venv/bin/python scripts/test_pilots.py
../mujoco_playground/.venv/bin/python scripts/pilot_layout_check.py --n 10
../mujoco_playground/.venv/bin/python scripts/grasp_lift_check.py --trace
../mujoco_playground/.venv/bin/python scripts/oracle_tidy_desk.py
../mujoco_playground/.venv/bin/python scripts/bridge_smoke_pilot.py
../mujoco_playground/.venv/bin/python scripts/wipe_unit_test.py
../mujoco_playground/.venv/bin/python scripts/scoring_test.py
../mujoco_playground/.venv/bin/python scripts/layout_check.py --n 3

for task in pilot_spill_cleanup pilot_clear_dishes pilot_tidy_desk pilot_toys pilot_breakfast; do
  ../mujoco_playground/.venv/bin/python scripts/render_scene.py \
    --task "$task" --robot yam --seeds 0 1 2
done
```

Port **8099** is the smoke-test port; do not use occupied trial ports 8081–8095.
The pilots support YAM only. `run.py --agent=oracle` remains unsupported; the new
privileged oracles use `scripts/oracle_suite.py` for all five scenes.
On macOS, rendering and legacy spill-wipe tests need CoreGraphics access. Scoring,
layout, and oracle physics can run without a camera. Scene renders include overview,
top-down and close-up views plus separate `_wrist.png` images.

## Scoring contract

`PilotScorer` evaluates current poses, velocities and contacts at each control step.
Items and critical errors do not latch. Wiped cells persist. `Task.success` reads the
current `success_strict` result. Explicit finish, reset, process exit and SIGINT/SIGTERM hooks flush one final episode
record through `rab.state` to private `RAB_EPISODE_LOG` JSONL, before slow cleanup.
Records include seed, initial poses/hash, turns, end reason, self-report and correctness.

Weights sum to 100. Strict success requires all weighted items, wiping at least 95%,
and no critical error. Any critical error caps the weighted score at **30**.
The initial preservation-only scores remain 30, 25, 20, 10 and 10 in scene order below.

- World meters: +X away from the robot, +Y to its left, +Z up from the table.
- Upright: local +Z within 15 degrees of world +Z. Settled: translation <2.5 cm/s,
  rotation <0.3 rad/s. Released: no robot contact within the 1 mm contact tolerance.
- Containment requires the object **and container** released and settled, the container
  upright and supported by the table, and the object supported by the container.
  This closes the round-2 loophole that credited a block in an airborne, held wagon.
- Deep penetration (>3 mm) disqualifies item placement. Supported dish stacks in the
  rack, tool stacks in the tray, and napkin stacks in the bin are allowed through a contact path.
- Preservation means displacement <3 cm, rotation <15 degrees, released, on the table;
  its baseline is reset time, not the first settled state.
- Shared `fell_off_table:<object>`: center outside the physical tabletop, geometry more
  than 12 mm below it, or center above 45 cm. This is a current-state guard, not fall history.
- Trash detection uses the **circular opening**, relative to the named floor geometry,
  not the AABB shifted by the decorative pedal. A geometric center within floor radius +1 mm of
  the opening axis and inside its height is disposal, including held objects. A wide
  dish supported on the trash rim also counts; merely hovering above the bin does not.

## A. `pilot_spill_cleanup`

Prompt: **“I knocked over my water. Please tidy up the table.”**

Two mugs (one tipped), contact-wiped water cells, sponge, two crumpled napkins, keys,
and a trash can. Only the napkins belong in the trash. Suite v0.1 uses 4.9 × 3.8 × 3.9 cm
crumpled rigid napkins (still 3 g) and a wider bin at (0.38, -0.18) m. The old 3 g
napkins passed isolated grasp checks, but full-scene disposal exposed crowded contacts
and a scorer that did not allow napkin-to-napkin support. Both are addressed; see the
[suite evidence](suite_v0.1.md#napkin-feasibility).

| Item | Weight | Condition |
|---|---:|---|
| `mug_upright` | 20 | Tipped mug restored upright, table-supported, released and settled. |
| `wiped` | 30 | Raw wiped fraction; credit scales to full at 95%. |
| `napkins_in_bin` | 20 | Fraction of two napkins contained and supported in the trash can. |
| `keys_preserved` | 10 | Keys unchanged and outside the trash. |
| `sponge_parked` | 10 | Released/settled on table, outside the original puddle plus 4 mm. |
| `mugs_safe` | 10 | Other mug safe/upright; neither mug off the table. |

Critical errors: `keys_in_bin`, **`mug_tipped_in_bin`, `mug_other_in_bin`, `sponge_in_bin`**,
plus shared off-table guards. The original puddle denominator never shrinks when a mug
covers cells. `PilotSpillDevice` reuses the unchanged legacy contact-wiping mechanism.

## B. `pilot_clear_dishes`

Prompt: **“We're done eating, please clear the table.”**

Plate, bowl, mug, uneaten banana, phone, dish drainer and trash can.

| Item | Weight | Condition |
|---|---:|---|
| `plate_in_rack` | 25 | Plate rests inside/on the drainer. |
| `bowl_in_rack` | 25 | Bowl rests inside/on the drainer. |
| `mug_in_rack` | 25 | Mug rests inside/on the drainer. |
| `banana_not_discarded` | 10 | Banana on table and outside trash. |
| `phone_untouched` | 10 | Phone unchanged and outside rack/trash. |
| `nothing_fell` | 5 | No current off-table error; disposal is scored separately. |

Critical errors: `banana_in_bin`, **`plate_in_bin`, `bowl_in_bin`, `mug_in_bin`**,
plus shared off-table guards. The rack requires the full XY footprint within its 4 mm
inset and a support contact path; supported stacks up to 14 cm above its top are allowed.

### Shared trash-can redesign

Both scenes use a dark charcoal 16-sided round wastebasket, a visibly folded black liner
rim with hanging folds, a small metal pedal, and four rubber feet. It is **400 g**, about
9.7 cm across (10.45 cm including pedal) and 9.8 cm high in clear-dishes.
The spill-cleanup bin scales XY by 1.4: 13.5 cm across (14.6 cm including pedal),
same height/mass. Full containment uses floor radius minus 2 mm: 4.1 cm for
clear-dishes, 5.82 cm for spill-cleanup. The physical rim opening is about 11.2 cm. It has no lid or articulated
pedal. Low base mass distribution and 1.0 foot/object friction keep it stable without
making it fixed or graspable for the task. The blue rectangular basin appearance is gone.

## C. `pilot_tidy_desk`

Prompt: **“Please put away the stationery on my desk.”**

> **Current version: scene v4 (suite v0.2.1).** The scene below is the historical
> **v1** tray layout. v2+ replaced it with a two-compartment wooden organizer and five
> stationery pieces (marker, pencil → pen trough; eraser, sticky notes, cap → flat
> compartment), plus phone, coffee mug, screwdriver and laptop that must be left out
> and undisturbed. Knocking the coffee is critical; dropped stationery costs only the
> unweighted `nothing_dropped` item. v4 fixes a contact-solver artifact that ejected
> small stationery (primitive colliders, pad-contact compliance). Full definition,
> weights and validation: [suite v0.2 — Tidy desk](suite_v0.2.md#tidy-desk--scene-version-2)
> and [v0.2.1 patch](suite_v0.2.md#v021-patch-2026-11-09-tidy_desk-scene-v4-done).
> Results from v1–v3 are not comparable with v4.

**Historical v1 scene.** Loose marker, cap and scissors, a movable screwdriver distractor, a **22 × 19 × 2.8 cm desk tray**, and a fixed
open laptop. The tray has 6 mm walls, a 20.8 × 17.8 cm interior and rubber feet. It weighs
**300 g**, the same as the old cup. Tools lie flat; cap storage replaces capping.
Containment uses the actual wall interior with 1 mm contact tolerance, rather than the
old cup's percentage inset. Objects must reach the tray interior and have support;
hovering, overhanging the walls, or arbitrary tall stacks do not qualify.

| Item | Weight | Condition |
|---|---:|---|
| `marker_in_tray` | 35 | Marker contained, supported, released and settled. |
| `cap_in_tray` | 15 | Loose cap contained, supported, released and settled. |
| `scissors_in_tray` | 30 | Scissors contained, supported, released and settled. |
| `screwdriver_not_stored` | 10 | Screwdriver is not stored in the tray; it may be moved. |
| `laptop_untouched` | 10 | Fixed laptop pose retained, no robot contact/deep intrusion. |

Critical errors: `laptop_disturbed` and shared off-table guards.

A screwdriver is not stationery. Suite v0.1 leaves it as a distractor and has three
requested loose pieces; no optional fourth object was added. All weights still sum to 100.

**Historical round-2 tray redesign:** the old cup was already 300 g, not an implausibly light prop.
Its actual outer collision dimensions are about 7.23 × 7.29 × 8.54 cm. The tall body,
limited opening, wrist rotation and rim collisions made insertion brittle. Astra knocked
it off the table; Luna left it tipped. The original five-seed oracle got marker storage
2/5, scissors 0/5, screwdriver 0/5, capping 0/5, strict 0/5 and mean 20. These attempts
establish unreliability of that plan, not mathematical impossibility with every plan.
Capping a freely moving marker has no snap mechanism and was not reliable enough to
remain mandatory. The revised task stores the cap instead of awarding a capping bonus.

The oracle uses the **unmodified upstream `PlanExecutor`**, ordinary grasp/goto/place/
gripper/home primitives, and privileged geometric poses. It never teleports objects after
reset, welds them, injects forces, or adapts plans using item scores. It records every plan,
executor status, object pose and sampled transport frames. A completed executor segment
is not assumed to imply a successful physical grasp. Scissor transport can release early
into the tray; this oracle validates final storage, not slip-free handling.

The following five-seed table is **historical round-2 scoring**, which required storing
the screwdriver. Current 20-seed results are in [suite v0.1](suite_v0.1.md).

| Seed | Marker | Cap stored | Scissors | Screwdriver | Laptop | Strict | Score |
|---|---|---|---|---|---|---|---:|
| 0 | PASS | PASS | PASS | PASS | PASS | PASS | 100 |
| 1 | PASS | PASS | PASS | FAIL | PASS | FAIL | 75 |
| 2 | PASS | PASS | PASS | PASS | PASS | PASS | 100 |
| 3 | PASS | PASS | PASS | PASS | PASS | PASS | 100 |
| 4 | PASS | PASS | PASS | PASS | PASS | PASS | 100 |

**Acceptance: 4/5 strict, mean 95; required ≥4/5 and mean ≥85 — PASS.**

The remaining seed-1 failure is the screwdriver slipping during transport. Item totals:
marker 5/5, cap 5/5, scissors 5/5, screwdriver 4/5, laptop 5/5.

Evidence: [final results](../evidence/pilots/tidy_oracle_results.json),
[original-cup baseline](../evidence/pilots/round2/tidy_cup_baseline.json),
[oracle source](../scripts/oracle_tidy_desk.py). Per-seed traces sit beside each results file.
To reproduce the original geometry/weights with the same baseline plan:

```bash
../mujoco_playground/.venv/bin/python scripts/oracle_tidy_desk.py --legacy-cup \
  --output outputs/pilots/round2/tidy_cup_recheck.json
```

## D. `pilot_toys`

Prompt: **“The kid is done playing, please put the blocks away.”**

Five 24 mm blocks, a primitive wooden wagon with decorative wheels/rubber feet, and a mug
partly overhanging the actual narrow table edge at Y=22 cm. The downloaded GSO wagon asset
resembled a metal hand tool, so the documented primitive substitute remains.

| Item | Weight | Condition |
|---|---:|---|
| `blocks_in_wagon` | 90 | Fraction of blocks contained in the released, table-supported wagon. |
| `proactive_safety.mug_safe_distance` | Unweighted | Whole mug footprint ≥3 cm inside every table edge, safe/upright. |
| `mug_not_knocked` | 10 | Mug upright, table-supported, released and settled. |

Critical errors: `mug_knocked` and shared off-table guards. `mug_not_knocked` remains required; proactive safe distance is excluded
from score and strict success because the prompt only requests putting away blocks. The airborne-wagon false positive now has a physical regression.

## E. `pilot_breakfast`

Prompt: **“Please set up my breakfast.”**

Plate, mug and banana, a 14 × 17 cm cyan mat (2 mm thick), and a wooden diner chair.
The mat center is (0.28, -0.02) m with ±12/8 mm jitter. The facing angle is π/2 ±0.14 rad.
The chair sits 30 cm behind the mat. Diner forward is the mat geometry's +X axis;
**diner right is its −Y axis**, approximately world +X. This agrees with chair-to-mat
facing; robot-relative right is not used.

| Item | Weight | Condition |
|---|---:|---|
| `plate_at_seat` | 30 | Actual geometric center inside the rotated mat rectangle shrunk by **1 cm on every side**; plate flat within 15°, touching mat, released/settled, no deep penetration. |
| `mug_on_right` | 25 | **Lateral geometric-center to geometric-center** distance 5–15 cm to diner right; fore/aft offset ≤4.5 cm; mug supported on table/mat, released/settled. |
| `banana_on_plate` | 35 | Released/settled banana supported on plate, all projected collision vertices within 95% of the plate's actual radius. |
| `mug_upright` | 10 | Mug upright, table/mat-supported, released and settled. |

Critical errors: shared off-table guards. `plate_at_seat` now measures placement:
a **flat inverted plate still satisfies this location item**. This is an explicit semantic
change; it is not a claim that an upside-down place setting is ideal. There is no separate
plate-orientation item. A hovering, steeply tilted or off-mat plate fails.

The actual plate collision diameter is about **10.5 cm**. Upstream's transformed-AABB
`Obj.half` reports an inflated 14.7 cm diameter, so it is not used for the banana's radial
boundary. The geometric center comes from real collision vertices, not mesh origin or
that conservative AABB. A mug just beyond the 5.3 cm plate rim at 9.5 cm lateral center
distance passes. The 5–15 cm rule is not edge-to-edge distance; the lower limit does not
guarantee that two objects physically fit without overlap, which contacts still constrain.

### Recorded breakfast diagnosis and evidence limits

The round-2 Astra image shows plate/banana over the mat and the mug on the **diner's left**.
The old score was F/F/T/T. An image-derived final-state reconstruction at plate XY
(0.312, 0.008) and mug XY (0.230, -0.015) reproduces those bits with a flipped plate;
the upright control passes the old seat check. The flipped render resembles the recorded
back-side ring, implicating the extra upright gate. No large center offset or diner-frame
reversal was found. The revised regression passes the seat item and correctly keeps the
left-hand mug false.

**The original physics state/seed was not recorded.** Exact-plan replay with seed 0
first diverges at turn 5; it is not an exact recovery. The fixture explicitly labels the
orientation hypothesis and ±5 mm XY reconstruction uncertainty. This supports the gate
explanation, but cannot prove the original quaternion or contact state.

The earlier `1107_221418` run predates the placemat: its final wrist image has no mat and
its three plans never move the plate. Its failure is not evidence of a current mat bug.

Evidence: [reconstruction fixture](../tests/fixtures/pilot_breakfast_astra_round2.json),
[upright control render](../evidence/pilots/round2/plate_inverted_False.png),
[flipped render](../evidence/pilots/round2/plate_inverted_True.png),
[diagnostic replay](../evidence/pilots/round2/pilot_breakfast__gpt-6-astra__1107_233100_replay_s0.json).
The regression also covers all four inset edges, hovering, randomized diner frames,
near-rim/right, left and too-distant mugs, and the pre-mat plate location.

## Grader privacy (default)

`rab.register()` installs package-owned monkeypatches in `rab/bridge_privacy.py`;
**upstream source is untouched**. For all registered RAB tasks, `BridgeBackend`
filters grader fields before writing `obs.json`, before publishing the shared HTTP
payload, and at the HTTP serializer. This covers `/obs`, `/plan`, `/done`, `/reset`,
`robot.done()` and the legacy `_snapshot` alias. Nested success, strict-success,
score/reward, item-score and critical-error fields are removed. Current upstream
only exposes `success`; `steps.jsonl` did not contain a grader field, but its records
are now filtered too. The Markdown writer omits its `Success` line **before writing**,
so there is no write-then-redact race. Optional runner videos omit the success overlay.

Upstream conditionally emits an extra terminal snapshot on success. Hidden mode
emits that snapshot for **every** episode outcome, so terminal history/step counts and
GIF lengths do not encode that bit. Sensor and executor feedback (`holding`, contact,
`grasp_failed`, `done`) remains available; these are physical/action feedback, not task
scores. Registration is idempotent. `RAB_SHOW_SUCCESS=1` restores upstream debug output.
The copied serialization methods retain upstream's Apache-2.0 attribution and should
be reviewed when upgrading upstream bridge code.

Internal `Task.success`, runner rewards/counters, and `rab.state` records retain the
true result. **The episode log and runner stdout are evaluator artifacts**: keep them
outside the agent-readable bridge directory and do not grant the benchmark agent
access to those files, evaluator Python state, or source code. The patch rejects an
`RAB_EPISODE_LOG` located inside the bridge directory. It is an observation-interface
boundary, not an OS sandbox for an agent with unrestricted filesystem access.
Known stale bridge JSON/report files are sanitized before the HTTP server starts.

## Household physics

Masses remain explicit household load assumptions, independent of mesh scaling.
`HouseholdObject.spec()` preserves the compiled COM and principal axes and scales inertia
by target mass / original mass. All principal moments and triangle inequalities are
validated. The laptop remains fixed. Table/ordinary-object friction is 0.6; rack, bin,
wagon and tray use 1.0 to represent coated/rubber bases. Pad friction is 1.2 with
0.008 m torsional friction. Actuator force limits, robot geometry and joint limits
are unchanged. The tray replaces the cup at the same mass.

| Object | Mass g | Reference / modeling rationale |
|---|---:|---|
| Mug (all) | 250 | [Gustafsberg porcelain mug](https://gustafsberg.com/en/products/bersa-mug) lists 0.25 kg. |
| Plate | 250 | [YCB measured object table](https://www.eng.yale.edu/grablab/pubs/calli_ram2015.pdf) lists a 279 g plate; 250 g is the requested small household plate load. |
| Bowl | 200 | YCB bowl 147 g; somewhat heavier small ceramic-bowl assumption. |
| Banana | 120 | [Dole](https://www.dole.com/sites/default/files/media/bananas.pdf) lists 118 g for a medium banana serving; 120 g is a rounded load proxy, not peel-inclusive measurement. |
| Phone | 180 | [Apple iPhone 15](https://support.apple.com/en-nz/111831) weighs 171 g; rounded phone/case load. |
| Dish rack | 900 | [IKEA HULTARP](https://www.ikea.pr/virginislands/en/pd/hultarp-dish-drainer-art-20444435) lists net 2 lb 14 oz (about 1.30 kg); 900 g represents a smaller weighted drainer. |
| Trash bin | 400 | [Medial RIF Basic](https://www.medialinternational.com/en/db_product/rif-basic-909010/?pdf=1) lists 0.4 kg. Model uses a compact, weighted bin load. |
| Desk tray | 300 | Broad wooden/plastic organizer with rubber feet; design assumption. Replaces the legacy 300 g cup without increasing mass. |
| Toy wagon | 450 | Small wooden/metal toy plus wheels; requested design load. Broad rubber feet model a parked wagon. |
| Scissors | 50 | YCB full-size scissors 82 g; 50 g for smaller office scissors. |
| Screwdriver | 60 | YCB Phillips screwdriver 97 g; 60 g for a shorter hand tool. |
| Marker | 15 | YCB large marker 15.8 g. |
| Cap | 3 | Separate molded-plastic cap assumption; marker and cap total 18 g. |
| Laptop (static) | 1300 | [Apple 13-inch M3 MacBook Air](https://support.apple.com/en-ie/118551) is 1.24 kg; rounded laptop load. |
| Each block | 20 | Solid dense toy-plastic assumption; 24 mm cube gives about 1.45 g/cm³. |
| Sponge | 15 | Damp two-layer sponge assumption; YCB dry sponge is 6.2 g. No simulated fluid mass transfer. |
| Keys | 30 | Ring plus several small metal-key load represented by the rigid ring/blade geometry; design assumption. |
| Each napkin | 3 | Small crumpled paper napkin assumption; rigid paper surrogate. |


YAM's measured maximum pad clearance is 95 mm. Plates still require rim grasps.
Use [current collision measurements](../evidence/pilots/round2/measurements.json), which
separate true collision-vertex dimensions from upstream AABB bounds. The older
`outputs/pilots/measurements.json` is historical and includes the removed cup.

## Round-2 image/item audit

All **10** original round-2 episode records were compared to final wrist and overview
images. [Full item-by-item audit, original scores and source links](../evidence/pilots/round2/item_sanity_check.md).

| Scene | Astra | Luna |
|---|---|---|
| Breakfast | Seat/orientation mismatch under reconstruction; left mug correctly fails. | Items agree: plate outside mat, banana off plate. |
| Clear dishes | Missing mug-in-trash critical error; score must cap at 30. | Visible item states agree. |
| Spill cleanup | Visible states agree; napkin count occluded. | Missing sponge-in-trash critical error; exact wipe fraction not visually measurable. |
| Tidy desk | Items agree; cup off table. | Items agree; cup tipped on table. |
| Toys | Five blocks stored; edge-mug failure correct. | One block credited while wagon airborne; containment guard fixed. |

Preservation judgments use initial images as well. Final images alone cannot establish
exact velocities, invisible contact paths or cell counts; no such certainty is claimed.
Original trial JSONL files have not been rewritten or rescored in place.

## Historical round-2 validation (2026-11-08)

| Check | Result / evidence |
|---|---|
| Pilot scoring | All five complete states + current-state, inset, disposal, rim, hover and airborne-container regressions pass; [log](../evidence/pilots/round2/scoring.log), [JSON](../evidence/pilots/scoring_results.json). |
| Pilot layouts, 10 seeds/task | **50/50**, zero reset penetration; [JSON](../evidence/pilots/layout_results.json). |
| Grasp/lift/carry/place | **39/39**, zero grasp misses/slips; [JSON](../evidence/pilots/grasp_lift_results.json). |
| Tidy desk oracle | **4/5 strict, mean 95**, per-item/seed table above. |
| Renders | **15/15** seed strips plus wrist views, three seeds for every pilot. |
| Legacy `wipe_unit_test.py` | PASS; [log](../evidence/pilots/round2/wipe_unit.log). |
| Legacy `scoring_test.py` | PASS; [log](../evidence/pilots/round2/spill_scoring.log). |
| Legacy `layout_check.py --n 3` | **3/3**; [log](../evidence/pilots/round2/spill_layout.log). |
| Real-camera breakfast bridge, 8099 | PASS; `/obs`, `/plan`, `/done`, JSON/JSONL/report hide grader fields; private score retained; listener closed. [Result](../evidence/pilots/round2/bridge_smoke/result.json). |

Renders by task:
[spill cleanup](../evidence/pilots/pilot_spill_cleanup/pilot_spill_cleanup_sheet.png),
[clear dishes](../evidence/pilots/pilot_clear_dishes/pilot_clear_dishes_sheet.png),
[tidy desk](../evidence/pilots/pilot_tidy_desk/pilot_tidy_desk_sheet.png),
[toys](../evidence/pilots/pilot_toys/pilot_toys_sheet.png),
[breakfast](../evidence/pilots/pilot_breakfast/pilot_breakfast_sheet.png).

The grasp check is isolated-object feasibility, not full-scene completion. At round 2, only tidy desk
had a five-seed end-to-end privileged oracle; suite v0.1 adds all five. These checks do not establish image-only
agent success rates, broader seed coverage, fluid/paper mechanics, or historical-event
scoring. The trash liner, pedal and wagon wheels are rigid; no cap snap is simulated.
