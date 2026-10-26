"""One command for the operator day: manual brief or agent-prompted run.

MANUAL (deterministic, no agent): latest scans -> compile -> every suite dump ->
data-only pages (*-manual.html).
    python src/run_operator_day.py
    python src/run_operator_day.py --no-producers          # reuse the newest scans

AGENT (same refresh, then a headless Claude Code run with the day's prompt):
    python src/run_operator_day.py --agent                 # full Daily if today has no judgment yet, else UPDATE
    python src/run_operator_day.py --agent --mode full     # force the full DAILY + MOVERS + PLAYBOOK run
    python src/run_operator_day.py --agent --mode update   # force UPDATE (review only what moved)
    python src/run_operator_day.py --agent --print-prompt  # refresh, then print the prompt to paste in the IDE
    UPDATE skips the agent when nothing moved past a threshold (--force-agent to run it anyway).

Prompts: src/research_prompt/agent_runs/daily_full.txt and daily_update.txt.
Claude CLI: $CLAUDE_CLI, `claude` on PATH, or the IDE extension binary.

Every run gets its own folder, logs/canvases/<day>/<HHMM>_<full|update|manual>/
(run.json, prompt.txt, agent.log, judgment snapshots, update.md, the pages);
the day's live judgment is logs/canvases/<day>/judgment.json and
logs/canvases/index.html lists every day and run (generic_utils/canvas_runs.py).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SRC_ROOT.parent
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

PROMPTS = SRC_ROOT / "research_prompt" / "agent_runs"

# Tools the headless agent may use without a prompt. Project .claude/settings.json still applies.
AGENT_ALLOWED_TOOLS = [
    "Read", "Grep", "Glob", "Edit", "Write", "Skill", "WebSearch", "WebFetch",
    "Bash(python src/*)", "Bash(python -m pytest *)", "Bash(git status *)", "Bash(git diff *)",
]


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _repo_rel(path: str | Path) -> str:
    try:
        return Path(path).resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return Path(path).as_posix()


def run_manual(day: str, run_dir: Path, *, producers: bool = True, serial: bool = False) -> dict[str, Any]:
    """Fresh briefing and data with the latest inputs, pages into `run_dir`. No agent judgment."""
    from run_daily_refresh import run_daily_refresh

    return run_daily_refresh(producers=producers, compile_pack=True, suites=True, serial=serial, pages=True, run_dir=run_dir)


def pick_mode(day: str, mode: str) -> str:
    from generic_utils.canvas_runs import judgment_path

    if mode in ("full", "update"):
        return mode
    return "update" if judgment_path(day).exists() else "full"


def prepare_agent_data(day: str, mode: str, run_dir: Path, *, producers: bool = True, serial: bool = False) -> dict[str, Any]:
    """Deterministic part of the agent run: refresh (and for UPDATE, replay + diff + re-render into `run_dir`)."""
    from generic_utils.canvas_runs import judgment_path, snapshot
    from run_daily_refresh import run_daily_refresh

    if mode == "full":
        snapshot(judgment_path(day), run_dir, "judgment.prev.json")  # a forced full run replaces the live judgment
    return run_daily_refresh(producers=producers, compile_pack=True, suites=True, serial=serial,
                             pages=False, update_day=day if mode == "update" else None, run_dir=run_dir)


def write_skeleton(day: str, run_dir: Path, *, pack: str) -> Path | None:
    """Full mode: <run>/judgment.skeleton.json (template block ids + shapes, the prior day's courses and scans carried).

    Written in the run folder, never as the live judgment: a live file would turn the next run into UPDATE.
    """
    from generic_utils.canvas_page import load_template
    from generic_utils.canvas_runs import previous_judgment
    from generic_utils.judgment_tools import build_skeleton, load_json
    from generic_utils.page_update import load_refresh_config

    prior = previous_judgment(day)
    templates = [load_template(t) for t in load_refresh_config().get("templates") or []]
    skeleton = build_skeleton(day, pack=pack, templates=templates,
                              prev=load_json(prior[1]) if prior else None, prev_day=prior[0] if prior else None)
    path = run_dir / "judgment.skeleton.json"
    path.write_text(json.dumps(skeleton, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def build_agent_prompt(day: str, mode: str, *, pack: str, refresh_json: str | None, run_dir: str | Path | None = None) -> str:
    from generic_utils.canvas_runs import judgment_path

    text = (PROMPTS / f"daily_{mode}.txt").read_text(encoding="utf-8")
    text = text.replace("{{PACK}}", "\0")  # literal placeholder the agent writes into judgment scans
    mapping = {"DAY": day, "PACK": pack, "REFRESH_JSON": refresh_json or "n/a",
               "RUN_DIR": _repo_rel(run_dir) if run_dir else f"logs/canvases/{day}",
               "JUDGMENT": _repo_rel(judgment_path(day))}
    for key, value in mapping.items():
        text = text.replace("{" + key + "}", value)
    return text.replace("\0", "{PACK}")


def find_claude_cli() -> str | None:
    explicit = os.environ.get("CLAUDE_CLI")
    if explicit and Path(explicit).exists():
        return explicit
    on_path = shutil.which("claude")
    if on_path:
        return on_path
    home = Path.home()
    exe = "claude.exe" if os.name == "nt" else "claude"
    candidates: list[Path] = []
    for root in (home / ".vscode" / "extensions", home / ".cursor" / "extensions", home / ".vscode-insiders" / "extensions"):
        candidates += list(root.glob(f"anthropic.claude-code-*/resources/native-binary/{exe}"))
    candidates += [home / ".local" / "bin" / exe, home / ".claude" / "local" / exe]
    existing = [c for c in candidates if c.exists()]
    return str(max(existing, key=lambda p: p.stat().st_mtime)) if existing else None


def run_agent(prompt: str, *, run_dir: Path, cli: str | None = None, model: str | None = None) -> int:
    """Headless Claude Code run (`claude -p`) from the repo root. Streams to stdout and <run_dir>/agent.log.
    CANVAS_RUN_DIR sends page renders that omit --out-dir to the same run folder."""
    from generic_utils.canvas_runs import RUN_ENV

    binary = cli or find_claude_cli()
    if not binary:
        raise SystemExit("Claude CLI not found. Set CLAUDE_CLI, install `claude`, or rerun with --print-prompt and paste it.")
    argv = [binary, "-p", prompt, "--permission-mode", "acceptEdits"]
    if model:
        argv += ["--model", model]
    argv += ["--allowedTools", *AGENT_ALLOWED_TOOLS]  # variadic: keep it last
    log_path = run_dir / "agent.log"
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(SRC_ROOT), str(REPO_ROOT)]), PYTHONIOENCODING="utf-8")
    env[RUN_ENV] = str(run_dir)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n=== {datetime.now(timezone.utc).isoformat()} agent run ({binary})\n")
        proc = subprocess.Popen(argv, cwd=REPO_ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            log.write(line)
        return proc.wait()


def run_operator_day(
    *,
    agent: bool = False,
    mode: str = "auto",
    day: str | None = None,
    producers: bool = True,
    serial: bool = False,
    print_prompt: bool = False,
    model: str | None = None,
    force_agent: bool = False,
) -> dict[str, Any]:
    from generic_utils.canvas_runs import judgment_path, new_run_dir, snapshot, write_index, write_run_record

    day = day or today()
    if not agent:
        run_dir = new_run_dir(day, "manual")
        record = run_manual(day, run_dir, producers=producers, serial=serial)
        return {"mode": "manual", "day": day, "run_dir": run_dir.as_posix(), "pack": record.get("pack"),
                "pages": record.get("manual_pages"), "refresh_json": record.get("refresh_json")}
    chosen = pick_mode(day, mode)
    run_dir = new_run_dir(day, chosen)
    record = prepare_agent_data(day, chosen, run_dir, producers=producers, serial=serial)
    if chosen == "full":
        write_skeleton(day, run_dir, pack=record["pack"])
    prompt = build_agent_prompt(day, chosen, pack=record["pack"], refresh_json=record.get("refresh_json"), run_dir=run_dir)
    (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    out: dict[str, Any] = {"mode": f"agent:{chosen}", "day": day, "run_dir": run_dir.as_posix(), "pack": record["pack"],
                           "refresh_json": record.get("refresh_json")}
    if chosen == "update" and record.get("update"):
        out["update_md"] = record["update"].get("update_md")
        out["n_review"] = record["update"].get("n_review")
    write_run_record(run_dir, pack=record["pack"], refresh_json=record.get("refresh_json"))
    if print_prompt:
        write_run_record(run_dir, origin="prompt printed for the IDE")
        write_index()
        out["prompt"] = prompt
        return out
    if chosen == "update" and not out.get("n_review") and not force_agent:
        out["agent_rc"] = None
        out["agent_skipped"] = "nothing moved past a threshold; pages re-rendered with the same judgment"
    else:
        out["agent_rc"] = run_agent(prompt, run_dir=run_dir, model=model)
        out["agent_log"] = (run_dir / "agent.log").as_posix()
        snapshot(judgment_path(day), run_dir, "judgment.json")  # the judgment as the agent left it
    write_run_record(run_dir, agent_rc=out.get("agent_rc"), agent_skipped=out.get("agent_skipped"),
                     closed_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    write_index()
    return out


def run_operator_day_local_main() -> dict[str, Any]:
    """No-arg default: the manual brief with the latest inputs."""
    return run_operator_day(agent=False)


def main(argv: list[str] | None = None) -> dict[str, Any]:
    if argv is None and len(sys.argv) == 1:
        result = run_operator_day_local_main()
    else:
        parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
        parser.add_argument("--agent", action="store_true", help="Agent-prompted run after the refresh.")
        parser.add_argument("--mode", choices=("auto", "full", "update"), default="auto")
        parser.add_argument("--day", default=None, help="YYYYMMDD (default: today, UTC).")
        parser.add_argument("--no-producers", action="store_true", help="Reuse the newest scans.")
        parser.add_argument("--serial", action="store_true")
        parser.add_argument("--print-prompt", action="store_true", help="Print the agent prompt instead of launching Claude.")
        parser.add_argument("--model", default=None, help="Claude model for the agent run.")
        parser.add_argument("--force-agent", action="store_true", help="Launch the UPDATE agent even when nothing moved.")
        args = parser.parse_args(argv)
        result = run_operator_day(agent=args.agent, mode=args.mode, day=args.day, producers=not args.no_producers,
                                  serial=args.serial, print_prompt=args.print_prompt, model=args.model,
                                  force_agent=args.force_agent)
    prompt = result.pop("prompt", None)
    print(json.dumps(result, indent=1, default=str))
    if prompt:
        print("\n----- PROMPT -----\n" + prompt)
    return result


if __name__ == "__main__":
    main()
