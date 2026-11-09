"""Real-camera pilot_breakfast HTTP smoke, exclusively on port 8099."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/pilots/round2/bridge_smoke"
FORBIDDEN = {
    "success",
    "success_strict",
    "score",
    "score_0_100",
    "reward",
    "rewards",
    "items",
    "critical_errors",
    "report_correct",
    "proactive_safety",
}


def private_keys(value):
    if isinstance(value, dict):
        assert not ({k.lower().split("/")[-1] for k in value} & FORBIDDEN), value
        assert not any(k.startswith("pilot/") for k in value), value
        for child in value.values():
            private_keys(child)
    elif isinstance(value, list):
        for child in value:
            private_keys(child)


def main():
    with socket.socket() as check:
        assert check.connect_ex(("127.0.0.1", 8099)) != 0, "8099 already occupied"
    OUT.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(endpoint, body=None):
        req = urllib.request.Request(
            "http://127.0.0.1:8099/" + endpoint, data=body.encode() if body else None
        )
        with opener.open(req, timeout=30) as response:
            data = json.load(response)
        private_keys(data)
        return data

    env = dict(
        os.environ, RAB_SHOW_SUCCESS="0", RAB_EPISODE_LOG=str(OUT / "episode.jsonl")
    )
    with (OUT / "runner.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "run.py",
                "--mode=sim",
                "--robot=yam",
                "--agent=bridge",
                "--task=pilot_breakfast",
                "--episodes=1",
                "--http_port=8099",
                "--bridge_dir=" + str(OUT / "bridge"),
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            for _ in range(120):
                if process.poll() is not None:
                    raise RuntimeError("Bridge exited before ready; inspect runner.log")
                try:
                    initial = request("obs")
                    if initial.get("status") == "reset" and initial.get(
                        "wrist_cam_path"
                    ):
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(0.25)
            else:
                raise RuntimeError("Bridge startup timed out")
            plan = request("plan", "# Thought: smoke test\nrobot.home()")
            for _ in range(120):
                current = request("obs")
                if current.get("turn", 0) >= 1 and current.get("status") == "done":
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError("Home plan did not finish")
            done = request("done", "# Thought: smoke complete")
            assert process.wait(timeout=30) == 0
            for path in (OUT / "bridge").rglob("*.json"):
                private_keys(json.loads(path.read_text()))
            for path in (OUT / "bridge").rglob("*.jsonl"):
                for line in path.read_text().splitlines():
                    private_keys(json.loads(line))
            assert "**Success**" not in (OUT / "bridge/report.md").read_text()
            episodes = [
                json.loads(line)
                for line in (OUT / "episode.jsonl").read_text().splitlines()
            ]
            assert episodes and "success_strict" in episodes[-1]
            result = dict(
                passed=True,
                port=8099,
                task="pilot_breakfast",
                public_success_leak=False,
                initial=initial,
                plan=plan,
                done=done,
                private_episode=episodes[-1],
            )
            (OUT / "result.json").write_text(json.dumps(result, indent=2) + "\n")
            print(
                "PASS real-camera breakfast smoke; /obs, /plan, /done and files hide grader fields; private episode retained"
            )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    with socket.socket() as check:
        assert check.connect_ex(("127.0.0.1", 8099)) != 0, "8099 listener leaked"


if __name__ == "__main__":
    main()
