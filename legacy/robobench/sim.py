"""Scene construction, physics stepping and offscreen rendering."""

from __future__ import annotations

from pathlib import Path

import mujoco
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
PANDA_XML = ROOT / "third_party/mujoco_menagerie/franka_emika_panda/panda.xml"

CUBE_HALF = 0.02  # 4 cm cube
TARGET_RADIUS = 0.05
TCP_OFFSET = 0.1034  # hand frame -> point between fingertip pads

# Reachable tabletop area (robot base at origin, table surface at z=0).
WORKSPACE_X = (0.35, 0.65)
WORKSPACE_Y = (-0.25, 0.25)


def build_model() -> mujoco.MjModel:
    spec = mujoco.MjSpec.from_file(str(PANDA_XML))
    wb = spec.worldbody

    # Lighting / look.
    spec.visual.headlight.diffuse = [0.6, 0.6, 0.6]
    spec.visual.headlight.ambient = [0.3, 0.3, 0.3]
    wb.add_light(pos=[0.5, 0, 2.5], dir=[0, 0, -1], diffuse=[0.5, 0.5, 0.5], castshadow=True)
    wb.add_light(pos=[1.5, 1.0, 1.5], dir=[-0.6, -0.4, -0.6], diffuse=[0.25, 0.25, 0.25], castshadow=False)

    # Table surface (the floor plane acts as the tabletop).
    spec.add_material(name="table", rgba=[0.62, 0.52, 0.40, 1.0])
    wb.add_geom(
        name="table",
        type=mujoco.mjtGeom.mjGEOM_PLANE,
        size=[1.5, 1.5, 0.05],
        material="table",
    )

    # Red cube with a free joint.
    cube = wb.add_body(name="cube", pos=[0.5, 0.0, CUBE_HALF])
    cube.add_freejoint(name="cube_free")
    cube.add_geom(
        name="cube_geom",
        type=mujoco.mjtGeom.mjGEOM_BOX,
        size=[CUBE_HALF] * 3,
        rgba=[0.85, 0.15, 0.15, 1.0],
        mass=0.05,
        friction=[1.5, 0.01, 0.001],
    )

    # Green target zone: visual only, no collision.
    target = wb.add_body(name="target", pos=[0.5, 0.2, 0.0], mocap=True)
    target.add_geom(
        name="target_geom",
        type=mujoco.mjtGeom.mjGEOM_CYLINDER,
        size=[TARGET_RADIUS, 0.001, 0],
        rgba=[0.15, 0.75, 0.25, 0.6],
        contype=0,
        conaffinity=0,
    )

    # Gravity compensation on the arm so position servos track without sag.
    for i in range(1, 8):
        spec.body(f"link{i}").gravcomp = 1.0
    for b in ("hand", "left_finger", "right_finger"):
        spec.body(b).gravcomp = 1.0

    # Tool center point on the hand.
    hand = spec.body("hand")
    hand.add_site(name="tcp", pos=[0, 0, TCP_OFFSET], size=[0.005, 0, 0], rgba=[1, 1, 0, 0])

    # Cameras. "top" image: up = +x (away from robot), right = -y.
    wb.add_camera(name="top", pos=[0.5, 0.0, 2.0], xyaxes=[0, -1, 0, 1, 0, 0], fovy=22)
    wb.add_camera(name="front", pos=[1.55, 0.0, 0.95], xyaxes=_look_at([1.55, 0.0, 0.95], [0.3, 0.0, 0.15]), fovy=50)
    wb.add_camera(name="side", pos=[0.45, -1.35, 0.75], xyaxes=_look_at([0.45, -1.35, 0.75], [0.4, 0.0, 0.2]), fovy=50)

    return spec.compile()


def _look_at(eye, target, up=(0.0, 0.0, 1.0)) -> list[float]:
    """Return MuJoCo camera xyaxes so the camera at `eye` looks at `target`."""
    eye, target, up = map(np.asarray, (eye, target, up))
    fwd = target - eye
    fwd = fwd / np.linalg.norm(fwd)
    right = np.cross(fwd, up)
    right /= np.linalg.norm(right)
    cam_up = np.cross(right, fwd)
    return [*right.tolist(), *cam_up.tolist()]


class Sim:
    """Thin wrapper around MjModel/MjData plus rendering and frame recording."""

    def __init__(self, width: int = 480, height: int = 360):
        self.model = build_model()
        self.data = mujoco.MjData(self.model)
        self.width, self.height = width, height
        self._renderer: mujoco.Renderer | None = None
        self.recording = False
        self.frames: list[np.ndarray] = []
        self._steps_since_frame = 0
        self.frame_every = 25  # physics steps between recorded frames (~20 fps at dt=0.002)
        self.record_camera = "front"
        self.reset()

    # ---- state -------------------------------------------------------------
    def reset(self) -> None:
        key = self.model.key("home").id
        mujoco.mj_resetDataKeyframe(self.model, self.data, key)
        mujoco.mj_forward(self.model, self.data)

    def step(self, n: int = 1) -> None:
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
            if self.recording:
                self._steps_since_frame += 1
                if self._steps_since_frame >= self.frame_every:
                    self._steps_since_frame = 0
                    self.frames.append(self.render(self.record_camera))

    def body_pos(self, name: str) -> np.ndarray:
        return self.data.body(name).xpos.copy()

    def site_pos(self, name: str) -> np.ndarray:
        return self.data.site(name).xpos.copy()

    # ---- rendering ---------------------------------------------------------
    @property
    def renderer(self) -> mujoco.Renderer:
        if self._renderer is None:
            self._renderer = mujoco.Renderer(self.model, self.height, self.width)
        return self._renderer

    def render(self, camera: str = "front") -> np.ndarray:
        self.renderer.update_scene(self.data, camera=camera)
        return self.renderer.render().copy()
