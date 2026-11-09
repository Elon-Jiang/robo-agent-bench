"""Validate reset contacts, reach, support, and one-second stability for every pilot."""

import os
import argparse
import json
import mujoco
import numpy as np
import torch

from pilot_test_utils import ROOT, create, settle
from rab.tasks.pilots import SCENES
from rab.pilot_physics import MASSES, kind


def check(task, n):
    env = create(task)
    sim = env.device_layer.sim
    m, d = sim.mj_model, sim.mj_data
    scorer = env.device_layer.get_device("pilot")
    rows = []
    for seed in range(n):
        if "pilot_" in task:
            os.environ["RAB_SEED"] = str(seed)
        torch.manual_seed(seed)
        env.reset()
        mujoco.mj_forward(m, d)
        scorer._prepare(d)
        errors = []
        initial = {k: v.copy() for k, v in scorer.pos.items()}
        max_pen = 0.0
        bodies = set(scorer.bodies.values())
        for i in range(d.ncon):
            c = d.contact[i]
            a, b = m.geom_bodyid[c.geom1], m.geom_bodyid[c.geom2]
            if a != b and (a in bodies or b in bodies):
                max_pen = max(max_pen, -float(c.dist))
                if c.dist < -0.0001:
                    errors.append(
                        f"reset penetration {m.body(a).name}/{m.body(b).name}: {c.dist:.5f}"
                    )
        for name, p in initial.items():
            b = scorer.bodies[name]
            if not np.isclose(m.body_mass[b], MASSES[kind(name)], rtol=1e-6):
                errors.append(f"{name} wrong mass: {m.body_mass[b]}")
            if not np.all(m.body_inertia[b] > 0):
                errors.append(f"{name} invalid inertia")
            inertia = m.body_inertia[b]
            if np.max(inertia) > np.sum(inertia) - np.max(inertia) + 1e-10:
                errors.append(f"{name} violates inertia triangle inequality")
            if not scorer.on_table(name):
                errors.append(f"{name} off table at reset")
            if np.linalg.norm(p[:2]) > 0.44 or not (
                0.16 <= p[0] <= 0.5 and abs(p[1]) <= 0.22
            ):
                errors.append(f"{name} outside YAM reach: {p.tolist()}")
        settle(env, 1.0)
        max_drift = 0.0
        for name, p in scorer.pos.items():
            drift = float(np.linalg.norm(p[:2] - initial[name][:2]))
            max_drift = max(max_drift, drift)
            if drift > 0.02:
                errors.append(f"{name} drift {drift:.4f}")
            if not scorer.on_table(name) or not scorer.settled(name):
                errors.append(f"{name} not stable on table after 1s")
            fixed = m.body_jntnum[scorer.bodies[name]] == 0
            supported = (
                abs(scorer.points[name][:, 2].min()) < 0.001
                if fixed
                else scorer.table_supported(name)
            )
            if not supported:
                errors.append(f"{name} lacks table support after 1s")
            if name in scorer.scene.containers or name in (
                "mug",
                "mug_other",
                "bowl",
                "plate",
            ):
                if not scorer.upright(name):
                    errors.append(f"{name} not upright")
        if task == "pilot_toys":
            y = scorer.points["mug"][:, 1]
            if not (y.max() > 0.22 and scorer.pos["mug"][1] < 0.22):
                errors.append("mug does not overhang a real table edge")
        if task == "pilot_spill_cleanup":
            spill = scorer.spill
            if spill.active.sum() < 30:
                errors.append("puddle too small")
            if (spill._cells_under_mugs(d, margin=0) & spill.active).any():
                errors.append("puddle under mug")
        if task == "pilot_breakfast":
            center, facing, right = scorer.diner_frame()
            chair = d.xpos[m.body("diner_chair").id]
            direction = (center - chair)[:2]
            direction /= np.linalg.norm(direction)
            if not np.allclose(direction, facing[:2]):
                errors.append("chair and placemat disagree on diner facing")
        row = dict(
            task=task,
            seed=seed,
            passed=not errors,
            max_reset_penetration_m=max_pen,
            max_xy_drift_m=max_drift,
            errors=errors,
        )
        rows.append(row)
        print(json.dumps(row), flush=True)
    env.close()
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--task", choices=SCENES)
    args = ap.parse_args()
    rows = [r for t in ([args.task] if args.task else SCENES) for r in check(t, args.n)]
    out = ROOT / "outputs/pilots/layout_results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2) + "\n")
    assert all(
        r["passed"] for r in rows
    ), f"{sum(not r['passed'] for r in rows)} layouts failed; see {out}"


if __name__ == "__main__":
    main()
