"""README figures: hero banner (1280x640, also the GitHub social preview) and the
results chart (light + dark). Inputs: results/runs (scores) and outputs/suites rollouts
(banner stills).

    python scripts/make_figures.py
"""
import glob
import json
import os
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets"
MODELS = ["gpt-6-astra", "gpt-5.6-luna"]
SCENES = [  # (task, label, showcase seed)
    ("pilot_spill_cleanup", "Wipe a spill", 3),
    ("pilot_clear_dishes", "Clear dishes", 16),
    ("pilot_tidy_desk", "Tidy a desk", 9),
    ("pilot_toys", "Put toys away", 5),
    ("pilot_breakfast", "Set breakfast", 13),
]
# Validated categorical slots 1-2 (dataviz reference palette), light / dark.
THEMES = {
    "light": dict(surface="#fcfcfb", ink="#0b0b0b", ink2="#52514e", grid="#e6e5e1",
                  neutral="#d4d3cf", series=["#2a78d6", "#eb6834"]),
    "dark": dict(surface="#1a1a19", ink="#f0efec", ink2="#c3c2b7", grid="#33332f",
                 neutral="#4a4a46", series=["#3987e5", "#d95926"]),
}
FONT = "/System/Library/Fonts/Helvetica.ttc"


def load_runs():
    rows = []
    for f in glob.glob(str(ROOT / "results/runs/*/pilot_*/episode.jsonl")):
        task, model, seed = Path(f).parent.name.split("__")
        e = json.loads(Path(f).read_text().strip().splitlines()[-1])
        rows.append(dict(task=task, model=model, seed=seed, score=e["score_0_100"],
                         strict=e["success_strict"], claim=e.get("self_report")))
    return rows


def boot_ci(x, n=10000, seed=0):
    x = np.asarray(x, float)
    rng = np.random.default_rng(seed)
    m = rng.choice(x, (n, len(x))).mean(1)
    return np.percentile(m, [2.5, 97.5])


def results_chart(rows, mode):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    t = THEMES[mode]
    plt.rcParams.update({"font.family": "Helvetica", "font.size": 11,
                         "text.color": t["ink"], "axes.labelcolor": t["ink2"],
                         "xtick.color": t["ink2"], "ytick.color": t["ink"]})
    by = defaultdict(list)
    for r in rows:
        by[(r["task"], r["model"])].append(r["score"])
        by[("overall", r["model"])].append(r["score"])
    cats = [("overall", "Overall")] + [(s, l) for s, l, _ in SCENES]

    fig, (a, b) = plt.subplots(1, 2, figsize=(12, 4.6), dpi=200,
                               gridspec_kw=dict(width_ratios=[1.45, 1], wspace=0.32))
    fig.patch.set_facecolor(t["surface"])
    for ax in (a, b):
        ax.set_facecolor(t["surface"])
        for s in ax.spines.values():
            s.set_visible(False)

    # Panel A: mean score per scene, 95% bootstrap CI.
    h, gap = 0.36, 0.04
    for j, m in enumerate(MODELS):
        for i, (task, _) in enumerate(cats):
            y = i + (j - 0.5) * (h + gap)
            v = by[(task, m)]
            mu, (lo, hi) = np.mean(v), boot_ci(v)
            a.barh(y, mu, height=h, color=t["series"][j], zorder=2,
                   label=m if i == 0 else None)
            a.plot([lo, hi], [y, y], color=t["ink2"], lw=1.2, zorder=3, solid_capstyle="round")
            a.text(hi + 1.5, y, f"{mu:.0f}", va="center", ha="left", fontsize=10,
                   color=t["ink"], fontweight="bold" if task == "overall" else "normal")
    a.set_yticks(range(len(cats)), [l for _, l in cats])
    a.get_yticklabels()[0].set_fontweight("bold")
    a.invert_yaxis()
    a.set_xlim(0, 105)
    a.set_xticks([0, 25, 50, 75, 100])
    a.xaxis.grid(True, color=t["grid"], lw=0.8, zorder=0)
    a.tick_params(length=0)
    a.set_xlabel("Mean score (0–100), 20 paired seeds per scene · whiskers: 95% CI")
    a.set_title("Score by scene", loc="left", fontsize=13, fontweight="bold", pad=10)
    a.legend(loc="lower right", frameon=False, fontsize=10, labelcolor=t["ink"])

    # Panel B: of the episodes where the model said "SELF_REPORT: success", how many were?
    for j, m in enumerate(MODELS):
        claims = [r for r in rows if r["model"] == m and r["claim"] == "success"]
        ok = sum(r["strict"] for r in claims)
        n = len(claims)
        b.barh(j, ok / n * 100, height=0.5, color=t["series"][j], zorder=2)
        b.barh(j, (n - ok) / n * 100, left=ok / n * 100 + 0.6, height=0.5,
               color=t["neutral"], zorder=2)
        b.text(103.5, j, f"{(n - ok) / n:.0%} false", va="center", ha="left",
               fontsize=11, fontweight="bold", color=t["ink"])
        b.text(1.5, j + 0.42, f"{ok} of {n} claims true", va="center", ha="left",
               fontsize=9.5, color=t["ink2"])
    b.set_yticks(range(len(MODELS)), MODELS)
    b.invert_yaxis()
    b.set_xlim(0, 128)
    b.set_ylim(1.75, -0.6)
    b.set_xticks([0, 50, 100], ["0%", "50%", "100%"])
    b.xaxis.grid(True, color=t["grid"], lw=0.8, zorder=0)
    b.tick_params(length=0)
    b.set_xlabel("Share of “success” self-reports · colored = actually succeeded")
    b.set_title("“I'm done.” — Is it?", loc="left", fontsize=13, fontweight="bold", pad=10)

    fig.subplots_adjust(left=0.1, right=0.97, top=0.88, bottom=0.16)
    p = OUT / f"results_{mode}.png"
    fig.savefig(p, facecolor=t["surface"])
    plt.close(fig)
    return p


def last_frame(task, seed, model="gpt-6-astra"):
    run = sorted(glob.glob(str(ROOT / f"outputs/suites/*/{task}__{model}__s{seed}")))[-1]
    tmp = Path(tempfile.mkstemp(suffix=".png")[1])
    subprocess.check_call(["ffmpeg", "-v", "error", "-y", "-sseof", "-0.2", "-i",
                           f"{run}/rollout.mp4", "-frames:v", "1", str(tmp)])
    im = Image.open(tmp).convert("RGB").crop((0, 36 + 18, 480, 396))  # 3rd-person view, no HUD
    os.unlink(tmp)
    return im


def banner(rows):
    W, H = 1280, 640
    bg, ink, ink2 = (26, 26, 25), (240, 239, 236), (195, 194, 183)
    im = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(im)
    f = lambda s, i=0: ImageFont.truetype(FONT, s, index=i)
    d.text((64, 64), "robo-agent-bench", font=f(60, 1), fill=ink)
    d.text((66, 142), "Can an LLM agent actually tidy a table?", font=f(30), fill=ink2)

    tw, gap = 224, 15
    x0, y0 = (W - (5 * tw + 4 * gap)) // 2, 238
    for k, (task, label, seed) in enumerate(SCENES):
        th = last_frame(task, seed)
        th = th.resize((tw, int(th.height * tw / th.width)), Image.LANCZOS)
        x = x0 + k * (tw + gap)
        mask = Image.new("L", th.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, *th.size], 12, fill=255)
        im.paste(th, (x, y0), mask)
        d.text((x + 2, y0 + th.height + 12), label, font=f(21), fill=ink)
    yb = H - 132

    n = len(rows)
    astra = [r for r in rows if r["model"] == "gpt-6-astra"]
    claims = [r for r in astra if r["claim"] == "success"]
    false = 1 - sum(r["strict"] for r in claims) / len(claims)
    d.text((64, yb), f"{len(SCENES)} household scenes  ·  physics-scored  ·  {n} paired episodes",
           font=f(24), fill=ink2)
    d.text((64, yb + 42), f"The best model says “done” — and is wrong {false:.0%} of the time.",
           font=f(28, 1), fill=(57, 135, 229))
    p = OUT / "banner.png"
    im.save(p, optimize=True)
    return p


def main():
    rows = load_runs()
    assert len(rows) == 200, len(rows)
    for p in (banner(rows), results_chart(rows, "light"), results_chart(rows, "dark")):
        print(p, f"{p.stat().st_size / 1e3:.0f} KB")


if __name__ == "__main__":
    main()
