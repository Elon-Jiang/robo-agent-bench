"""Verify production RAB_SEED reset poses across all five scenes, without cameras."""

import json
import os
from pilot_test_utils import ROOT, create
from rab import state
from rab.tasks.pilots import SCENES

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
    rows.append(
        dict(
            task=task,
            passed=True,
            seed0_hash=snapshots[0]["layout_sha256"],
            seed1_hash=snapshots[2]["layout_sha256"],
        )
    )
    env.close()
out = ROOT / "outputs/suite_v0.1/seeds.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(rows, indent=2) + "\n")
print("PASS identical seed 0 twice; seed 1 differs, all five scenes")
