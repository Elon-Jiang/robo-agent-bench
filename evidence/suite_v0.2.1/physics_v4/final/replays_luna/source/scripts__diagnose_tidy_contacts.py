"""Replay saved tidy plans locally; never starts a bridge or an agent.

Dump first and worst pre-guard contacts per body, including contact-frame forces.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

import mujoco
import numpy as np
import torch
from pilot_test_utils import ROOT, create, evaluate
from rab.devices.pilots import PilotScorer
from mujoco_playground.tasks.agentic.tabletop_v0.agent import PlanExecutor


def replay(path):
    os.environ["RAB_SEED"] = path.name.rsplit("__s", 1)[1]
    maximum, first, previous = {}, {}, {}
    all_guard_events, falls = {}, {}
    organizer_off_events = []
    organizer_was_off = False
    pre_step = {}
    context = {}
    original = PilotScorer.on_substep
    original_step = mujoco.mj_step

    def capture_step(m, d, *args, **kwargs):
        pre_step.update(time=float(d.time), qpos=d.qpos.copy(),
                        qvel=d.qvel.copy(), ctrl=d.ctrl.copy())
        return original_step(m, d, *args, **kwargs)

    def snapshot(s, d, name, speed):
        m, body = s.m, s.bodies[name]
        contacts = []
        for i, c in enumerate(d.contact):
            if body not in (m.geom_bodyid[c.geom1], m.geom_bodyid[c.geom2]):
                continue
            force = np.zeros(6)
            mujoco.mj_contactForce(m, d, i, force)
            contacts.append(dict(
                geoms=[m.geom(g).name or f"geom#{g}" for g in (c.geom1, c.geom2)],
                bodies=[m.body(m.geom_bodyid[g]).name for g in (c.geom1, c.geom2)],
                types=[int(m.geom_type[g]) for g in (c.geom1, c.geom2)],
                dist=float(c.dist), pos=c.pos.tolist(), force=force.tolist(),
                dim=int(c.dim), efc_address=int(c.efc_address),
                solref=c.solref.tolist(), solimp=c.solimp.tolist(),
            ))
        a = s.guard[name]
        return dict(time=float(d.time), body=name, speed=speed,
                    qvel=d.qvel[a:a+6].tolist(), qacc=d.qacc[a:a+6].tolist(),
                    position=d.xpos[body].tolist(), contacts=contacts,
                    pre_step={k: v.tolist() if isinstance(v, np.ndarray) else v
                              for k, v in pre_step.items()},
                    previous=previous.get(name), **context)

    def inspect(s, sim):
        nonlocal organizer_was_off
        d = sim.mj_data
        body = s.bodies["organizer"]
        position = d.xpos[body]
        relative = (position - d.geom_xpos[s.table]) @ d.geom_xmat[s.table].reshape(3, 3)
        lowest = float((s.local_points["organizer"] @ d.xmat[body].reshape(3, 3)[2]).min() + position[2])
        off = bool(np.any(np.abs(relative[:2]) >= s.m.geom_size[s.table, :2])
                   or lowest < -0.012 or position[2] >= 0.45)
        if off and not organizer_was_off:
            organizer_off_events.append(dict(time=float(d.time), episode=s.episode,
                                             position=position.tolist(), lowest_z=lowest))
        organizer_was_off = off
        # Track actual falls throughout the replay, including across automatic
        # episode resets. A carried object above 45 cm is not a physical drop.
        for name in (*s.guard, "organizer"):
            if d.xpos[s.bodies[name], 2] < -0.03 and name not in falls:
                falls[name] = dict(time=float(d.time), episode=s.episode,
                                   position=d.xpos[s.bodies[name]].tolist())
        for name, a in s.guard.items():
            speed = float(np.linalg.norm(d.qvel[a:a+3]))
            if speed > s.GUARD_MAX_SPEED:
                event = all_guard_events.setdefault(name, dict(substeps=0, max_speed=0.0))
                event["substeps"] += 1
                event["max_speed"] = max(event["max_speed"], speed)
                if name not in first:
                    first[name] = snapshot(s, d, name, speed)
                if speed > maximum.get(name, {}).get("speed", 0):
                    maximum[name] = snapshot(s, d, name, speed)
            previous[name] = dict(time=float(d.time), qvel=d.qvel[a:a+6].tolist(),
                                  position=d.xpos[s.bodies[name]].tolist())
        original(s, sim)

    PilotScorer.on_substep = inspect
    mujoco.mj_step = capture_step
    start = time.monotonic()
    env = create("pilot_tidy_desk")
    try:
        ts = env.reset()
        ex = PlanExecutor("yam")
        ex.sync(ts.observation)
        plans = 0
        for line, text in enumerate((path / "bridge/steps.jsonl").read_text().splitlines()):
            row = json.loads(text)
            if not row.get("plan"):
                continue
            plans += 1
            context.update(line=line, plan=row["plan"])
            ex.load(row["plan"], ts.observation)
            n = 0
            while ex.busy and n < 6000:
                context["control_step"] = n
                ts = env.step({"action": torch.tensor(ex.step(ts.observation), dtype=torch.float32)[None]})
                n += 1
        result = evaluate(env)
        s = env.device_layer.get_device("pilot")
        return dict(run=path.name, wall_s=time.monotonic()-start, plans=plans,
                    timestep=float(s.m.opt.timestep), record=result,
                    collision_geoms={n: [s.m.geom(g).name for g in range(s.m.ngeom)
                        if s.m.geom_bodyid[g] == s.bodies[n]
                        and (s.m.geom_contype[g] or s.m.geom_conaffinity[g])]
                        for n in s.guard},
                    small_dropped=[n for n in s.guard if not s.on_table(n)],
                    organizer_off=not s.on_table("organizer"),
                    organizer_off_events=organizer_off_events,
                    falls=falls, all_guard_events=all_guard_events,
                    first_events=first, worst_events=maximum)
    finally:
        env.close()
        PilotScorer.on_substep = original
        mujoco.mj_step = original_step


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--physics-v3", type=Path, help="Saved v3 pilot_physics.py, for baseline replay")
    args = ap.parse_args()
    if args.physics_v3:
        import dataclasses
        import runpy
        from rab.tasks.pilots import SCENES, task_for
        from mujoco_playground.tasks.agentic.tabletop_v0 import task as T
        original_household = runpy.run_path(str(args.physics_v3))["household"]
        name = "pilot_tidy_desk"
        SCENES[name] = dataclasses.replace(SCENES[name], objects=tuple(
            original_household(o) for o in SCENES[name].objects))
        T.TASKS[name] = task_for(name)
        # Label the reconstructed scene accurately without editing live code.
        import rab.devices.pilots as scorer_module
        current_version = scorer_module.scene_version
        scorer_module.scene_version = lambda task: 3 if task == name else current_version(task)
        PilotScorer.configure_spec = lambda self, spec, **kwargs: spec
    args.output.mkdir(parents=True, exist_ok=True)
    source_dir = args.output / "source"
    source_dir.mkdir(exist_ok=True)
    manifest = {}
    for relative in ("rab/pilot_physics.py", "rab/tasks/pilots.py",
                     "rab/devices/pilots.py", "rab/versions.py", "scripts/diagnose_tidy_contacts.py"):
        source = ROOT / relative
        manifest[relative] = hashlib.sha256(source.read_bytes()).hexdigest()
        shutil.copyfile(source, source_dir / relative.replace("/", "__"))
    (source_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for run in args.runs:
        result = replay(run)
        (args.output / (run.name + ".json")).write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({k:v for k,v in result.items() if k not in ("first_events", "worst_events")}), flush=True)


if __name__ == "__main__":
    main()
