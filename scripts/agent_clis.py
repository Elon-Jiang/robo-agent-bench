"""Agent CLI adapters for the "Agent CLI track": each vendor's own headless agent.

The model name picks the CLI: claude-* -> Claude Code, gemini-* -> Gemini CLI,
anything else -> Codex CLI. Every CLI gets the same prompt, caps and capabilities:
a shell (for curl to the bridge) and an image-capable file reader; no file editing,
no web access, no user plugins/MCP servers.

Isolation: Claude Code and Gemini CLI auto-load instruction files (CLAUDE.md /
GEMINI.md) from the working directory and all of its parents. Their working
directory is therefore a fresh workspace outside this repository tree
(``RAB_AGENT_WS``, default ~/.rab_agent_ws/<run>), with the run directory and the
upstream AGENT_PROMPT.md directory added read-only by flag. Codex keeps the run
directory as its workspace (it reads AGENTS.md only within a git root; verified).
"""
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_PROMPT_DIR = (ROOT / "../mujoco_playground/mujoco_playground/tasks/agentic/tabletop_v0").resolve()
LAST = "codex_last_message.txt"  # historical name, kept so export/report tooling is unchanged
LOG = "codex_session.log"


# Claude Code's OS sandbox (Seatbelt on macOS) for every Bash command: writes only in
# the agent workspace, no network except the local bridge, no unsandboxed escape hatch,
# and fail closed if the sandbox is unavailable. Parity with Codex workspace-write.
CLAUDE_SANDBOX = {
    "sandbox": {
        "enabled": True,
        "failIfUnavailable": True,
        "autoAllowBashIfSandboxed": True,
        "allowUnsandboxedCommands": False,
        "network": {"allowedDomains": ["127.0.0.1", "localhost"], "allowLocalBinding": True},
        "filesystem": {"denyRead": ["~/.ssh", "~/.aws", "~/.config/gh", "~/.codex",
                                     "~/.gemini", "~/.gitconfig"]},
    },
}


def cli_for(model):
    if model.startswith("claude"):
        return "claude"
    if model.startswith("gemini"):
        return "gemini"
    return "codex"


def workspace(out):
    base = Path(os.environ.get("RAB_AGENT_WS", Path.home() / ".rab_agent_ws"))
    ws = base / out.name
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def build(model, out, prompt):
    """Return (argv, cwd, env) for one headless agent episode."""
    cli = cli_for(model)
    env = dict(os.environ)
    if cli == "codex":
        argv = ["codex", "exec", "-m", model, "--approve-for-me", "--skip-git-repo-check",
                "-c", "sandbox_workspace_write.network_access=true",
                "-C", str(out), "-o", str(out / LAST), prompt]
        return argv, out, env
    ws = workspace(out)
    if cli == "claude":
        for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):  # not a nested session of ours
            env.pop(k, None)
        argv = ["claude", "-p", prompt, "--model", model,
                "--output-format", "stream-json", "--verbose",  # full tool trace for audit
                "--setting-sources", "",  # no user/project settings, plugins or hooks
                "--settings", json.dumps(CLAUDE_SANDBOX),
                "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                # Same capability set as the other CLIs: shell + file/image reading only
                # (no sub-agents, workflows, scheduling, editing or web tools).
                "--tools", "Bash,Read,Glob,Grep",
                "--allowedTools", "Bash", "Read", "Glob", "Grep",
                "--add-dir", str(out), str(UPSTREAM_PROMPT_DIR)]
        return argv, ws, env
    # No yolo: only the shell and file reader are pre-approved; headless mode denies
    # anything else (e.g. edits). Array flags are repeated, not space-separated.
    argv = ["gemini", "-p", prompt, "-m", model, "--output-format", "json",
            "--allowed-tools", "run_shell_command", "--allowed-tools", "read_file",
            "--allowed-tools", "read_many_files", "--allowed-tools", "glob",
            "--allowed-mcp-server-names", "__none__",
            "--include-directories", f"{out},{UPSTREAM_PROMPT_DIR}"]
    return argv, ws, env


def finish(model, out, log_text):
    """Extract (tokens, last_message) from the CLI's output; write LAST for non-Codex."""
    cli = cli_for(model)
    if cli == "codex":
        m = re.findall(r"tokens used\s*\n([\d,]+)", log_text)
        return (int(m[-1].replace(",", "")) if m else None), None
    blob = _last_json(log_text)
    if blob is None:
        return None, None
    if cli == "claude":  # last stream-json event is {"type": "result", usage, result, ...}
        u = blob.get("usage") or {}
        tokens = sum(int(u.get(k) or 0) for k in (
            "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
            "output_tokens")) or None
        msg = blob.get("result")
        extra = dict(cost_usd=blob.get("total_cost_usd"), num_turns=blob.get("num_turns"))
    else:
        models = ((blob.get("stats") or {}).get("models") or {})
        tokens = sum(int((v.get("tokens") or {}).get("total") or 0) for v in models.values()) or None
        msg = blob.get("response")
        extra = {}
    if msg:
        (out / LAST).write_text(msg)
    (out / "agent_usage.json").write_text(json.dumps(dict(cli=cli, tokens=tokens, **extra), indent=2))
    return tokens, msg


def _last_json(text):
    """The CLIs print one JSON object on stdout; stderr noise may surround it."""
    for start in [m.start() for m in re.finditer(r"^\{", text, re.M)][::-1]:
        try:
            return json.loads(text[start:])
        except ValueError:
            depth = 0
            for i, ch in enumerate(text[start:]):
                depth += ch == "{"
                depth -= ch == "}"
                if depth == 0:
                    try:
                        return json.loads(text[start:start + i + 1])
                    except ValueError:
                        break
    return None
