"""Step 3 check: move the TCP to a grid of targets and report tracking error."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from robobench.controller import Controller  # noqa: E402
from robobench.sim import Sim  # noqa: E402

sim = Sim()
ctl = Controller(sim)
targets = [
    (0.5, 0.0, 0.30), (0.35, -0.25, 0.10), (0.65, -0.25, 0.10),
    (0.65, 0.25, 0.10), (0.35, 0.25, 0.10), (0.5, 0.0, 0.02), (0.6, 0.1, 0.05),
]
errs = []
for t in targets:
    r = ctl.move_to(t, yaw=0.0)
    _, rot = ctl.tcp_pose()
    tilt = np.degrees(np.arccos(np.clip(-rot[2, 2], -1, 1)))
    errs.append(r.position_error_m)
    print(f"target={t}  err={r.position_error_m*1000:5.1f} mm  tilt={tilt:4.1f} deg  ik_ok={r.ik_converged}  coll={r.collisions}")
r = ctl.move_to((0.5, 0.0, 0.2), yaw=np.pi / 4)
print("yaw 45deg move err mm:", round(r.position_error_m * 1000, 1))
print("open width:", round(ctl.set_gripper(True), 4), " closed width:", round(ctl.set_gripper(False), 4))
print(f"max err = {max(errs)*1000:.1f} mm ->", "PASS" if max(errs) < 0.01 else "FAIL")
