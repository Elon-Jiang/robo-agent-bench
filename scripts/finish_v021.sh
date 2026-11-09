#!/usr/bin/env bash
# Finish suite v0.2.1 (3 lanes to limit memory): remaining seeds 5-19 of the four
# unchanged scenes, tidy_desk v4 seeds 0-19, then the combined report. Resumable.
set -e
cd "$(dirname "$0")/.."
PY=../mujoco_playground/.venv/bin/python
M="gpt-6-astra gpt-5.6-luna"
$PY scripts/eval_suite.py --models $M --tasks pilot_spill_cleanup pilot_clear_dishes pilot_toys pilot_breakfast \
  --seeds 5-19 --lanes 3 --name suite_v0.2
$PY scripts/eval_suite.py --models $M --tasks pilot_tidy_desk --seeds 0-19 --lanes 3 --name suite_v0.2
$PY scripts/report.py --models $M --name suite_v0.2.1_s0-19 --inputs \
  'outputs/suites/suite_v0.1/pilot_spill_cleanup__*' 'outputs/suites/suite_v0.1/pilot_clear_dishes__*' \
  'outputs/suites/suite_v0.1/pilot_toys__*' 'outputs/suites/suite_v0.1/pilot_breakfast__*' \
  'outputs/suites/suite_v0.2/pilot_*'
echo FINISHED_ALL
