"""Privileged YAM feasibility oracle using unmodified upstream PlanExecutor.

Only reset initializes object poses. Actions are ordinary grasp/goto/place/home
primitives; no object teleport, weld, force assist, or scoring-based retries.
"""

import argparse
import json
import os
import numpy as np
import torch

from pilot_test_utils import ROOT, create, evaluate
from mujoco_playground.tasks.agentic.tabletop_v0.agent import PlanExecutor


class Oracle:
    def __init__(self, env, seed):
        self.env = env
        if "cup" not in env.device_layer.get_device("pilot").objects:
            os.environ["RAB_SEED"] = str(seed)
        torch.manual_seed(seed)
        np.random.seed(seed)
        self.ts = env.reset()
        self.sim = env.device_layer.sim
        self.m, self.d = self.sim.mj_model, self.sim.mj_data
        self.s = env.device_layer.get_device("pilot")
        self.ex = PlanExecutor("yam")
        self.ex.sync(self.ts.observation)
        self.trace = []

    def run(self, stage, plan):
        self.ex.load(plan, self.ts.observation)
        steps = 0
        frames = []
        while self.ex.busy:
            self.ts = self.env.step(
                {
                    "action": torch.tensor(
                        self.ex.step(self.ts.observation), dtype=torch.float32
                    )[None]
                }
            )
            steps += 1
            if steps % 5 == 0:
                n = stage.split(":")[0]
                if n in self.s.bodies:
                    b = self.s.bodies[n]
                    frames.append(
                        dict(
                            xyz=self.d.xpos[b].tolist(),
                            ee=self.ee().tolist(),
                            gripper=float(
                                np.asarray(
                                    self.ts.observation["gripper_state"]
                                ).reshape(-1)[0]
                            ),
                        )
                    )
            if steps > 2500 or self.ts.last():
                raise RuntimeError(f"{stage} exceeded episode/primitive budget")
        rec = evaluate(self.env)
        self.trace.append(
            dict(
                stage=stage,
                plan=plan,
                steps=steps,
                status=self.ex.status,
                frames=frames,
                poses={
                    n: dict(pos=self.d.xpos[b].tolist(), quat=self.d.xquat[b].tolist())
                    for n, b in self.s.bodies.items()
                },
                record=rec,
            )
        )
        return self.ex.status

    def ee(self):
        return np.asarray(self.ts.observation["ee_pos"]).reshape(-1).copy()

    def grasp(self, name):
        self.s._prepare(self.d)
        p, r = self.s.pos[name].copy(), self.s.rot[name]
        yaw = np.arctan2(r[1, 0], r[0, 0])
        if name == "marker":
            yaw = np.arctan2(r[1, 2], r[0, 2])
        elif name == "scissors":
            yaw += np.pi / 2
            p[:2] += r[:2, :2] @ [0, 0.035]
        elif name == "screwdriver":
            p[:2] += r[:2, :2] @ [0.045, 0]
            p[2] = 0.017
        p[2] = max(0.010, p[2])
        azimuth = np.arctan2(p[1], p[0]) if "tray" in self.s.objects or "organizer" in self.s.objects else yaw
        common = dict(yaw=float(yaw), pitch_deg=90, azimuth=float(azimuth), speed=0.04)
        status = self.run(
            name + ":grasp",
            [
                dict(
                    op="grasp",
                    x=float(p[0]),
                    y=float(p[1]),
                    z=float(p[2]),
                    hover=(0.08 if "tray" in self.s.objects else 0.12),
                    verify_grasp=True,
                    **common,
                )
            ],
        )
        return common, status

    def cup(self):
        self.run("home", [dict(op="home")])
        for name, offset in (
            ("marker", [-0.017, 0]),
            ("scissors", [0.012, -0.012]),
            ("screwdriver", [0.012, 0.014]),
        ):
            common, status = self.grasp(name)
            if status != "done":
                self.run(
                    name + ":abort", [dict(op="gripper", gripper=1), dict(op="home")]
                )
                continue
            grip = 0.40 if name == "scissors" else 0.0
            p = self.ee()
            p[2] = 0.25
            self.run(
                name + ":lift",
                [
                    dict(
                        op="goto",
                        x=float(p[0]),
                        y=float(p[1]),
                        z=float(p[2]),
                        gripper=grip,
                        **common,
                    )
                ],
            )
            common["pitch_deg"] = 0
            self.run(
                name + ":vertical",
                [
                    dict(
                        op="goto",
                        x=float(p[0]),
                        y=float(p[1]),
                        z=float(p[2]),
                        gripper=grip,
                        **common,
                    )
                ],
            )
            self.s._prepare(self.d)
            cup = self.s.pos["cup"].copy()
            p = self.ee()
            obj = self.s.pos[name]
            delta = cup[:2] + offset - obj[:2]
            p[:2] += delta
            p[2] += (
                self.s.points["cup"][:, 2].max()
                + 0.008
                - self.s.points[name][:, 2].min()
            )
            self.run(
                name + ":insert",
                [
                    dict(
                        op="goto",
                        x=float(p[0]),
                        y=float(p[1]),
                        z=float(p[2]),
                        gripper=grip,
                        **common,
                    ),
                    dict(op="gripper", gripper=1),
                    dict(op="move_by", dz=0.08),
                    dict(op="home"),
                ],
            )
        # A real insertion attempt, with the marker constrained only by the cup.
        common, status = self.grasp("cap")
        if status == "done":
            self.s._prepare(self.d)
            target = self.s.pos["marker"] + self.s.rot["marker"][:, 2] * 0.057
            p = target - (self.s.pos["cap"] - self.ee())
            self.run(
                "cap:insert",
                [
                    dict(
                        op="place",
                        x=float(p[0]),
                        y=float(p[1]),
                        z=float(max(0.01, p[2])),
                        clearance=0.001,
                        **common,
                    ),
                    dict(op="home"),
                ],
            )
        else:
            self.run("cap:abort", [dict(op="gripper", gripper=1), dict(op="home")])

    def tray(self):
        self.run("home", [dict(op="home")])
        for name, offset in (
            ("cap", [0.075, 0.070]),
            ("scissors", [-0.045, 0]),
            ("marker", [0.020, 0.045]),
        ):
            common, status = self.grasp(name)
            if status != "done":
                self.run(
                    name + ":abort", [dict(op="gripper", gripper=1), dict(op="home")]
                )
                continue
            grip = 0.40 if name == "scissors" else 0.0
            if name == "scissors":
                p = self.ee()
                p[2] = 0.12
                self.run(
                    name + ":lift",
                    [
                        dict(
                            op="goto",
                            x=float(p[0]),
                            y=float(p[1]),
                            z=float(p[2]),
                            gripper=grip,
                            **common,
                        )
                    ],
                )
            self.s._prepare(self.d)
            target = self.s.pos["tray"][:2] + offset
            p = self.ee()
            p[:2] += target - self.s.geometric_center(name)[:2]
            p[2] = max(p[2], 0.12)
            self.run(
                name + ":carry",
                [
                    dict(
                        op="goto",
                        x=float(p[0]),
                        y=float(p[1]),
                        z=float(p[2]),
                        gripper=grip,
                        **common,
                    )
                ],
            )
            self.s._prepare(self.d)
            p = self.ee()
            p[:2] += target - self.s.geometric_center(name)[:2]
            # Tray floor is 10 mm above the table, including rubber feet.
            p[2] += 0.011 - self.s.points[name][:, 2].min()
            self.run(
                name + ":place",
                [
                    dict(
                        op="place",
                        x=float(p[0]),
                        y=float(p[1]),
                        z=float(max(0.01, p[2])),
                        clearance=0.010,
                        **common,
                    ),
                    dict(op="home"),
                ],
            )
        # Let the released objects settle using the same controller.
        self.run(
            "settle",
            [dict(op="goto", x=0.20, y=0.0, z=0.30, speed=0.02), dict(op="home")],
        )


def legacy_cup_scene():
    """Rebuild the recorded round-2 scene without editing upstream or assets."""
    from dataclasses import replace
    from rab.tasks.pilots import SCENES, Scene, CUP, MARKER, CAP, SCISSORS, SCREWDRIVER, LAPTOP, task_for
    from rab.pilot_physics import household
    from mujoco_playground.tasks.agentic.tabletop_v0 import task as T

    old = Scene("pilot_tidy_desk", "Please put away the stationery on my desk.",
                (MARKER, CAP, SCISSORS, SCREWDRIVER, LAPTOP),
                dict(marker=(0.21, 0.015), cap=(0.20, 0.075), scissors=(0.31, -0.07),
                     screwdriver=(0.27, -0.17), tray=(0.37, 0.13), laptop=(0.40, -0.16)), {})
    positions = dict(old.positions)
    positions.pop("tray")
    positions["cup"] = (0.37, 0.12)
    positions.update(
        marker=(0.23, 0.02),
        cap=(0.23, 0.075),
        scissors=(0.23, -0.12),
        screwdriver=(0.35, -0.05),
    )
    SCENES[old.name] = replace(
        old,
        objects=tuple(household(o) for o in (*old.objects, CUP)),
        positions=positions,
        containers=("cup",),
        weights=dict(
            cap_on_marker=25,
            marker_in_cup=25,
            scissors_in_cup=20,
            screwdriver_in_cup=20,
            laptop_untouched=10,
        ),
    )
    T.TASKS[old.name] = task_for(old.name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", nargs="+", type=int, default=list(range(5)))
    ap.add_argument("--output", default="outputs/suite_v0.2/tidy_oracle_results.json")
    ap.add_argument(
        "--legacy-cup",
        action="store_true",
        help="Attempt the old cup and capping task for comparison",
    )
    args = ap.parse_args()
    if args.legacy_cup:
        legacy_cup_scene()
    env = create("pilot_tidy_desk")
    rows = []
    out = ROOT / args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    for seed in args.seeds:
        if args.legacy_cup:
            oracle = Oracle(env, seed)
            oracle.cup()
        else:
            from oracle_suite import Oracle as SuiteOracle
            oracle = SuiteOracle(env, seed)
            oracle.solve("pilot_tidy_desk")
        row = dict(seed=seed, **evaluate(env))
        rows.append(row)
        (out.parent / (out.stem + f"_s{seed}_trace.json")).write_text(
            json.dumps(oracle.trace, indent=2) + "\n"
        )
        print(json.dumps(row), flush=True)
        out.write_text(json.dumps(rows, indent=2) + "\n")
    env.close()
    passed = sum(r["success_strict"] for r in rows)
    mean = np.mean([r["score_0_100"] for r in rows])
    print(f"strict={passed}/{len(rows)} mean={mean:.1f}", flush=True)
    return 0 if passed / len(rows) >= 0.8 and mean >= 85 else 1


if __name__ == "__main__":
    raise SystemExit(main())
