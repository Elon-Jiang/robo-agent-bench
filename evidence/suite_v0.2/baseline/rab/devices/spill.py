"""SpillDevice: puddle cells, wipe detection, collateral tracking and scoring.

Wipe rule (adapted from robosuite `Wipe._get_active_markers`): a liquid cell is
wiped when the sponge touches the table, no robot geom touches the table, and
the cell center projects inside the sponge's bottom face within `plane_tol`.
Wiped cells are hidden by setting their alpha to 0 (as robosuite does).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import mujoco
import numpy as np
import torch
from dm_env import specs
from reaf.simulation.torch._src.core import device
from reaf.simulation.torch._src.core import simulator as simulator_module

from rab import state

WATER_RGBA = (0.38, 0.45, 0.52, 1.0)
TABLE_GEOM = "table/table_collision"
ROBOT_PREFIX = "robot/"

# Final-state score weights (0-100). See docs/spill_wipe.md.
W_GRASP, W_WIPE, W_SAFE = 10.0, 70.0, 20.0
KNOCK_PENALTY, MOVE_PENALTY, MOVE_DIST = 10.0, 5.0, 0.03
ARM_TABLE_PENALTY, ARM_TABLE_CAP = 2.0, 10.0
KNOCK_TILT = np.deg2rad(45.0)
LIFT_HEIGHT = 0.02
SETTLE_STEPS = 50  # control steps (dt 0.02 s) -> 1 s

METRICS = (
    "wiped_frac",
    "n_cells",
    "knocked",
    "moved",
    "arm_table_events",
    "sponge_lifted",
    "score",
)


def cell_grid(x_range, y_range, pitch) -> np.ndarray:
    xs = np.arange(x_range[0] + pitch / 2, x_range[1], pitch)
    ys = np.arange(y_range[0] + pitch / 2, y_range[1], pitch)
    return np.stack(np.meshgrid(xs, ys, indexing="ij"), -1).reshape(-1, 2)


class SpillDevice(device.Device):
    def __init__(
        self,
        *,
        name: str = "",
        mugs: Sequence[str],
        sponge: str,
        x_range: tuple[float, float],
        y_range: tuple[float, float],
        pitch: float = 0.01,
        plane_tol: float = 0.006,
        mug_radius: float = 0.035,
        mug_half_len: float = 0.03,
    ):
        super().__init__()
        self._name = name
        self._mugs = tuple(mugs)
        self._sponge = sponge
        self._pitch = pitch
        self._plane_tol = plane_tol
        self.mug_radius = mug_radius
        self.mug_half_len = mug_half_len
        self.cell_xy = cell_grid(x_range, y_range, pitch)
        self._k = len(self.cell_xy)
        self.active = np.zeros(self._k, bool)
        self.wiped = np.zeros(self._k, bool)
        self._reset_tracking()

    @property
    def name(self) -> str:
        return self._name

    # ---- build -------------------------------------------------------------
    def configure_spec(self, spec: mujoco.MjSpec, **_) -> mujoco.MjSpec:
        spec.add_material(
            name=f"{self._name}_water",
            rgba=list(WATER_RGBA),
            specular=0.12,
            shininess=0.4,
            reflectance=0.0,
        )
        body = spec.worldbody.add_body(name=f"{self._name}_cells")
        radius = self._pitch * 0.75  # overlapping discs give rounded, seamless edges
        for i, (x, y) in enumerate(self.cell_xy):
            body.add_geom(
                name=f"{self._name}_cell_{i}",
                type=mujoco.mjtGeom.mjGEOM_CYLINDER,
                size=[radius, 0.0004, 0],
                pos=[float(x), float(y), 0.0006],
                material=f"{self._name}_water",
                rgba=[*WATER_RGBA[:3], 0.0],
                contype=0,
                conaffinity=0,
                group=1,
            )
        return spec

    def initialize(self, simulator: simulator_module.Simulator) -> None:
        m = simulator.mj_model
        self._m = m
        self._cell_ids = np.array(
            [m.geom(f"{self._name}_cell_{i}").id for i in range(self._k)]
        )
        self._table_id = m.geom(TABLE_GEOM).id
        self._robot_geoms = {
            g
            for g in range(m.ngeom)
            if m.geom(g).name.startswith(ROBOT_PREFIX)
            or m.body(m.geom_bodyid[g]).name.startswith(ROBOT_PREFIX)
        }
        sponge_body = m.body(f"{self._sponge}/{self._sponge}").id
        self._sponge_body = sponge_body
        self._sponge_geoms = [
            g
            for g in range(m.ngeom)
            if m.geom_bodyid[g] == sponge_body
            and m.geom_type[g] == mujoco.mjtGeom.mjGEOM_BOX
            and m.geom_contype[g]
        ]
        self._mug_bodies = {n: m.body(f"{n}/{n}").id for n in self._mugs}
        self._out = {k: torch.zeros((simulator.nworld, 1)) for k in METRICS}

    # ---- episode state -----------------------------------------------------
    def _reset_tracking(self):
        self.arm_contact_prev = False
        self.arm_table_events = 0
        self.sponge_lifted = False
        self.start_xy: dict[str, np.ndarray] = {}
        self.tipped: str | None = None
        self.steps = 0
        self.sponge_table_substeps = 0
        self.blocked_by_arm_substeps = 0
        self.arm_table_geoms: dict[str, int] = {}

    def reset(self, simulator, env_mask=None) -> None:
        state.begin_episode()
        self.active[:] = False
        self.wiped[:] = False
        self._reset_tracking()
        self._apply_rgba()

    def set_layout(self, active: np.ndarray, tipped: str) -> None:
        """Called by the resetter after it has placed the objects."""
        self.active[:] = active
        self.wiped[:] = False
        self.tipped = tipped
        self._apply_rgba()

    def on_begin_stepping(self, simulator, env_mask=None) -> None:
        d = simulator.mj_data
        self.start_xy = {n: d.xpos[b][:2].copy() for n, b in self._mug_bodies.items()}
        self._update_metrics(d)

    def _apply_rgba(self) -> None:
        alpha = np.where(self.active & ~self.wiped, WATER_RGBA[3], 0.0)
        self._m.geom_rgba[self._cell_ids, 3] = alpha

    # ---- per-substep -------------------------------------------------------
    def on_substep(self, simulator) -> None:
        d = simulator.mj_data
        sponge_table = arm_table = False
        for i in range(d.ncon):
            c = d.contact[i]
            pair = (c.geom1, c.geom2)
            if self._table_id not in pair:
                continue
            other = pair[1] if pair[0] == self._table_id else pair[0]
            if other in self._sponge_geoms:
                sponge_table = True
            elif other in self._robot_geoms:
                arm_table = True
                gname = (
                    self._m.geom(other).name
                    or self._m.body(self._m.geom_bodyid[other]).name
                )
                self.arm_table_geoms[gname] = self.arm_table_geoms.get(gname, 0) + 1
        if arm_table and not self.arm_contact_prev:
            self.arm_table_events += 1
        self.arm_contact_prev = arm_table
        if d.xpos[self._sponge_body][2] - self._sponge_rest_z() > LIFT_HEIGHT:
            self.sponge_lifted = True
        self.sponge_table_substeps += sponge_table and self.sponge_lifted
        self.blocked_by_arm_substeps += sponge_table and arm_table
        if sponge_table and not arm_table:
            newly = self._cells_under_sponge(d) & self.active & ~self.wiped
            if newly.any():
                self.wiped |= newly
                self._apply_rgba()

    def on_step(self, simulator) -> None:
        d = simulator.mj_data
        # Settling window (first ~1 s): liquid under a mug that rolled/settled
        # onto it is unreachable, so it stops counting; mug start poses are
        # taken at the end of the window.
        self.steps += 1
        if self.steps <= SETTLE_STEPS:
            covered = self._cells_under_mugs(d) & self.active & ~self.wiped
            if covered.any():
                self.active &= ~covered
                self._apply_rgba()
            if self.steps == SETTLE_STEPS:
                self.start_xy = {
                    n: d.xpos[b][:2].copy() for n, b in self._mug_bodies.items()
                }
        self._update_metrics(d)

    def _cells_under_mugs(self, d, margin: float = 0.004) -> np.ndarray:
        hit = np.zeros(self._k, bool)
        x, y = self.cell_xy[:, 0], self.cell_xy[:, 1]
        for b in self._mug_bodies.values():
            R = d.xmat[b].reshape(3, 3)
            c = d.xpos[b]
            axis = R[:, 2]
            if abs(axis[2]) > 0.5:  # upright: disc footprint
                hit |= np.hypot(x - c[0], y - c[1]) < self.mug_radius + margin
            else:  # lying: rectangle along the mug axis
                a = axis[:2] / (np.linalg.norm(axis[:2]) + 1e-9)
                rx, ry = x - c[0], y - c[1]
                u, v = rx * a[0] + ry * a[1], -rx * a[1] + ry * a[0]
                hit |= (np.abs(u) < self.mug_half_len + margin) & (
                    np.abs(v) < self.mug_radius + margin
                )
        return hit

    def _sponge_rest_z(self) -> float:
        g = self._sponge_geoms[0]
        return float(self._m.geom_size[g].min())

    def _cells_under_sponge(self, d) -> np.ndarray:
        hit = np.zeros(self._k, bool)
        p = np.column_stack([self.cell_xy, np.full(self._k, 0.0006)])
        for g in self._sponge_geoms:
            R = d.geom_xmat[g].reshape(3, 3)
            c = d.geom_xpos[g]
            h = self._m.geom_size[g]
            k = int(np.argmax(np.abs(R[2])))  # axis closest to vertical
            n = R[:, k] * np.sign(R[2, k])  # points up
            face = c - n * h[k]  # bottom face center
            a, b = [j for j in range(3) if j != k]
            rel = p - face
            ok = (
                (np.abs(rel @ n) < self._plane_tol)
                & (np.abs(rel @ R[:, a]) < h[a])
                & (np.abs(rel @ R[:, b]) < h[b])
            )
            hit |= ok
        return hit

    # ---- metrics / score ---------------------------------------------------
    def _update_metrics(self, d) -> None:
        n = int(self.active.sum())
        wiped_frac = float((self.wiped & self.active).sum() / n) if n else 1.0
        knocked = moved = 0
        for name, b in self._mug_bodies.items():
            if name == self.tipped:
                continue
            up = d.xmat[b].reshape(3, 3)[:, 2]
            if np.arccos(np.clip(up[2], -1, 1)) > KNOCK_TILT:
                knocked += 1
            elif (
                name in self.start_xy
                and np.linalg.norm(d.xpos[b][:2] - self.start_xy[name]) > MOVE_DIST
            ):
                moved += 1
        # Safety points are only earned once the agent engages (sponge lifted),
        # so a do-nothing policy scores 0 rather than a free 20.
        safe = (
            max(0.0, W_SAFE - KNOCK_PENALTY * knocked - MOVE_PENALTY * moved)
            if self.sponge_lifted
            else 0.0
        )
        score = (
            W_GRASP * self.sponge_lifted
            + W_WIPE * wiped_frac
            + safe
            - min(ARM_TABLE_CAP, ARM_TABLE_PENALTY * self.arm_table_events)
        )
        vals = dict(
            wiped_frac=wiped_frac,
            n_cells=n,
            knocked=knocked,
            moved=moved,
            arm_table_events=self.arm_table_events,
            sponge_lifted=float(self.sponge_lifted),
            score=float(np.clip(score, 0, 100)),
        )
        for k, v in vals.items():
            self._out[k][:] = float(v)
        if n:
            state.STATE.pending = {
                **vals,
                "tipped": self.tipped,
                "sponge_table_substeps": int(self.sponge_table_substeps),
                "blocked_by_arm_substeps": int(self.blocked_by_arm_substeps),
                "arm_table_geoms": dict(self.arm_table_geoms),
            }

    def substep_measurements_spec(self) -> Mapping[str, specs.Array]:
        return {f"{self._name}/{k}": specs.Array((1,), np.float32) for k in METRICS}

    def get_measurements(self, simulator) -> Mapping[str, torch.Tensor]:
        dev = simulator.data.qpos.device
        return {f"{self._name}/{k}": v.to(dev) for k, v in self._out.items()}
