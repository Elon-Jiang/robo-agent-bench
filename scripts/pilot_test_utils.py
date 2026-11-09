"""Physics-only helpers for reproducible pilot validation."""

import sys
from pathlib import Path

import mujoco
import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import rab

rab.register()
from mujoco_playground.tasks.agentic.tabletop_v0 import task as T
from reaf.simulation.torch._src.builders.flat_scene import build


def create(task):
    cfg = T.env_config(task=task, robot="yam")
    del cfg.device_layer.devices["wrist_cam"]
    del cfg.observation_adapter["wrist_rgb"]
    return build.build_env(cfg)


def settle(env, seconds=1.0):
    sim = env.device_layer.sim
    scorer = env.device_layer.get_device("pilot")
    spill = getattr(scorer, "spill", None)
    for i in range(round(seconds / sim.mj_model.opt.timestep)):
        mujoco.mj_step(sim.mj_model, sim.mj_data)
        if spill is not None:
            spill.on_substep(sim)
        if i % 10 == 9:
            if spill is not None:
                spill.on_step(sim)
            scorer.on_step(sim)
    mujoco.mj_forward(sim.mj_model, sim.mj_data)
    scorer.on_step(sim)
    return scorer.record


def put(env, name, xyz, quat=(1, 0, 0, 0)):
    sim = env.device_layer.sim
    m, d = sim.mj_model, sim.mj_data
    j = m.joint(f"{name}/{name}_freejoint")
    a, v = j.qposadr[0], j.dofadr[0]
    d.qpos[a : a + 7] = [*xyz, *quat]
    d.qvel[v : v + 6] = 0
    mujoco.mj_forward(m, d)


def evaluate(env):
    sim = env.device_layer.sim
    mujoco.mj_forward(sim.mj_model, sim.mj_data)
    scorer = env.device_layer.get_device("pilot")
    scorer.on_step(sim)
    feats = scorer.get_measurements(sim)
    assert bool(T.TASKS[scorer.scene.name].success(feats)[0]) == scorer.record["success_strict"]
    return scorer.record


def reset(env, seed=0):
    torch.manual_seed(seed)
    np.random.seed(seed)
    env.reset()
    return settle(env)
