"""Port-8099 HTTP/file privacy regression for both false and true task outcomes.

Uses physical scoring fixtures and the real BridgeBackend with a blank camera image;
run.py's real-camera smoke evidence is recorded separately in bridge_privacy.log.
"""

import json
import os
import subprocess
import torch
from pilot_test_utils import ROOT, create, settle, evaluate
from test_pilots import complete
from rab import state
from rab.bridge_privacy import public_data
from mujoco_playground.tasks.agentic.tabletop_v0 import agent as A


def curl(endpoint, body=None):
    cmd = [
        "curl",
        "--noproxy",
        "*",
        "-fsS",
        "--max-time",
        "10",
        "http://127.0.0.1:8099/" + endpoint,
    ]
    if body is not None:
        cmd += [
            "-X",
            "POST",
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps(body),
        ]
    return json.loads(subprocess.check_output(cmd))


def check():
    root = ROOT / "outputs/pilots/privacy_test"
    root.mkdir(parents=True, exist_ok=True)
    env = create("pilot_breakfast")
    records = []
    try:
        for show in (False, True):
            os.environ["RAB_SHOW_SUCCESS"] = "1" if show else "0"
            bridge = root / ("debug" if show else "hidden")
            backend = A.BridgeBackend(
                "Please set up my breakfast.", str(bridge), 8099, "yam"
            )
            try:
                for solved in (False, True):
                    torch.manual_seed(0)
                    ts = env.reset()
                    settle(env)
                    if solved:
                        complete(env)
                    rec = evaluate(env)
                    assert rec["success_strict"] == solved
                    obs = dict(ts.observation)
                    obs["wrist_rgb"] = torch.zeros((1, 480, 640, 3))
                    backend.on_reset(obs)
                    backend._snapshot(obs, "done", solved, event="plan")
                    policy = A.AgentPolicy(backend, "yam")
                    policy.finish(obs, solved)
                    if not show:
                        assert (
                            len(backend._steps_log) == 3
                        ), "terminal snapshot count leaks grader"

                    for endpoint, body in (
                        ("obs", None),
                        ("plan", {"code": "robot.done()"}),
                        ("done", {}),
                    ):
                        payload = curl(endpoint, body)
                        assert ("success" in payload) == show, payload
                        if show:
                            assert payload["success"] == solved
                        (
                            root
                            / f'{"debug" if show else "hidden"}_{solved}_{endpoint}.json'
                        ).write_text(json.dumps(payload, indent=2))
                    payload = json.loads((bridge / "obs.json").read_text())
                    assert ("success" in payload) == show
                    report = (bridge / "report.md").read_text()
                    assert ("**Success**" in report) == show
                    for line in (bridge / "steps.jsonl").read_text().splitlines():
                        assert public_data(json.loads(line)) == json.loads(line)
                    # Internal evaluator and trusted JSONL still retain the actual result.
                    os.environ["RAB_EPISODE_LOG"] = str(root / "episodes.jsonl")
                    state.flush_episode()
                    saved = json.loads(
                        (root / "episodes.jsonl").read_text().splitlines()[-1]
                    )
                    assert saved == rec and saved["success_strict"] == solved
                    records.append(dict(show=show, solved=solved, passed=True))
            finally:
                backend.close()
        assert public_data(
            {
                "history": [
                    {"success": True, "pilot/score_0_100": 100, "ee_pos": [1, 2, 3]}
                ]
            }
        ) == {"history": [{"ee_pos": [1, 2, 3]}]}
        os.environ["RAB_SHOW_SUCCESS"] = "0"
        os.environ["RAB_EPISODE_LOG"] = str(root / "unsafe_bridge" / "episodes.jsonl")
        try:
            A.BridgeBackend(
                "Please set up my breakfast.", str(root / "unsafe_bridge"), 8099, "yam"
            )
        except ValueError as exc:
            assert "outside" in str(exc)
        else:
            raise AssertionError(
                "private episode log was allowed in the public bridge directory"
            )
        print(json.dumps(records, indent=2))
        print(
            "PASS: HTTP /obs /plan /done, obs.json, report.md, steps.jsonl, true/false private records, opt-out"
        )
    finally:
        env.close()
        os.environ.pop("RAB_SHOW_SUCCESS", None)
        os.environ.pop("RAB_EPISODE_LOG", None)


if __name__ == "__main__":
    check()
