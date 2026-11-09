"""Five YAM household pilots. Definitions are lazy; importing fetches no assets."""

from dataclasses import dataclass, replace
import os

import mujoco
import numpy as np
import torch
from mujoco_playground.tasks.agentic.tabletop_v0 import reset, task as T
from reaf.simulation.torch import flat_scene as config

from rab import state
from rab.pilot_assets import mesh, primitive, vertices
from rab.pilot_physics import household
from rab.tasks.spill_wipe import SPONGE, _ellipse, _qy, _qz, _quat_mul


@dataclass(frozen=True)
class Scene:
    name: str
    prompt: str
    objects: tuple
    positions: dict
    weights: dict
    containers: tuple = ()


MUG = T.ycb("mug", "025_mug", 0.55)
PLATE = T.ycb("plate", "029_plate", 0.40)
BOWL = T.ycb("bowl", "024_bowl", 0.45)
BANANA = T.ycb("banana", "011_banana", 0.40)
BIN = primitive("bin", "bin", container=True)
RACK = mesh("rack", "gso/Rubbermaid_Large_Drainer/model.xml", 0.42, container=True)
CUP = mesh(
    "cup",
    "gso/BIA_Cordon_Bleu_White_Porcelain_Utensil_Holder_900028/model.xml",
    0.48,
    container=True,
)
WAGON = primitive("wagon", "wagon", container=True)
MARKER = mesh("marker", "mujoco/Marker/marker.xml", 0.85)
CAP = mesh("cap", "mujoco/Marker/cap.xml", 0.85)
SCISSORS = mesh("scissors", "gso/Diamond_Visions_Scissors_Red/model.xml", 0.55)
SCREWDRIVER = mesh(
    "screwdriver", "gso/Craftsman_Grip_Screwdriver_Phillips_Cushion/model.xml", 0.55
)
LAPTOP = mesh("laptop", "mujoco/Laptop/model.xml", 0.32)
BLOCKS = tuple(
    T.Obj(f"block_{i+1}", c, half_size=(0.012,) * 3)
    for i, c in enumerate((T.RED, T.GREEN, T.BLUE, T.YELLOW, (0.75, 0.25, 0.8, 1)))
)

SCENES = {
    "pilot_spill_cleanup": Scene(
        "pilot_spill_cleanup",
        "I knocked over my water. Please tidy up the table.",
        (
            T.ycb("mug_tipped", "025_mug", 0.55),
            T.ycb("mug_other", "025_mug", 0.55),
            SPONGE,
            primitive("napkin_1", "napkin"),
            primitive("napkin_2", "napkin"),
            primitive("keys", "keys"),
            primitive("bin", "wide_bin", container=True),
        ),
        dict(
            mug_tipped=(0.29, 0.015),
            mug_other=(0.41, -0.065),
            sponge=(0.20, -0.12),
            napkin_1=(0.23, 0.16),
            napkin_2=(0.40, 0.12),
            keys=(0.205, -0.025),
            bin=(0.38, -0.18),
        ),
        dict(
            mug_upright=20,
            wiped=30,
            napkins_in_bin=20,
            keys_preserved=10,
            sponge_parked=10,
            mugs_safe=10,
        ),
        ("bin",),
    ),
    "pilot_clear_dishes": Scene(
        "pilot_clear_dishes",
        "We're done eating, please clear the table.",
        (PLATE, BOWL, MUG, BANANA, primitive("phone", "phone"), RACK, BIN),
        dict(
            plate=(0.22, -0.045),
            bowl=(0.21, 0.13),
            mug=(0.40, -0.055),
            banana=(0.285, -0.17),
            phone=(0.20, -0.205),
            rack=(0.37, 0.105),
            bin=(0.38, -0.18),
        ),
        dict(
            plate_in_rack=25,
            bowl_in_rack=25,
            mug_in_rack=25,
            banana_not_discarded=10,
            phone_untouched=10,
            nothing_fell=5,
        ),
        ("rack", "bin"),
    ),
    "pilot_tidy_desk": Scene(
        "pilot_tidy_desk",
        "Please put away the stationery on my desk.",
        (
            MARKER, CAP,
            primitive("pencil", "pencil"),
            primitive("eraser", "eraser"),
            primitive("notes", "notes"),
            SCREWDRIVER,
            primitive("phone", "phone"),
            primitive("coffee", "coffee"),
            primitive("organizer", "organizer", container=True),
            LAPTOP,
        ),
        dict(
            marker=(0.21, 0.025), cap=(0.22, 0.075),
            pencil=(0.265, -0.085), eraser=(0.205, 0.125), notes=(0.21, 0.185),
            screwdriver=(0.265, -0.205), phone=(0.20, -0.135),
            coffee=(0.375, -0.055), organizer=(0.37, 0.11), laptop=(0.40, -0.16),
        ),
        dict(
            marker_in_pen_slot=20, pencil_in_pen_slot=20,
            eraser_in_flat=15, notes_in_flat=15, cap_in_flat=10,
            screwdriver_not_stored=5, phone_not_stored=5,
            coffee_not_stored=5, laptop_untouched=5,
        ),
        ("organizer",),
    ),
    "pilot_toys": Scene(
        "pilot_toys",
        "The kid is done playing, please put the blocks away.",
        (*BLOCKS, WAGON, MUG),
        dict(
            block_1=(0.21, -0.12),
            block_2=(0.22, -0.02),
            block_3=(0.23, 0.08),
            block_4=(0.33, 0.07),
            block_5=(0.41, 0.085),
            wagon=(0.36, -0.10),
            mug=(0.30, 0.207),
        ),
        dict(blocks_in_wagon=90, mug_not_knocked=10),
        ("wagon",),
    ),
    "pilot_breakfast": Scene(
        "pilot_breakfast",
        "Please set up my breakfast.",
        (PLATE, MUG, BANANA),
        dict(plate=(0.38, 0.09), mug=(0.23, 0.13), banana=(0.24, -0.11)),
        dict(plate_at_seat=30, mug_on_right=25, banana_on_plate=35, mug_upright=10),
    ),
}

# Wrap only pilot assets; the legacy spill_wipe object and behavior stay intact.
SCENES = {
    name: replace(scene, objects=tuple(
        household(o, desk=name == "pilot_tidy_desk") for o in scene.objects
    ))
    for name, scene in SCENES.items()
}


def add_breakfast_seat(spec):
    mat = spec.worldbody.add_body(name="placemat", pos=[0.28, -0.02, 0.001])
    mat.add_geom(
        name="placemat_surface",
        type=mujoco.mjtGeom.mjGEOM_BOX,
        size=[0.070, 0.085, 0.001],
        rgba=[0.17, 0.42, 0.45, 1],
        friction=[0.6, 0.005, 0.0001],
        contype=1,
        conaffinity=1,
    )
    chair = spec.worldbody.add_body(name="diner_chair", pos=[0.28, -0.38, 0])
    wood = [0.48, 0.25, 0.11, 1]

    def box(name, size, pos):
        chair.add_geom(
            name=name,
            type=mujoco.mjtGeom.mjGEOM_BOX,
            size=size,
            pos=pos,
            rgba=wood,
            contype=1,
            conaffinity=1,
        )

    box("chair_seat", [0.080, 0.080, 0.012], [0, 0, -0.16])
    box("chair_back", [0.009, 0.080, 0.12], [-0.071, 0, -0.025])
    for i, (x, y) in enumerate(
        ((-0.06, -0.06), (-0.06, 0.06), (0.06, -0.06), (0.06, 0.06))
    ):
        box(f"chair_leg_{i}", [0.010, 0.010, 0.18], [x, y, -0.35])


def check_pilot(features, **_):
    return features["pilot/success_strict"][:, 0] > 0.5


def no_oracle(_):
    raise NotImplementedError(
        "Pilot tasks are evaluated with bridge/LLM agents; no oracle is supplied."
    )


def task_for(name):
    s = SCENES[name]
    return T.Task(s.name, s.prompt, s.objects, check_pilot, {}, no_oracle)


class PilotResetter(reset.ScatterObjectsResetter):
    """Seeded, separated household arrangements; no failed-sampling fallback."""

    def __init__(self, *, scene, robot):
        super().__init__(objects=(), robot=robot)
        self.scene = SCENES[scene]

    def on_reset(self, cfg, env_mask):
        super().on_reset(cfg, env_mask)
        if self._env.nworld != 1:
            raise ValueError("Pilot tasks require the CPU single-world environment")
        seed = int(os.environ.get("RAB_SEED", torch.randint(0, 2**31 - 1, (1,)).item()))
        state.STATE.metadata["seed"] = seed
        rng = np.random.default_rng(seed)
        sim = self._env.device_layer.sim
        scorer = self._env.device_layer.get_device("pilot")
        scene = self.scene
        if scene.name == "pilot_breakfast":
            center = np.array([0.28, -0.02]) + rng.uniform(
                [-0.012, -0.008], [0.012, 0.008]
            )
            yaw = np.pi / 2 + rng.uniform(-0.14, 0.14)
            facing = np.array([np.cos(yaw), np.sin(yaw)])
            for name, xy in (
                ("placemat", center),
                ("diner_chair", center - 0.30 * facing),
            ):
                b = sim.mj_model.body(name).id
                sim.mj_model.body_pos[b, :2] = xy
                sim.mj_model.body_quat[b] = _qz(yaw)
        for obj in scene.objects:
            if obj.name == "laptop":
                continue
            xy = np.array(scene.positions[obj.name], dtype=float)
            jitter = 0.012 if scene.name == "pilot_breakfast" else 0.004
            xy += rng.uniform(-jitter, jitter, 2)
            yaw = rng.uniform(-0.10, 0.10) if obj.name not in scene.containers else 0.0
            quat = _qz(yaw)
            if scene.name == "pilot_tidy_desk" and obj.name == "pencil":
                quat = _qz(np.pi / 2 + yaw)
            if obj.name == "marker":
                quat = _quat_mul(_qz(yaw), _qy(np.pi / 2))
            if obj.name == "mug_tipped":
                quat = _quat_mul(_quat_mul(_qz(np.pi / 2), _qy(np.pi / 2)), _qz(np.pi))
            if scene.name == "pilot_toys" and obj.name == "mug":
                xy[1] = 0.207
                quat = _qz(0)
            if scene.name == "pilot_breakfast":
                quat = _qz(rng.uniform(-np.pi, np.pi))
            rot = np.empty(9)
            mujoco.mju_quat2Mat(rot, quat)
            z = (
                -float(
                    np.einsum("ij,kj->ki", rot.reshape(3, 3), vertices(obj))[:, 2].min()
                )
                + 0.0005
            )
            if scene.name == "pilot_breakfast":
                z += 0.002  # Clear the real 2 mm mat during reset, then settle.
            dev = self._env.device_layer.get_device(obj.name)
            tensor = lambda v: torch.tensor(
                [np.asarray(v).tolist()],
                dtype=torch.float32,
                device=self._env.xpu_device,
            )
            dev.set_pos(env_mask, tensor([*xy, z]))
            dev.set_quat(env_mask, tensor(quat))
            dev.set_vel(env_mask, tensor([0.0] * 6))
        state.STATE.layout = None
        if scene.name == "pilot_spill_cleanup":
            spill = self._env.device_layer.get_device("spill")
            scorer.spill = spill
            adr = sim.mj_model.joint("mug_tipped/mug_tipped_freejoint").qposadr[0]
            xy = sim.data.qpos[0, adr : adr + 2].cpu().numpy()
            mouth = xy + [0, scene.objects[0].half[2]]
            cells = spill.cell_xy
            active = _ellipse(
                cells, mouth + [0, 0.045], np.array([0.0, 1.0]), 0.055, 0.035
            )
            active &= cells[:, 1] > mouth[1] + 0.004
            spill.set_layout(active, "mug_tipped")
            state.STATE.layout = state.Layout(
                cells,
                active.copy(),
                "mug_tipped",
                ("mug_other",),
                mouth,
                np.pi / 2,
                float(scene.objects[0].radius),
            )
        scorer.episode += 1


def extend_config(cfg, robot, *, name):
    if robot != "yam":
        raise ValueError("The household pilots support --robot=yam only")
    from rab.devices.pilots import PilotScorer, PilotSpillDevice

    scene = SCENES[name]
    cfg.base_scene.geom("table/table_collision").friction = [0.6, 0.005, 0.0001]
    if name == "pilot_tidy_desk":
        # Static laptop is attached to the scene, not driven by a FreeDevice.
        del cfg.device_layer.devices["laptop"]
        spec = next(o for o in scene.objects if o.name == "laptop").spec()
        body = spec.worldbody.first_body()
        for joint in list(body.joints):
            spec.delete(joint)
        frame = cfg.base_scene.worldbody.add_frame(
            pos=[*scene.positions["laptop"], LAPTOP.rest_z]
        )
        cfg.base_scene.attach(spec, prefix="laptop/", frame=frame)
    if name in ("pilot_toys", "pilot_breakfast"):
        # The original table's +/-61 cm side edges are beyond YAM's reach.
        # Use a physically matching narrow tabletop, with the edge at y=22 cm.
        table = cfg.base_scene.geom("table/table_collision")
        half_width = 0.22 if name == "pilot_toys" else 0.28
        table.size = [half_width, 0.37, 0.1]
        visual = cfg.base_scene.geom("table/tabletop")
        visual.type = mujoco.mjtGeom.mjGEOM_BOX
        visual.meshname = ""
        visual.size = [half_width, 0.37, 0.012]
        visual.pos = [0, 0, -0.012]
        visual.quat = [1, 0, 0, 0]
        cfg.base_scene.geom("table/tablelegs").rgba = [0, 0, 0, 0]
    if name == "pilot_breakfast":
        add_breakfast_seat(cfg.base_scene)
    if name == "pilot_spill_cleanup":
        cfg.device_layer.devices["spill"] = config.Component(
            PilotSpillDevice,
            mugs=("mug_tipped", "mug_other"),
            sponge="sponge",
            x_range=(0.20, 0.40),
            y_range=(-0.01, 0.17),
            pitch=0.008,
            mug_radius=0.035,
            mug_half_len=0.03,
        )
    cfg.device_layer.devices["pilot"] = config.Component(PilotScorer, scene=name)
    cfg.resetters["scatter"] = config.Component(PilotResetter, scene=name, robot=robot)
