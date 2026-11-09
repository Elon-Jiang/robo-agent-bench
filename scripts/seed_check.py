"""Verify deterministic reset poses and compare v0.1's unchanged scenes exactly."""

import argparse
import json
import os
from pilot_test_utils import ROOT, create, settle
from rab import state
from rab.tasks.pilots import SCENES


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", default="outputs/suite_v0.1/seeds.json")
    ap.add_argument(
        "--baseline-snapshots", help="Optional full seeded poses/scores baseline"
    )
    ap.add_argument("--output", default="outputs/suite_v0.2/seeds.json")
    args = ap.parse_args()
    baseline = {r["task"]: r for r in json.loads((ROOT / args.baseline).read_text())}
    full = (
        json.loads((ROOT / args.baseline_snapshots).read_text())
        if args.baseline_snapshots
        else []
    )
    rows = []
    for task in SCENES:
        env = create(task)
        snapshots = []
        for seed in (0, 0, 1):
            os.environ["RAB_SEED"] = str(seed)
            env.reset()
            snapshots.append(json.loads(json.dumps(state.STATE.metadata)))
        assert snapshots[0]["initial_poses"] == snapshots[1]["initial_poses"], task
        assert snapshots[0]["layout_sha256"] != snapshots[2]["layout_sha256"], task
        row = dict(
            task=task,
            passed=True,
            seed0_hash=snapshots[0]["layout_sha256"],
            seed1_hash=snapshots[2]["layout_sha256"],
        )
        if task != "pilot_tidy_desk":
            assert row["seed0_hash"] == baseline[task]["seed0_hash"], task
            assert row["seed1_hash"] == baseline[task]["seed1_hash"], task
            count = 0
            for old in (r for r in full if r["task"] == task):
                os.environ["RAB_SEED"] = str(old["seed"])
                env.reset()
                for k in ("initial_poses", "layout_sha256"):
                    assert state.STATE.metadata[k] == old["metadata"][k], (
                        task,
                        old["seed"],
                        k,
                    )
                rec = settle(env)
                for k in (
                    "items",
                    "score_0_100",
                    "success_strict",
                    "critical_errors",
                    "proactive_safety",
                ):
                    assert rec[k] == old["record"][k], (task, old["seed"], k)
                count += 1
            row.update(v01_hashes_identical=True, full_pose_score_comparisons=count)
        rows.append(row)
        env.close()
    out = ROOT / args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2) + "\n")
    print(
        "PASS all five deterministic; four unchanged scenes exactly match v0.1",
        flush=True,
    )


if __name__ == "__main__":
    main()
