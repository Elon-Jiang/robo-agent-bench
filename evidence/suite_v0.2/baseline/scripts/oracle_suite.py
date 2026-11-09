"""Privileged pose-feedback oracles; only upstream PlanExecutor moves the robot.

No scoring-based retries, teleports, welds, or force changes. Each scene has a
fixed action schedule; measured object/EE poses compensate grasp offsets.
"""

import argparse
import json
import os
import time
import numpy as np
from oracle_tidy_desk import Oracle as BaseOracle
from pilot_test_utils import ROOT, create, evaluate
from rab.tasks.pilots import SCENES
from rab.pilot_physics import kind


class Oracle(BaseOracle):
    def __init__(self, env, seed):
        os.environ["RAB_SEED"] = str(seed)
        super().__init__(env, seed)

    def grasp(self, name):
        if name in ("marker", "cap", "scissors", "screwdriver"):
            return super().grasp(name)
        self.s._prepare(self.d)
        p, r = self.s.pos[name].copy(), self.s.rot[name]
        yaw = np.arctan2(r[1, 0], r[0, 0])
        obj = self.s.objects[name]
        if obj.half[1] > obj.half[0]:
            yaw += np.pi / 2
        if kind(name) == "banana":
            p[:2] += r[:2, :2] @ obj.grasp_offset
        if kind(name) == "plate":
            p[:2] = self.s.geometric_center(name)[:2] + [-0.037, 0]
            yaw = 0.0
        common = dict(
            yaw=float(yaw),
            pitch_deg=90,
            azimuth=float(np.arctan2(p[1], p[0])),
            speed=0.06,
        )
        status = self.run(
            name + ":grasp",
            [
                dict(
                    op="grasp",
                    x=float(p[0]),
                    y=float(p[1]),
                    z=float(max(0.010, p[2])),
                    hover=0.12,
                    verify_grasp=True,
                    **common,
                )
            ],
        )
        return common, status

    def goto(self, name, p, common, **kw):
        return self.run(
            name,
            [
                dict(
                    op="goto",
                    x=float(p[0]),
                    y=float(p[1]),
                    z=float(p[2]),
                    **(common | kw),
                )
            ],
        )

    def transfer(self, name, xy, floor, *, drop=False):
        common, status = self.grasp(name)
        if status != "done":
            self.run(name + ":abort", [dict(op="gripper", gripper=1), dict(op="home")])
            return
        grip = 0.4 if name == "scissors" else 0.0
        self.s._prepare(self.d)
        p = self.ee()
        p[2] += max(0, 0.18 - self.s.points[name][:, 2].min())
        if name != "plate":
            self.goto(name + ":lift", p, common, gripper=grip)
        # Two pose-feedback transits correct the actual held offset without
        # querying the grader or changing any object state.
        for i in range(2):
            self.s._prepare(self.d)
            p = self.ee()
            p[:2] += np.asarray(xy) - self.s.geometric_center(name)[:2]
            self.goto(name + f":carry{i}", p, common, gripper=grip)
        self.s._prepare(self.d)
        p = self.ee()
        p[:2] += np.asarray(xy) - self.s.geometric_center(name)[:2]
        p[2] += floor + 0.002 - self.s.points[name][:, 2].min()
        if name == "plate" and self.s.scene.name == "pilot_clear_dishes":
            self.goto(name + ":lower", p, common, gripper=0.0)
            self.run(
                name + ":release",
                [
                    dict(op="gripper", gripper=1.0),
                    dict(op="move_by", dz=0.08),
                    dict(op="home"),
                ],
            )
        else:
            self.run(
                name + ":place",
                [
                    dict(
                        op="place",
                        x=float(p[0]),
                        y=float(p[1]),
                        z=float(max(0.010, p[2])),
                        clearance=0.002,
                        opening=0.45 if drop else 1.0,
                        **common,
                    ),
                    dict(op="home"),
                ],
            )

    def napkins(self):
        for name, dx in (("napkin_1", -0.025), ("napkin_2", 0.025)):
            self.s._prepare(self.d)
            floor = self.d.geom_xpos[self.m.geom("bin/bin_floor").id].copy()
            rim = self.s.points["bin"][:, 2].max()
            self.transfer(name, floor[:2] + [0, dx], rim + 0.003, drop=True)

    def toys(self):
        offsets = [
            (-0.04, -0.016),
            (0, -0.016),
            (0.04, -0.016),
            (-0.02, 0.016),
            (0.02, 0.016),
        ]
        for i, offset in enumerate(offsets, 1):
            self.s._prepare(self.d)
            xy = self.s.pos["wagon"][:2] + offset
            self.transfer(
                f"block_{i}",
                xy,
                float(self.s.points["wagon"][:, 2].max()) + 0.005,
                drop=True,
            )

    def breakfast(self):
        self.s._prepare(self.d)
        center, _, right = self.s.diner_frame()
        # Move mug first to clear the plate's transit/placement area.
        self.transfer("mug", (center + 0.12 * right)[:2], 0.0)
        self.transfer("plate", center[:2], 0.002)
        self.s._prepare(self.d)
        self.transfer(
            "banana",
            self.s.geometric_center("plate")[:2],
            self.s.points["plate"][:, 2].max(),
        )

    def dishes(self):
        for name in ("plate", "bowl", "mug"):
            self.s._prepare(self.d)
            xy = self.s.geometric_center("rack")[:2]
            # Stack released dishes in the physical rack; support is measured.
            floor = self.s.points["rack"][:, 2].max()
            if name == "bowl":
                floor = self.s.points["plate"][:, 2].max()
            if name == "mug":
                floor = self.s.points["bowl"][:, 2].max()
            self.transfer(name, xy, float(floor), drop=False)

    def spill(self):
        self.s._prepare(self.d)
        p = self.s.pos["mug_tipped"].copy()
        self.run(
            "mug_tipped:grasp",
            [
                dict(
                    op="grasp",
                    x=float(p[0]),
                    y=float(p[1]),
                    z=float(p[2]),
                    yaw_deg=90,
                    verify_grasp=True,
                )
            ],
        )
        common = dict(pitch_deg=0, azimuth_deg=90, roll_deg=90, speed=0.06)
        self.goto("mug_tipped:right", [0.35, 0.015, 0.15], common, gripper=0.0)
        self.s._prepare(self.d)
        p = self.ee()
        p[2] += 0.002 - self.s.points["mug_tipped"][:, 2].min()
        self.goto("mug_tipped:lower", p, common, gripper=0.0)
        self.run(
            "mug_tipped:release", [dict(op="gripper", gripper=1.0), dict(op="home")]
        )
        common, status = self.grasp("sponge")
        if status == "done":
            cells = self.s.spill.cell_xy[self.s.spill.active]
            lo, hi = cells.min(0), cells.max(0)
            common = dict(yaw=0.0, pitch_deg=90, speed=0.035)
            # Fixed raster covering the initial liquid footprint; no wiped-bit feedback.
            for i, x in enumerate(np.linspace(lo[0], hi[0], 4)):
                ends = (lo[1], hi[1]) if i % 2 == 0 else (hi[1], lo[1])
                if i == 0:
                    self.goto(
                        "sponge:approach", [x, ends[0], 0.12], common, gripper=0.0
                    )
                for y in ends:
                    self.goto("sponge:wipe", [x, y, 0.011], common, gripper=0.0)
            self.goto("sponge:lift", [0.28, 0.10, 0.14], common, gripper=0.0)
            self.run(
                "sponge:park",
                [
                    dict(
                        op="place", x=0.20, y=-0.12, z=0.013, clearance=0.002, **common
                    ),
                    dict(op="home"),
                ],
            )
        self.napkins()

    def solve(self, task, napkins_only=False):
        self.run("home", [dict(op="home")])
        if napkins_only:
            self.napkins()
        elif task == "pilot_tidy_desk":
            self.tray()
        elif task == "pilot_toys":
            self.toys()
        elif task == "pilot_breakfast":
            self.breakfast()
        elif task == "pilot_clear_dishes":
            self.dishes()
        else:
            self.spill()
        self.run(
            "settle",
            [dict(op="goto", x=0.20, y=0.0, z=0.30, speed=0.02), dict(op="home")],
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", nargs="+", choices=SCENES, default=list(SCENES))
    ap.add_argument("--seeds", nargs="+", type=int, default=list(range(20)))
    ap.add_argument("--napkins-only", action="store_true")
    ap.add_argument("--output", default="outputs/suite_v0.1/oracles.json")
    a = ap.parse_args()
    out = ROOT / a.output
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for task in a.tasks:
        env = create(task)
        for seed in a.seeds:
            start = time.monotonic()
            oracle = Oracle(env, seed)
            error = None
            try:
                oracle.solve(task, a.napkins_only)
            except RuntimeError as exc:
                error = str(exc)
            row = dict(
                evaluate(env),
                seed=seed,
                wall_s=time.monotonic() - start,
                turns_used=len(oracle.trace),
                error=error,
            )
            rows.append(row)
            trace = out.parent / (out.stem + f"_{task}_s{seed}_trace.json")
            trace.write_text(json.dumps(oracle.trace) + "\n")
            print(json.dumps(row), flush=True)
            out.write_text(json.dumps(rows, indent=2) + "\n")
        env.close()
    for task in a.tasks:
        rr = [r for r in rows if r["task"] == task]
        print(
            task,
            "mean",
            np.mean([r["score_0_100"] for r in rr]),
            "strict",
            sum(r["success_strict"] for r in rr),
            "/",
            len(rr),
            flush=True,
        )
    return (
        0
        if all(
            np.mean([r["score_0_100"] for r in rows if r["task"] == t]) >= 85
            and np.mean([r["success_strict"] for r in rows if r["task"] == t]) >= 0.8
            for t in a.tasks
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
