"""Household masses independent of workspace mesh scaling (SI units)."""

import dataclasses
import mujoco
from mujoco_playground.tasks.agentic.tabletop_v0 import task as T

MASSES = dict(
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
        body.ipos = model.body_ipos[b]
        body.iquat = model.body_iquat[b]
        body.explicitinertial = True
        spec.compiler.inertiafromgeom = mujoco.mjtInertiaFromGeom.mjINERTIAFROMGEOM_AUTO
        # Equal-priority MuJoCo contacts take max friction. Table is set to .6;
        # rubber gripper pads keep their own higher friction on object contacts.
        mu = 1.0 if self.name in ("rack", "bin", "wagon", "tray") else 0.6
        for geom in spec.geoms:
            if geom.contype or geom.conaffinity:
                geom.friction = [mu, 0.005, 0.0001]
                geom.condim = 4
        return spec


def household(obj):
    return HouseholdObject(
        **{f.name: getattr(obj, f.name) for f in dataclasses.fields(T.Obj)}
    )


def configure_grip_contacts(model):
    """Dry rubber pads on household objects; keep the real actuator force limits."""
    for g in range(model.ngeom):
        if model.geom(g).name.startswith("robot/") and "pad" in model.geom(g).name:
            model.geom_friction[g, 0] = 1.2
            model.geom_friction[g, 1] = 0.008
