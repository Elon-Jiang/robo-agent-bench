"""Inverse kinematics + Cartesian motion + gripper control for the Panda.

The Panda actuators in Menagerie are joint-position servos, so control works as:
  target TCP pose --(damped least-squares IK)--> joint targets --> ctrl
Straight-line Cartesian motions are split into small waypoints, each solved
with IK warm-started from the previous solution.
"""

from __future__ import annotations

from dataclasses import dataclass

import mujoco
import numpy as np

from .sim import Sim

ARM_JOINTS = [f"joint{i}" for i in range(1, 8)]
GRIPPER_OPEN = 255.0
GRIPPER_CLOSED = 0.0


def down_rotation(yaw: float) -> np.ndarray:
    """TCP orientation pointing straight down, rotated by `yaw` about world z.

    yaw=0 means the fingers close along the world y axis.
    """
    c, s = np.cos(yaw), np.sin(yaw)
    rz = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    return rz @ np.diag([1.0, -1.0, -1.0])


@dataclass
class MoveResult:
    reached: bool
    position_error_m: float
    ik_converged: bool
    collisions: list[str]

    def as_dict(self) -> dict:
        return {
            "reached": self.reached,
            "position_error_m": round(self.position_error_m, 4),
            "ik_converged": self.ik_converged,
            "collisions": self.collisions,
        }


class Controller:
    def __init__(self, sim: Sim):
        self.sim = sim
        m = sim.model
        self.qadr = np.array([m.joint(j).qposadr[0] for j in ARM_JOINTS])
        self.dofadr = np.array([m.joint(j).dofadr[0] for j in ARM_JOINTS])
        self.lo = np.array([m.joint(j).range[0] for j in ARM_JOINTS])
        self.hi = np.array([m.joint(j).range[1] for j in ARM_JOINTS])
        self.tcp_id = m.site("tcp").id
        self.q_rest = m.key("home").qpos[self.qadr].copy()
        self._ik_data = mujoco.MjData(m)  # scratch data for IK, never stepped
        self.gripper_act = m.actuator("actuator8").id
        self.robot_geoms = self._robot_geom_ids()
        self.table_geom = m.geom("table").id

    # ---- IK ----------------------------------------------------------------
    def solve_ik(self, pos, rot, q_init, iters: int = 200, tol: float = 1e-4):
        m, d = self.sim.model, self._ik_data
        d.qpos[:] = self.sim.data.qpos
        q = np.array(q_init, dtype=float)
        jacp = np.zeros((3, m.nv))
        jacr = np.zeros((3, m.nv))
        damping = 1e-2
        for _ in range(iters):
            d.qpos[self.qadr] = q
            mujoco.mj_kinematics(m, d)
            mujoco.mj_comPos(m, d)
            cur_p = d.site_xpos[self.tcp_id]
            cur_r = d.site_xmat[self.tcp_id].reshape(3, 3)
            err_p = pos - cur_p
            # Orientation error as axis-angle (small-angle approx via cross products).
            err_r = 0.5 * sum(np.cross(cur_r[:, i], rot[:, i]) for i in range(3))
            err = np.concatenate([err_p, err_r])
            if np.linalg.norm(err_p) < tol and np.linalg.norm(err_r) < 1e-3:
                return q, True
            mujoco.mj_jacSite(m, d, jacp, jacr, self.tcp_id)
            J = np.vstack([jacp[:, self.dofadr], jacr[:, self.dofadr]])
            JJt = J @ J.T + damping**2 * np.eye(6)
            dq = J.T @ np.linalg.solve(JJt, err)
            # Null-space pull toward the rest posture keeps the elbow sensible.
            J_pinv = J.T @ np.linalg.inv(JJt)
            dq += (np.eye(7) - J_pinv @ J) @ (0.05 * (self.q_rest - q))
            q = np.clip(q + np.clip(dq, -0.2, 0.2), self.lo, self.hi)
        return q, False

    # ---- motion ------------------------------------------------------------
    def tcp_pose(self) -> tuple[np.ndarray, np.ndarray]:
        d = self.sim.data
        return d.site_xpos[self.tcp_id].copy(), d.site_xmat[self.tcp_id].reshape(3, 3).copy()

    def move_to(self, pos, yaw: float = 0.0, speed: float = 0.25, step_m: float = 0.01) -> MoveResult:
        """Move the TCP in a straight line to `pos` with a downward orientation."""
        pos = np.asarray(pos, dtype=float)
        rot = down_rotation(yaw)
        start, _ = self.tcp_pose()
        dist = float(np.linalg.norm(pos - start))
        n_wp = max(1, int(np.ceil(dist / step_m)))
        q = self.sim.data.qpos[self.qadr].copy()
        q_start = q.copy()
        waypoints, converged = [], True
        for k in range(1, n_wp + 1):
            q, ok = self.solve_ik(start + (pos - start) * k / n_wp, rot, q)
            converged &= ok
            waypoints.append(q.copy())
        # Orientation-only changes (e.g. yaw) need a joint-space blend too.
        if dist < step_m:
            waypoints = [q_start + (q - q_start) * a for a in np.linspace(0.1, 1.0, 10)]

        dt = self.sim.model.opt.timestep
        steps_per_wp = max(1, int(round((max(dist, 0.02) / speed) / dt / len(waypoints))))
        collisions: set[str] = set()
        for wq in waypoints:
            self.sim.data.ctrl[:7] = wq
            for _ in range(steps_per_wp):
                self.sim.step()
                collisions |= self._robot_collisions()
        self._settle(q, max_steps=600)
        collisions |= self._robot_collisions()
        err = float(np.linalg.norm(self.tcp_pose()[0] - pos))
        return MoveResult(err < 0.01, err, converged, sorted(collisions))

    def set_joints(self, q, steps: int = 1000) -> None:
        self.sim.data.ctrl[:7] = q
        self._settle(np.asarray(q), max_steps=steps)

    def _settle(self, q_target, max_steps: int) -> None:
        for _ in range(max_steps):
            self.sim.step()
            qd = self.sim.data.qvel[self.dofadr]
            qerr = self.sim.data.qpos[self.qadr] - q_target
            if np.abs(qd).max() < 0.02 and np.abs(qerr).max() < 0.005:
                break

    # ---- gripper -----------------------------------------------------------
    def gripper_width(self) -> float:
        d, m = self.sim.data, self.sim.model
        return float(d.qpos[m.joint("finger_joint1").qposadr[0]] + d.qpos[m.joint("finger_joint2").qposadr[0]])

    def set_gripper(self, open_: bool, steps: int = 400) -> float:
        self.sim.data.ctrl[self.gripper_act] = GRIPPER_OPEN if open_ else GRIPPER_CLOSED
        self.sim.step(steps)
        return self.gripper_width()

    # ---- contacts ----------------------------------------------------------
    def _robot_geom_ids(self) -> set[int]:
        m = self.sim.model
        ids = set()
        for g in range(m.ngeom):
            body = m.body(m.geom_bodyid[g]).name
            if body.startswith("link") or body in ("hand", "left_finger", "right_finger"):
                ids.add(g)
        return ids

    def _robot_collisions(self) -> set[str]:
        """Robot-vs-table contacts (robot-vs-cube is expected when grasping)."""
        d, m = self.sim.data, self.sim.model
        hits = set()
        for i in range(d.ncon):
            c = d.contact[i]
            pair = {c.geom1, c.geom2}
            if self.table_geom in pair and pair & self.robot_geoms:
                other = (pair - {self.table_geom}).pop()
                body = m.body(m.geom_bodyid[other]).name
                if body != "link0":  # base always touches the table
                    hits.add(f"{body}-table")
        return hits
