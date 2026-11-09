"""Conservative HEURISTIC labels from archived final messages, never physics.

Does not edit original runs. Unknown/ambiguous text stays null. These labels
must not be presented as structured /done declarations or human annotations.
"""

import argparse
import glob
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def classify(text):
    text = text.strip().lower()
    if not text:
        return None, "empty"
    # Explicit partial language wins over a successful subtask description.
    if re.search(r"\bpartial(?:ly)?\b|部分(?:完成|成功)|未完全|尚未完成|只完成", text):
        return "partial", "explicit partial"
    if re.search(
        r"\b(?:task|attempt|run|cleanup)\s+(?:has\s+|was\s+)?failed\b|\b(?:task|attempt|run)\s+(?:was\s+)?(?:a\s+)?failure\b|任务失败|未能完成任务|任务未完成",
        text,
    ):
        return "failure", "explicit failure"
    # Do not infer success from a successful action when another clause reports
    # uncertainty, a missed action, or incomplete work.
    ambiguous = re.search(
        r"\b(?:couldn['’]t|cannot|unable|unsure|uncertain|incomplete|not complete|not successful|didn['’]t|failed|remaining|remains? outside)\b|未能|无法|不确定|没有完成|没能|仍有|还剩",
        text,
    )
    if ambiguous:
        return None, "uncertain or conflicting outcome"
    if re.search(
        r"\bsucceeded\b|\bsuccessful(?:ly)?\b|\bsuccess\b|\btask (?:is |was )?complete(?:d)?\b|任务成功|认为.*成功|完成了任务|任务已完成",
        text,
    ):
        return "success", "explicit success"
    return None, "no explicit outcome"


def backfill(patterns):
    paths = sorted(
        {
            Path(p)
            for pattern in patterns
            for p in glob.glob(pattern)
            if Path(p).is_dir()
        }
    )
    rows = []
    for p in paths:
        f = p / "codex_last_message.txt"
        text = f.read_text() if f.exists() else ""
        label, rule = classify(text)
        rows.append(
            dict(
                run=p.name,
                path=str(p.resolve()),
                self_report=label,
                self_report_source="heuristic_final_message",
                rule=rule,
                text=text.strip(),
            )
        )
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--inputs", nargs="+", default=["outputs/agent_pilots/pilot_*__1108_*"]
    )
    ap.add_argument("--output", default="outputs/reports/round3_self_reports.json")
    a = ap.parse_args()
    rows = backfill(a.inputs)
    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
    lines = [
        "# Heuristic self-report backfill",
        "",
        "Conservative English/Chinese keywords; unknown labels are null. Original runs are unchanged.",
        "",
        "| Run | Heuristic label | Rule |",
        "|---|---|---|",
    ]
    lines += [
        f"| {r['run']} | {r['self_report'] or 'unknown'} | {r['rule']} |" for r in rows
    ]
    out.with_suffix(".md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
