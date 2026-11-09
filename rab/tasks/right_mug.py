"""Right a randomly tipped YCB mug using the existing tabletop primitives.

RAB_RIGHT_STRATEGY selects an oracle; RAB_RIGHT_SEED fixes the reset sequence.
The default "best" uses rim_hook on SO-101 and rim_pull on YAM.
The oracle reads full simulated poses (privileged feasibility study), but every
motion goes through the unmodified upstream primitive executor and controller.
"""

from __future__ import annotations

import functools
import os
import warnings
from pathlib import Path
from typing import Any, Mapping

import mujoco
import numpy as np
import torch
from mujoco_playground._src import paths
from mujoco_playground.tasks.agentic.tabletop_v0 import embodiment as E
from mujoco_playground.tasks.agentic.tabletop_v0 import reset
from mujoco_playground.tasks.agentic.tabletop_v0 import task as T
from reaf.simulation.torch import ContactSensorDevice
from reaf.simulation.torch import flat_scene as config

NAME = "right_mug"
PROMPT = "The mug has fallen over. Stand it back upright."
MUG = T.ycb("mug", "025_mug", 0.55)
STRATEGIES = (
    "body_direct",
    "body_radial",
    "rim_pivot",
    "handle",
    "rim_lift",
    "rim_hook",
    "rim_pull",
    "body_offset",
    "rim_feedback",
    "rim_hybrid",
    "best",
)
ACTIVE: RightMugResetter | None = None


def configure_local_assets() -> None:
    """Read existing cached assets directly, without upstream cache lock writes."""
    root = Path(T.__file__).resolve().parents[4]
    for repo, suffix in (("mujoco_menagerie", ""), ("aloha_sim", "/aloha_sim")):
        prefix = f"github:google-deepmind/{repo}{suffix}"
        cached = root / ".local" / f"github.com_google-deepmind_{repo}" / "HEAD"
        candidate = cached / suffix.lstrip("/") if suffix else cached
        original = paths.URI_PREFIX_TO_LOCAL_DIR.get(prefix)
        if candidate.is_dir() and (original is None or not original.is_dir()):
            paths.URI_PREFIX_TO_LOCAL_DIR[prefix] = candidate


def check_righted(features, *, gripper_range, released_norm):
    """Require upright, released, supported, and nearly stationary.

    Actual contact sensors avoid the generic 45 mm release-distance shortcut:
    a rim grasp can hold this mug farther than 45 mm from its body center.
    """
    del gripper_range, released_norm
    q = features["mug/xquat"]
    upright = 1.0 - 2.0 * (q[:, 1] ** 2 + q[:, 2] ** 2) >= np.cos(np.pi / 12)
    released = features["righting_robot_contact/found"].flatten(1).sum(1) == 0
    supported = features["righting_table_contact/found"].flatten(1).sum(1) > 0
    vel = features["mug/joint_vel"]
    settled = (torch.linalg.norm(vel[:, :3], dim=1) < 0.02) & (
        torch.linalg.norm(vel[:, 3:], dim=1) < 0.2
    )
    return upright & released & supported & settled


@functools.lru_cache(maxsize=1)
def collision_vertices() -> np.ndarray:
    """Vertices in the centered body frame, including the off-axis handle."""
    model = MUG.spec().compile()
    points = []
    for g in range(model.ngeom):
        if not (model.geom_contype[g] or model.geom_conaffinity[g]):
            continue
        mesh = model.geom_dataid[g]
        adr, count = model.mesh_vertadr[mesh], model.mesh_vertnum[mesh]
        rot = np.empty(9)
        mujoco.mju_quat2Mat(rot, model.geom_quat[g])
        # einsum avoids spurious Accelerate BLAS warnings on small matrices.
        v = np.einsum(
            "ij,kj->ki", rot.reshape(3, 3), model.mesh_vert[adr : adr + count]
        )
        points.append(v + model.geom_pos[g])
    return np.concatenate(points)


def rotation(axis: np.ndarray, angle: float) -> np.ndarray:
    axis = np.asarray(axis, dtype=float)
    axis = axis / np.linalg.norm(axis)
    x, y, z = axis
    cross = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    return np.eye(3) + np.sin(angle) * cross + (1 - np.cos(angle)) * (cross @ cross)


class RightMugResetter(reset.ScatterObjectsResetter):
    """Use the normal robot reset, then sample a collision-free lying mug."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.seed = int(os.environ.get("RAB_RIGHT_SEED", "101"))
        self.rng = np.random.default_rng(self.seed)
        self.episode = 0
        self.initial = {}

    def on_reset(self, cfg: Any, env_mask: torch.Tensor) -> None:
        global ACTIVE
        super().on_reset(cfg, env_mask)
        if self._env.nworld != 1:
            raise ValueError("right_mug feasibility oracle requires nworld=1")
        ACTIVE = self
        self.feedback_grasped = False
        self.oracle_calls = 0
        self.episode += 1
        emb = self._emb
        while True:
            xy = self.rng.uniform(
                [emb.spawn_x[0], emb.spawn_y[0]], [emb.spawn_x[1], emb.spawn_y[1]]
            )
            # Leave 2 cm for the asymmetric handle's roll while settling.
            if emb.reach(*xy) <= emb.max_reach - 0.02:
                break
        mouth, spin = self.rng.uniform(-np.pi, np.pi, 2)
        rot = rotation([0, 0, 1], mouth) @ rotation([0, 1, 0], np.pi / 2)
        rot = rot @ rotation([0, 0, 1], spin)
        quat = np.empty(4)
        mujoco.mju_mat2Quat(quat, rot.ravel())
        z = (
            -float(np.einsum("ij,kj->ki", rot, collision_vertices())[:, 2].min())
            + 0.001
        )
        dev = self._env.device_layer.get_device("mug")
        tensor = lambda v: torch.tensor(
            [v], dtype=torch.float32, device=self._env.xpu_device
        )
        dev.set_pos(env_mask, tensor([*xy, z]))
        dev.set_quat(env_mask, tensor(quat.tolist()))
        dev.set_vel(env_mask, tensor([0.0] * 6))
        self.initial = dict(
            episode=self.episode,
            seed=self.seed,
            xyz=[*xy, z],
            mouth_deg=float(np.degrees(mouth)),
            spin_deg=float(np.degrees(spin)),
        )


def mug_pose() -> tuple[np.ndarray, np.ndarray]:
    """Read actual pose after settling or contact, rather than the reset pose."""
    if ACTIVE is None:
        raise RuntimeError("Reset right_mug before requesting an oracle plan")
    sim = ACTIVE._env.device_layer.sim
    model, data = sim.mj_model, sim.mj_data
    data.qpos[:] = sim.data.qpos[0].cpu().numpy()
    mujoco.mj_kinematics(model, data)
    body = model.body("mug/mug").id
    return data.xpos[body].copy(), data.xmat[body].reshape(3, 3).copy()


def _goto(pos, *, pitch, azimuth, roll, **kwargs):
    return dict(
        op="goto",
        x=float(pos[0]),
        y=float(pos[1]),
        z=float(pos[2]),
        pitch=float(pitch),
        azimuth=float(azimuth),
        roll=float(roll),
        **kwargs,
    )


def oracle(poses: Mapping[str, np.ndarray]) -> T.Plan:
    del poses  # Upstream's [x,y,z,yaw] loses the tipped mug's mouth and handle axes.
    pos, rot = mug_pose()
    emb = ACTIVE._emb
    strategy = os.environ.get("RAB_RIGHT_STRATEGY", "best")
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown righting strategy {strategy!r}; choose {STRATEGIES}")
    if strategy == "best":
        strategy = "rim_hook" if emb.name == "so101" else "rim_pull"
    if strategy == "rim_feedback":
        from rab.tasks import right_mug_feedback

        return right_mug_feedback.plan(ACTIVE, pos, rot)
    if strategy == "rim_hybrid":
        # Recovery changes the contact mode instead of repeating the same miss.
        schedule = (
            ("rim_pull", "rim_hook", "rim_pivot")
            if emb.name == "so101"
            else ("rim_pull", "rim_pivot", "rim_lift")
        )
        strategy = schedule[min(ACTIVE.oracle_calls, len(schedule) - 1)]
        ACTIVE.oracle_calls += 1
    if rot[2, 2] >= np.cos(np.pi / 12):
        return [{"op": "gripper", "gripper": 1.0}, {"op": "home"}]

    axis = rot[:, 2]
    phi = float(np.arctan2(axis[1], axis[0]))
    radial = emb.heading(*pos[:2])
    # Cylinder center is offset from the AABB center because the handle is +X.
    cylinder = pos + rot @ np.array([-0.007, -0.0014, 0.0])
    grasp = cylinder - 0.012 * axis
    if strategy == "handle":
        grasp = pos + rot @ np.array([0.026, -0.0014, 0.004])
    is_rim = strategy in ("rim_pivot", "rim_lift", "rim_hook", "rim_pull")
    if is_rim:
        # Pinch the upper lip: one pad inside the mouth and one outside.
        up_side = np.array([0.0, 0.0, 1.0]) - axis[2] * axis
        up_side /= np.linalg.norm(up_side)
        grasp = cylinder + 0.021 * axis + 0.022 * up_side
        yaw = phi - np.pi / 2  # Closing axis along the mug axis.
    else:
        yaw = phi  # Closing axis across the cylinder diameter.
    grasp[2] = max(float(grasp[2]), emb.workspace_min[2] + 0.004)

    # Keep the same canonical wrist branch as the executor's modulo-pi folding.
    jaw_yaw = E.fold(yaw + np.pi / 2)
    rel0 = emb.relative_yaw(jaw_yaw, *grasp[:2], radial)
    rel0 = float(
        np.clip(rel0, emb.wrist_yaw_offset - 1.48, emb.wrist_yaw_offset + 1.48)
    )
    initial_tool = E.tool_rotation(radial, rel0, np.pi / 2)
    if strategy == "body_offset" and emb.name == "so101":
        # SO-101 has a fixed jaw only 17 mm from the reported pinch center.
        # A centered 46 mm body intersects it during descent; offset the pinch
        # so the fixed pad clears the cylinder before the moving jaw closes.
        grasp -= 0.011 * initial_tool[:, 0]
    plan = [
        dict(
            op="grasp",
            x=float(grasp[0]),
            y=float(grasp[1]),
            z=float(grasp[2]),
            yaw=float(yaw),
            pitch_deg=90,
            hover=0.008 if strategy in ("rim_pivot", "rim_hook", "rim_pull") else 0.07,
            speed=0.06,
            verify_grasp=strategy != "rim_hook",
        )
    ]
    if strategy == "rim_hook":
        # With a narrow jaw opening, insert the fingertips at the upper lip
        # and move the hook along the pivot arc without establishing a grasp.
        start = grasp.copy()
        start[2] += 0.05
        plan = [
            _goto(
                start,
                pitch=np.pi / 2,
                azimuth=radial,
                roll=rel0,
                gripper=0.15,
                speed=0.06,
            ),
            _goto(
                grasp,
                pitch=np.pi / 2,
                azimuth=radial,
                roll=rel0,
                gripper=0.15,
                speed=0.035,
            ),
        ]
    if strategy in ("rim_pivot", "rim_hook", "rim_pull"):
        # Rotate about the base/table edge instead of transporting in free space.
        pivot = cylinder - 0.023 * axis
        pivot[2] = 0.0
        start = grasp.copy()
        start[2] += 0.0 if strategy == "rim_hook" else 0.008
        for angle in np.linspace(0, np.pi / 2, 7)[1:]:
            turn = rotation([-np.sin(phi), np.cos(phi), 0], -angle)
            target = pivot + turn @ (start - pivot)
            tool = initial_tool if strategy == "rim_pull" else turn @ initial_tool
            plan.append(_pose_command(target, tool, emb, speed=0.035))
        plan += [
            {"op": "gripper", "gripper": 1.0},
            {"op": "move_by", "dz": 0.08},
            {"op": "home"},
        ]
        return plan

    lifted = grasp.copy()
    lifted[2] += 0.07
    if strategy in ("body_radial", "rim_lift", "body_offset"):
        # First yaw the held mug into the arm's radial plane. This uses the
        # top-down wrist DOF before the pitch change on the five-DOF SO-101.
        candidates = []
        if strategy == "body_offset":
            lifted = np.array([0.285 if emb.name == "so101" else 0.40, 0.0, 0.13])
            radial = 0.0
        for roll in (
            emb.wrist_yaw_offset - 1.48,
            emb.wrist_yaw_offset + 1.48,
            emb.wrist_yaw_offset,
        ):
            for pitch in (
                (np.radians(25),) if strategy == "body_offset" else (0.0, np.pi)
            ):
                goal_tool = E.tool_rotation(radial, roll, pitch)
                new_axis = goal_tool @ initial_tool.T @ axis
                candidates.append((float(new_axis[2]), pitch, roll))
        _, final_pitch, final_roll = max(candidates)
        # Rotate wrist while high, then pitch in small increments.
        plan.append(
            _goto(lifted, pitch=np.pi / 2, azimuth=radial, roll=final_roll, speed=0.05)
        )
        for pitch in np.linspace(np.pi / 2, final_pitch, 7)[1:]:
            plan.append(
                _goto(lifted, pitch=pitch, azimuth=radial, roll=final_roll, speed=0.05)
            )
        final_tool = E.tool_rotation(radial, final_roll, final_pitch)
    else:
        # Direct 90-degree pitch about the world tangent to the mug axis.
        for angle in np.linspace(0, np.pi / 2, 7)[1:]:
            turn = rotation([-np.sin(phi), np.cos(phi), 0], -angle)
            final_tool = turn @ initial_tool
            plan.append(_pose_command(lifted, final_tool, emb, speed=0.05))
    # Predict the held object's translation from its rigid attachment to the
    # pinch. This is a plan only: no welding, teleporting or pose correction.
    new_rot = final_tool @ initial_tool.T @ rot
    new_offset = final_tool @ initial_tool.T @ (pos - grasp)
    bottom = float(np.einsum("ij,kj->ki", new_rot, collision_vertices())[:, 2].min())
    release = lifted.copy()
    release[2] = max(emb.workspace_min[2], -bottom - new_offset[2] + 0.003)
    if strategy in ("body_radial", "rim_lift", "body_offset"):
        plan.append(
            _goto(
                release, pitch=final_pitch, azimuth=radial, roll=final_roll, speed=0.035
            )
        )
    else:
        plan.append(_pose_command(release, final_tool, emb, speed=0.035))
    plan += [
        {"op": "gripper", "gripper": 1.0},
        {"op": "move_by", "dz": 0.08},
        {"op": "home"},
    ]
    return plan


def _pose_command(pos, rot, emb, **kwargs):
    """Express a desired rigid tool frame with public primitive parameters."""
    approach = rot[:, 2]
    h = float(np.arctan2(approach[1], approach[0]))
    pitch = float(np.arctan2(-approach[2], np.hypot(*approach[:2])))
    ortho = np.array(
        [np.sin(pitch) * np.cos(h), np.sin(pitch) * np.sin(h), np.cos(pitch)]
    )
    tangent = np.array([-np.sin(h), np.cos(h), 0])
    roll = float(np.arctan2(rot[:, 0] @ tangent, rot[:, 0] @ ortho))
    return _goto(pos, pitch=pitch, azimuth=h, roll=roll, **kwargs)


TASK = T.Task(
    name=NAME,
    prompt=PROMPT,
    objects=(MUG,),
    success=check_righted,
    success_params={},
    oracle=oracle,
)


def extend_config(cfg: config.Environment, robot: str) -> None:
    # MuJoCo's default warning callback appends to an existing MUJOCO_LOG.TXT
    # in cwd. Keep study warnings on stderr instead, preserving existing files.
    if mujoco.get_mju_user_warning() is None:
        mujoco.set_mju_user_warning(
            lambda message: warnings.warn(message, RuntimeWarning)
        )
    cfg.device_layer.devices["mug"].runtime_params["measurements"] = [
        "xpos",
        "xquat",
        "joint_vel",
    ]
    cfg.device_layer.devices["righting_robot_contact"] = config.Component(
        ContactSensorDevice, fields=("found",)
    ).configure_spec(
        objtype="body",
        objname="robot/.*",
        reftype="body",
        refname="mug/mug",
        reduce="maxforce",
    )
    cfg.device_layer.devices["righting_table_contact"] = config.Component(
        ContactSensorDevice, fields=("found",)
    ).configure_spec(
        objtype="body",
        objname="mug/mug",
        reftype="geom",
        refname="table/table_collision",
        reduce="maxforce",
    )
    cfg.resetters["scatter"] = config.Component(
        RightMugResetter,
        objects=[(MUG.name, MUG.rest_z, True, MUG.radius)],
        robot=robot,
    )
