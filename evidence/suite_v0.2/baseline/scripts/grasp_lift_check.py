"""YAM physical grasp/lift/carry/place checks using the unmodified PlanExecutor.

Isolated-object feasibility test, not a five-task oracle. Seeds jitter XY/yaw.
No welds, pose changes after reset, force assist, or grader-based plan adaptation.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mujoco
import numpy as np
import torch
import rab

rab.register()
from rab.tasks.pilots import SCENES
from rab.pilot_assets import vertices
from rab.pilot_physics import kind, configure_grip_contacts
from rab.tasks.spill_wipe import _qz, _qy, _quat_mul
from mujoco_playground.tasks.agentic.tabletop_v0 import (
    task as T,
    reset as R,
    agent as A,
)
from reaf.simulation.torch import flat_scene as config
from reaf.simulation.torch._src.builders.flat_scene import build

OBJECTS = {}
for scene in SCENES.values():
    for obj in scene.objects:
        OBJECTS.setdefault(kind(obj.name), obj)
OBJECTS["mug"] = next(o for o in SCENES["pilot_breakfast"].objects if o.name == "mug")
NAMES = (
    "mug",
    "plate",
    "bowl",
    "banana",
    "phone",
    "scissors",
    "screwdriver",
    "marker",
    "cap",
    "block",
    "sponge",
    "keys",
    "napkin",
)


class Reset(R.ScatterObjectsResetter):
    seed = 0

    def __init__(self, *, obj):
        super().__init__(objects=(), robot="yam")
        self.obj = obj

    def on_reset(self, cfg, env_mask):
        super().on_reset(cfg, env_mask)
        rng = np.random.default_rng(self.seed)
        xy = np.array([0.29, -0.05]) + rng.uniform(-0.008, 0.008, 2)
        yaw = rng.uniform(-0.12, 0.12)
        quat = _qz(yaw)
        if self.obj.name == "marker":
            quat = _quat_mul(quat, _qy(np.pi / 2))
        rot = np.empty(9)
        mujoco.mju_quat2Mat(rot, quat)
        bottom = np.einsum("ij,kj->ki", rot.reshape(3, 3), vertices(self.obj))[
            :, 2
        ].min()
        dev = self._env.device_layer.get_device(self.obj.name)
        tensor = lambda v: torch.tensor([np.asarray(v).tolist()], dtype=torch.float32)
        dev.set_pos(env_mask, tensor([*xy, -bottom + 0.0005]))
        dev.set_quat(env_mask, tensor(quat))
        dev.set_vel(env_mask, tensor([0.0] * 6))


def create(obj):
    name = "_rab_grasp_check"
    T.TASKS[name] = T.Task(
        name,
        "Grasp feasibility",
        (obj,),
        lambda f, **kw: torch.zeros(1, dtype=torch.bool),
        {},
        lambda p: [],
    )
    cfg = T.env_config(task=name, robot="yam")
    del cfg.device_layer.devices["wrist_cam"]
    del cfg.observation_adapter["wrist_rgb"]
    cfg.base_scene.geom("table/table_collision").friction = [0.6, 0.005, 0.0001]
    cfg.resetters["scatter"] = config.Component(Reset, obj=obj)
    cfg.episode_length_secs = 180
    env = build.build_env(cfg)
    configure_grip_contacts(env.device_layer.sim.mj_model)
    return env


def trial(env, obj, seed, *, trace=False):
    torch.manual_seed(seed)
    Reset.seed = seed
    ts = env.reset()
    ex = A.PlanExecutor("yam")
    ex.sync(ts.observation)
    sim = env.device_layer.sim
    m, d = sim.mj_model, sim.mj_data
    b = m.body(f"{obj.name}/{obj.name}").id
    trajectory = []
    plans = []
    robot_bodies = {i for i in range(m.nbody) if m.body(i).name.startswith("robot/")}

    def held():
        for c in d.contact:
            a, other = int(m.geom_bodyid[c.geom1]), int(m.geom_bodyid[c.geom2])
            if c.dist <= 0.001 and (
                (a == b and other in robot_bodies) or (other == b and a in robot_bodies)
            ):
                return True
        return False

    def run(plan, stage):
        nonlocal ts
        plans.append(dict(stage=stage, primitives=plan))
        ex.load(plan, ts.observation)
        steps = 0
        while ex.busy and steps < 1500:
            action = torch.tensor(ex.step(ts.observation), dtype=torch.float32)[None]
            ts = env.step({"action": action})
            trajectory.append(
                dict(
                    stage=stage,
                    xyz=d.xpos[b].tolist(),
                    ee=np.asarray(ts.observation["ee_pos"]).reshape(-1).tolist(),
                    status=ex.status,
                    segment=ex._segments[0].label if ex._segments else "",
                    robot_contact=held(),
                    gripper=float(
                        np.asarray(ts.observation["gripper_state"]).reshape(-1)[0]
                    ),
                )
            )
            steps += 1
        if ex.busy:
            raise RuntimeError("PlanExecutor exceeded step budget")
        return ex.status

    run([dict(op="home")], "settle")
    initial = d.xpos[b].copy()
    rotation = d.xmat[b].reshape(3, 3).copy()
    yaw = np.arctan2(rotation[1, 0], rotation[0, 0])
    if kind(obj.name) == "marker":
        yaw = np.arctan2(rotation[1, 2], rotation[0, 2])
    elif obj.half[1] > obj.half[0]:
        yaw += np.pi / 2
    grasp = initial.copy()
    grasp[2] = max(grasp[2], 0.010)
    if kind(obj.name) == "banana":
        grasp[:2] += rotation[:2, :2] @ obj.grasp_offset
    if kind(obj.name) == "plate":
        # Pinch near rim: jaws close across a short edge chord, not diameter.
        grasp[:2] += rotation[:2, :2] @ np.array([-0.037, 0.0])
        yaw = np.arctan2(rotation[1, 0], rotation[0, 0])
    if kind(obj.name) == "screwdriver":
        grasp[:2] += rotation[:2, :2] @ np.array([0.045, 0.0])
        grasp[2] = 0.017
    if kind(obj.name) == "scissors":
        grasp[:2] += rotation[:2, :2] @ np.array([0.0, 0.035])
    if kind(obj.name) == "keys":
        grasp[:2] += rotation[:2, :2] @ np.array([-0.012, 0.0])
    common = dict(
        yaw=float(yaw),
        pitch_deg=90,
        speed=0.06,
        azimuth=float(np.arctan2(grasp[1], grasp[0])),
    )
    if kind(obj.name) == "keys":
        common["azimuth"] = float(yaw)
        common["pitch_deg"] = 85
    # Reduce squeeze on the thin scissors handle after the verified grasp;
    # this is an ordinary gripper command, not an actuator/force-limit change.
    holding_gripper = 0.40 if kind(obj.name) == "scissors" else 0.0
    statuses = {}
    statuses["grasp"] = run(
        [
            dict(
                op="grasp",
                x=float(grasp[0]),
                y=float(grasp[1]),
                z=float(grasp[2]),
                hover=(0.105 if kind(obj.name) == "plate" else 0.08),
                verify_grasp=True,
                **common,
            )
        ],
        "grasp",
    )
    # Hold a measured pose instead of lowering to a nominal hover after IK's
    # settling tolerance. Oracle pose feedback targets 10 cm clearance so tracking stays above 8 cm.
    ee = np.asarray(ts.observation["ee_pos"]).reshape(-1).copy()
    clearance_height = 0.09 if kind(obj.name) == "scissors" else 0.10
    ee[2] += max(0.0, clearance_height - (d.xpos[b, 2] - initial[2]))
    if kind(obj.name) != "plate":
        statuses["lift"] = run(
            [
                dict(
                    op="goto",
                    x=float(ee[0]),
                    y=float(ee[1]),
                    z=float(ee[2]),
                    gripper=holding_gripper,
                    **common,
                )
            ],
            "lift",
        )
    lifted = d.xpos[b].copy()
    grasp_failed = statuses["grasp"] != "done" or not held()
    ee = np.asarray(ts.observation["ee_pos"]).reshape(-1).copy()
    delta = np.array([0.0, 0.10, 0.0])
    statuses["carry"] = run(
        [
            dict(
                op="goto",
                x=float(ee[0] + delta[0]),
                y=float(ee[1] + delta[1]),
                z=float(ee[2]),
                gripper=holding_gripper,
                **common,
            )
        ],
        "carry",
    )
    carried = d.xpos[b].copy()
    ee = np.asarray(ts.observation["ee_pos"]).reshape(-1).copy()
    offset = carried - ee
    target = initial + delta - offset
    points = np.einsum("ij,kj->ki", d.xmat[b].reshape(3, 3), vertices(obj)) + carried
    target[2] = max(0.010, ee[2] - points[:, 2].min() + 0.001)
    statuses["place"] = run(
        [
            dict(
                op="place",
                x=float(target[0]),
                y=float(target[1]),
                z=float(target[2]),
                clearance=(0.025 if kind(obj.name) == "keys" else 0.010),
                **common,
            ),
            dict(op="home"),
        ],
        "place",
    )
    # Hold the home target for a further second to measure released stability.
    for _ in range(50):
        ts = env.step(
            {"action": torch.tensor(ex.step(ts.observation), dtype=torch.float32)[None]}
        )
    final = d.xpos[b].copy()
    table = m.geom_bodyid[m.geom("table/table_collision").id]
    supported = False
    robot_contact = False
    for c in d.contact:
        pair = {int(m.geom_bodyid[c.geom1]), int(m.geom_bodyid[c.geom2])}
        if b in pair and c.dist <= 0.001:
            supported |= table in pair
            robot_contact |= any(m.body(x).name.startswith("robot/") for x in pair)
    lift = float(lifted[2] - initial[2])
    carry = float(np.linalg.norm((carried - lifted)[:2]))
    carry_frames = [x for x in trajectory if x["stage"] == "carry"]
    lost, longest_loss = 0, 0
    for frame in trajectory:
        monitor = frame["stage"] == "carry" or (
            frame["stage"] == "place"
            and frame["segment"]
            in ("place:rise", "place:transit", "place:descend", "goto")
        )
        lost = lost + 1 if monitor and not frame["robot_contact"] else 0
        longest_loss = max(longest_loss, lost)
    min_height = min(x["xyz"][2] for x in carry_frames) - initial[2]
    slip = not grasp_failed and (longest_loss >= 3 or min_height < 0.08)
    carry_error = float(np.linalg.norm((carried - lifted - delta)[:2]))
    error = float(np.linalg.norm(final[:2] - (initial + delta)[:2]))
    j = m.joint(f"{obj.name}/{obj.name}_freejoint")
    vel = d.qvel[j.dofadr[0] : j.dofadr[0] + 6]
    settled = np.linalg.norm(vel[:3]) < 0.025 and np.linalg.norm(vel[3:]) < 0.3
    # At least 8 cm actual lift, 10 cm carry within 1.5 cm, released placement
    # within 2.5 cm. Contact monitoring prevents ballistic drops counting as carry.
    passed = (
        all(v == "done" for v in statuses.values())
        and lift >= 0.08
        and carry_error < 0.015
        and not grasp_failed
        and not slip
        and error < 0.025
        and supported
        and not robot_contact
        and settled
    )
    row = dict(
        object=kind(obj.name),
        seed=seed,
        mass_g=round(float(m.body_mass[b]) * 1000, 2),
        success=bool(passed),
        slip=bool(slip),
        grasp_failed=bool(grasp_failed),
        lift_cm=round(lift * 100, 2),
        carry_cm=round(carry * 100, 2),
        place_error_cm=round(error * 100, 2),
        min_carry_height_cm=round(min_height * 100, 2),
        carry_error_cm=round(carry_error * 100, 2),
        pre_release_contact_loss_ticks=longest_loss,
        supported=bool(supported),
        released=not robot_contact,
        settled=bool(settled),
        statuses=statuses,
    )
    if trace:
        path = ROOT / f"outputs/pilots/grasp_{kind(obj.name)}_{seed}_trace.json"
        path.write_text(json.dumps(dict(plans=plans, frames=trajectory)))
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--objects", nargs="+", choices=NAMES, default=NAMES)
    ap.add_argument("--seeds", nargs="+", type=int, default=[0, 1, 2])
    ap.add_argument("--trace", action="store_true")
    ap.add_argument("--output", default="outputs/pilots/grasp_lift_results.json")
    args = ap.parse_args()
    rows = []
    for name in args.objects:
        obj = OBJECTS[name]
        env = create(obj)
        for seed in args.seeds:
            row = trial(env, obj, seed, trace=args.trace)
            rows.append(row)
            print(json.dumps(row), flush=True)
        env.close()
    path = ROOT / args.output
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2) + "\n")
    print("| Object | Mass g | Success | Slip |")
    print("|---|---:|---:|---:|")
    for name in args.objects:
        r = [x for x in rows if x["object"] == name]
        print(
            f"| {name} | {r[0]['mass_g']:g} | {sum(x['success'] for x in r)}/{len(r)} | {sum(x['slip'] for x in r)}/{len(r)} |"
        )
    return 0 if all(r["success"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
