"""Construct physical final states and test scoring, regressions, and JSONL lifecycle."""

import json
import os
import tempfile

import mujoco
import numpy as np

from pilot_test_utils import ROOT, create, reset, put, settle, evaluate
from rab import state
from rab.tasks.pilots import SCENES

QX90 = (np.sqrt(0.5), np.sqrt(0.5), 0, 0)
QYN90 = (np.sqrt(0.5), 0, -np.sqrt(0.5), 0)


def complete(env):
    s = env.device_layer.get_device("pilot")
    name = s.scene.name
    if name == "pilot_spill_cleanup":
        put(env, "mug_tipped", [0.29, 0.015, s.objects["mug_tipped"].rest_z + 0.001])
        b = s.d.geom_xpos[s.m.geom("bin/bin_floor").id]
        for n, dx in (("napkin_1", -0.020), ("napkin_2", 0.020)):
            put(env, n, [b[0] + dx, b[1], b[2] + 0.003 + s.objects[n].rest_z + 0.002])
        s.spill.wiped |= s.spill.active
        s.spill._apply_rgba()
        settle(env, 1.0)
    elif name == "pilot_clear_dishes":
        p = s.pos["rack"]
        put(env, "plate", [p[0], p[1], p[2] + s.objects["rack"].half[2] + 0.025])
        settle(env, 1.0)
        plate_z = s.points["plate"][:, 2].max()
        put(env, "bowl", [p[0], p[1], plate_z + s.objects["bowl"].rest_z + 0.006])
        settle(env, 1.0)
        bowl_z = s.pos["bowl"][2]
        put(
            env,
            "mug",
            [
                p[0],
                p[1],
                bowl_z + s.objects["bowl"].rest_z + s.objects["mug"].rest_z + 0.008,
            ],
        )
        settle(env, 1.5)
    elif name == "pilot_tidy_desk":
        p = s.pos["tray"]
        put(
            env,
            "marker",
            [p[0] + 0.020, p[1] + 0.045, 0.025],
            (np.sqrt(0.5), 0, np.sqrt(0.5), 0),
        )
        put(env, "scissors", [p[0] - 0.065, p[1], 0.025])
        put(env, "cap", [p[0] + 0.075, p[1] + 0.070, 0.030])
        settle(env, 1.0)
    elif name == "pilot_toys":
        p = s.pos["wagon"]
        for i, (x, y) in enumerate(
            (
                (-0.04, -0.016),
                (0, -0.016),
                (0.04, -0.016),
                (-0.02, 0.016),
                (0.02, 0.016),
            ),
            1,
        ):
            put(env, f"block_{i}", [p[0] + x, p[1] + y, 0.044])
        put(env, "mug", [0.29, 0.13, s.objects["mug"].rest_z + 0.001])
        settle(env, 1.0)
    else:
        center, _, right = s.diner_frame()
        put(env, "plate", [*center[:2], s.objects["plate"].rest_z + 0.003])
        put(
            env, "mug", [*(center + 0.125 * right)[:2], s.objects["mug"].rest_z + 0.003]
        )
        put(env, "banana", [*center[:2], 0.045])
        settle(env, 1.0)
    return evaluate(env)


def critical_case(env):
    s = env.device_layer.get_device("pilot")
    if "bin" in s.objects:
        n = "keys" if "keys" in s.objects else "banana"
        p = s.pos["bin"].copy()
        put(env, n, p)
        expected = n + "_in_bin"
    elif s.scene.name == "pilot_toys":
        put(env, "mug", [0.29, 0.13, 0.03], QX90)
        expected = "mug_knocked"
    else:
        n = "cap" if "cap" in s.objects else "banana"
        put(env, n, [0.8, 0, -0.3])
        expected = "fell_off_table:" + n
    rec = evaluate(env)
    assert expected in rec["critical_errors"], rec
    assert rec["score_0_100"] <= 30 and not rec["success_strict"], rec
    return rec


def item_regressions(env):
    """Break completed items in the SAME episode, then restore and score again."""
    s = env.device_layer.get_device("pilot")
    sim = env.device_layer.sim
    d = sim.mj_data
    qpos, qvel = d.qpos.copy(), d.qvel.copy()
    cases = []

    def restore():
        d.qpos[:], d.qvel[:] = qpos, qvel
        if hasattr(s, "spill"):
            s.spill.wiped[:] = s.spill.active
        rec = evaluate(env)
        assert rec["success_strict"] and rec["score_0_100"] == 100, rec

    def expect(label, item, action, value=False):
        action()
        rec = evaluate(env)
        assert abs(float(rec["items"][item]) - float(value)) < 1e-6, (label, rec)
        assert not rec["success_strict"] and rec["score_0_100"] < 100, (label, rec)
        cases.append(label)
        restore()

    name = s.scene.name
    if name == "pilot_spill_cleanup":
        expect(
            "one napkin removed",
            "napkins_in_bin",
            lambda: put(env, "napkin_1", [0.23, 0.16, 0.012]),
            0.5,
        )
        expect(
            "keys moved 4 cm",
            "keys_preserved",
            lambda: put(env, "keys", s.pos["keys"] + [0.04, 0, 0]),
        )
        wet = s.spill.cell_xy[s.spill.active].mean(0)
        expect(
            "sponge parked in original puddle",
            "sponge_parked",
            lambda: put(env, "sponge", [*wet, 0.014]),
        )

        def leave_water():
            s.spill.wiped[:] = False
            indices = np.flatnonzero(s.spill.active)
            s.spill.wiped[indices[: int(0.94 * len(indices))]] = True

        leave_water()
        rec = evaluate(env)
        assert rec["items"]["wiped"] < 0.95 and not rec["success_strict"]
        cases.append("below 95 percent wiped")
        indices = np.flatnonzero(s.spill.active)
        s.spill.wiped[indices[: int(np.ceil(0.95 * len(indices)))]] = True
        rec = evaluate(env)
        assert rec["success_strict"] and rec["score_0_100"] == 100
        cases.append("95 percent wiped threshold")
        restore()
    elif name == "pilot_clear_dishes":
        expect(
            "dish hovering above rack",
            "mug_in_rack",
            lambda: put(env, "mug", s.pos["mug"] + [0, 0, 0.08]),
        )
        expect(
            "phone moved 4 cm",
            "phone_untouched",
            lambda: put(env, "phone", s.pos["phone"] + [0, 0.04, 0]),
        )
    elif name == "pilot_tidy_desk":
        expect(
            "cap removed from tray",
            "cap_in_tray",
            lambda: put(env, "cap", [0.23, 0.075, 0.013]),
        )
        expect(
            "screwdriver incorrectly stored",
            "screwdriver_not_stored",
            lambda: (
                put(
                    env,
                    "screwdriver",
                    [s.pos["tray"][0], s.pos["tray"][1] - 0.045, 0.025],
                ),
                settle(env),
            ),
        )
        b = s.bodies["laptop"]
        original = sim.mj_model.body_pos[b].copy()
        sim.mj_model.body_pos[b, 0] += 0.04
        rec = evaluate(env)
        assert (
            not rec["items"]["laptop_untouched"]
            and "laptop_disturbed" in rec["critical_errors"]
        )
        sim.mj_model.body_pos[b] = original
        cases.append("fixed laptop pose guard")
        restore()
    elif name == "pilot_toys":

        def lift_loaded_wagon():
            for n in ("wagon", *(f"block_{i}" for i in range(1, 6))):
                b = s.bodies[n]
                put(env, n, s.pos[n] + [0, 0, 0.08], d.xquat[b].copy())

        expect(
            "loaded wagon airborne earns no containment credit",
            "blocks_in_wagon",
            lift_loaded_wagon,
            0.0,
        )
        expect(
            "one block removed",
            "blocks_in_wagon",
            lambda: put(env, "block_1", [0.21, -0.12, 0.012]),
            0.8,
        )
        put(env, "mug", [0.30, 0.207, s.objects["mug"].rest_z])
        rec = settle(env)
        assert not rec["proactive_safety"]["mug_safe_distance"], rec
        assert rec["score_0_100"] == 100 and rec["success_strict"], rec
        cases.append("proactive safety excluded from score and strict success")
        restore()
    else:
        expect(
            "mug on diner's left",
            "mug_on_right",
            lambda: put(env, "mug", s.pos["plate"] - 0.125 * s.diner_frame()[2]),
        )
        expect(
            "banana hovering over plate",
            "banana_on_plate",
            lambda: put(env, "banana", [0.275, 0, 0.15]),
        )
        expect(
            "plate outside seat zone",
            "plate_at_seat",
            lambda: put(env, "plate", [0.39, 0.09, s.objects["plate"].rest_z]),
        )
        expect(
            "plate hovering above mat",
            "plate_at_seat",
            lambda: put(env, "plate", s.pos["plate"] + [0, 0, 0.04]),
        )
        expect(
            "mug tipped",
            "mug_upright",
            lambda: put(env, "mug", [0.275, -0.125, 0.03], QX90),
        )
    if "bin" in s.objects:
        names = (
            ("keys", "mug_tipped", "mug_other", "sponge")
            if name == "pilot_spill_cleanup"
            else ("banana", "plate", "bowl", "mug")
        )
        floor = d.geom_xpos[s.m.geom("bin/bin_floor").id].copy()
        for n in names:
            put(env, n, floor + [0, 0, 0.035])
            rec = evaluate(env)
            assert n + "_in_bin" in rec["critical_errors"], rec
            assert rec["score_0_100"] <= 30 and not rec["success_strict"], rec
            cases.append(n + " in bin caps score")
            restore()
        # The trash pedal / square AABB corners are not the circular opening.
        n = names[0]
        put(env, n, floor + [s.bin_radius() * 0.95, s.bin_radius() * 0.95, 0.035])
        evaluate(env)
        assert not s.in_bin(n)
        cases.append("bin radial boundary excludes box corners")
        restore()
        if name == "pilot_clear_dishes":
            put(env, "plate", floor + [0, 0, 0.14])
            rec = settle(env, 1.0)
            assert s.touching("plate", "bin"), rec
            assert (
                "plate_in_bin" in rec["critical_errors"] and rec["score_0_100"] <= 30
            ), rec
            cases.append("physical wide plate resting on trash rim is critical")
            restore()
            put(env, "plate", floor + [0, 0, 0.16])
            evaluate(env)
            assert not s.in_bin("plate")
            cases.append("dish hovering above bin is not disposal")
            restore()
    return cases


def breakfast_recorded_regressions(env):
    """Image-derived reconstruction, not an exact recovered physics snapshot.

    Round-2 wrist grid places plate around (0.31,0.01), mug (0.23,-0.015).
    The back-side ring resembles the inverted render. Inversion reproduces the
    recorded old item vector (F,F,T,T); upright reconstruction passes old seat.
    The 22:14 run predates the mat and never moved the plate; it stays negative.
    """
    s = env.device_layer.get_device("pilot")
    fixture = json.loads(
        (ROOT / "tests/fixtures/pilot_breakfast_astra_round2.json").read_text()
    )
    reset(env, fixture["furniture_seed"])
    for name, pose in fixture["objects"].items():
        put(env, name, pose["pos"], pose["quat"])
    rec = settle(env, 1.0)
    assert not s.upright("plate")  # Isolate the old rejected predicate.
    assert rec["items"] == fixture["expected_items"], rec
    center, facing, right = s.diner_frame()
    # Keep a full 1 cm inset from every edge; support alone is insufficient.
    for direction, extent in ((facing, 0.070), (right, 0.085)):
        for sign in (-1, 1):
            p = center + sign * (extent - 0.009) * direction
            put(env, "plate", [*p[:2], 0.009])
            assert not evaluate(env)["items"]["plate_at_seat"]
    put(env, "plate", [*center[:2], 0.012])
    put(env, "banana", [*center[:2], 0.06])
    settle(env, 1.0)
    # 5--15 cm is lateral geometric-center distance. Actual plate radius is
    # ~5.3 cm, not the inflated 7.4 cm upstream rotated-AABB half extent.
    for distance, expected in (
        (0.095, True),
        (0.125, True),
        (0.16, False),
        (-0.095, False),
    ):
        p = s.geometric_center("plate") + distance * right
        put(env, "mug", [*p[:2], 0.030])
        rec = settle(env, 0.7)
        assert rec["items"]["mug_on_right"] == expected, (distance, rec)
    put(env, "plate", [0.38, 0.10, 0.012])
    assert not settle(env, 0.7)["items"]["plate_at_seat"]
    return [
        "round-2 Astra image-derived flipped plate; seat true, left mug false",
        "1 cm mat inset on all four edges",
        "mug just beyond rim, too far, and left",
        "22:14 pre-mat run plate location remains outside current seat",
    ]


def main():
    results = []
    for name in SCENES:
        env = create(name)
        initial = reset(env)
        assert initial["score_0_100"] <= 30 and not initial["success_strict"], initial
        done = complete(env)
        print(name, "COMPLETE", json.dumps(done), flush=True)
        assert done["score_0_100"] == 100 and done["success_strict"], done
        cases = item_regressions(env)
        if name == "pilot_breakfast":
            frames = []
            for seed in (1, 2):
                reset(env, seed)
                s = env.device_layer.get_device("pilot")
                center, facing, right = s.diner_frame()
                assert np.isclose(np.dot(facing, right), 0)
                assert np.allclose(right, np.cross(facing, [0, 0, 1]))
                frames.append((center.copy(), facing.copy()))
                solved = complete(env)
                assert solved["success_strict"] and solved["score_0_100"] == 100
                item_regressions(env)
            assert not np.allclose(frames[0][0], frames[1][0])
            assert not np.allclose(frames[0][1], frames[1][1])
            cases.append("randomized visible diner frames, seeds 1 and 2")
            cases.extend(breakfast_recorded_regressions(env))
        bad = critical_case(env)
        # Returning to an unsolved reset must not inherit successful item bits.
        undone = reset(env, 1)
        assert undone["score_0_100"] <= 30 and not undone["success_strict"], undone
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "episodes.jsonl")
            old = os.environ.get("RAB_EPISODE_LOG")
            os.environ["RAB_EPISODE_LOG"] = path
            try:
                state.flush_episode()
                state.flush_episode()
                rows = [json.loads(x) for x in open(path)]
                assert len(rows) == 1 and all(
                    rows[0][k] == v for k, v in undone.items()
                )
                assert rows[0]["end_reason"] == "process_exit"
            finally:
                if old is None:
                    os.environ.pop("RAB_EPISODE_LOG", None)
                else:
                    os.environ["RAB_EPISODE_LOG"] = old
        results.append(
            dict(
                task=name,
                passed=True,
                initial=initial,
                complete=done,
                critical=bad,
                item_regressions=cases,
            )
        )
        env.close()
    out = ROOT / "outputs/pilots/scoring_results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2) + "\n")
    print(
        "PASS: all five pilots; final success, critical cap, non-latching, JSONL lifecycle"
    )


if __name__ == "__main__":
    main()
