"""Household masses independent of workspace mesh scaling (SI units)."""

import dataclasses
import mujoco
import numpy as np
from mujoco_playground.tasks.agentic.tabletop_v0 import task as T

MASSES = dict(
    pencil=0.008,
    eraser=0.015,
    notes=0.030,
    coffee=0.350,
    organizer=0.350,
    mug=0.250,
    plate=0.250,
    bowl=0.200,
    banana=0.120,
    phone=0.180,
    rack=0.900,
    bin=0.400,
    cup=0.300,
    tray=0.300,
    wagon=0.450,
    scissors=0.050,
    screwdriver=0.060,
    marker=0.015,
    cap=0.003,
    laptop=1.300,
    block=0.020,
    sponge=0.015,
    keys=0.030,
    napkin=0.003,
)


# Legacy v3 stationery settings. These alone do not prevent the pinching
# artifact; DeskObject and configure_desk_contacts supply the tidy v4 fix.
SMALL_STATIONERY = ("marker", "pencil", "cap", "eraser", "notes")
SMALL_INERTIA_FLOOR = 2e-6  # kg m^2, ~ a 30 g, 4 cm block
SMALL_SOLREF = [0.02, 1.0]
SMALL_SOLIMP = [0.8, 0.95, 0.003, 0.5, 2]


def kind(name):
    return name.split("_")[0]


class HouseholdObject(T.Obj):
    def spec(self):
        spec = super().spec()
        model = spec.compile()
        body = spec.worldbody.first_body()
        b = model.body(body.name).id
        target = MASSES[kind(self.name)]
        body.mass = target
        body.inertia = model.body_inertia[b] * (target / model.body_mass[b])
        small = kind(self.name) in SMALL_STATIONERY
        if small:
            body.inertia = [max(v, SMALL_INERTIA_FLOOR) for v in body.inertia]
        body.ipos = model.body_ipos[b]
        body.iquat = model.body_iquat[b]
        body.explicitinertial = True
        spec.compiler.inertiafromgeom = mujoco.mjtInertiaFromGeom.mjINERTIAFROMGEOM_AUTO
        # Equal-priority MuJoCo contacts take max friction. Table is set to .6;
        # rubber gripper pads keep their own higher friction on object contacts.
        mu = 1.0 if self.name in ("rack", "bin", "wagon", "tray", "organizer") else 0.6
        for geom in spec.geoms:
            if geom.contype or geom.conaffinity:
                geom.friction = [mu, 0.005, 0.0001]
                geom.condim = 4
                if small:
                    geom.solref = SMALL_SOLREF
                    geom.solimp = SMALL_SOLIMP
        return spec


class DeskObject(HouseholdObject):
    """Tidy-only collision proxies; detailed meshes remain visual geometry."""

    def spec(self):
        spec = super().spec()
        name = kind(self.name)
        if name not in SMALL_STATIONERY:
            return spec
        body = spec.worldbody.first_body()
        if name in ("marker", "cap", "pencil"):
            for geom in body.geoms:
                geom.contype = geom.conaffinity = 0
            if name == "pencil":
                shape = dict(
                    type=mujoco.mjtGeom.mjGEOM_BOX,
                    size=[0.0705, 0.006, 0.004], pos=[0, 0, 0],
                )
                half = np.asarray(shape["size"])
                inertia = MASSES[name] / 3 * (sum(half**2) - half**2)
            else:
                # Bounds of the original centered collision meshes, in metres.
                radius = 0.00925
                half_length = 0.0537 if name == "marker" else 0.0118
                center = -0.00147 if name == "marker" else -0.00022
                capsule = name == "marker"
                shape = dict(
                    type=(mujoco.mjtGeom.mjGEOM_CAPSULE if capsule
                          else mujoco.mjtGeom.mjGEOM_CYLINDER),
                    size=[radius, half_length - radius if capsule else half_length, 0],
                    pos=[0, 0, center],
                )
                # Solid-cylinder approximation, preserving the measured size/mass.
                transverse = MASSES[name] * (3 * radius**2 + 4 * half_length**2) / 12
                axial = MASSES[name] * radius**2 / 2
                inertia = np.array([transverse, transverse, axial])
            body.add_geom(name=f"{self.name}_collision", group=3, mass=0, **shape)
            body.ipos = shape["pos"]
            body.iquat = [1, 0, 0, 0]
            body.inertia = np.maximum(inertia, SMALL_INERTIA_FLOOR)
        for geom in body.geoms:
            if geom.contype or geom.conaffinity:
                if not geom.name:
                    geom.name = f"{self.name}_collision"
                # Pad priority=1 otherwise silently overrides object softness.
                geom.priority = 2
                geom.solref = [0.02, 1.0]
                geom.solimp = [0.8, 0.9, 0.003, 0.5, 2]
                geom.friction = [0.6, 0.005, 0.0001]
                geom.condim = 4
                geom.margin = 0.0005
                if name in ("marker", "cap"):
                    geom.condim = 6
                    geom.friction = [0.6, 0.005, 0.001]
                if name in ("cap", "pencil"):
                    # Rigid plastic/wood must resist squeeze across a narrow
                    # grasp, rather than yielding through the closed fingers.
                    geom.solref = [0.008, 1.0]
                    geom.solimp = [0.95, 0.99, 0.001, 0.5, 2]
        return spec


def configure_desk_contacts(spec):
    """Couple servo/contact integration and preserve rubber-pad friction."""
    # implicitfast solves contact before its implicit actuator damping update.
    # In the recorded pinching state that update creates 117 m/s despite a
    # moderate solved acceleration. Discrete solves servos and contacts jointly.
    spec.option.integrator = mujoco.mjtIntegrator.mjINT_DISCRETE
    robots = [
        geom for geom in spec.geoms
        if geom.name.startswith("robot/") and (geom.contype or geom.conaffinity)
    ]
    for name in SMALL_STATIONERY:
        small = spec.geom(f"{name}/{name}_collision")
        if small is None:
            continue
        for robot in robots:
            pad = "pad" in robot.name
            mu = 1.2 if pad else max(small.friction[0], robot.friction[0])
            torsion = 0.008 if pad else max(small.friction[1], robot.friction[1])
            rolling = max(small.friction[2], robot.friction[2])
            spec.add_pair(
                geomname1=small.name, geomname2=robot.name,
                condim=small.condim, margin=small.margin,
                solref=small.solref, solimp=small.solimp,
                friction=[mu, mu, torsion, rolling, rolling],
            )
    return spec


def household(obj, *, desk=False):
    return (DeskObject if desk else HouseholdObject)(
        **{f.name: getattr(obj, f.name) for f in dataclasses.fields(T.Obj)}
    )


def configure_grip_contacts(model):
    """Dry rubber pads on household objects; keep the real actuator force limits."""
    for g in range(model.ngeom):
        if model.geom(g).name.startswith("robot/") and "pad" in model.geom(g).name:
            model.geom_friction[g, 0] = 1.2
            model.geom_friction[g, 1] = 0.008
