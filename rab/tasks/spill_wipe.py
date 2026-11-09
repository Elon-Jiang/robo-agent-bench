"""spill_wipe: a mug of water was knocked over; wipe the spill with a sponge.

Scene: 3 YCB mugs (one tipped on its side, mouth in a random direction), a
puddle of visual-only liquid cells spreading from the mouth, and a two-layer
kitchen sponge. Success: >=95% of the puddle wiped, no upright mug knocked
over, sponge released. A 0-100 score is computed by `SpillDevice`.
"""

from __future__ import annotations

import dataclasses
import functools
from typing import Any, Mapping

import mujoco
import numpy as np
import torch
from mujoco_playground.tasks.agentic.tabletop_v0 import embodiment as embodiment_lib
from mujoco_playground.tasks.agentic.tabletop_v0 import mdp
from mujoco_playground.tasks.agentic.tabletop_v0 import reset as reset_lib
from mujoco_playground.tasks.agentic.tabletop_v0 import task as T
from reaf.simulation.torch import flat_scene as config

from rab import state
from rab.devices.spill import SpillDevice

NAME = "spill_wipe"
PROMPT = (
    "A mug of water was knocked over and spilled on the table. Use the sponge "
    "to wipe up all of the spilled water without knocking over the other mugs, "
    "then put the sponge down."
)

MUG_SCALE = 0.55
MUG_NAMES = ("mug_a", "mug_b", "mug_c")
SPONGE_HALF = (0.025, 0.015, 0.015)   # 5 x 3 x 3 cm
PAD_T = 0.004                         # green scrub layer thickness
GRID_X, GRID_Y, PITCH = (0.14, 0.34), (-0.18, 0.18), 0.01

GRASP_Z = 0.030       # pinch height when grasping the sponge (jaw tips ~1 cm above table)
WIPE_Z = 0.026        # sponge pressed ~4 mm into the table, jaw tips stay clear
HOVER_Z = 0.10        # transit height: sponge bottom (~pinch-3 cm) clears 6 cm mugs
WIPE_SPEED = 0.06


# --- objects -----------------------------------------------------------------
def _sponge_spec(name: str) -> mujoco.MjSpec:
    spec = mujoco.MjSpec()
    body = spec.worldbody.add_body(name=name)
    body.add_freejoint(name=f"{name}_freejoint")
    hx, hy, hz = SPONGE_HALF
    main_h = hz - PAD_T / 2
    body.add_geom(  # yellow foam body: grippy so the jaws hold it
        name=f"{name}_foam", type=mujoco.mjtGeom.mjGEOM_BOX,
        size=[hx, hy, main_h], pos=[0, 0, PAD_T / 2],
        rgba=[0.96, 0.50, 0.16, 1.0], mass=0.015, friction=[1.5, 0.02, 0.002],
        contype=1, conaffinity=1,
    )
    body.add_geom(  # green scrub pad: soft, slippy, wins friction via priority
        name=f"{name}_pad", type=mujoco.mjtGeom.mjGEOM_BOX,
        size=[hx, hy, PAD_T / 2], pos=[0, 0, -hz + PAD_T / 2],
        rgba=[0.10, 0.36, 0.16, 1.0], mass=0.003, friction=[0.25, 0.005, 0.0001],
        priority=1, solref=[0.02, 1.0], solimp=[0.2, 0.9, 0.01, 0.5, 2.0],
        contype=1, conaffinity=1,
    )
    return spec


MUGS = tuple(T.ycb(n, "025_mug", MUG_SCALE) for n in MUG_NAMES)
SPONGE = T.Obj("sponge", spec_fn=_sponge_spec)
OBJECTS = (*MUGS, SPONGE)


# --- success -----------------------------------------------------------------
def check_spill(features, *, gripper_range, released_norm, min_wiped: float = 0.95):
    wiped = features["spill/wiped_frac"][:, 0] >= min_wiped
    safe = features["spill/knocked"][:, 0] < 0.5
    released = mdp._released(features, features["sponge/xpos"], gripper_range, released_norm)
    return wiped & safe & released


# --- reset -------------------------------------------------------------------
def _quat_mul(a, b):
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return np.array([w1*w2 - x1*x2 - y1*y2 - z1*z2, w1*x2 + x1*w2 + y1*z2 - z1*y2,
                     w1*y2 - x1*z2 + y1*w2 + z1*x2, w1*z2 + x1*y2 - y1*x2 + z1*w2])


def _qz(a):
    return np.array([np.cos(a / 2), 0, 0, np.sin(a / 2)])


def _qy(a):
    return np.array([np.cos(a / 2), 0, np.sin(a / 2), 0])


def _ellipse(cells, c, d, a, b):
    rx, ry = cells[:, 0] - c[0], cells[:, 1] - c[1]
    u = rx * d[0] + ry * d[1]
    v = -rx * d[1] + ry * d[0]
    return (u / a) ** 2 + (v / b) ** 2 <= 1.0


class SpillResetter(reset_lib.ScatterObjectsResetter):
    """Scatter (upstream) -> tip one mug -> grow a puddle from its mouth -> place sponge."""

    def on_reset(self, config: Any, env_mask: torch.Tensor) -> None:
        super().on_reset(config, env_mask)
        sim = self._env.device_layer.sim
        m, d = sim.mj_model, sim.mj_data
        dev = self._env.device_layer.get_device("spill")
        rng = np.random.default_rng(int(torch.randint(0, 2**31 - 1, (1,)).item()))
        emb = self._emb
        base = np.asarray(emb.base_xy)
        cells = dev.cell_xy

        def xy_of(name):
            adr = m.joint(f"{name}/{name}_freejoint").qposadr[0]
            return d.qpos[adr:adr + 2].copy()

        mug_half = MUGS[0].half
        mug_r = float(max(mug_half[:2]))
        reach_ok = (np.hypot(cells[:, 0] - base[0], cells[:, 1] - base[1]) <= emb.max_reach + 0.01) \
            & (cells[:, 0] >= 0.15) & (np.abs(cells[:, 1]) <= 0.17)
        self._place_mugs(env_mask, rng, mug_r)

        # Try mugs in random order; for each, sample spill directions. Prefer a
        # layout where the lying mug clears the upright ones; fall back otherwise.
        best = None
        for allow_clash in (False, True):
            for tipped in rng.permutation(MUG_NAMES):
                best = self._search(rng, str(tipped), xy_of, cells, reach_ok, mug_half, mug_r, allow_clash)
                if best is not None:
                    break
            if best is not None:
                break
        _, phi, mask, mouth, tipped, upright, centre = best
        # Fairness: keep only liquid that some collision-free sponge pose can cover.
        dvec = np.array([np.cos(phi), np.sin(phi)])
        discs = [(*xy_of(k), mug_r + 0.004) for k in upright]
        rects = [(*centre, dvec[0], dvec[1], float(mug_half[2]), float(mug_half[1]))]
        mask &= _wipeable(cells, mask, discs, rects)
        self._finish(env_mask, rng, xy_of, cells, mask, mouth, phi, tipped, upright, centre, mug_half, mug_r)

    def _place_mugs(self, env_mask, rng, mug_r, gap=0.02):
        """Re-place mugs on a grid inside the spawn area with a guaranteed gap.

        Upstream scatter accepts overlaps after 100 failed tries, which happens
        with 3 mugs in SO-101's small spawn rectangle.
        """
        emb = self._emb
        base = np.asarray(emb.base_xy)
        cand = np.stack(np.meshgrid(np.arange(emb.spawn_x[0], emb.spawn_x[1] + 1e-6, 0.005),
                                    np.arange(emb.spawn_y[0], emb.spawn_y[1] + 1e-6, 0.005),
                                    indexing="ij"), -1).reshape(-1, 2)
        cand = cand[np.hypot(cand[:, 0] - base[0], cand[:, 1] - base[1]) <= emb.max_reach]
        placed = []
        n, dt, xpu = self._env.nworld, torch.float32, self._env.xpu_device
        for name in MUG_NAMES:
            ok = np.ones(len(cand), bool)
            for p in placed:
                ok &= np.linalg.norm(cand - p, axis=1) >= 2 * mug_r + gap
            pool = np.flatnonzero(ok)
            xy = cand[rng.choice(pool)] if len(pool) else cand[rng.integers(len(cand))]
            placed.append(xy)
            yaw = rng.uniform(-np.pi, np.pi)
            dev = self._env.device_layer.get_device(name)
            dev.set_pos(env_mask, torch.tensor([[*xy, MUGS[0].rest_z]] * n, dtype=dt, device=xpu))
            dev.set_quat(env_mask, torch.tensor([_qz(yaw)] * n, dtype=dt, device=xpu))

    def _search(self, rng, tipped, xy_of, cells, reach_ok, mug_half, mug_r, allow_clash):
        upright = tuple(n for n in MUG_NAMES if n != tipped)
        centre = xy_of(tipped)
        blocked = np.zeros(len(cells), bool)
        for n in upright:
            blocked |= np.linalg.norm(cells - xy_of(n), axis=1) < mug_r + 0.012
        best = None
        for phi in rng.uniform(-np.pi, np.pi, 24):
            dvec = np.array([np.cos(phi), np.sin(phi)])
            mouth = centre + dvec * mug_half[2]
            mask = np.zeros(len(cells), bool)
            mask |= _ellipse(cells, mouth + dvec * rng.uniform(0.03, 0.05), dvec,
                             rng.uniform(0.05, 0.08), rng.uniform(0.03, 0.045))
            off = np.array([-dvec[1], dvec[0]]) * rng.uniform(-0.02, 0.02)
            mask |= _ellipse(cells, mouth + dvec * rng.uniform(0.07, 0.10) + off, dvec,
                             rng.uniform(0.03, 0.045), rng.uniform(0.025, 0.04))
            # Lying mug footprint (rectangle along dvec) is not wipeable; keep only
            # a 2 mm gap in front of the mouth so the spill visibly flows out of it.
            rx, ry = cells[:, 0] - centre[0], cells[:, 1] - centre[1]
            u = rx * dvec[0] + ry * dvec[1]
            lying = (u > -mug_half[2] - 0.008) & (u < mug_half[2] + 0.006) \
                & (np.abs(-rx * dvec[1] + ry * dvec[0]) < mug_r + 0.008)
            mask &= reach_ok & ~blocked & ~lying
            # Reject directions where the lying mug would hit an upright mug.
            clash = False
            for n_up in upright:
                q = xy_of(n_up) - centre
                t = np.clip(q @ dvec, -mug_half[2], mug_half[2])
                if np.linalg.norm(q - t * dvec) < 2 * mug_r + 0.01:
                    clash = True
            if clash and not allow_clash:
                continue
            if mask.sum() < 30:
                continue
            if best is None or mask.sum() > best[0]:
                best = (int(mask.sum()), phi, mask, mouth, tipped, upright, centre)
        return best

    def _finish(self, env_mask, rng, xy_of, cells, mask, mouth, phi, tipped, upright, centre, mug_half, mug_r):
        emb = self._emb
        dev = self._env.device_layer.get_device("spill")
        # Tip the mug: body +z (mouth) -> horizontal direction phi, random spin.
        quat = _quat_mul(_qz(phi), _quat_mul(_qy(np.pi / 2), _qz(rng.uniform(-np.pi, np.pi))))
        mug_dev = self._env.device_layer.get_device(tipped)
        n = self._env.nworld
        dt = torch.float32
        dev_xpu = self._env.xpu_device
        mug_dev.set_pos(env_mask, torch.tensor([[*centre, mug_half[1] + 0.003]] * n, dtype=dt, device=dev_xpu))
        mug_dev.set_quat(env_mask, torch.tensor([quat] * n, dtype=dt, device=dev_xpu))

        # Sponge: reachable, clear of the puddle and mugs.
        sponge_dev = self._env.device_layer.get_device("sponge")
        # Candidate spots over the whole reachable table; keep those with enough
        # clearance and pick one at random (falls back to the most clear spot).
        wet = cells[mask]
        base = np.asarray(emb.base_xy)
        cand = np.stack(np.meshgrid(np.arange(0.15, 0.31, 0.005), np.arange(-0.17, 0.171, 0.005),
                                    indexing="ij"), -1).reshape(-1, 2)
        cand = cand[np.hypot(cand[:, 0] - base[0], cand[:, 1] - base[1]) <= emb.topdown_reach]
        obst = [(xy_of(k), mug_r) for k in upright] + [(centre, float(mug_half[2]))]
        clear = np.full(len(cand), np.inf)
        for p, rad in obst:
            clear = np.minimum(clear, np.linalg.norm(cand - p, axis=1) - rad - 0.03)
        if len(wet):
            dw = np.min(np.linalg.norm(cand[:, None] - wet[None], axis=2), axis=1)
            clear = np.minimum(clear, dw - 0.04)
        ok = np.flatnonzero(clear > 0.005)
        xy = cand[rng.choice(ok)] if len(ok) else cand[int(np.argmax(clear))]
        yaw = rng.uniform(-np.pi, np.pi)
        sponge_dev.set_pos(env_mask, torch.tensor([[*xy, SPONGE_HALF[2]]] * n, dtype=dt, device=dev_xpu))
        sponge_dev.set_quat(env_mask, torch.tensor([_qz(yaw)] * n, dtype=dt, device=dev_xpu))

        dev.set_layout(mask, tipped)
        state.STATE.layout = state.Layout(
            cell_xy=cells, active=mask.copy(), tipped=tipped, upright=upright,
            mouth_xy=mouth, spill_dir=float(phi), mug_radius=mug_r,
        )
        state.STATE.device = dev


# --- oracle ------------------------------------------------------------------
def _sponge_rect(cx, cy, yaw, n=6):
    """Points on the sponge footprint boundary and interior (long axis along `yaw`)."""
    c, s = np.cos(yaw), np.sin(yaw)
    u, v = np.meshgrid(np.linspace(-SPONGE_HALF[0], SPONGE_HALF[0], n),
                       np.linspace(-SPONGE_HALF[1], SPONGE_HALF[1], n))
    u, v = u.ravel(), v.ravel()
    return np.column_stack([cx + c * u - s * v, cy + s * u + c * v])


def _wipeable(cells, mask, discs, rects):
    """Cells for which at least one collision-free sponge pose covers them."""
    ok = np.zeros(len(cells), bool)
    offs = [(du, dv) for du in (-0.8, 0.0, 0.8) for dv in (-0.6, 0.0, 0.6)]
    for i in np.flatnonzero(mask):
        x, y = cells[i]
        for yaw in (0.0, np.pi / 2, np.pi / 4, -np.pi / 4):
            c, s = np.cos(yaw), np.sin(yaw)
            for du, dv in offs:
                u, v = du * SPONGE_HALF[0], dv * SPONGE_HALF[1]
                cx, cy = x - (u * c - v * s), y - (u * s + v * c)
                if not _sponge_hits_mug(cx, cy, yaw, discs, rects):
                    ok[i] = True
                    break
            if ok[i]:
                break
    return ok


def _sponge_hits_mug(cx, cy, yaw, discs, rects):
    """True if the sponge footprint overlaps an upright-mug disc or the lying-mug rectangle."""
    c, s = np.cos(yaw), np.sin(yaw)
    for (mx, my, r) in discs:
        dx, dy = mx - cx, my - cy
        u, v = c * dx + s * dy, -s * dx + c * dy
        qx = np.clip(u, -SPONGE_HALF[0], SPONGE_HALF[0])
        qy = np.clip(v, -SPONGE_HALF[1], SPONGE_HALF[1])
        if np.hypot(u - qx, v - qy) < r:
            return True
    if rects:
        pts = _sponge_rect(cx, cy, yaw)
        for (ox, oy, ax, ay, hl, hw) in rects:
            rx, ry = pts[:, 0] - ox, pts[:, 1] - oy
            if np.any((np.abs(rx * ax + ry * ay) < hl) & (np.abs(-rx * ay + ry * ax) < hw)):
                return True
    return False


def oracle(poses: Mapping[str, np.ndarray]) -> T.Plan:
    lay = state.STATE.layout
    dev = getattr(state.STATE, "device", None)
    todo = (dev.active & ~dev.wiped) if dev is not None else lay.active.copy()
    sx, sy, _, syaw = (float(v) for v in poses["sponge"])
    plan: T.Plan = [{"op": "grasp", "x": sx, "y": sy, "z": GRASP_Z, "yaw": syaw}]
    if not todo.any():
        return plan + [T._place(sx, sy, 0.035), {"op": "home"}]

    # Obstacles: upright mugs as discs (+4 mm); the lying mug as its footprint
    # rectangle (touching it lightly is allowed: it is already knocked over).
    discs = [(*poses[n][:2], lay.mug_radius + 0.004) for n in lay.upright]
    tx, ty = poses[lay.tipped][:2]
    dvec = np.array([np.cos(lay.spill_dir), np.sin(lay.spill_dir)])
    rects = [(tx, ty, dvec[0], dvec[1], float(MUGS[0].half[2]), float(MUGS[0].half[1]))]

    # Boustrophedon passes, first along X; leftover cells (corners between
    # mugs) get extra passes along Y and the diagonals, like re-wiping at an angle.
    cells = lay.cell_xy
    left = todo.copy()
    for wipe_yaw in (0.0, np.pi / 2, np.pi / 4, -np.pi / 4):
        if left.sum() == 0:
            break
        strokes, covered = _row_strokes(cells, left, wipe_yaw, discs, rects)
        left &= ~covered
        for (ax, ay), (bx, by) in strokes:
            plan += _stroke(ax, ay, bx, by, wipe_yaw)
    # Spot-wipe whatever the row passes missed: a short collision-free stroke
    # centred on a pose that covers each leftover cell.
    for i in np.flatnonzero(left):
        if not left[i]:
            continue
        pose = _cover_pose(cells[i], discs, rects)
        if pose is None:
            continue
        cx, cy, yaw = pose
        c, s = np.cos(yaw), np.sin(yaw)
        a = (cx - 0.006 * c, cy - 0.006 * s)
        b = (cx + 0.006 * c, cy + 0.006 * s)
        if _sponge_hits_mug(*a, yaw, discs, rects) or _sponge_hits_mug(*b, yaw, discs, rects):
            a = b = (cx, cy)
        plan += _stroke(*a, *b, yaw)
        rx, ry = cells[:, 0] - cx, cells[:, 1] - cy
        left &= ~((np.abs(c * rx + s * ry) < SPONGE_HALF[0]) & (np.abs(-s * rx + c * ry) < SPONGE_HALF[1]))
    return plan + [T._place(sx, sy, 0.035), {"op": "home"}]


def _stroke(ax, ay, bx, by, yaw):
    ax, ay, bx, by, yaw = map(float, (ax, ay, bx, by, yaw))
    return [
        {"op": "goto", "x": ax, "y": ay, "z": HOVER_Z, "yaw": yaw},
        {"op": "goto", "x": ax, "y": ay, "z": WIPE_Z, "yaw": yaw, "speed": WIPE_SPEED},
        {"op": "goto", "x": bx, "y": by, "z": WIPE_Z, "yaw": yaw, "speed": WIPE_SPEED},
        {"op": "goto", "x": bx, "y": by, "z": HOVER_Z, "yaw": yaw},
    ]


def _cover_pose(cell, discs, rects):
    """A collision-free sponge pose (cx, cy, yaw) whose footprint covers `cell`, centred if possible."""
    x, y = cell
    for du, dv in [(0, 0), (0.5, 0), (-0.5, 0), (0, 0.5), (0, -0.5), (0.8, 0.6), (-0.8, 0.6),
                   (0.8, -0.6), (-0.8, -0.6)]:
        for yaw in (0.0, np.pi / 2, np.pi / 4, -np.pi / 4):
            c, s = np.cos(yaw), np.sin(yaw)
            u, v = du * SPONGE_HALF[0], dv * SPONGE_HALF[1]
            cx, cy = x - (u * c - v * s), y - (u * s + v * c)
            if not _sponge_hits_mug(cx, cy, yaw, discs, rects):
                return cx, cy, yaw
    return None


def _row_strokes(cells, todo, yaw, discs, rects, step=0.004):
    """Collision-free strokes along direction `yaw` covering `todo` cells.

    Works in a frame where strokes run along +u and rows are spaced along v.
    Returns (strokes as ((x0, y0), (x1, y1)) world points, cells covered).
    """
    c, s = np.cos(yaw), np.sin(yaw)
    u_of = lambda p: p[:, 0] * c + p[:, 1] * s          # noqa: E731
    v_of = lambda p: -p[:, 0] * s + p[:, 1] * c         # noqa: E731
    to_xy = lambda u, v: (float(u * c - v * s), float(u * s + v * c))  # noqa: E731
    wet = cells[todo]
    wu, wv = u_of(wet), v_of(wet)
    cu, cv = u_of(cells), v_of(cells)
    stripe = 2 * SPONGE_HALF[1] * 0.8
    strokes, covered, forward = [], np.zeros(len(cells), bool), True
    for v in np.arange(wv.min() + stripe / 2 - 0.004, wv.max() + stripe / 2, stripe):
        in_row = np.abs(wv - v) <= stripe / 2 + 1e-6
        if not in_row.any():
            continue
        us = np.arange(wu[in_row].min() - 0.008, wu[in_row].max() + 0.008 + 1e-6, step)
        free = [not _sponge_hits_mug(*to_xy(u, v), yaw, discs, rects) for u in us]
        segs, start = [], None
        for i, (u, f) in enumerate(zip(us, free)):
            if f and start is None:
                start = u
            if start is not None and (not f or i == len(us) - 1):
                end = u if f else u - step
                if end - start > 0.003:
                    segs.append((start, end))
                start = None
        if not forward:
            segs = [(b, a) for a, b in reversed(segs)]
        for a, b in segs:
            strokes.append((to_xy(a, v), to_xy(b, v)))
            lo, hi = min(a, b), max(a, b)
            covered |= (cu > lo - SPONGE_HALF[0]) & (cu < hi + SPONGE_HALF[0]) \
                & (np.abs(cv - v) < SPONGE_HALF[1])
        forward = not forward
    return strokes, covered


# --- registration ------------------------------------------------------------
TASK = T.Task(
    name=NAME, prompt=PROMPT, objects=OBJECTS,
    success=check_spill, success_params={},
    oracle=oracle,
)


def extend_config(cfg: config.Environment, robot: str) -> None:
    """Add the spill device and swap the scatter resetter for SpillResetter."""
    emb = embodiment_lib.get(robot)
    cfg.device_layer.devices["spill"] = config.Component(
        SpillDevice, mugs=MUG_NAMES, sponge="sponge",
        x_range=GRID_X, y_range=GRID_Y, pitch=PITCH,
        mug_radius=float(max(MUGS[0].half[:2])), mug_half_len=float(MUGS[0].half[2]),
    )
    cfg.resetters["scatter"] = config.Component(
        SpillResetter,
        objects=[(o.name, o.rest_z, not o.container, o.radius) for o in OBJECTS],
        robot=emb.name,
        margin=0.035,
    )
