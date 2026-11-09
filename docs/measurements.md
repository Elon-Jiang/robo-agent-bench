# M0 measurements (SO-101, tabletop_v0 dev @ 2a38565)

## Workspace (embodiment.py)
- base_xy (0.04, 0); spawn_x 0.16–0.27; spawn_y ±0.13; max_reach 0.24
- IK workspace: x 0.14–0.34, y ±0.18, z 0.020–0.24 (pinch-center z floor = 2 cm)
- jaw_tip_below_pinch 0.02; topdown_reach 0.22; min_pitch 1.15 rad

## Gripper (rotary jaw; distance between jaw tip spheres)
| gripper joint q (rad) | tip gap |
|---|---|
| -0.175 (closed) | 0.3 cm |
| 0.0 | 1.5 cm |
| 0.5 | 5.3 cm |
| 1.0 | 8.7 cm |
| 1.745 (max) | 12.8 cm |

## Assets
- YCB 025_mug (aloha_sim) half extents at scale 1.0: 0.063 x 0.0535 x 0.0534 m (with handle)
  -> use scale 0.55: ~6.9 x 5.9 x 5.9 cm

## Sponge decision
- Rigid box 5.0 x 3.0 x 3.0 cm (long x grasp-width x height); grasp across the 3 cm side.
- Grasp with pinch z ~ 0.024; wipe passes at pinch z = 0.020 (IK floor) -> ~4 mm press absorbed by soft contact.
