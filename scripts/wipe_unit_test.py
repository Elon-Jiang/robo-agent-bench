"""M2 check: SpillDevice wipe rule, independent of the robot.

Teleports the sponge (freejoint qpos) and calls the device's per-substep hook
directly. Checks:
  1. sponge hovering 1 cm above the puddle wipes nothing;
  2. sponge resting on the puddle wipes exactly the cells under its footprint;
  3. sliding the sponge across the puddle wipes a stripe and nothing outside it.
"""

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
from rab.tasks.spill_wipe import SPONGE_HALF  # noqa: E402

torch.manual_seed(0)
env = agentic_env.create_sim_env(task="spill_wipe", robot="so101")
env.reset()
sim = env.device_layer.sim
m, d = sim.mj_model, sim.mj_data
dev = state.STATE.device
lay = state.STATE.layout
adr = m.joint("sponge/sponge_freejoint").qposadr[0]
vadr = m.joint("sponge/sponge_freejoint").dofadr[0]
wet = lay.cell_xy[lay.active]
target = wet[len(wet) // 2]


def put_sponge(x, y, z, yaw=0.0, steps=30):
    for _ in range(steps):
        d.qpos[adr:adr + 3] = [x, y, z]
        d.qpos[adr + 3:adr + 7] = [np.cos(yaw / 2), 0, 0, np.sin(yaw / 2)]
        d.qvel[vadr:vadr + 6] = 0
        mujoco.mj_step(m, d)
        dev.on_substep(sim)


def footprint(x, y, yaw, cells):
    c, s = np.cos(yaw), np.sin(yaw)
    rx, ry = cells[:, 0] - x, cells[:, 1] - y
    u, v = c * rx + s * ry, -s * rx + c * ry
    return (np.abs(u) < SPONGE_HALF[0]) & (np.abs(v) < SPONGE_HALF[1])


fails = 0
# 1. hover
put_sponge(*target, SPONGE_HALF[2] + 0.01)
n1 = int(dev.wiped.sum())
print(f"[1] hover 1 cm: wiped={n1} (expect 0)", "OK" if n1 == 0 else "FAIL")
fails += n1 != 0

# 2. rest on the puddle (slightly pressed)
yaw = 0.4
put_sponge(*target, SPONGE_HALF[2] - 0.001, yaw)
expect = footprint(*target, yaw, lay.cell_xy) & lay.active
got = dev.wiped & lay.active
extra, missed = int((got & ~expect).sum()), int((expect & ~got).sum())
print(f"[2] rest: wiped={int(got.sum())} expected={int(expect.sum())} extra={extra} missed={missed}",
      "OK" if extra == 0 and missed <= 1 else "FAIL")
fails += not (extra == 0 and missed <= 1)

# 3. slide across along x
y = target[1]
xs = np.linspace(wet[:, 0].min() - 0.03, wet[:, 0].max() + 0.03, 60)
union = np.zeros_like(lay.active)
for x in xs:
    put_sponge(x, y, SPONGE_HALF[2] - 0.001, 0.0, steps=3)
    union |= footprint(x, y, 0.0, lay.cell_xy)
expect = (union | expect) & lay.active
got = dev.wiped & lay.active
extra, missed = int((got & ~expect).sum()), int((expect & ~got).sum())
print(f"[3] slide: wiped={int(got.sum())}/{int(lay.active.sum())} expected={int(expect.sum())} "
      f"extra={extra} missed={missed}", "OK" if extra == 0 and missed <= 2 else "FAIL")
fails += not (extra == 0 and missed <= 2)

# Hidden cells really have alpha 0, remaining wet cells are visible.
alpha = m.geom_rgba[dev._cell_ids, 3]
vis_ok = np.all(alpha[got] == 0) and np.all(alpha[lay.active & ~dev.wiped] > 0)
print("[4] rgba: wiped hidden & remaining visible", "OK" if vis_ok else "FAIL")
fails += not vis_ok
dev.on_step(sim)
print("metrics:", {k: round(float(v), 3) for k, v in state.STATE.pending.items() if isinstance(v, (int, float))})
print("RESULT:", "PASS" if fails == 0 else f"FAIL ({fails})")
