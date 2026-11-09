"""Side-by-side GIF of two models on the same scene/seed (3rd-person view only).

    python scripts/make_gif.py --task pilot_toys --seed 5 --out assets/toys_s5.gif
Uses ffmpeg + Pillow (labels). Rollouts come from outputs/suites/*/<task>__<model>__s<seed>/.
"""
import argparse
import glob
import json
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def find_run(task, model, seed):
    hits = sorted(glob.glob(str(ROOT / f"outputs/suites/*/{task}__{model}__s{seed}")))
    hits = [h for h in hits if (Path(h) / "rollout.mp4").exists()]
    if not hits:
        raise SystemExit(f"no rollout for {task} {model} s{seed}")
    return Path(hits[-1])


def duration(p):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "csv=p=0", str(p)])
    return float(out)


def label(path, text, w=480, h=30):
    im = Image.new("RGB", (w, h), (24, 24, 28))
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 17)
    except OSError:
        font = ImageFont.load_default()
    ImageDraw.Draw(im).text((10, 6), text, fill=(235, 235, 235), font=font)
    im.save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--models", nargs=2, default=["gpt-6-astra", "gpt-5.6-luna"])
    ap.add_argument("--speed", type=float, default=4.0)
    ap.add_argument("--fps", type=int, default=8)
    ap.add_argument("--width", type=int, default=720)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    runs = [find_run(a.task, m, a.seed) for m in a.models]
    durs = [duration(r / "rollout.mp4") / a.speed for r in runs]
    T = max(durs)
    tmp = Path(tempfile.mkdtemp())
    labels = []
    for i, (m, r) in enumerate(zip(a.models, runs)):
        ep = json.loads((r / "episode.jsonl").read_text().strip().splitlines()[-1])
        tag = "strict success" if ep["success_strict"] else "not strict"
        crit = ep.get("critical_errors") or []
        txt = f"{m}  |  score {ep['score_0_100']:.0f}  |  {tag}" + (f"  |  {crit[0]}" if crit else "")
        lp = tmp / f"l{i}.png"
        label(lp, txt)
        labels.append(lp)

    inputs, chains = [], []
    for i, r in enumerate(runs):
        inputs += ["-i", str(r / "rollout.mp4")]
    for lp in labels:
        inputs += ["-i", str(lp)]
    for i in range(2):
        chains.append(
            f"[{i}:v]crop=480:360:0:36,setpts=PTS/{a.speed},fps={a.fps},"
            f"tpad=stop_mode=clone:stop_duration={T - durs[i] + 0.5:.2f}[v{i}];"
            f"[{i + 2}:v][v{i}]vstack[s{i}]")
    graph = ";".join(chains) + (
        f";[s0][s1]hstack,trim=duration={T + 0.5:.2f},scale={a.width}:-2:flags=lanczos,split[x][y];"
        "[x]palettegen=max_colors=128[p];[y][p]paletteuse=dither=bayer:bayer_scale=4")
    out = ROOT / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph,
                           "-loop", "0", str(out)])
    print(out, f"{out.stat().st_size / 1e6:.1f} MB", f"{T:.1f}s")


if __name__ == "__main__":
    main()
