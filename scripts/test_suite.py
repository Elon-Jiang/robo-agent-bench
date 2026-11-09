"""Dependency-light checks of missingness, metric boundaries, and resume safety."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rab.metrics import parse_self_report, report_correct
from report import load, summarize, project_v01, wilson, pairing, provenance_text
from self_report_backfill import classify
from eval_suite import complete, seeds, plan


class SuiteTests(unittest.TestCase):
    def test_report_preserves_dotted_name(self):
        import report

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = root / "run"
            run.mkdir()
            (run / "run_meta.json").write_text(
                json.dumps(dict(model="m", task="pilot_toys"))
            )
            with patch.object(report, "ROOT", root), patch.object(
                sys, "argv", ["report.py", "--inputs", str(run), "--name", "suite_v0.1"]
            ), patch("builtins.print"):
                report.main()
            self.assertTrue((root / "outputs/reports/suite_v0.1.md").exists())
            self.assertTrue((root / "outputs/reports/suite_v0.1.json").exists())

    def test_multiple_suite_dirs_versions_and_headers(self):
        import report

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for version in (1, 2):
                suite = root / f"v{version}"
                suite.mkdir()
                for model in ("astra", "luna"):
                    run = suite / f"pilot_tidy_desk__{model}__s0"
                    run.mkdir()
                    meta = dict(
                        model=model,
                        task="pilot_tidy_desk",
                        seed=0,
                        suite_version=f"0.{version}",
                        completed=True,
                    )
                    ep = dict(
                        meta,
                        score_0_100=100,
                        success_strict=True,
                        items={"item_v" + str(version): 1},
                        critical_errors=[],
                        self_report="success",
                    )
                    if version == 2:
                        ep["scene_version"] = 2
                    (run / "run_meta.json").write_text(json.dumps(meta))
                    (run / "episode.jsonl").write_text(json.dumps(ep) + "\n")
            # Overlapping inputs must not duplicate runs. Never scan incomplete/.
            archive = root / "v1/incomplete/old"
            archive.mkdir(parents=True)
            (archive / "run_meta.json").write_text(json.dumps(meta))
            patterns = [
                str(root / "v1"),
                str(root / "v2/pilot_*"),
                str(root / "v1/pilot_*"),
            ]
            rows = load(patterns)
            self.assertEqual(len(rows), 4)
            self.assertEqual({r["scene_version"] for r in rows}, {1, 2})
            self.assertTrue(pairing(rows)["paired"])
            with patch.object(report, "ROOT", root), patch.object(
                sys, "argv", ["report.py", "--inputs", *patterns, "--name", "mixed"]
            ), patch("builtins.print"):
                report.main()
            data = json.loads((root / "outputs/reports/mixed.json").read_text())
            self.assertEqual(
                set(data["scenes"]), {"pilot_tidy_desk@v1", "pilot_tidy_desk@v2"}
            )
            text = (root / "outputs/reports/mixed.md").read_text()
            self.assertIn("paired-by-seed", text)
            self.assertIn("Structured **SELF_REPORT**", text)
            self.assertNotIn("heuristic", text)
            self.assertIn("| pilot_tidy_desk | 2 |", text)
            # Every markdown table must have the same column count as its header.
            columns = None
            for line in text.splitlines():
                if line.startswith("|"):
                    columns = columns or line.count("|")
                    self.assertEqual(line.count("|"), columns, line)
                else:
                    columns = None

    def test_pairing_requires_all_models_known_unique_seeds_and_versions(self):
        rows = [
            dict(task="t", scene_version=1, model=m, seed=i)
            for m in ("a", "b")
            for i in (0, 1)
        ]
        self.assertTrue(pairing(rows)["paired"])
        self.assertFalse(pairing(rows[:-1])["paired"])
        self.assertFalse(pairing(rows + [rows[0]])["paired"])
        self.assertFalse(
            pairing(rows + [dict(task="u", scene_version=1, model="a", seed=0)])[
                "paired"
            ]
        )
        for change in ({"seed": None}, {"scene_version": "unknown"}):
            self.assertFalse(pairing([dict(r, **change) for r in rows])["paired"])

    def test_report_provenance_mixed_backfill_and_missing(self):
        rows = [
            dict(
                missing_episode=False,
                self_report="success",
                self_report_source="structured_done",
            ),
            dict(
                missing_episode=False,
                self_report="failure",
                self_report_source="heuristic_final_message",
            ),
            dict(missing_episode=True, self_report=None),
        ]
        text = provenance_text(rows)
        self.assertIn("Structured **SELF_REPORT** declarations: 1", text)
        self.assertIn("**heuristic** for 1 labeled episodes only", text)
        self.assertIn("No self-report", provenance_text(rows[2:]))

    def test_backfill_load_does_not_replace_structured_or_explicit_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            labels = []
            for name in ("structured", "legacy", "unknown"):
                run = root / name
                run.mkdir()
                meta = dict(model="m", task="pilot_toys", seed=0, suite_version="0.1")
                ep = dict(
                    task="pilot_toys",
                    items={},
                    score_0_100=100,
                    success_strict=True,
                    critical_errors=[],
                )
                if name != "legacy":
                    ep["self_report"] = "success" if name == "structured" else None
                (run / "run_meta.json").write_text(json.dumps(meta))
                (run / "episode.jsonl").write_text(json.dumps(ep) + "\n")
                labels.append(
                    dict(
                        run=name,
                        self_report="failure",
                        self_report_source="heuristic_final_message",
                    )
                )
            backfill = root / "labels.json"
            backfill.write_text(json.dumps(labels))
            rows = {r["run"]: r for r in load([str(root)], backfill)}
            self.assertEqual(rows["structured"]["self_report"], "success")
            self.assertEqual(
                rows["structured"]["self_report_source"], "structured_done"
            )
            self.assertEqual(
                rows["legacy"]["self_report_source"], "heuristic_final_message"
            )
            self.assertIsNone(rows["unknown"]["self_report"])
            self.assertTrue(all(r["scene_version"] == 1 for r in rows.values()))

    def test_resume_rejects_old_desk_but_reuses_unchanged_scenes(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            for task, version, expected in (
                ("pilot_tidy_desk", 1, False),
                ("pilot_tidy_desk", 2, False),  # v2 had the ejection artifact
                ("pilot_tidy_desk", 3, False),  # v3 retained sustained ejections
                ("pilot_tidy_desk", 4, True),
                ("pilot_toys", 1, True),
            ):
                meta = dict(
                    completed=True,
                    model="m",
                    task=task,
                    seed=0,
                    turn_cap=30,
                    time_cap_s=1500,
                    suite_version="0.2",
                    scene_version=version,
                )
                ep = dict(
                    meta,
                    end_reason="agent_done",
                    score_0_100=100,
                    success_strict=True,
                    items={},
                    turns_used=5,
                    self_report="success",
                )
                (p / "run_meta.json").write_text(json.dumps(meta))
                (p / "episode.jsonl").write_text(json.dumps(ep) + "\n")
                self.assertEqual(complete(p, task, "m", 0), expected)

    def test_cleanup_handles_child_exit_race(self):
        from agent_pilot_runner import stop

        process = Mock(pid=123, returncode=0)
        process.poll.return_value = None
        with patch("agent_pilot_runner.os.killpg", side_effect=ProcessLookupError):
            stop(process)
        process.wait.assert_called_once()

    def test_structured_reports(self):
        self.assertEqual(
            parse_self_report("# Thought: checked\nSELF_REPORT: partial"), "partial"
        )
        self.assertIsNone(parse_self_report("looks successful"))
        self.assertIsNone(
            parse_self_report("SELF_REPORT: success\nSELF_REPORT: failure")
        )
        self.assertIsNone(parse_self_report("SELF_REPORT: successful"))

    def test_report_boundaries(self):
        for label, score, strict, critical, expected in [
            ("success", 100, True, [], True),
            ("success", 100, False, [], False),
            ("partial", 0, False, [], False),
            ("partial", 0.1, False, [], True),
            ("partial", 99.9, False, [], True),
            ("partial", 100, False, [], False),
            ("partial", 90, True, [], False),
            ("failure", 29.9, False, [], True),
            ("failure", 30, False, [], False),
            ("failure", 30, False, ["fall"], True),
            (None, 10, False, [], None),
        ]:
            self.assertEqual(
                report_correct(
                    dict(
                        self_report=label,
                        score_0_100=score,
                        success_strict=strict,
                        critical_errors=critical,
                    )
                ),
                expected,
            )

    def test_heuristic_conservative(self):
        self.assertEqual(classify("The task appears successful.")[0], "success")
        self.assertEqual(
            classify("Cleanup was partially successful; a tissue remains.")[0],
            "partial",
        )
        self.assertEqual(classify("认为任务成功。")[0], "success")
        self.assertEqual(classify("任务只完成了一部分，部分完成。")[0], "partial")
        self.assertEqual(classify("任务失败。")[0], "failure")
        self.assertIsNone(classify("I moved the cup.")[0])
        self.assertIsNone(classify("I believe it succeeded, but I am unsure.")[0])

    def test_missing_is_not_zero(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in ("recorded", "missing"):
                p = root / name
                p.mkdir()
                (p / "run_meta.json").write_text(
                    json.dumps(
                        dict(model="m", task="pilot_toys", tokens=None, wall_s=12)
                    )
                )
            (root / "recorded/episode.jsonl").write_text(
                json.dumps(
                    dict(
                        task="pilot_toys",
                        items={"blocks_in_wagon": 1, "mug_not_knocked": True},
                        score_0_100=100,
                        success_strict=True,
                        critical_errors=[],
                        self_report="success",
                    )
                )
                + "\n"
            )
            rows = load([str(root / "*")])
            s = summarize(rows, np.random.default_rng(0))
            self.assertEqual(s["score"]["mean"], 100)
            self.assertEqual((s["attempted"], s["recorded"], s["missing"]), (2, 1, 1))
            self.assertEqual(s["overclaim"]["n"], 1)
            self.assertIsNone(s["tokens"]["mean"])

    def test_native_records_and_projection(self):
        old = dict(
            task="pilot_toys",
            items=dict(
                blocks_in_wagon=1, mug_safe_distance=False, mug_not_knocked=True
            ),
            score_0_100=90,
            success_strict=False,
            critical_errors=[],
        )
        new = project_v01(old)
        self.assertEqual(new["score_0_100"], 100)
        self.assertTrue(new["success_strict"])
        self.assertFalse(new["proactive_safety"]["mug_safe_distance"])
        self.assertEqual(old["score_0_100"], 90)
        new = project_v01(dict(old, critical_errors=["fell_off_table:wagon"]))
        self.assertEqual(new["score_0_100"], 30)
        self.assertFalse(new["success_strict"])

    def test_wilson_not_degenerate(self):
        self.assertIsNone(wilson(0, 0))
        self.assertAlmostEqual(wilson(0, 5)[1], 0.4344824648)
        self.assertGreater(wilson(5, 5)[0], 0.56)
        self.assertLess(wilson(5, 5)[0], 0.57)

    def test_resume_requires_matching_completed_protocol(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = root / "pilot_toys__m__s3"
            p.mkdir()
            meta = dict(
                completed=True,
                model="m",
                task="pilot_toys",
                seed=3,
                turn_cap=30,
                time_cap_s=1500,
                suite_version="0.1",
            )
            (p / "run_meta.json").write_text(json.dumps(meta))
            self.assertFalse(complete(p, "pilot_toys", "m", 3))
            (p / "episode.jsonl").write_text(
                json.dumps(
                    dict(
                        seed=3,
                        end_reason="turn_cap",
                        task="pilot_toys",
                        suite_version="0.1",
                        score_0_100=10,
                        success_strict=False,
                        items={},
                        turns_used=30,
                        self_report=None,
                    )
                )
                + "\n"
            )
            self.assertTrue(complete(p, "pilot_toys", "m", 3))
            self.assertFalse(complete(p, "pilot_toys", "m", 4))
            self.assertFalse(complete(p, "pilot_toys", "m", 3, time_cap=1800))
            jobs, skips = plan(root, ["m"], [3], ["pilot_toys"], 30, 1500)
            self.assertEqual((len(jobs), len(skips)), (0, 1))
        self.assertEqual(seeds("0-2,5,1"), [0, 1, 2, 5])


if __name__ == "__main__":
    unittest.main()
