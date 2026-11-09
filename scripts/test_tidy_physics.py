"""Replay a measured v3 pinching state without the velocity guard.

The fixture is the substep immediately before astra s4 ejected the eraser at
117.32 m/s and notes at 83.37 m/s. No oracle or bridge is involved.
"""
import json

import mujoco
import numpy as np

from pilot_test_utils import ROOT, create
from rab.pilot_physics import MASSES, SMALL_STATIONERY


def main():
    fixture = json.loads((ROOT / "tests/fixtures/tidy_v3_pinched.json").read_text())
    env = create("pilot_tidy_desk")
    try:
        scorer = env.device_layer.get_device("pilot")
        model = scorer.m
        data = mujoco.MjData(model)
        initial = fixture["pre_step"]
        for field in ("qpos", "qvel", "ctrl"):
            target = getattr(data, field)
            assert target.shape == np.asarray(initial[field]).shape, field
            target[:] = initial[field]
        data.time = initial["time"]
        assert model.opt.timestep == fixture["timestep"]
        for name in SMALL_STATIONERY:
            assert np.isclose(model.body_mass[scorer.bodies[name]], MASSES[name])
        mujoco.mj_forward(model, data)
        peaks = {name: 0.0 for name in SMALL_STATIONERY}
        for _ in range(250):
            # Deliberately bypass PilotScorer.on_substep: no clamp can hide a failure.
            mujoco.mj_step(model, data)
            assert np.isfinite(data.qpos).all() and np.isfinite(data.qvel).all()
            for name, adr in scorer.guard.items():
                peaks[name] = max(peaks[name], float(np.linalg.norm(data.qvel[adr:adr+3])))
        assert max(peaks.values()) < 10, peaks
        assert not scorer.guard_events
        print(json.dumps(dict(passed=True, guard_enabled=False, duration_s=0.5,
                              peak_speeds_m_s=peaks), indent=2))
    finally:
        env.close()


if __name__ == "__main__":
    main()
