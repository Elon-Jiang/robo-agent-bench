# Suite v0.2.1 results

The [six-model report](reports/agentcli_v0.2.1_6models.md) covers all 600 episodes: six models × five scenes × seeds 0–19, every one completed. The [JSON report](reports/agentcli_v0.2.1_6models.json) includes per-episode scores and layout hashes.

| Directory under `runs/` | Scored episodes | Contents |
|---|---:|---|
| `suite_v0.1` | 40 | Both GPT models on seeds 0–4 of the four scenes carried forward from v0.1 |
| `suite_v0.2` | 160 | Remaining GPT episodes, including all 20 tidy-desk layouts on scene v4 |
| `agentcli_pilot` | 400 | 100 each for Fable, Opus, Sonnet and Haiku |

Each episode directory contains a final physics record (`episode.jsonl`) and harness metadata (`run_meta.json`). The CLI’s last message is stored as `codex_last_message.txt` when available. Claude runs also include `agent_usage.json` with the CLI’s list-price cost estimate.

All leaderboard episodes have `completed: true`. Attempts cut off by a CLI usage limit were archived (not published) and rerun from a fresh scene, like any other interrupted run. Episodes ending at the benchmark’s declared turn or time cap are eligible.

To regenerate the published report from the repository root, run the following with the benchmark Python environment. The report is written to `outputs/reports/`.

```sh
python scripts/report.py --inputs 'results/runs/*' --name agentcli_v0.2.1_6models
```

To repeat the Claude export from the original local runs, use `python scripts/export_results.py --suites agentcli_pilot`. The exporter selects completed attempts.
