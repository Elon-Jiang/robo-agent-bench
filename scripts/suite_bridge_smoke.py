"""Real HTTP/camera lifecycle acceptance on port 8099; no LLM client."""

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from bridge_smoke_pilot import private_keys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/suite_v0.2/bridge_smoke"


def trial(name, seed, mode, task="pilot_breakfast"):
    out = OUT / name
    out.mkdir(parents=True, exist_ok=False)
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 8099))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(endpoint, body=None):
        req = urllib.request.Request(
            "http://127.0.0.1:8099/" + endpoint,
            data=body.encode() if body is not None else None,
        )
        with opener.open(req, timeout=30) as r:
            data = json.load(r)
        private_keys(data)
        assert "report_correct" not in data and "proactive_safety" not in data
        return data

    with (out / "runner.log").open("w") as log:
        p = subprocess.Popen(
            [
                sys.executable,
                "run.py",
                "--mode=sim",
                "--robot=yam",
                "--agent=bridge",
                "--task=" + task,
                "--episodes=1",
                "--max_turns=" + ("1" if mode == "turn_cap" else "30"),
                "--http_port=8099",
                "--bridge_dir=" + str(out / "bridge"),
            ],
            cwd=ROOT,
            stdout=log,
            stderr=log,
            env=dict(
                os.environ,
                RAB_SEED=str(seed),
                RAB_EPISODE_LOG=str(out / "episode.jsonl"),
            ),
        )
    try:
        for _ in range(240):
            if p.poll() is not None:
                raise RuntimeError(f"{name}: exited before ready")
            try:
                obs = request("obs")
                if obs.get("status") == "reset":
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("startup timeout")
        if mode == "turn_cap":
            request("plan", "# Thought: scripted home\nrobot.home()")
        elif mode == "interrupt":

            def active_motion():
                try:
                    request(
                        "plan",
                        "# Thought: interrupt during motion\n"
                        "robot.goto(0.4, 0.0, 0.25, speed=0.001)",
                    )
                except (OSError, ValueError):
                    pass

            motion = threading.Thread(target=active_motion, daemon=True)
            motion.start()
            time.sleep(1)
            p.send_signal(signal.SIGINT)
        elif mode == "harness_done":
            request("done", "# Thought: [harness] scripted completion")
        elif mode == "time_cap":
            request("done", "# Thought: [harness:time_cap] deadline")
        else:
            request("plan", "# Thought: scripted home\nrobot.home()")
            # JSON multiline thought exercises the documented metadata parser.
            request(
                "done",
                (
                    "# Thought: Checked the scene.\nSELF_REPORT: failure"
                    if name.endswith("_a")
                    else json.dumps(
                        {"thought": "Checked the scene.\nSELF_REPORT: failure"}
                    )
                ),
            )
        expected = {"done": "agent_done"}.get(mode, mode)
        # Persistence must happen promptly, before waiting for process cleanup.
        for _ in range(120):
            if (out / "episode.jsonl").exists():
                break
            time.sleep(0.1)
        records = [
            json.loads(l) for l in (out / "episode.jsonl").read_text().splitlines()
        ]
        assert len(records) == 1, records
        ep = records[0]
        assert ep["end_reason"] == expected, (name, ep)
        assert ep["suite_version"] == "0.2"
        assert ep["scene_version"] == (2 if task == "pilot_tidy_desk" else 1)
        assert ep["seed"] == seed and ep["layout_sha256"] and ep["initial_poses"]
        assert ep["turns_used"] == (
            1 if mode in ("done", "turn_cap", "interrupt") else 0
        ), ep
        assert ep["self_report"] == ("failure" if mode == "done" else None), ep
        if mode == "done":
            assert ep["report_correct"] is True, ep
        p.wait(timeout=45)
        for path in (out / "bridge").rglob("*.json"):
            private_keys(json.loads(path.read_text()))
        for path in (out / "bridge").rglob("*.jsonl"):
            for line in path.read_text().splitlines():
                private_keys(json.loads(line))
        assert "**Success**" not in (out / "bridge/report.md").read_text()
        assert len((out / "episode.jsonl").read_text().splitlines()) == 1
        return dict(name=name, passed=True, episode=ep)
    finally:
        if p.poll() is None:
            p.send_signal(signal.SIGINT)
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()


def main():
    # Unique invocation directory; never overwrite prior evidence.
    global OUT
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--task", default="pilot_breakfast")
    ap.add_argument(
        "--quick", action="store_true", help="One SELF_REPORT/no-leak case only"
    )
    ap.add_argument("--output", default="outputs/suite_v0.2/bridge_smoke")
    args = ap.parse_args()
    OUT = ROOT / args.output / time.strftime("%Y%m%d_%H%M%S")
    OUT.mkdir(parents=True)
    if args.quick:
        rows = [trial("done_seed0_a", 0, "done", args.task)]
        (OUT / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
        print(f"PASS SELF_REPORT; no grader leak; {OUT}")
        return
    rows = [
        trial("done_seed0_a", 0, "done", args.task),
        trial("done_seed0_b", 0, "done", args.task),
        trial("done_seed1", 1, "done", args.task),
    ]
    assert rows[0]["episode"]["initial_poses"] == rows[1]["episode"]["initial_poses"]
    assert rows[0]["episode"]["layout_sha256"] != rows[2]["episode"]["layout_sha256"]
    rows += [
        trial(mode, 0, mode, args.task)
        for mode in ("turn_cap", "harness_done", "time_cap", "interrupt")
    ]
    (OUT / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
    print(f"PASS {len(rows)} bridge cases; seed determinism; no grader leak; {OUT}")


if __name__ == "__main__":
    main()
