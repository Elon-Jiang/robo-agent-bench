# spill_wipe — "A mug was knocked over; wipe up the spill"

Task id: `spill_wipe` (v0) · Robot: SO-101 · Base: MuJoCo Playground `tabletop_v0`

## Prompt given to the agent
> A mug of water was knocked over and spilled on the table. Use the sponge to wipe up all of the
> spilled water without knocking over the other mugs, then put the sponge down.

## Scene
| Element | Model | Randomized |
|---|---|---|
| 3 mugs | YCB `025_mug`, scale 0.55 (~7 x 6 x 6 cm) | positions (>= 2 cm gap), yaw |
| Tipped mug | one of the 3, lying on its side | which mug, mouth direction, spin about its axis |
| Spill | visual-only liquid cells (1 cm grid, overlapping discs), grown from the mouth as 2 random ellipses | size, shape, direction; clipped to reach; never under a mug |
| Sponge | rigid 5 x 3 x 3 cm, orange foam + dark-green scrub pad (soft, low-friction contact) | position (clear of liquid and mugs), yaw |
| Lighting | upstream tabletop_v0 light randomization | per reset |

Liquid is approximated the same way robosuite `Wipe`, RLBench `wipe_desk`, VLMbench and
OmniGibson visual particles do: non-colliding markers removed by a logical wipe rule
(MuJoCo has no fluid solver).

## Wipe rule (per physics substep)
A liquid cell is wiped when **all** hold (adapted from robosuite `Wipe._get_active_markers`):
1. the sponge touches the table, and no robot geom touches the table;
2. the cell centre is within 6 mm of the sponge's bottom face plane;
3. its projection lies inside the bottom face rectangle.

Liquid that ends up under a mug during the first 1 s settling window is removed from the
target (it is unreachable). Wiped cells are hidden (alpha 0).

## Scoring (final state, programmatic)
| Term | Points |
|---|---|
| Sponge picked up (lifted > 2 cm) | 10 |
| Wiping progress | 70 x fraction of liquid wiped |
| No collateral (only if the sponge was picked up) | 20, -10 per upright mug knocked over (tilt > 45 deg), -5 per mug moved > 3 cm |
| Arm hits the table | -2 per contact event, capped at -10 |

Score is clipped to [0, 100]. A do-nothing policy scores 0.

**Binary success** (episode termination): >= 95% of liquid wiped, no upright mug knocked over,
sponge released.

Efficiency is reported separately (turns, sim steps, wall time).

## Validation
| Check | Script | Result |
|---|---|---|
| Wipe rule (hover / rest / slide / visibility) | `scripts/wipe_unit_test.py` | PASS |
| 40 random layouts valid | `scripts/layout_check.py --n 40` | 40/40 |
| Score & success on constructed states | `scripts/scoring_test.py` | PASS |
| Oracle stroke coverage (geometric, 20 layouts) | `scripts/oracle_coverage.py --n 20` | 20/20 at 100% |
| Oracle solvability (full physics, 10 episodes) | `run.py --agent=oracle --task=spill_wipe --episodes=10` | **9/10 success, mean score 96.4** |

Known failure mode (2 of 23 physics episodes so far): wiping stalls around 60% and an upright mug is
moved > 3 cm. Not yet diagnosed; needs a video of a failing seed.

Fairness guarantees: every liquid cell is coverable by at least one collision-free sponge pose
(`_wipeable`), liquid under a mug after settling is dropped, and a do-nothing policy scores 0.

Demo: `outputs/spill_wipe_demo.gif`, keyframes `outputs/spill_wipe_strip.png`.

## Not in v0
Righting the tipped mug (v1), sponge capacity / wringing out (v2), YAM support.
