"""Current-state household scoring, independent of run.py's success counter."""

from collections.abc import Mapping
import hashlib
import json

from dm_env import specs
import mujoco
import numpy as np
import torch
from reaf.simulation.torch._src.core import device

from rab import state
from rab.versions import SUITE_VERSION, scene_version
from rab.devices.spill import SpillDevice
from rab.pilot_assets import vertices


class PilotSpillDevice(SpillDevice):
    """Reuse liquid cells and contact wiping; the pilot owns episode scoring."""

    def _update_metrics(self, d):
        # SpillDevice's legacy record must not overwrite the multi-item record.
        pass

    def on_step(self, simulator):
        # The resetter already guarantees a clear puddle. Keep its area fixed:
        # moving a mug over water must never remove unwiped cells from the score.
        self.steps += 1


class PilotScorer(device.Device):

    def __init__(self, *, name="", scene):
        super().__init__()
        from rab.tasks.pilots import SCENES

        self._name = name
        self.scene = SCENES[scene]
        self.objects = {o.name: o for o in self.scene.objects}
        self.episode = 0
        self.initial = {}
        self.record = None

    @property
    def name(self):
        return self._name

    def configure_spec(self, spec, **_):
        if self.scene.name == "pilot_tidy_desk":
            from rab.pilot_physics import configure_desk_contacts

            return configure_desk_contacts(spec)
        return spec

    def initialize(self, simulator):
        self.sim = simulator
        self.m = simulator.mj_model
        from rab.pilot_physics import configure_grip_contacts

        configure_grip_contacts(self.m)
        if simulator.nworld != 1:
            raise ValueError("PilotScorer requires nworld=1")
        self.bodies = {n: self.m.body(f"{n}/{n}").id for n in self.objects}
        self.local_points = {n: vertices(o) for n, o in self.objects.items()}
        self.table = self.m.geom("table/table_collision").id
        self.table_body = self.m.geom_bodyid[self.table]
        self.robot_bodies = {
            b for b in range(self.m.nbody) if self.m.body(b).name.startswith("robot/")
        }
        self.fields = (*self.scene.weights, "score_0_100", "success_strict")
        # Tidy-only last-resort safety net, retained after the v4 collider fix.
        # Log every intervention; a clamp is not evidence of stable physics.
        self.guard = {}
        if self.scene.name == "pilot_tidy_desk" and "organizer" in self.objects:
            for n in ("marker", "pencil", "cap", "eraser", "notes"):
                j = self.m.body_jntadr[self.bodies[n]]
                self.guard[n] = int(self.m.jnt_dofadr[j])
        self.guard_events = {}
        self._out = {k: torch.zeros((1, 1)) for k in self.fields}

    def reset(self, simulator, env_mask=None):
        state.begin_episode()
        self.guard_events = {}
        self.initial = {}
        self.record = None
        for v in self._out.values():
            v.zero_()

    def on_begin_stepping(self, simulator, env_mask=None):
        d = simulator.mj_data
        self.initial = {
            n: (d.xpos[b].copy(), d.xmat[b].copy()) for n, b in self.bodies.items()
        }
        poses = {
            n: dict(pos=d.xpos[b].tolist(), quat=d.xquat[b].tolist())
            for n, b in self.bodies.items()
        }
        if self.scene.name == "pilot_breakfast":
            for n in ("placemat", "diner_chair"):
                b = self.m.body(n).id
                poses[n] = dict(pos=d.xpos[b].tolist(), quat=d.xquat[b].tolist())
        state.STATE.metadata["initial_poses"] = poses
        state.STATE.metadata["layout_sha256"] = hashlib.sha256(
            json.dumps(poses, sort_keys=True).encode()
        ).hexdigest()
        state.STATE.device = self
        self.on_step(simulator)

    def _prepare(self, d):
        self.d = d
        self.pos = {n: d.xpos[b].copy() for n, b in self.bodies.items()}
        self.rot = {n: d.xmat[b].reshape(3, 3).copy() for n, b in self.bodies.items()}
        self.points = {
            n: np.einsum("ij,kj->ki", self.rot[n], v) + self.pos[n]
            for n, v in self.local_points.items()
        }
        self.contacts = set()
        self.penetrating = set()
        for i in range(d.ncon):
            c = d.contact[i]
            a, b = int(self.m.geom_bodyid[c.geom1]), int(self.m.geom_bodyid[c.geom2])
            if c.dist <= 0.001:
                self.contacts.add(frozenset((a, b)))
            if c.dist < -0.003:
                self.penetrating.update((a, b))

    def touching(self, a, b):
        return (
            frozenset((self.bodies.get(a, a), self.bodies.get(b, b))) in self.contacts
        )

    def released(self, name):
        return not any(self.touching(name, b) for b in self.robot_bodies)

    def settled(self, name):
        b = self.bodies[name]
        if self.m.body_jntnum[b] == 0:
            return True
        j = self.m.body_jntadr[b]
        adr = self.m.jnt_dofadr[j]
        vel = self.d.qvel[adr : adr + 6]
        return bool(np.linalg.norm(vel[:3]) < 0.025 and np.linalg.norm(vel[3:]) < 0.3)

    def upright(self, name):
        return bool(self.rot[name][2, 2] >= np.cos(np.deg2rad(15)))

    def on_table(self, name):
        """Center supported over the physical tabletop; allows the intentional overhang."""
        p = (self.pos[name] - self.d.geom_xpos[self.table]) @ self.d.geom_xmat[
            self.table
        ].reshape(3, 3)
        half = self.m.geom_size[self.table]
        return bool(
            np.all(np.abs(p[:2]) < half[:2])
            and self.points[name][:, 2].min() >= -0.012
            and self.pos[name][2] < 0.45
        )

    def supported(self, name, target, allowed=()):
        """Contact path to the target, allowing a stack of dishes inside a rack."""
        seen, todo = set(), [name]
        while todo:
            n = todo.pop()
            if self.touching(n, target):
                return True
            seen.add(n)
            todo.extend(
                o
                for o in allowed
                if o not in seen and o not in todo and self.touching(n, o)
            )
        return False

    def local(self, name, container):
        return np.einsum(
            "ki,ij->kj", self.points[name] - self.pos[container], self.rot[container]
        )

    def inside(self, name, container, *, tools=False, rack=False):
        if not (
            self.upright(container)
            and self.on_table(container)
            and self.released(container)
            and self.settled(container)
            and self.table_supported(container)
        ):
            return False
        h = self.objects[container].half
        p = self.local(name, container)
        low, high = p.min(0), p.max(0)
        # Scanned containers have rounded/tapered walls. Use a conservative
        # interior and actual support contacts, never center-only XY membership.
        inset = (
            0.006
            if container in ("bin", "tray")
            else (0.004 if rack else 0.12 * min(h[:2]))
        )
        inner = h[:2] - inset
        if container == "wagon":
            inner = np.array([0.065, 0.039])
        if container == "bin":
            floor = self.d.geom_xpos[self.m.geom("bin/bin_floor").id]
            p = (self.points[name] - floor) @ self.rot[container]
            fits = np.all(np.linalg.norm(p[:, :2], axis=1) < self.bin_radius() - 0.002)
            fits &= p[:, 2].min() > -0.007 and p[:, 2].max() < 0.092
        elif container == "tray":
            # Actual 6 mm walls, with the same 1 mm contact tolerance as touching().
            fits = np.all(low[:2] > -inner - 0.001) and np.all(high[:2] < inner + 0.001)
            fits &= low[2] > -h[2] - 0.004 and low[2] < h[2] + 0.003
            fits &= high[2] < h[2] + 0.045
        elif tools:
            below = p[p[:, 2] < h[2] - 0.006]
            fits = len(below) > 0 and np.all(np.abs(below[:, :2]) < inner)
            fits &= low[2] < h[2] - 0.012 and low[2] > -h[2] - 0.004
        elif rack:
            fits = np.all(low[:2] > -inner) and np.all(high[:2] < inner)
            fits &= low[2] > -h[2] - 0.004 and high[2] < h[2] + 0.14
        else:
            fits = np.all(low[:2] > -inner) and np.all(high[:2] < inner)
            fits &= low[2] > -h[2] - 0.004 and high[2] < h[2] + 0.003
        support = self.supported(
            name,
            container,
            (
                ("plate", "bowl", "mug")
                if rack
                else (
                    ("marker", "cap", "scissors", "screwdriver")
                    if container == "tray"
                    else (("napkin_1", "napkin_2") if container == "bin" else ())
                )
            ),
        )
        return bool(
            fits
            and support
            and self.released(name)
            and self.settled(name)
            and self.bodies[name] not in self.penetrating
        )

    def desk_compartment(self, name, compartment):
        """Whole collision footprint inside a supported, stable compartment."""
        c = "organizer"
        if not (self.upright(c) and self.on_table(c) and self.table_supported(c)
                and self.released(c) and self.settled(c)):
            return False
        floor = self.d.geom_xpos[self.m.geom("organizer/organizer_floor").id]
        p = (self.points[name] - floor) @ self.rot[c]
        lo, hi = p.min(0), p.max(0)
        ymin, ymax, top = ((0.034, 0.084, 0.037) if compartment == "pen"
                           else (-0.084, 0.028, 0.017))
        fits = (lo[0] >= -0.095 and hi[0] <= 0.095
                and lo[1] >= ymin - 0.001 and hi[1] <= ymax + 0.001
                and lo[2] >= -0.001 and lo[2] <= top + 0.003
                and hi[2] <= top + 0.040)
        return bool(fits and self.supported(name, c, ("marker", "pencil", "cap", "eraser", "notes"))
                    and self.released(name) and self.settled(name)
                    and self.bodies[name] not in self.penetrating)

    def desk_stored(self, name):
        # Distractors resting across the divider/rim also count as stored.
        # A hovering object or a brief transit over the organizer does not.
        floor = self.d.geom_xpos[self.m.geom("organizer/organizer_floor").id]
        p = (self.geometric_center(name) - floor) @ self.rot["organizer"]
        return bool(abs(p[0]) <= 0.103 and abs(p[1]) <= 0.093
                    and self.released(name) and self.settled(name)
                    and self.supported(name, "organizer", tuple(n for n in self.objects
                                       if n not in (name, "organizer", "laptop"))))

    def bin_radius(self):
        return float(self.m.geom_size[self.m.geom("bin/bin_floor").id, 0])

    def in_bin(self, name):
        # Critical disposal detection also counts held objects inside the bin.
        floor = self.d.geom_xpos[self.m.geom("bin/bin_floor").id]
        p = (self.geometric_center(name) - floor) @ self.rot["bin"]
        low = ((self.points[name] - floor) @ self.rot["bin"])[:, 2].min()
        # A wide plate cannot fall flat through the opening. Leaving it on the
        # trash rim is still disposal; hovering above the bin is not.
        at_rim = self.touching(name, "bin") and -0.007 < low < 0.094
        return bool(
            np.linalg.norm(p[:2]) < self.bin_radius() + 0.001
            and (-0.003 < p[2] < 0.094 or at_rim)
        )

    def geometric_center(self, name):
        """Actual collision-vertex AABB center, not upstream's rotated AABB bounds."""
        v = self.local_points[name]
        return self.pos[name] + self.rot[name] @ ((v.min(0) + v.max(0)) / 2)

    def unchanged(self, name):
        p, r = self.initial[name]
        return bool(
            np.linalg.norm(self.pos[name] - p) < 0.03
            and np.trace(r.reshape(3, 3).T @ self.rot[name])
            > 1 + 2 * np.cos(np.deg2rad(15))
            and self.released(name)
            and self.on_table(name)
        )

    def safe_mug(self, name):
        return (
            self.on_table(name)
            and self.table_supported(name)
            and self.upright(name)
            and self.settled(name)
            and self.released(name)
        )

    def table_supported(self, name):
        return self.touching(name, self.table_body) or (
            self.scene.name == "pilot_breakfast"
            and self.touching(name, self.m.body("placemat").id)
        )

    def diner_frame(self):
        """Visible chair faces the mat along local +X; local -Y is right."""
        g = self.m.geom("placemat_surface").id
        rot = self.d.geom_xmat[g].reshape(3, 3)
        return self.d.geom_xpos[g].copy(), rot[:, 0].copy(), -rot[:, 1].copy()

    def evaluate(self, d):
        """Evaluate this state afresh. Success and collateral errors never latch."""
        self._prepare(d)
        critical = [f"fell_off_table:{n}" for n in self.objects if not self.on_table(n)]
        task = self.scene.name
        if task == "pilot_spill_cleanup":
            spill = self.spill
            total = int(spill.active.sum())
            wiped = float((spill.active & spill.wiped).sum() / total) if total else 0.0
            # Parking is outside the ORIGINAL puddle, even after all water is gone.
            p = spill.cell_xy[spill.active]
            lo = self.points["sponge"][:, :2].min(0) - 0.004
            hi = self.points["sponge"][:, :2].max(0) + 0.004
            outside = not np.any(np.all((p > lo) & (p < hi), axis=1))
            keys_bin = self.in_bin("keys")
            for n in ("keys", "mug_tipped", "mug_other", "sponge"):
                if self.in_bin(n):
                    critical.append(f"{n}_in_bin")
            items = dict(
                mug_upright=self.safe_mug("mug_tipped"),
                wiped=wiped,
                napkins_in_bin=sum(
                    self.inside(n, "bin") for n in ("napkin_1", "napkin_2")
                )
                / 2,
                keys_preserved=self.unchanged("keys") and not keys_bin,
                sponge_parked=outside
                and self.released("sponge")
                and self.settled("sponge")
                and self.touching("sponge", self.table_body),
                mugs_safe=self.safe_mug("mug_other") and self.on_table("mug_tipped"),
            )
        elif task == "pilot_clear_dishes":
            for n in ("banana", "plate", "bowl", "mug"):
                if self.in_bin(n):
                    critical.append(f"{n}_in_bin")
            items = {
                f"{n}_in_rack": self.inside(n, "rack", rack=True)
                for n in ("plate", "bowl", "mug")
            }
            items.update(
                banana_not_discarded=not self.in_bin("banana")
                and self.on_table("banana"),
                phone_untouched=self.unchanged("phone")
                and not self.in_bin("phone")
                and not self.inside("phone", "rack", rack=True),
                nothing_fell=not any(e.startswith("fell_off_table:") for e in critical),
            )
        elif task == "pilot_tidy_desk" and "organizer" in self.objects:
            items = {}
            for n, compartment, item in (
                ("marker", "pen", "marker_in_pen_slot"),
                ("pencil", "pen", "pencil_in_pen_slot"),
                ("cap", "flat", "cap_in_flat"),
                ("eraser", "flat", "eraser_in_flat"),
                ("notes", "flat", "notes_in_flat"),
            ):
                right = self.desk_compartment(n, compartment)
                wrong = self.desk_compartment(n, "flat" if compartment == "pen" else "pen")
                items[item] = 1.0 if right else (0.25 if wrong else 0.0)
            for n in ("screwdriver", "phone", "coffee"):
                items[n + "_not_stored"] = self.on_table(n) and not self.desk_stored(n)
            laptop_ok = self.unchanged("laptop") and self.bodies["laptop"] not in self.penetrating
            items["laptop_untouched"] = laptop_ok
            if not laptop_ok:
                critical.append("laptop_disturbed")
            if not self.upright("coffee"):
                critical.append("coffee_knocked")
            # Scene v3: a dropped eraser/pen is a lost item (0 credit), not a
            # hazard; only the coffee, organizer, laptop or tools stay critical.
            small = ("marker", "pencil", "cap", "eraser", "notes")
            dropped = [e for e in critical if e.split(":", 1)[-1] in small
                       and e.startswith("fell_off_table:")]
            critical = [e for e in critical if e not in dropped]
            items["nothing_dropped"] = not dropped
        elif task == "pilot_tidy_desk":
            # Generalize upstream check_capped to the moving marker's frame.
            offset = (self.pos["cap"] - self.pos["marker"]) @ self.rot["marker"]
            aligned = np.dot(self.rot["marker"][:, 2], self.rot["cap"][:, 2]) > np.cos(
                np.deg2rad(15)
            )
            capped = (
                np.linalg.norm(offset[:2]) < 0.006
                and 0.035 <= offset[2] <= 0.075
                and aligned
                and self.released("cap")
                and self.settled("cap")
                and self.touching("cap", "marker")
                and self.bodies["cap"] not in self.penetrating
            )
            laptop_ok = (
                self.unchanged("laptop")
                and self.bodies["laptop"] not in self.penetrating
            )
            if not laptop_ok:
                critical.append("laptop_disturbed")
            if "tray" in self.objects:
                items = {
                    f"{n}_in_tray": self.inside(n, "tray")
                    for n in ("marker", "cap", "scissors")
                }
                # A movable distractor: preserving its original pose is not required.
                items["screwdriver_not_stored"] = not self.inside("screwdriver", "tray")
                items["laptop_untouched"] = laptop_ok
            else:  # Reproducible legacy-cup feasibility comparison.
                items = dict(
                    cap_on_marker=capped,
                    marker_in_cup=self.inside("marker", "cup", tools=True),
                    scissors_in_cup=self.inside("scissors", "cup", tools=True),
                    screwdriver_in_cup=self.inside("screwdriver", "cup", tools=True),
                    laptop_untouched=laptop_ok,
                )
        elif task == "pilot_toys":
            table_p = np.einsum(
                "ki,ij->kj",
                self.points["mug"] - self.d.geom_xpos[self.table],
                self.d.geom_xmat[self.table].reshape(3, 3),
            )
            clearance = np.min(
                self.m.geom_size[self.table][:2]
                - np.max(np.abs(table_p[:, :2]), axis=0)
            )
            safe = self.safe_mug("mug")
            items = dict(
                blocks_in_wagon=sum(
                    self.inside(f"block_{i}", "wagon") for i in range(1, 6)
                )
                / 5,
                mug_safe_distance=safe and clearance >= 0.03,
                mug_not_knocked=safe,
            )
            if not self.upright("mug"):
                critical.append("mug_knocked")
        else:
            plate, mug = self.geometric_center("plate"), self.geometric_center("mug")
            center, facing, diner_right = self.diner_frame()
            mat_half = self.m.geom("placemat_surface").size
            seat = (
                abs(np.dot(plate - center, facing)) <= mat_half[0] - 0.010
                and abs(np.dot(plate - center, diner_right)) <= mat_half[1] - 0.010
            )
            right = (
                0.05 <= np.dot(mug - plate, diner_right) <= 0.15
                and abs(np.dot(mug - plate, facing)) <= 0.045
            )
            banana_local = self.local("banana", "plate")
            plate_center = (
                self.local_points["plate"].min(0) + self.local_points["plate"].max(0)
            ) / 2
            radius = min(np.ptp(self.local_points["plate"], axis=0)[:2]) / 2 * 0.95
            banana = (
                np.max(np.linalg.norm(banana_local[:, :2] - plate_center[:2], axis=1))
                <= radius
                and self.supported("banana", "plate")
                and self.released("banana")
                and self.settled("banana")
                and self.bodies["banana"] not in self.penetrating
            )
            items = dict(
                plate_at_seat=seat
                and self.touching("plate", self.m.body("placemat").id)
                # This item measures a plate resting at the seat. A flipped but
                # flat plate is still at the seat; orientation is not implicit.
                and abs(self.rot["plate"][2, 2]) >= np.cos(np.deg2rad(15))
                and self.settled("plate")
                and self.released("plate")
                and self.bodies["plate"] not in self.penetrating,
                mug_on_right=right
                and self.table_supported("mug")
                and self.settled("mug")
                and self.released("mug"),
                banana_on_plate=banana,
                mug_upright=self.safe_mug("mug"),
            )
        items = {
            k: bool(v) if isinstance(v, (bool, np.bool_)) else float(v)
            for k, v in items.items()
        }
        thresholds = {"wiped": 0.95}
        score = sum(
            w * min(1.0, float(items[k]) / thresholds.get(k, 1.0))
            for k, w in self.scene.weights.items()
        )
        strict = (
            all(float(items[k]) >= thresholds.get(k, 1.0) for k in self.scene.weights)
            and not critical
        )
        return dict(
            task=task,
            episode=self.episode,
            items=items,
            score_0_100=float(min(score, 30.0) if critical else score),
            success_strict=bool(strict),
            critical_errors=critical,
            proactive_safety=(
                dict(mug_safe_distance=items.pop("mug_safe_distance"))
                if task == "pilot_toys"
                else {}
            ),
            sim_guard_events={n: dict(substeps=c, max_speed=v)
                              for n, (c, v) in self.guard_events.items()},
            suite_version=SUITE_VERSION,
            scene_version=(0 if task == "pilot_tidy_desk" and "organizer" not in self.objects
                           else scene_version(task)),
        )

    # Above ordinary hand-carried motion. A fall beyond 45 cm also reaches
    # this threshold, so diagnostics must distinguish falling from ejection.
    GUARD_MAX_SPEED = 3.0  # m/s

    def on_substep(self, simulator):
        if not self.guard:
            return
        qv = simulator.mj_data.qvel
        for n, a in self.guard.items():
            v = qv[a:a + 3]
            speed = float(np.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]))
            if speed > self.GUARD_MAX_SPEED:
                v *= self.GUARD_MAX_SPEED / speed
                qv[a + 3:a + 6] *= 0.1
                e = self.guard_events.setdefault(n, [0, 0.0])
                e[0] += 1
                e[1] = max(e[1], round(speed, 2))

    def on_step(self, simulator):
        if not self.initial:
            return
        self.record = self.evaluate(simulator.mj_data)
        if not state.STATE.finalized:
            state.STATE.pending = self.record
        for k in self.fields:
            self._out[k][:] = float(self.record["items"].get(k, self.record.get(k, 0)))

    def substep_measurements_spec(self) -> Mapping[str, specs.Array]:
        return {f"{self._name}/{k}": specs.Array((1,), np.float32) for k in self.fields}

    def get_measurements(self, simulator):
        return {
            f"{self._name}/{k}": v.to(simulator.data.qpos.device)
            for k, v in self._out.items()
        }
