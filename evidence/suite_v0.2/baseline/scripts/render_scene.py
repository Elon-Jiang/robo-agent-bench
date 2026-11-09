"""M1/M3 check: build a task, reset with several seeds, render stills.

  ../mujoco_playground/.venv/bin/python scripts/render_scene.py --task spill_wipe --seeds 0 1 2
Writes outputs/scenes/<task>_s<seed>.png (overview | top-down | close-up) and a
contact sheet when more than one seed is given.
"""

import os
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import rab  # noqa: E402

rab.register()

import mujoco  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from mujoco_playground.tasks.agentic.tabletop_v0 import embodiment as E  # noqa: E402
from mujoco_playground.tasks.agentic.tabletop_v0 import env as agentic_env  # noqa: E402

from rab import state  # noqa: E402


def cam(lookat, distance, azimuth, elevation):
    c = mujoco.MjvCamera()
    c.lookat[:] = lookat
    c.distance, c.azimuth, c.elevation = distance, azimuth, elevation
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="spill_wipe")
    ap.add_argument("--robot", default="so101")
    ap.add_argument("--seeds", type=int, nargs="+", default=[0])
    ap.add_argument(
        "--settle", type=int, default=50, help="control steps to let objects settle"
    )
    args = ap.parse_args()

    out = ROOT / (
        f"outputs/pilots/{args.task}"
        if args.task.startswith("pilot_")
        else "outputs/scenes"
    )
    out.mkdir(parents=True, exist_ok=True)
    env = agentic_env.create_sim_env(task=args.task, robot=args.robot)
    sim = env.device_layer.sim
    m, d = sim.mj_model, sim.mj_data
    emb = E.get(args.robot)
    r = mujoco.Renderer(m, 360, 480)
    cx = float(np.mean(emb.spawn_x))
    views = [
        cam(*emb.overview_camera),
        cam([cx + 0.02, 0.0, 0.0], 0.62, 180.0, -89.0),
        cam([cx + 0.02, 0.0, 0.01], 0.38, 150.0, -32.0),
    ]
    if args.task == "pilot_breakfast":
        views[0] = cam([cx + 0.02, -0.06, -0.02], 0.90, 145.0, -35.0)
        views[1] = cam([cx + 0.02, -0.08, 0], 0.90, 180.0, -89.0)
    tiles = []
    for seed in args.seeds:
        if "pilot_" in args.task:
            os.environ["RAB_SEED"] = str(seed)
        torch.manual_seed(seed)
        np.random.seed(seed)
        env.reset()
        for _ in range(args.settle * 10):
            mujoco.mj_step(m, d)
        if args.task.startswith("pilot_"):
            env.device_layer.get_device("pilot").on_step(sim)
        imgs = []
        for v in views:
            r.update_scene(d, camera=v)
            imgs.append(r.render())
        strip = Image.fromarray(np.concatenate(imgs, axis=1))
        lay = state.STATE.layout
        ImageDraw.Draw(strip).text(
            (8, 8),
            f"{args.task} seed={seed} tipped={lay.tipped if lay else '-'} "
            f"cells={int(lay.active.sum()) if lay else 0}",
            fill=(255, 255, 255),
        )
        strip.save(out / f"{args.task}_s{seed}.png")
        r.update_scene(d, camera=emb.wrist_cam_fullname)
        Image.fromarray(r.render()).save(out / f"{args.task}_s{seed}_wrist.png")
        tiles.append(strip)
        print(
            f"seed {seed}: saved; cells={int(lay.active.sum()) if lay else 0} tipped={lay.tipped if lay else '-'}"
        )
    if len(tiles) > 1:
        w, h = tiles[0].size
        sheet = Image.new("RGB", (w, h * len(tiles)))
        for i, t in enumerate(tiles):
            sheet.paste(t, (0, h * i))
        sheet.save(out / f"{args.task}_sheet.png")
        print("sheet:", out / f"{args.task}_sheet.png")
    r.close()
    env.close()


if __name__ == "__main__":
    main()
