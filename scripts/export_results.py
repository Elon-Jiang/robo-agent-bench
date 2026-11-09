"""Copy the small, committable part of finished suite runs into results/.

Per run: episode.jsonl (scores, items, self-report), run_meta.json (turns, tokens,
wall time) and codex_last_message.txt (the agent's final /done message). Rollout
videos, bridge frames and session logs stay in outputs/ (gitignored).

    python scripts/export_results.py --suites suite_v0.1 suite_v0.2 --reports suite_v0.2.1_s0-19 \
        --exclude 'suite_v0.1/pilot_tidy_desk__*'
"""
import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEEP = ("episode.jsonl", "run_meta.json", "codex_last_message.txt")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suites", nargs="+", required=True)
    ap.add_argument("--reports", nargs="*", default=[])
    ap.add_argument("--exclude", nargs="*", default=[],
                    help="suite/run globs to skip, e.g. 'suite_v0.1/pilot_tidy_desk__*' (superseded scene)")
    ap.add_argument("--out", default="results")
    a = ap.parse_args()
    out = ROOT / a.out
    n = 0
    for suite in a.suites:
        src = ROOT / "outputs" / "suites" / suite
        for run in sorted(src.glob("pilot_*")):
            if any(Path(suite, run.name).match(x) for x in a.exclude):
                continue
            if not run.is_dir() or run.name.endswith(".lock"):
                continue
            meta = run / "run_meta.json"
            if not meta.exists() or not json.loads(meta.read_text()).get("completed"):
                continue
            dst = out / "runs" / suite / run.name
            dst.mkdir(parents=True, exist_ok=True)
            for f in KEEP:
                if (run / f).exists():
                    shutil.copy2(run / f, dst / f)
            n += 1
    for name in a.reports:
        for ext in (".md", ".json"):
            p = ROOT / "outputs" / "reports" / (name + ext)
            if p.exists():
                (out / "reports").mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, out / "reports" / p.name)
    print(f"exported {n} runs to {out}")


if __name__ == "__main__":
    main()
