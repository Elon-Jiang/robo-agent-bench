"""Fast offline check: how much of the spill would the oracle's strokes cover?

Builds layouts, asks the oracle for a plan, and sweeps the sponge footprint
along every wipe stroke geometrically (no robot physics). Use it to tune the
oracle / puddle generator before running full physics episodes.
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
from mujoco_playground.tasks.agentic.tabletop_v0 import env as E  # noqa: E402

from rab import state  # noqa: E402
from rab.tasks import spill_wipe as S  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--n", type=int, default=10)
args = ap.parse_args()

env = E.create_sim_env(task="spill_wipe", robot="so101")
sim = env.device_layer.sim
m, d = sim.mj_model, sim.mj_data
covs = []
for seed in range(args.n):
    torch.manual_seed(seed)
    env.reset()
    dev = state.STATE.device
    for _ in range(60):
        for _ in range(10):
            mujoco.mj_step(m, d)
        dev.on_step(sim)
    poses = {}
    for o in S.OBJECTS:
        b = m.body(f"{o.name}/{o.name}").id
        q = d.xquat[b]
        poses[o.name] = np.array([*d.xpos[b], np.arctan2(2 * (q[0] * q[3] + q[1] * q[2]),
                                                          1 - 2 * (q[2] ** 2 + q[3] ** 2))])
    plan = S.oracle(poses)
    strokes = [(a, b) for a, b in zip(plan, plan[1:])
               if a["op"] == b["op"] == "goto" and a["z"] == b["z"] == S.WIPE_Z]
    cov = np.zeros(len(dev.cell_xy), bool)
    for a, b in strokes:
        yaw = a.get("yaw", 0.0)
        c, s = np.cos(yaw), np.sin(yaw)
        for t in np.linspace(0, 1, 60):
            x, y = a["x"] + t * (b["x"] - a["x"]), a["y"] + t * (b["y"] - a["y"])
            rx, ry = dev.cell_xy[:, 0] - x, dev.cell_xy[:, 1] - y
            cov |= (np.abs(c * rx + s * ry) < S.SPONGE_HALF[0]) & (np.abs(-s * rx + c * ry) < S.SPONGE_HALF[1])
    act = dev.active
    frac = (cov & act).sum() / act.sum()
    covs.append(frac)
    print(f"seed {seed:2d}: strokes={len(strokes):2d} wet={act.sum():3d} predicted_coverage={frac:.2f}")
print(f"mean={np.mean(covs):.2f} min={np.min(covs):.2f} >=0.95: {sum(c >= 0.95 for c in covs)}/{len(covs)}")
