"""Bounded process lifecycle for the explicitly invoked LLM pilot harness."""

import argparse
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


sys.path.insert(0, str(ROOT))
from rab.versions import SUITE_VERSION, scene_version
sys.path.insert(0, str(ROOT / "scripts"))
import agent_clis


def request(port, body=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    endpoint = "done" if body is not None else "obs"
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/{endpoint}",
        data=body.encode() if body is not None else None,
    )
    with opener.open(req, timeout=5) as r:
        return json.load(r)


def stop(process, timeout=30):
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGINT)
    except ProcessLookupError:
        process.wait()
        return
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def main():
    ap = argparse.ArgumentParser()
    for k in ("task", "model", "robot", "out"):
        ap.add_argument("--" + k, required=True)
    for k, d in (("port", 8099), ("seed", 0), ("turn-cap", 30), ("time-cap", 1500)):
        ap.add_argument("--" + k, type=int, default=d)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.port not in range(8096, 8100):
        ap.error("ports must be 8096-8099")
    if a.turn_cap < 1 or a.time_cap < 1:
        ap.error("caps must be positive")
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(
            ("127.0.0.1", a.port)
        )  # fail before launching against somebody else's bridge
    if (out / "episode.jsonl").exists():
        raise SystemExit("Refusing to overwrite an episode")
    start = time.monotonic()
    bridge = agent = None
    rc = None
    reason = None
    error = None
    called_done = False
    meta = dict(
        task=a.task,
        model=a.model,
        seed=a.seed,
        suite_version=SUITE_VERSION,
        scene_version=scene_version(a.task),
        turn_cap=a.turn_cap,
        time_cap_s=a.time_cap,
        port=a.port,
        completed=False,
    )

    def save():
        tmp = out / "run_meta.json.tmp"
        tmp.write_text(json.dumps(meta, indent=2) + "\n")
        tmp.replace(out / "run_meta.json")

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    save()
    try:
        with (out / "bridge.log").open("w") as log:
            bridge = subprocess.Popen(
                [
                    sys.executable,
                    "run.py",
                    "--mode=sim",
                    f"--robot={a.robot}",
                    "--agent=bridge",
                    f"--task={a.task}",
                    "--episodes=1",
                    f"--max_turns={a.turn_cap}",
                    f"--http_port={a.port}",
                    f'--bridge_dir={out / "bridge"}',
                    f'--video={out / "rollout.mp4"}',
                ],
                cwd=ROOT,
                env=dict(
                    os.environ,
                    RAB_SEED=str(a.seed),
                    RAB_SHOW_SUCCESS="0",
                    RAB_EPISODE_LOG=str(out / "episode.jsonl"),
                ),
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        for _ in range(240):
            if bridge.poll() is not None:
                raise RuntimeError("bridge exited before ready")
            try:
                if request(a.port).get("status") == "reset":
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError("bridge startup timed out")
        agent_start = time.monotonic()
        argv, cwd, env = agent_clis.build(a.model, out, (out / "prompt.txt").read_text())
        meta["agent_cli"] = agent_clis.cli_for(a.model)
        with (out / agent_clis.LOG).open("w") as log:
            agent = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        while agent.poll() is None:
            if time.monotonic() - agent_start >= a.time_cap:
                reason = "time_cap"
                stop(agent, 10)
                break
            time.sleep(0.5)
        rc = agent.returncode
    except KeyboardInterrupt:
        reason = "interrupt"
    except Exception as exc:
        error = str(exc)
        reason = "harness_error"
    finally:
        stop(agent, 10)
        if bridge is not None and bridge.poll() is None:
            try:
                obs = request(a.port)
                called_done = obs.get("status") == "episode_end"
                if not called_done:
                    tag = f"[harness:{reason}]" if reason else "[harness]"
                    request(
                        a.port, f"# Thought: {tag} agent stopped without calling /done"
                    )
            except (OSError, ValueError):
                pass
            try:
                bridge.wait(timeout=45)
            except subprocess.TimeoutExpired:
                stop(bridge, 10)
        episodes = []
        if (out / "episode.jsonl").exists():
            episodes = [
                json.loads(l)
                for l in (out / "episode.jsonl").read_text().splitlines()
                if l.strip()
            ]
        text = (
            (out / "codex_session.log").read_text()
            if (out / "codex_session.log").exists()
            else ""
        )
        tokens, _ = agent_clis.finish(a.model, out, text)
        ep = episodes[-1] if episodes else {}
        meta.update(
            codex_rc=rc,
            wall_s=time.monotonic() - start,
            tokens=tokens,
            agent_called_done=ep.get("end_reason") == "agent_done",
            turns_used=ep.get("turns_used"),
            end_reason=ep.get("end_reason"),
            error=error,
            completed=(
                bool(ep)
                and error is None
                and (rc == 0 or reason == "time_cap")
                and reason != "interrupt"
            ),
        )
        save()
    print(json.dumps(meta), flush=True)
    return 0 if meta["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
