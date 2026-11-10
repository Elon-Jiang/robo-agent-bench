# Suite v0.2.1 results

The [six-model report](reports/agentcli_v0.2.1_6models.md) covers 599 completed episodes. The six models attempted seeds 0–19 on each of five scenes. The Fable toy results cover seeds 0–18. The [JSON report](reports/agentcli_v0.2.1_6models.json) includes per-episode scores and layout hashes.

| Directory under `runs/` | Scored episodes | Contents |
|---|---:|---|
| `suite_v0.1` | 40 | Both GPT models on seeds 0–4 of the four scenes carried forward from v0.1 |
| `suite_v0.2` | 160 | Remaining GPT episodes, including all 20 tidy-desk layouts on scene v4 |
| `agentcli_pilot` | 399 | 99 Fable episodes and 100 each for Opus, Sonnet and Haiku |

Each episode directory contains a final physics record (`episode.jsonl`) and harness metadata (`run_meta.json`). The CLI’s last message is stored as `codex_last_message.txt` when available. Claude runs also include `agent_usage.json` with the CLI’s list-price cost estimate.

All leaderboard episodes have `completed: true`. The excluded [Fable toy attempt, seed 19](interrupted/pilot_toys__claude-fable-5-2__s19) ended when the CLI hit its five-hour usage limit. The harness ended the simulation after 12 turns, with three blocks in the wagon and the mug undisturbed. The saved score of 64 is available for inspection. All published metrics exclude this attempt. Episodes ending at the benchmark’s declared turn or time cap are eligible.

To regenerate the published report from the repository root, run the following with the benchmark Python environment. The report is written to `outputs/reports/`.

```sh
python scripts/report.py --inputs 'results/runs/*' --name agentcli_v0.2.1_6models
```

To repeat the Claude export from the original local runs, use `python scripts/export_results.py --suites agentcli_pilot`. The exporter selects completed attempts.
