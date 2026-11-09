"""Measure right_mug with the upstream oracle policy and physics unchanged.

Run as ``python -m scripts.right_mug_study --robot=so101 --episodes=10``.
Camera rendering is omitted by default; the oracle never consumes its pixels.
Unlike upstream run.py, acceptance is checked after five seconds of settling,
not latched from any transient success during the motion.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np
import torch
from absl import app, flags

import rab

rab.register()

from mujoco_playground.tasks.agentic.tabletop_v0 import agent, embodiment, task
from reaf.simulation.torch import flat_scene

from rab.tasks import right_mug as R

ROBOT = flags.DEFINE_enum("robot", "so101", ["so101", "yam"], "Embodiment.")
EPISODES = flags.DEFINE_integer("episodes", 10, "Number of reset samples.")
STRATEGY = flags.DEFINE_enum("strategy", "best", R.STRATEGIES, "Primitive strategy.")
SEED = flags.DEFINE_integer("seed", 101, "Reset sequence seed.")
TURNS = flags.DEFINE_integer("turns", 1, "Maximum oracle attempts per episode.")
OUTPUT = flags.DEFINE_string(
    "output", "outputs/right_mug/results.jsonl", "Per-episode JSONL."
)
RENDER = flags.DEFINE_bool("render", False, "Retain normal wrist-camera rendering.")
SETTLE = flags.DEFINE_float("settle", 5.0, "Seconds to settle after the final plan.")


class RecordingBackend(agent.OracleBackend):
    """Only records plans and status; execution stays in the upstream policy."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events = []
        self.plans = []

    def plan(self, obs):
        result = super().plan(obs)
        if result is not None:
            self.plans.append(result)
        return result

    def on_plan_done(self, obs, status, success):
        self.events.append(dict(turn=self.turn, status=status, success=bool(success)))
        super().on_plan_done(obs, status, success)


def sample(env, obs):
    pos, rot = R.mug_pose()
    ee = agent._obs(obs, "ee_pos")
    opening = float(agent._obs(obs, "gripper_state")[0])
    emb = embodiment.get(ROBOT.value)
    sim = env.device_layer.sim
    joint = sim.mj_model.joint("mug/mug_freejoint")
    adr = int(joint.dofadr[0])
    vel = sim.data.qvel[0, adr : adr + 6].cpu().numpy()
    site = sim.mj_model.site(emb.ee_site).id
    tool = sim.mj_data.site_xmat[site].reshape(3, 3)
    body = sim.mj_model.body("mug/mug").id
    table = sim.mj_model.geom("table/table_collision").id
    robot_contacts = 0
    supported = False
    for contact in sim.mj_data.contact:
        a, b = int(contact.geom1), int(contact.geom2)
        if sim.mj_model.geom_bodyid[a] == body:
            other = b
        elif sim.mj_model.geom_bodyid[b] == body:
            other = a
        else:
            continue
        supported |= other == table
        robot_contacts += int(
            sim.mj_model.body(sim.mj_model.geom_bodyid[other]).name.startswith("robot/")
        )
    released = robot_contacts == 0
    return dict(
        xyz=pos.tolist(),
        axis=rot[:, 2].tolist(),
        tool_rotation=tool.tolist(),
        tilt_deg=float(np.degrees(np.arccos(np.clip(rot[2, 2], -1, 1)))),
        released=bool(released),
        gripper=opening,
        ee=ee.tolist(),
        robot_contacts=robot_contacts,
        supported=bool(supported),
        linear_speed=float(np.linalg.norm(vel[:3])),
        angular_speed=float(np.linalg.norm(vel[3:])),
    )


def main(argv):
    del argv
    os.environ["RAB_RIGHT_SEED"] = str(SEED.value)
    os.environ["RAB_RIGHT_STRATEGY"] = STRATEGY.value
    torch.manual_seed(SEED.value)
    np.random.seed(SEED.value)
    cfg = task.env_config(task=R.NAME, robot=ROBOT.value)
    if not RENDER.value:
        del cfg.device_layer.devices["wrist_cam"]
        del cfg.observation_adapter["wrist_rgb"]
    cfg.episode_length_secs = 180.0
    env = flat_scene.build_env(cfg)
    backend = RecordingBackend(env, R.TASK, max_turns=TURNS.value)
    policy = agent.AgentPolicy(
        backend, ROBOT.value, dt=env.device_layer.control_timestep
    )
    output = Path(OUTPUT.value)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite study evidence: {output}")
    count = 0
    try:
        with output.open("x") as stream:
            for episode in range(1, EPISODES.value + 1):
                started = time.monotonic()
                ts = env.reset()
                policy.reset(ts.observation)
                backend.events, backend.plans = [], []
                initial = sample(env, ts.observation)
                trace, tail = [], []
                previous_segment = None
                idle = 0
                max_z, min_tilt, max_tracking_error = initial["xyz"][2], 180.0, 0.0
                ever_success = False
                for step in range(9000):
                    current = sample(env, ts.observation)
                    instantaneous = (
                        current["tilt_deg"] <= 15
                        and current["released"]
                        and current["supported"]
                        and current["linear_speed"] < 0.02
                        and current["angular_speed"] < 0.2
                    )
                    action = policy.step(ts.observation, instantaneous)
                    seg = (
                        policy.executor._segments[0]
                        if policy.executor._segments
                        else None
                    )
                    if seg is not previous_segment:
                        record = dict(
                            step=step, label=seg.label if seg else "idle", **current
                        )
                        if seg:
                            record["target"] = seg.pose.tolist()
                        trace.append(record)
                        previous_segment = seg
                    ts = env.step({"action": action})
                    max_z = max(max_z, current["xyz"][2])
                    min_tilt = min(min_tilt, current["tilt_deg"])
                    err = float(np.linalg.norm(action[0, :3].numpy() - current["ee"]))
                    max_tracking_error = max(max_tracking_error, err)
                    ever_success |= instantaneous
                    idle = idle + 1 if policy.idle and backend.exhausted else 0
                    tail.append(current)
                    tail = tail[-50:]
                    if idle >= round(SETTLE.value / 0.02) or ts.last():
                        break
                final = sample(env, ts.observation)
                # Stable acceptance also excludes an airborne, upright mug.
                stable = all(
                    s["tilt_deg"] <= 15
                    and s["released"]
                    and s["supported"]
                    and s["linear_speed"] < 0.02
                    and s["angular_speed"] < 0.2
                    and 0.012 < s["xyz"][2] < 0.050
                    for s in tail
                )
                success = bool(stable and idle >= round(SETTLE.value / 0.02))
                policy.finish(ts.observation, success)
                lifted = max_z - initial["xyz"][2] > 0.025
                if success:
                    reason = "success"
                elif final["xyz"][2] < -0.01:
                    reason = "mug_off_table"
                elif any(e["status"] == "grasp_failed" for e in backend.events):
                    reason = "grasp_slip" if lifted else "grasp_failed_no_lift"
                elif not final["released"]:
                    reason = "not_released"
                elif final["tilt_deg"] > 15:
                    reason = (
                        "lifted_but_ended_on_side"
                        if lifted
                        else "no_lift_ended_on_side"
                    )
                else:
                    reason = "unstable_or_timeout"
                row = dict(
                    robot=ROBOT.value,
                    strategy=STRATEGY.value,
                    seed=SEED.value,
                    episode=episode,
                    max_turns=TURNS.value,
                    success=success,
                    reason=reason,
                    initial=initial,
                    reset=R.ACTIVE.initial,
                    final=final,
                    steps=step + 1,
                    wall_seconds=time.monotonic() - started,
                    max_mug_z=max_z,
                    min_tilt_deg=min_tilt,
                    lifted=lifted,
                    max_tracking_error=max_tracking_error,
                    ever_success=bool(ever_success),
                    events=backend.events,
                    plans=backend.plans,
                    trace=trace,
                    rendering=RENDER.value,
                    settle_seconds=SETTLE.value,
                    task_success=bool(
                        final["tilt_deg"] <= 15
                        and final["released"]
                        and final["supported"]
                    ),
                    source_sha256=hashlib.sha256(
                        Path(R.__file__).read_bytes()
                    ).hexdigest(),
                )
                stream.write(json.dumps(row) + "\n")
                stream.flush()
                count += success
                print(
                    f"{ROBOT.value} {STRATEGY.value} episode={episode} success={success} "
                    f"tilt={final['tilt_deg']:.1f} reason={reason} total={count}/{episode} "
                    f"wall={row['wall_seconds']:.1f}s",
                    flush=True,
                )
    finally:
        env.close()


if __name__ == "__main__":
    app.run(main)
