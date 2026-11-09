"""Closed-loop rim pulling, expressed exclusively with existing primitives."""

import numpy as np


def plan(state, pos, rot):
    """Replan a short pulling arc from the current mug and end-effector poses.

    The original open-loop arc can pass through upright and tip the mug over
    again. This oracle instead checks the pose between 12-degree increments.
    It requires up to 16 oracle turns (each is a short primitive plan).
    """
    from rab.tasks import right_mug as R

    sim = state._env.device_layer.sim
    emb = state._emb
    model, data = sim.mj_model, sim.mj_data
    axis = rot[:, 2]
    tilt = float(np.arccos(np.clip(axis[2], -1, 1)))
    phi = float(np.arctan2(axis[1], axis[0]))
    cylinder = pos + rot @ np.array([-0.007, -0.0014, 0.0])
    site = model.site(emb.ee_site).id
    ee = data.site_xpos[site].copy()
    body = model.body("mug/mug").id
    touching = False
    for c in data.contact:
        bodies = [int(model.geom_bodyid[int(g)]) for g in (c.geom1, c.geom2)]
        if body in bodies:
            other = bodies[1] if bodies[0] == body else bodies[0]
            touching |= model.body(other).name.startswith("robot/")

    if tilt <= np.radians(12):
        state.feedback_grasped = False
        return [
            {"op": "gripper", "gripper": 1.0},
            {"op": "move_by", "dz": 0.08},
            {"op": "home"},
        ]

    if not getattr(state, "feedback_grasped", False) or not touching:
        # Reacquire from the current pose after a miss or a slip.
        up = np.array([0.0, 0.0, 1.0]) - axis[2] * axis
        up /= max(np.linalg.norm(up), 1e-8)
        grasp = cylinder + 0.021 * axis + 0.022 * up
        grasp[2] = max(grasp[2], emb.workspace_min[2] + 0.004)
        state.feedback_yaw = phi - np.pi / 2
        state.feedback_grasped = True
        state.feedback_last_tilt = tilt
        state.feedback_stalls = 0
        return [
            dict(
                op="grasp",
                x=float(grasp[0]),
                y=float(grasp[1]),
                z=float(grasp[2]),
                yaw=state.feedback_yaw,
                pitch_deg=90,
                hover=0.008,
                speed=0.04,
                verify_grasp=True,
            )
        ]

    # A held upper lip can rotate in the finger pads while the tool stays
    # top-down. The base/table contact supplies the pivot reaction force.
    improvement = state.feedback_last_tilt - tilt
    state.feedback_stalls = (state.feedback_stalls + 1) if improvement < 0.015 else 0
    state.feedback_last_tilt = tilt
    if state.feedback_stalls >= 3 or tilt > np.radians(145):
        state.feedback_grasped = False
        return [
            {"op": "gripper", "gripper": 1.0},
            {"op": "move_by", "dz": 0.06},
            {"op": "home"},
        ]
    pivot = cylinder - 0.023 * axis
    pivot[2] = 0.0
    delta = min(np.radians(12), max(0.0, tilt - np.radians(5)))
    turn = R.rotation([-np.sin(phi), np.cos(phi), 0], -delta)
    target = pivot + turn @ (ee - pivot)
    return [
        dict(
            op="goto",
            x=float(target[0]),
            y=float(target[1]),
            z=float(target[2]),
            yaw=state.feedback_yaw,
            pitch_deg=90,
            speed=0.025,
        )
    ]
