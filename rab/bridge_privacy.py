# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Agent-facing bridge serialization without grader feedback.

The two writers below are adapted from upstream tabletop_v0/agent.py (Apache-2.0,
Copyright 2026 Google LLC). Keep rendering/trace behavior, but remove grader data
BEFORE writing or publishing, including the Markdown report. No global JSON patch.
"""

from collections.abc import Mapping
from typing import Any
import json
import os
import numpy as np
from mujoco_playground.tasks.agentic.tabletop_v0 import agent as A
from mujoco_playground.tasks.agentic.tabletop_v0.agent import (
    _obs,
    save_wrist_image,
    render_wrist_image,
    create_trajectory_strip,
    embodiment_lib,
)

ACTIVE_TASK = None
_INSTALLED = False
GRADER_KEYS = frozenset(
    {
        "success",
        "success_strict",
        "score",
        "score_0_100",
        "reward",
        "rewards",
        "critical_errors",
        "items",
        "proactive_safety",
        "report_correct",
    }
)


def public_data(value):
    """Strip grader fields recursively, retaining sensor/executor feedback."""
    if isinstance(value, dict):
        return {
            k: public_data(v)
            for k, v in value.items()
            if k.lower().split("/")[-1] not in GRADER_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [public_data(v) for v in value]
    return value


def _write_report(self, success: bool) -> None:
    """Writes a self-contained markdown episode summary to `report.md`."""
    max_t = self._max_turns if self._max_turns > 0 else "unlimited"
    strategy_str = self._strategy or "Autonomous closed-loop execution"
    gif_abs = str(self._dir / "rollout.gif")
    lines = [
        f"# Episode Rollout Trace: {self._emb.display_name}",
        "",
        f"![Rollout GIF]({gif_abs})",
        "",
        f"- **Embodiment**: {self._emb.display_name} (`{self._emb.name}`)",
        f"- **Task**: {self._prompt}",
        f"- **Agent Upfront Plan / Granularity**: {strategy_str}",
        f"- **Turn**: {self._turn} / {max_t}",
        f"- **Status**: {self._executor.status}",
        "",
        "## Turn-by-Turn Thinking Trace & Trajectory",
        "",
    ]
    for entry in self._steps_log:
        turn_idx = entry["turn"]
        img_abs = str(self._dir / entry["image"])
        traj_abs = str(self._dir / f"obs_frames/trajectory_{turn_idx:03d}.png")
        plan_str = json.dumps(entry["plan"])
        thought_str = entry.get("thought") or "(Initial reset observation)"
        lines.extend(
            [
                (
                    f"### Turn {turn_idx:03d} (`{entry['event']}`) —"
                    f" `status={entry['status']}`"
                ),
                "",
                f"![Turn {turn_idx:03d} Trajectory Strip]({traj_abs})",
                "",
                f"- **Thinking Trace**: {thought_str}",
                f"- **Plan**: `{plan_str}`",
                (
                    f"- **State**: `ee_pos={entry['ee_pos']}`,"
                    f" `holding={entry['holding']}`,"
                    f" `stopped_on_contact={entry['stopped_on_contact']}`,"
                    f" `contact_pos={entry['contact_pos']}`"
                ),
                f"- **Snapshot**: [{entry['image']}]({img_abs})",
                "",
            ]
        )
    (self._dir / "report.md").write_text("\n".join(lines) + "\n")


def _write_turn_files(
    self,
    obs: Mapping[str, Any],
    status: str | None = None,
    success: bool = False,
    event: str | None = None,
) -> dict[str, Any]:
    """Writes `wrist_cam.png`, `obs.json`, `obs_frames/`, `steps.jsonl`, and `report.md`."""
    if status is not None:
        self._executor.status = status
    elif self._turn > 0 and self._executor.status in ("idle", "reset"):
        self._executor.status = "done"
    eff_status = self._executor.status
    if event is None:
        if eff_status == "reset" or (self._turn == 0 and not self._last_plan):
            event = "reset"
        elif self._done or eff_status == "episode_end":
            event = "done"
        else:
            event = "plan"

    qpos = _obs(obs, "joint_pos")
    self._image_qpos = qpos
    img = save_wrist_image(
        obs["wrist_rgb"],
        str(self._dir / "wrist_cam.png"),
        qpos,
        self._emb,
        grid_z=self.grid_z,
    )
    img_bytes = (self._dir / "wrist_cam.png").read_bytes()
    (self._dir / f"wrist_cam_{self._turn:02d}.png").write_bytes(img_bytes)
    frames_dir = self._dir / "obs_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    rel_frame = f"obs_frames/turn_{self._turn:03d}.png"
    (self._dir / rel_frame).write_bytes(img_bytes)

    self._frame_2 = render_wrist_image(
        obs["wrist_rgb"], qpos, self._emb, grid_z=self.grid_z
    )
    if self._turn > 0 and self._frame_2 is not None:
        self._gif_frames.append(self._frame_2)
    gif_path = self._dir / "rollout.gif"
    if self._gif_frames:
        self._gif_frames[0].save(
            str(gif_path),
            save_all=True,
            append_images=self._gif_frames[1:],
            duration=600,
            loop=0,
        )
    f0 = self._frame_0 if self._frame_0 is not None else self._frame_2
    f1 = self._frame_1 if self._frame_1 is not None else self._frame_2
    f2 = self._frame_2
    strip = create_trajectory_strip([f0, f1, f2])
    strip_path = self._dir / "wrist_cam_trajectory.png"
    strip.save(str(strip_path), optimize=True)
    turn_strip_rel = f"obs_frames/trajectory_{self._turn:03d}.png"
    strip.save(str(self._dir / turn_strip_rel), optimize=True)

    ee, ori = _obs(obs, "ee_pos"), _obs(obs, "ee_ori")
    grip = float(_obs(obs, "gripper_state")[0])
    holding = self._executor.holding(obs)
    yaw_long = embodiment_lib.fold(float(ori[0]) - np.pi / 2)
    az_off = float(ori[2]) if len(ori) > 2 else 0.0
    azimuth = embodiment_lib.wrap_pi(
        self._emb.heading(float(ee[0]), float(ee[1])) + az_off
    )
    roll = embodiment_lib.fold(float(ori[0]) - azimuth)
    stopped_on_contact = bool(self._executor.stopped_on_contact)
    contact_pos = self._executor.contact_pos
    state = {
        "ee_pos": [round(v, 4) for v in ee],
        "yaw_deg": round(float(np.degrees(yaw_long)), 1),
        "jaw_yaw_deg": round(float(np.degrees(ori[0])), 1),
        "roll_deg": round(float(np.degrees(roll)), 1),
        "pitch": round(float(ori[1]), 3),
        "pitch_deg": round(float(np.degrees(ori[1])), 1),
        "azimuth_deg": round(float(np.degrees(azimuth)), 1),
        "gripper": round(grip, 3),
        "holding": holding,
        "stopped_on_contact": stopped_on_contact,
        "contact_pos": contact_pos,
        "grid_z": round(float(self.grid_z), 4),
    }
    self._history.append({"turn": self._turn, "status": eff_status, **state})
    payload = {
        "robot": self._emb.display_name,
        "task": self._prompt,
        "strategy": self._strategy,
        "thought": self._last_thought,
        "turn": self._turn,
        "max_turns": self._max_turns,
        "status": eff_status,
        "success": success,
        **state,
        "joint_pos": [round(v, 3) for v in qpos],
        "wrist_cam_path": img,
        "wrist_cam_trajectory_path": str(strip_path),
        "rollout_gif_path": str(gif_path),
        "report_path": str(self._dir / "report.md"),
        "calibration_image": str(self._dir / "calibration.png"),
        "history": self._history[-20:],
        "workspace": {
            "min": list(self._emb.workspace_min),
            "max": list(self._emb.workspace_max),
        },
    }
    payload = public_data(payload)
    (self._dir / "obs.json").write_text(json.dumps(payload, indent=1))
    step_record = {
        "turn": self._turn,
        "event": event,
        "strategy": self._strategy,
        "thought": self._last_thought,
        "plan": self._last_plan,
        "status": self._executor.status,
        "stopped_on_contact": stopped_on_contact,
        "contact_pos": contact_pos,
        "ee_pos": state["ee_pos"],
        "yaw_deg": state["yaw_deg"],
        "roll_deg": state["roll_deg"],
        "pitch_deg": state["pitch_deg"],
        "azimuth_deg": state["azimuth_deg"],
        "gripper": state["gripper"],
        "holding": state["holding"],
        "image": rel_frame,
        "wrist_cam_trajectory_path": str(strip_path),
    }
    step_record = public_data(step_record)
    self._steps_log.append(step_record)
    with open(self._dir / "steps.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(step_record) + "\n")
    self._write_report(success)
    with self._lock:
        self._payload = payload
    return payload


def install(task_names):
    """Patch only our task backends; internal reward and episode records are intact."""
    global _INSTALLED
    if _INSTALLED:
        return
    cls = A.BridgeBackend
    original_init = cls.__init__
    original_turn = cls._write_turn_files
    original_report = cls._write_report
    original_handler = cls._handler

    def init(self, task_prompt, bridge_dir, http_port, emb, max_turns=0):
        from pathlib import Path
        from mujoco_playground.tasks.agentic.tabletop_v0 import task as T

        self._rab_hide_grader = os.environ.get("RAB_SHOW_SUCCESS") != "1" and (
            ACTIVE_TASK in task_names
            or any(task_prompt == T.TASKS[n].prompt for n in task_names)
        )
        if self._rab_hide_grader:
            root = Path(bridge_dir).resolve()
            log = os.environ.get("RAB_EPISODE_LOG")
            if log and Path(log).resolve().is_relative_to(root):
                raise ValueError(
                    "RAB_EPISODE_LOG must be outside the agent-visible bridge directory"
                )
            # Sanitize known stale files before the HTTP server starts.
            for name in ("obs.json", "steps.jsonl", "report.md"):
                p = root / name
                if p.exists():
                    text = p.read_text()
                    if name == "report.md":
                        text = (
                            "\n".join(
                                line
                                for line in text.splitlines()
                                if not line.startswith("- **Success**:")
                            )
                            + "\n"
                        )
                    elif name.endswith("jsonl"):
                        text = "".join(
                            json.dumps(public_data(json.loads(line))) + "\n"
                            for line in text.splitlines()
                            if line.strip()
                        )
                    else:
                        text = json.dumps(public_data(json.loads(text)), indent=1)
                    p.write_text(text)
        original_init(self, task_prompt, bridge_dir, http_port, emb, max_turns)

    def turn(self, *args, **kwargs):
        writer = _write_turn_files if self._rab_hide_grader else original_turn
        return writer(self, *args, **kwargs)

    def report(self, success):
        writer = _write_report if self._rab_hide_grader else original_report
        return writer(self, success)

    def handler(self):
        base = original_handler(self)
        backend = self

        class PublicHandler(base):
            def _send(self, code, data):
                return super()._send(
                    code, public_data(data) if backend._rab_hide_grader else data
                )

        return PublicHandler

    cls.__init__ = init
    cls._write_turn_files = cls._snapshot = turn
    cls._write_report = report
    cls._handler = handler
    # Upstream finish emits an extra snapshot only for success. That would leak
    # the grader through step count/history/GIF length even without the field.
    original_finish = A.AgentPolicy.finish

    def finish(self, obs, success):
        if getattr(self._backend, "_rab_hide_grader", False):
            self._reported = True
            self._backend.on_plan_done(obs, "episode_end", success)
        else:
            original_finish(self, obs, success)

    A.AgentPolicy.finish = finish
    # Optional runner videos must not overlay the grader either.
    original_video = A.VideoRecorder.record

    def video(self, obs, policy, steps, success):
        if getattr(policy._backend, "_rab_hide_grader", False):
            success = False
        return original_video(self, obs, policy, steps, success)

    A.VideoRecorder.record = video
    _INSTALLED = True
