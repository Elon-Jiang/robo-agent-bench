"""Isolate the integration artifact in a saved v3 pinching state.

Reconstructs v3 from the saved physics module, never edits the live scene or
upstream. Each row starts from identical qpos/qvel/ctrl and takes one raw step.
"""
import argparse
import dataclasses
import json
from pathlib import Path
import runpy

import mujoco
import numpy as np

from pilot_test_utils import ROOT, create
from rab.devices.pilots import PilotScorer
from rab.tasks.pilots import SCENES, task_for
from mujoco_playground.tasks.agentic.tabletop_v0 import task as T


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--v3-physics", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()
    fixture = json.loads((ROOT / "tests/fixtures/tidy_v3_pinched.json").read_text())
    household = runpy.run_path(str(args.v3_physics))["household"]
    task = "pilot_tidy_desk"
    SCENES[task] = dataclasses.replace(SCENES[task], objects=tuple(
        household(obj) for obj in SCENES[task].objects))
    T.TASKS[task] = task_for(task)
    PilotScorer.configure_spec = lambda self, spec, **kwargs: spec
    env = create(task)
    rows = []
    try:
        scorer = env.device_layer.get_device("pilot")
        model = scorer.m
        initial = fixture["pre_step"]
        for integrator in (mujoco.mjtIntegrator.mjINT_IMPLICITFAST,
                           mujoco.mjtIntegrator.mjINT_IMPLICIT,
                           mujoco.mjtIntegrator.mjINT_EULER,
                           mujoco.mjtIntegrator.mjINT_DISCRETE):
            for exact in (False, True):
                model.opt.integrator = integrator
                model.opt.enableflags = int(mujoco.mjtEnableBit.mjENBL_DIAGEXACT) if exact else 0
                data = mujoco.MjData(model)
                for field in ("qpos", "qvel", "ctrl"):
                    getattr(data, field)[:] = initial[field]
                data.time = initial["time"]
                mujoco.mj_forward(model, data)
                mujoco.mj_step(model, data)
                row = dict(integrator=integrator.name, exact_diagonal=exact,
                           speeds_m_s={name: float(np.linalg.norm(data.qvel[a:a+3]))
                                       for name, a in scorer.guard.items()},
                           eraser_qacc=data.qacc[scorer.guard["eraser"]:scorer.guard["eraser"]+3].tolist())
                rows.append(row)
        for name, speed in fixture["expected_v3_first_guard_m_s"].items():
            assert np.isclose(rows[0]["speeds_m_s"][name], speed, rtol=1e-6)
        assert max(rows[6]["speeds_m_s"].values()) < 3
        result = dict(mujoco_version=mujoco.__version__, timestep=model.opt.timestep,
                      source_run=fixture["source_run"], guard_enabled=False, rows=rows)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
    finally:
        env.close()


if __name__ == "__main__":
    main()
