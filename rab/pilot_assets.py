"""Pilot asset loading and primitive objects; upstream assets remain read-only."""

from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache, partial
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import mujoco
import numpy as np
from mujoco_playground.tasks.agentic.tabletop_v0 import task as T

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "third_party/pilot_assets"
REMOTE = (
    "https://raw.githubusercontent.com/google-deepmind/aloha_sim/main/aloha_sim/assets/"
)


def _fetch(relative):
    target = CACHE / relative
    if target.is_file():
        return target
    cached = (
        Path(T.__file__).resolve().parents[4]
        / ".local/github.com_google-deepmind_aloha_sim/HEAD/aloha_sim/assets"
        / relative
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    if cached.is_file():
        shutil.copyfile(cached, target)
    else:
        temporary = target.with_suffix(target.suffix + ".partial")
        subprocess.run(
            [
                "curl",
                "-fL",
                "--retry",
                "5",
                "--retry-all-errors",
                "--connect-timeout",
                "15",
                "--max-time",
                "90",
                "-sS",
                REMOTE + relative,
                "-o",
                str(temporary),
            ],
            check=True,
        )
        temporary.replace(target)
    return target


@lru_cache(None)
def asset_file(relative):
    """Fetch only the XML and its dependencies, retrying transient SSL failures."""
    path = _fetch(relative)
    xml = ET.parse(path).getroot()
    compiler = xml.find("compiler")
    opts = compiler.attrib if compiler is not None else {}
    deps = []
    for tag in ("mesh", "texture"):
        directory = opts.get(tag + "dir", opts.get("assetdir", ""))
        for node in xml.findall(f"./asset/{tag}"):
            if "file" in node.attrib:
                deps.append(
                    str(Path(relative).parent / directory / node.attrib["file"])
                )
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(_fetch, sorted(set(deps))))
    return path


def mesh_spec(name, *, relative, scale=1.0, rotation=None):
    spec = T._raw_asset_spec(name, "local:" + str(asset_file(relative)), scale)
    if relative == "mujoco/Laptop/model.xml":
        # Bake the articulated source into a rigid, open laptop. The upstream
        # generic object centering assumes a single body and cannot center it.
        keyboard = spec.body("laptop_body")
        keyboard.quat = [np.sqrt(0.5), np.sqrt(0.5), 0, 0]
        keyboard.pos = [0, 0, 0]
        spec.joint("screen_hinge").pos *= scale
        model = spec.compile()
        data = mujoco.MjData(model)
        data.qpos[model.joint("screen_hinge").qposadr[0]] = -1.3
        mujoco.mj_forward(model, data)
        root = spec.worldbody.first_body()
        baked = []
        for geom in list(spec.geoms):
            g = model.geom(geom.name).id if geom.name else len(baked)
            b = model.geom_bodyid[g]
            q = np.empty(4)
            mujoco.mju_mulQuat(q, data.xquat[b], geom.quat)
            pos = data.xpos[b] + np.einsum(
                "ij,j->i", data.xmat[b].reshape(3, 3), geom.pos
            )
            baked.append(
                dict(
                    type=geom.type,
                    meshname=geom.meshname,
                    material=geom.material,
                    group=geom.group,
                    contype=geom.contype,
                    conaffinity=geom.conaffinity,
                    pos=pos,
                    quat=q,
                    mass=0.02,
                    friction=[1, 0.01, 0.001],
                )
            )
        for exclude in list(spec.excludes):
            spec.delete(exclude)
        spec.delete(keyboard)
        for i, kwargs in enumerate(baked):
            root.add_geom(name=f"{name}_geom_{i}", **kwargs)
    if rotation is not None:
        q = np.asarray(rotation)
        rot = np.empty(9)
        mujoco.mju_quat2Mat(rot, q)
        for geom in spec.worldbody.first_body().geoms:
            geom.pos = rot.reshape(3, 3) @ geom.pos
            result = np.empty(4)
            mujoco.mju_mulQuat(result, q, geom.quat)
            geom.quat = result
    return spec


def mesh(name, relative, scale=1.0, *, container=False, rotation=None):
    return T.Obj(
        name,
        container=container,
        spec_fn=partial(mesh_spec, relative=relative, scale=scale, rotation=rotation),
    )


def primitive_spec(name, kind):
    spec = mujoco.MjSpec()
    body = spec.worldbody.add_body(name=name)
    body.add_freejoint(name=f"{name}_freejoint")

    def box(size, pos=(0, 0, 0), color=(0.85, 0.85, 0.85, 1), **kwargs):
        body.add_geom(
            type=mujoco.mjtGeom.mjGEOM_BOX,
            size=size,
            pos=pos,
            rgba=color,
            mass=kwargs.pop("mass", 0.015),
            friction=[1, 0.01, 0.001],
            **kwargs,
        )

    if kind in ("bin", "wide_bin"):
        # Polygonal wastebasket, with a conspicuous folded black bag rim.
        # The named floor also defines the interior frame (decorative parts
        # must not shift containment through the upstream AABB centering).
        body.add_geom(
            name="bin_floor",
            type=mujoco.mjtGeom.mjGEOM_CYLINDER,
            size=[0.043, 0.003, 0],
            pos=[0, 0, -0.042],
            rgba=[0.045, 0.045, 0.05, 1],
            mass=0.20,
        )
        for i, a in enumerate(np.linspace(0, 2 * np.pi, 16, endpoint=False)):
            q = [np.cos(a / 2), 0, 0, np.sin(a / 2)]
            box(
                [0.0025, 0.009, 0.044],
                [0.044 * np.cos(a), 0.044 * np.sin(a), 0],
                [0.12, 0.13, 0.14, 1],
                quat=q,
                mass=0.008,
            )
            box(
                [0.004, 0.010, 0.004],
                [0.044 * np.cos(a), 0.044 * np.sin(a), 0.043],
                [0.025, 0.025, 0.03, 1],
                quat=q,
                mass=0.001,
            )
            # Uneven hanging folds remain outside the usable opening.
            if i % 3 == 0:
                box(
                    [0.0015, 0.006, 0.010],
                    [0.047 * np.cos(a), 0.047 * np.sin(a), 0.032],
                    [0.035, 0.035, 0.04, 1],
                    quat=q,
                    mass=0.0005,
                )
        for x in (-0.025, 0.025):
            for y in (-0.025, 0.025):
                box(
                    [0.009, 0.009, 0.003],
                    [x, y, -0.048],
                    [0.025, 0.025, 0.03, 1],
                    mass=0.006,
                )
        # Small metal pedal at the robot-facing side; no articulated mechanism.
        box(
            [0.010, 0.016, 0.003], [-0.046, 0, -0.043], [0.42, 0.44, 0.46, 1], mass=0.01
        )
    elif kind == "organizer":
        # A real horizontal pen trough beside a shallow small-item compartment.
        # Coordinates below use the named floor, so automatic mesh centering
        # cannot silently move the scoring frame. Dimensions: 20 x 18 x 4 cm.
        wood = [0.48, 0.32, 0.18, 1]
        box([0.100, 0.090, 0.003], [0, 0, 0.003], wood, name="organizer_floor")
        for x in (-0.097, 0.097):
            box([0.003, 0.090, 0.010], [x, 0, 0.010], wood)
            box([0.003, 0.028, 0.020], [x, 0.062, 0.020], wood)
        box([0.094, 0.003, 0.010], [0, -0.087, 0.010], wood)
        for y in (0.031, 0.087):
            box([0.094, 0.003, 0.020], [0, y, 0.020], wood)
    elif kind == "pencil":
        # Flat carpenter pencil: 14 x 1.2 x 0.8 cm, sharpened graphite tip.
        box([0.063, 0.006, 0.004], [-0.007, 0, 0], [0.95, 0.66, 0.08, 1])
        body.add_geom(type=mujoco.mjtGeom.mjGEOM_BOX, size=[0.007, 0.004, 0.003],
                      pos=[0.063, 0, 0], rgba=[0.70, 0.52, 0.30, 1], mass=0.001)
        box([0.002, 0.002, 0.002], [0.069, 0, 0], [0.12, 0.12, 0.13, 1])
    elif kind == "eraser":
        box([0.020, 0.011, 0.007], color=[0.95, 0.55, 0.53, 1])
        box([0.010, 0.0112, 0.0072], color=[0.22, 0.38, 0.63, 1],
            contype=0, conaffinity=0)
    elif kind == "notes":
        box([0.025, 0.025, 0.006], color=[0.98, 0.89, 0.20, 1])
        for z in (-0.003, 0, 0.003):
            box([0.0251, 0.0251, 0.0002], [0, 0, z], [0.79, 0.70, 0.19, 1],
                contype=0, conaffinity=0)
    elif kind == "coffee":
        # 7 cm diameter, 8 cm tall ceramic mug with a visible drink surface.
        body.add_geom(type=mujoco.mjtGeom.mjGEOM_CYLINDER,
                      size=[0.035, 0.003, 0], pos=[0, 0, -0.037],
                      rgba=[0.86, 0.89, 0.93, 1], mass=0.05)
        for a in np.linspace(0, 2 * np.pi, 24, endpoint=False):
            box([0.002, 0.0046, 0.037], [0.033*np.cos(a), 0.033*np.sin(a), 0],
                [0.86, 0.89, 0.93, 1], quat=[np.cos(a/2), 0, 0, np.sin(a/2)])
        for z in (-0.022, 0.022):
            box([0.012, 0.004, 0.004], [0.044, 0, z], [0.86, 0.89, 0.93, 1])
        box([0.004, 0.004, 0.025], [0.053, 0, 0], [0.86, 0.89, 0.93, 1])
        body.add_geom(type=mujoco.mjtGeom.mjGEOM_CYLINDER,
                      size=[0.030, 0.001, 0], pos=[0, 0, 0.023],
                      rgba=[0.16, 0.065, 0.025, 1], mass=0.001,
                      contype=0, conaffinity=0)
    elif kind == "tray":
        # A low, broad 22 x 19 cm desk organizer; tools can lie flat.
        color = [0.48, 0.32, 0.18, 1]
        box([0.110, 0.095, 0.003], [0, 0, -0.009], color, mass=0.20)
        for x in (-0.107, 0.107):
            box([0.003, 0.095, 0.012], [x, 0, 0], color, mass=0.015)
        for y in (-0.092, 0.092):
            box([0.104, 0.003, 0.012], [0, y, 0], color, mass=0.015)
        for x in (-0.075, 0.075):
            for y in (-0.060, 0.060):
                box(
                    [0.012, 0.010, 0.002],
                    [x, y, -0.014],
                    [0.08, 0.08, 0.08, 1],
                    mass=0.005,
                )
    elif kind == "wagon":
        wood = [0.75, 0.41, 0.14, 1]
        box([0.070, 0.044, 0.003], [0, 0, -0.010], wood)
        for x in (-0.070, 0.070):
            box([0.003, 0.047, 0.019], [x, 0, 0.006], wood)
        for y in (-0.044, 0.044):
            box([0.067, 0.003, 0.019], [0, y, 0.006], wood)
        for x in (-0.050, 0.050):
            for y in (-0.050, 0.050):
                body.add_geom(
                    type=mujoco.mjtGeom.mjGEOM_CYLINDER,
                    size=[0.016, 0.006, 0],
                    pos=[x, y, -0.020],
                    quat=[np.sqrt(0.5), np.sqrt(0.5), 0, 0],
                    mass=0.025,
                    rgba=[0.16, 0.18, 0.20, 1],
                    friction=[1, 0.01, 0.001],
                    contype=0,
                    conaffinity=0,
                )
                # Parked wagon: decorative wheels over broad rubber feet.
                box(
                    [0.012, 0.010, 0.004],
                    [x, np.sign(y) * 0.043, -0.032],
                    [0.16, 0.18, 0.20, 1],
                    mass=0.003,
                )
    elif kind == "keys":
        for a in np.linspace(0, 2 * np.pi, 12, endpoint=False):
            box(
                [0.0018, 0.0033, 0.0018],
                [0.011 * np.cos(a), 0.011 * np.sin(a), 0],
                [0.7, 0.72, 0.75, 1],
                quat=[np.cos(a / 2), 0, 0, np.sin(a / 2)],
                mass=0.001,
            )
        box([0.014, 0.003, 0.0018], [0.023, 0, 0], [0.65, 0.67, 0.7, 1], mass=0.004)
        for x in (0.020, 0.028, 0.033):
            box([0.002, 0.002, 0.0018], [x, 0.004, 0], [0.65, 0.67, 0.7, 1], mass=0.001)
    elif kind == "phone":
        box([0.040, 0.021, 0.0035], color=[0.09, 0.10, 0.12, 1])
        box(
            [0.036, 0.018, 0.0003],
            [0, 0, 0.0037],
            [0.17, 0.25, 0.32, 1],
            contype=0,
            conaffinity=0,
        )
    elif kind == "napkin":
        box([0.019, 0.016, 0.014], color=[0.96, 0.95, 0.91, 1], mass=0.002)
        for size, pos, quat in (
            ([0.011, 0.009, 0.008], [0.009, 0.005, 0.010], [1, 0.3, 0.2, 0.1]),
            ([0.010, 0.010, 0.008], [-0.010, -0.004, 0.006], [1, -0.2, 0.3, 0.2]),
        ):
            box(size, pos, [0.93, 0.93, 0.90, 1], quat=quat, mass=0.002)
    else:
        raise ValueError(kind)
    if kind == "wide_bin":
        # Spill-cleanup desk bin: ~11.5 cm clear opening, original 9 cm rim.
        for geom in body.geoms:
            geom.pos[:2] *= 1.4
            geom.size[:2] *= 1.4
    return spec


def primitive(name, kind, *, container=False):
    return T.Obj(name, container=container, spec_fn=partial(primitive_spec, kind=kind))


@lru_cache(None)
def vertices(obj):
    """Collision geometry vertices in the centered object frame."""
    model = obj.spec().compile()
    points = []
    signs = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)])
    for g in range(model.ngeom):
        if not (model.geom_contype[g] or model.geom_conaffinity[g]):
            continue
        if model.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
            mesh_id = model.geom_dataid[g]
            start, n = model.mesh_vertadr[mesh_id], model.mesh_vertnum[mesh_id]
            v = model.mesh_vert[start : start + n]
        else:
            v = model.geom_aabb[g, :3] + signs * model.geom_aabb[g, 3:]
        rot = np.empty(9)
        mujoco.mju_quat2Mat(rot, model.geom_quat[g])
        points.append(np.einsum("ij,kj->ki", rot.reshape(3, 3), v) + model.geom_pos[g])
    return np.concatenate(points)
