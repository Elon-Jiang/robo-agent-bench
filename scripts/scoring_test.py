"""M4 check: 0-100 score and binary success on constructed final states."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import rab  # noqa: E402

rab.register()

import mujoco  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
from mujoco_playground.tasks.agentic.tabletop_v0 import embodiment as E  # noqa: E402
from mujoco_playground.tasks.agentic.tabletop_v0 import env as agentic_env  # noqa: E402

from rab import state  # noqa: E402
from rab.tasks.spill_wipe import check_spill  # noqa: E402

torch.manual_seed(3)
env = agentic_env.create_sim_env(task="spill_wipe", robot="so101")
emb = E.get("so101")
sim = env.device_layer.sim
m, d = sim.mj_model, sim.mj_data
fails = 0


def fresh():
    env.reset()
    dev = state.STATE.device
    for _ in range(60):
        for _ in range(10):
            mujoco.mj_step(m, d)
        dev.on_step(sim)
    return dev, state.STATE.layout


def evaluate(dev):
    dev.on_step(sim)
    feats = {k: v.clone() for k, v in dev.get_measurements(sim).items()}
    adr = m.body("sponge/sponge").id
    feats["sponge/xpos"] = torch.tensor(d.xpos[adr][None], dtype=torch.float32)
    # Gripper wide open and pinch far from the sponge -> sponge counts as released.
    from mujoco_playground.tasks.agentic.tabletop_v0 import mdp
    feats[mdp.EE_XPOS_KEY] = torch.tensor([[0.1, 0.0, 0.2]])
    feats["robot_state/joint_pos"] = torch.tensor([[0.0, 0.0, 0.0, 0.0, 0.0, 100.0]])
    ok = bool(check_spill(feats, gripper_range=emb.gripper_obs_range,
                          released_norm=emb.gripper_released_norm)[0])
    return float(feats["spill/score"][0, 0]), ok


def check(name, dev, exp_score, exp_ok):
    global fails
    score, ok = evaluate(dev)
    good = abs(score - exp_score) < 0.6 and ok == exp_ok
    fails += not good
    print(f"{name:32s} score={score:6.1f} (exp {exp_score:5.1f}) success={ok} (exp {exp_ok})",
          "OK" if good else "FAIL")


def tip(name):
    b = m.body(f"{name}/{name}")
    adr = m.joint(f"{name}/{name}_freejoint").qposadr[0]
    d.qpos[adr + 3:adr + 7] = [np.cos(np.pi / 4), np.sin(np.pi / 4), 0, 0]
    d.qpos[adr + 2] = 0.035
    mujoco.mj_forward(m, d)


dev, lay = fresh()
dev.sponge_lifted = True
dev.wiped |= dev.active
check("all wiped, sponge used", dev, 100.0, True)

dev, lay = fresh()
dev.sponge_lifted = True
idx = np.flatnonzero(dev.active)
dev.wiped[idx[: len(idx) // 2]] = True
frac = (len(idx) // 2) / len(idx)
check("half wiped", dev, 10 + 70 * frac + 20, False)

dev, lay = fresh()
dev.sponge_lifted = True
dev.wiped |= dev.active
tip(lay.upright[0])
check("all wiped, knocked 1 mug", dev, 10 + 70 + 10, False)

dev, lay = fresh()
dev.sponge_lifted = True
dev.wiped |= dev.active
dev.arm_table_events = 3
check("all wiped, 3 arm-table hits", dev, 100 - 6, True)

dev, lay = fresh()
check("nothing done", dev, 0.0, False)

print("RESULT:", "PASS" if fails == 0 else f"FAIL ({fails})")
