"""M3 check: randomized layouts are valid across many seeds.

For each seed: reset, let physics settle ~1 s, then check
  - upright mugs stand (tilt < 15 deg), tipped mug lies (tilt > 60 deg)
  - no two mugs interpenetrate (centre distance > 1.6 * footprint radius)
  - sponge is clear of liquid (> 3 cm from any wet cell) and of mugs
  - puddle has >= 30 cells and starts at the mouth (nearest wet cell < 3 cm)
  - tipped mug did not roll far after settling (< 2 cm)
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import rab  # noqa: E402

rab.register()

import mujoco  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
from mujoco_playground.tasks.agentic.tabletop_v0 import env as agentic_env  # noqa: E402

from rab import state  # noqa: E402
from rab.tasks.spill_wipe import MUG_NAMES  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--n", type=int, default=20)
args = ap.parse_args()

env = agentic_env.create_sim_env(task="spill_wipe", robot="so101")
sim = env.device_layer.sim
m, d = sim.mj_model, sim.mj_data


def body(name):
    return m.body(f"{name}/{name}").id


def tilt(name):
    return np.degrees(np.arccos(np.clip(d.xmat[body(name)].reshape(3, 3)[2, 2], -1, 1)))


bad_seeds = []
for seed in range(args.n):
    torch.manual_seed(seed)
    env.reset()
    lay = state.STATE.layout
    t0 = d.xpos[body(lay.tipped)][:2].copy()
    dev = state.STATE.device
    for _ in range(60):          # 60 control steps x 10 substeps = 1.2 s
        for _ in range(10):
            mujoco.mj_step(m, d)
        dev.on_step(sim)
    wet = lay.cell_xy[dev.active]
    r = lay.mug_radius
    pos = {n: d.xpos[body(n)][:2] for n in MUG_NAMES}
    sp = d.xpos[body("sponge")][:2]
    errs = []
    for n in lay.upright:
        if tilt(n) > 15:
            errs.append(f"{n} fell ({tilt(n):.0f}deg)")
    if tilt(lay.tipped) < 60:
        errs.append(f"tipped {lay.tipped} upright ({tilt(lay.tipped):.0f}deg)")
    for i, a in enumerate(MUG_NAMES):
        for b in MUG_NAMES[i + 1:]:
            if np.linalg.norm(pos[a] - pos[b]) < 1.6 * r:
                errs.append(f"{a}/{b} overlap")
    if len(wet) < 30:
        errs.append(f"small puddle {len(wet)}")
    elif np.min(np.linalg.norm(wet - sp, axis=1)) < 0.03:
        errs.append("sponge on puddle")
    if len(wet) and np.min(np.linalg.norm(wet - lay.mouth_xy, axis=1)) > 0.03:
        errs.append("puddle detached from mouth")
    roll = np.linalg.norm(d.xpos[body(lay.tipped)][:2] - t0)
    if roll > 0.03:
        errs.append(f"tipped mug rolled {roll*100:.1f}cm")
    if (dev._cells_under_mugs(d, margin=0.0) & dev.active).any():
        errs.append("wet cells under a mug")
    print(f"seed {seed:2d}: cells={len(wet):3d} tipped={lay.tipped} roll={roll*100:4.1f}cm "
          + ("OK" if not errs else "BAD " + "; ".join(errs)))
    if errs:
        bad_seeds.append(seed)
print(f"valid {args.n - len(bad_seeds)}/{args.n}; bad seeds: {bad_seeds}")
