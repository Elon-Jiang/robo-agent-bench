"""Privileged diagnostic replay of saved primitive plans; never changes evidence.

The old bridge did not save reset seeds or simulator state. Seed is explicit and
EE discrepancies are reported, so an approximate replay cannot masquerade as an
exact recovery. Outputs belong outside outputs/agent_pilots.
"""

import argparse
import json
import numpy as np
import torch

from pilot_test_utils import ROOT, create, evaluate
from mujoco_playground.tasks.agentic.tabletop_v0.agent import PlanExecutor


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    source = ROOT / args.run
    task = source.name.split("__")[0]
    env = create(task)
    torch.manual_seed(args.seed)
    ts = env.reset()
    ex = PlanExecutor("yam")
    ex.sync(ts.observation)
    sim = env.device_layer.sim
    m, d = sim.mj_model, sim.mj_data
    scorer = env.device_layer.get_device("pilot")
    rows = []
    plans = [dict(turn=-1, plan=[dict(op="home")])]
    plans += [
        json.loads(line)
        for line in (source / "bridge/steps.jsonl").read_text().splitlines()
    ]
    for row in plans:
        if not row["plan"]:
            continue
        ex.load(row["plan"], ts.observation)
        steps = 0
        while ex.busy:
            ts = env.step(
                {
                    "action": torch.tensor(
                        ex.step(ts.observation), dtype=torch.float32
                    )[None]
                }
            )
            steps += 1
            if steps > 4000:
                raise RuntimeError("plan exceeded 4000 steps")
        # AgentPolicy reports a plan, then advances one idle control tick.
        ts = env.step(
            {"action": torch.tensor(ex.step(ts.observation), dtype=torch.float32)[None]}
        )
        record = evaluate(env)
        ee = np.asarray(ts.observation["ee_pos"]).reshape(-1)
        result = dict(
            turn=row["turn"],
            status=ex.status,
            steps=steps,
            ee_error_m=(
                float(np.linalg.norm(ee - row["ee_pos"])) if "ee_pos" in row else None
            ),
            record=record,
            poses={
                n: dict(pos=d.xpos[b].tolist(), quat=d.xquat[b].tolist())
                for n, b in scorer.bodies.items()
            },
        )
        if task == "pilot_breakfast":
            center, facing, right = scorer.diner_frame()
            result["plate_checks"] = dict(
                center_local=(
                    (scorer.pos["plate"] - center)
                    @ d.xmat[m.body("placemat").id].reshape(3, 3)
                ).tolist(),
                touching=scorer.touching("plate", m.body("placemat").id),
                upright=scorer.upright("plate"),
                settled=scorer.settled("plate"),
                released=scorer.released("plate"),
                velocity=d.qvel[
                    m.joint("plate/plate_freejoint")
                    .dofadr[0] : m.joint("plate/plate_freejoint")
                    .dofadr[0]
                    + 6
                ].tolist(),
            )
        rows.append(result)
        print(json.dumps(result), flush=True)
    out = ROOT / "outputs/pilots/round2" / (source.name + f"_replay_s{args.seed}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            dict(
                source=str(source),
                seed=args.seed,
                rows=rows,
                qpos=d.qpos.tolist(),
                qvel=d.qvel.tolist(),
                furniture={
                    n: dict(pos=m.body(n).pos.tolist(), quat=m.body(n).quat.tolist())
                    for n in ("placemat", "diner_chair")
                    if task == "pilot_breakfast"
                },
            ),
            indent=2,
        )
        + "\n"
    )
    env.close()


if __name__ == "__main__":
    main()
