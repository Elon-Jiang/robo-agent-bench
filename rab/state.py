"""Process-local state shared between the resetter, device, oracle and eval.

tabletop_v0 runs on CPU with nworld=1, so a single slot per process is enough.
The resetter writes the episode layout here (puddle cells, which mug is
tipped); the oracle reads it to plan, and the device appends finished-episode
metrics to EPISODES (and to $RAB_EPISODE_LOG as JSONL when set).
"""

from __future__ import annotations

import atexit
import json
import os
import signal
import time
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Layout:
    cell_xy: np.ndarray  # [K, 2] world XY of every grid cell
    active: np.ndarray  # [K] bool, cells that hold liquid this episode
    tipped: str  # name of the tipped mug
    upright: tuple[str, ...]  # names of the upright mugs
    mouth_xy: np.ndarray  # [2] where the spill starts
    spill_dir: float  # radians, direction the mouth points
    mug_radius: float  # footprint radius used for clearance


@dataclass
class _State:
    layout: Layout | None = None
    episodes: list[dict] = field(default_factory=list)
    pending: dict | None = None  # metrics of the episode in progress
    device: object | None = None  # live SpillDevice (oracle reads wiped cells)
    metadata: dict = field(default_factory=dict)
    finalized: bool = False


STATE = _State()


def begin_episode() -> None:
    flush_episode("reset")
    STATE.finalized = False
    STATE.metadata = {"started_at": time.time()}


def flush_episode(end_reason="process_exit", **metadata) -> None:
    """Durably write once; defer termination signals until the append is complete."""
    old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
    try:
        _flush_episode(end_reason, **metadata)
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)


def _flush_episode(end_reason, **metadata):
    if STATE.pending is None or STATE.finalized:
        return
    rec = dict(STATE.pending)
    rec.update(STATE.metadata)
    rec.update(metadata)
    rec["end_reason"] = end_reason
    rec.setdefault("turns_used", 0)
    rec.setdefault("self_report", None)
    rec["wall_s"] = time.time() - rec.pop("started_at", time.time())
    from rab.metrics import report_correct

    rec["report_correct"] = report_correct(rec)
    path = os.environ.get("RAB_EPISODE_LOG")
    if path:
        with open(path, "a") as f:
            f.write(json.dumps(rec) + "\n")
            f.flush()
            os.fsync(f.fileno())
    STATE.episodes.append(rec)
    STATE.pending = None
    STATE.finalized = True


atexit.register(flush_episode)
