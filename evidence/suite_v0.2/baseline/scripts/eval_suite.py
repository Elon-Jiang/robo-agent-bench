"""Resumable, seeded model evaluation with one subprocess per port/lane.

This command launches LLM trials unless --dry-run is specified. It is never
imported/called by oracle or validation scripts.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import re
import signal
import socket
import subprocess
import threading

ROOT = Path(__file__).resolve().parents[1]
TASKS = (
    "pilot_spill_cleanup",
    "pilot_clear_dishes",
    "pilot_tidy_desk",
    "pilot_toys",
    "pilot_breakfast",
)


def seeds(value):
    result = []
    for part in value.split(","):
        if "-" in part:
            lo, hi = map(int, part.split("-", 1))
            if lo < 0 or hi < lo:
                raise argparse.ArgumentTypeError("invalid seed range")
            result.extend(range(lo, hi + 1))
        else:
            result.append(int(part))
    if not result or min(result) < 0:
        raise argparse.ArgumentTypeError("seeds must be nonnegative")
    return sorted(set(result))


def complete(path, task, model, seed, turn_cap=30, time_cap=1500):
    try:
        meta = json.loads((path / "run_meta.json").read_text())
        records = [
            json.loads(l)
            for l in (path / "episode.jsonl").read_text().splitlines()
            if l.strip()
        ]
        return (
            meta.get("completed") is True
            and meta.get("model") == model
            and meta.get("task") == task
            and meta.get("seed") == seed
            and meta.get("turn_cap") == turn_cap
            and meta.get("time_cap_s") == time_cap
            and meta.get("suite_version") == "0.1"
            and len(records) == 1
            and records[0].get("seed") == seed
            and records[0].get("task") == task
            and records[0].get("suite_version") == "0.1"
            and all(
                k in records[0]
                for k in (
                    "score_0_100",
                    "success_strict",
                    "items",
                    "turns_used",
                    "self_report",
                )
            )
            and records[0].get("end_reason")
            in ("agent_done", "turn_cap", "harness_done", "time_cap", "episode_limit")
        )
    except (OSError, ValueError, KeyError):
        return False


def plan(root, models, seed_list, tasks, turn_cap, time_cap):
    jobs = []
    skipped = []
    for seed in seed_list:
        for task in tasks:
            for model in models:
                path = root / f"{task}__{model}__s{seed}"
                job = dict(task=task, model=model, seed=seed, path=str(path))
                (
                    skipped
                    if complete(path, task, model, seed, turn_cap, time_cap)
                    else jobs
                ).append(job)
    return jobs, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--seeds", type=seeds, default=seeds("0-19"))
    ap.add_argument("--tasks", nargs="+", choices=TASKS, default=list(TASKS))
    ap.add_argument("--lanes", type=int, default=3)
    ap.add_argument("--name", default="suite_v0.1")
    ap.add_argument("--turn-cap", type=int, default=30)
    ap.add_argument("--time-cap", type=int, default=1500)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not 1 <= a.lanes <= 4:
        ap.error("lanes must be 1-4 (ports 8096-8099)")
    if a.turn_cap < 1 or a.time_cap < 1:
        ap.error("caps must be positive")
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", x) for x in [a.name, *a.models]):
        ap.error("name and models must be safe filename components")
    root = ROOT / "outputs/suites" / a.name
    jobs, skipped = plan(root, a.models, a.seeds, a.tasks, a.turn_cap, a.time_cap)
    manifest = dict(
        suite_version="0.1",
        models=a.models,
        seeds=a.seeds,
        tasks=a.tasks,
        lanes=a.lanes,
        ports=list(range(8096, 8096 + a.lanes)),
        turn_cap=a.turn_cap,
        time_cap_s=a.time_cap,
        pending=jobs,
        skipped=skipped,
        dry_run=a.dry_run,
    )
    if a.dry_run:
        print(json.dumps(manifest, indent=2))
        return 0
    if not jobs:
        print(json.dumps(dict(pending=0, skipped=len(skipped), status="complete")))
        return 0
    # Check all requested ports before any costly agent starts.
    for port in manifest["ports"]:
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    work = queue.Queue()
    for job in jobs:
        work.put(job)
    cancelled = threading.Event()
    active = {}
    lock = threading.Lock()
    results = []

    def interrupt(signum, frame):
        cancelled.set()
        with lock:
            for p in active.values():
                if p.poll() is None:
                    p.send_signal(signal.SIGINT)

    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)

    def lane(port):
        while not cancelled.is_set():
            try:
                job = work.get_nowait()
            except queue.Empty:
                return
            path = Path(job["path"])
            claim = path.with_name(path.name + ".lock")
            try:
                claim.mkdir()
            except FileExistsError:
                results.append(dict(job, status="locked"))
                continue
            try:
                if complete(
                    path, job["task"], job["model"], job["seed"], a.turn_cap, a.time_cap
                ):
                    results.append(dict(job, status="skipped"))
                    continue
                if path.exists():
                    archive = root / "incomplete"
                    archive.mkdir(exist_ok=True)
                    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
                    path.rename(archive / (path.name + "__" + stamp))
                path.mkdir()
                command = [
                    str(ROOT / "scripts/run_agent_pilot.sh"),
                    job["task"],
                    job["model"],
                    str(port),
                    "yam",
                    str(job["seed"]),
                    str(path),
                ]
                with (path / "harness.log").open("w") as log:
                    p = subprocess.Popen(
                        command,
                        cwd=ROOT,
                        stdout=log,
                        stderr=log,
                        env=dict(
                            os.environ,
                            RAB_TURN_CAP=str(a.turn_cap),
                            RAB_TIME_CAP=str(a.time_cap),
                        ),
                    )
                    with lock:
                        active[port] = p
                    rc = p.wait()
                    with lock:
                        active.pop(port, None)
                results.append(
                    dict(
                        job,
                        returncode=rc,
                        status=(
                            "completed"
                            if complete(
                                path,
                                job["task"],
                                job["model"],
                                job["seed"],
                                a.turn_cap,
                                a.time_cap,
                            )
                            else "incomplete"
                        ),
                    )
                )
                print(json.dumps(results[-1]), flush=True)
            finally:
                claim.rmdir()
                work.task_done()

    with ThreadPoolExecutor(max_workers=a.lanes) as pool:
        list(pool.map(lane, manifest["ports"]))
    (root / "evaluation_results.json").write_text(json.dumps(results, indent=2) + "\n")
    return (
        1
        if cancelled.is_set()
        or any(r["status"] in ("incomplete", "locked") for r in results)
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
