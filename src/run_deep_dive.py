"""Deep-dive agent: one name or a suite of names (or a question over them), the project's data plus the agent's outside read.

DATA ONLY (deterministic): the dossier (generic_utils.name_dossier) and the data-only page.
    python src/run_deep_dive.py --ids NYSE:DOW
    python src/run_deep_dive.py --theme memory_storage

AGENT (same data, then a headless Claude run: outside research, catalysts, trajectory per horizon, band / kill / targets,
verdicts, the page):
    python src/run_deep_dive.py --ids NYSE:DOW --agent
    python src/run_deep_dive.py --ids NASDAQ:AMZN,NASDAQ:GOOG,NASDAQ:MSFT,NASDAQ:NVDA \
        --question "Position how trading the Anthropic IPO could work for the market and the company" --agent
    python src/run_deep_dive.py --ids NYSE:DOW --print-prompt      # paste the prompt into the IDE instead

From Python (src/main.py): deep_dive_analysis_agent("NYSE:DOW") or deep_dive_analysis_agent(["NASDAQ:AMZN", ...], question=...).

Folder: logs/tradingview_analysis/name_dossier/<YYYYMMDD_HHMM>_<label>/ (dossier.md, dossier.duckdb + CSVs,
dossier.judgment.skeleton.json, prompt.txt, agent.log, dossier.judgment.json, dossier-<label>.html).
Prompt: src/research_prompt/agent_runs/deep_dive.txt. Skill: .claude/skills/deep-dive-agent/SKILL.md.
No ranking and no second budget: verdicts are candidates for the Daily's queue.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

SRC_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SRC_ROOT.parent
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

PROMPT = SRC_ROOT / "research_prompt" / "agent_runs" / "deep_dive.txt"


def _rel(path: str | Path) -> str:
    try:
        return Path(path).resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return Path(path).as_posix()


def build_prompt(out_dir: str | Path, ids: Sequence[str], question: str | None) -> str:
    text = PROMPT.read_text(encoding="utf-8")
    mapping = {"DIR": _rel(out_dir), "IDS": ", ".join(ids), "QUESTION": question or "(none: the names themselves)"}
    for key, value in mapping.items():
        text = text.replace("{" + key + "}", value)
    return text


def deep_dive_analysis_agent(
    ids: str | Sequence[str] = (),
    *,
    question: str | None = None,
    theme: str | None = None,
    label: str | None = None,
    agent: bool = True,
    print_prompt: bool = False,
    pack: str | None = None,
    model: str | None = None,
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Dossier + data-only page, then (agent=True) the headless agent run that fills the judgment and renders the page."""
    from generic_utils.name_dossier import build_dossier, dossier_page, judgment_skeleton, write_dossier

    id_list = [ids] if isinstance(ids, str) else list(ids)
    id_list = [s.strip() for part in id_list for s in str(part).split(",") if s.strip()]
    payload = build_dossier(id_list, pack=pack, question=question, theme=theme, label=label)
    exported = write_dossier(payload, out_dir)
    folder = Path(exported["out_dir"])
    (folder / "dossier.judgment.skeleton.json").write_text(json.dumps(judgment_skeleton(folder), indent=1, ensure_ascii=False), encoding="utf-8")
    page = dossier_page(folder)
    prompt = build_prompt(folder, payload["spec"]["ids"], question)
    (folder / "prompt.txt").write_text(prompt, encoding="utf-8")
    out: dict[str, Any] = {"dir": folder.as_posix(), "ids": payload["spec"]["ids"], "dossier": exported["dossier"],
                           "page": page["html"], "notes": payload["spec"].get("notes")}
    if print_prompt or not agent:
        out["prompt"] = prompt if print_prompt else None
        return out
    from run_operator_day import run_agent

    out["agent_rc"] = run_agent(prompt, run_dir=folder, model=model)
    out["agent_log"] = (folder / "agent.log").as_posix()
    if (folder / "dossier.judgment.json").exists():
        out["page"] = dossier_page(folder)["html"]
    return out


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="Deep-dive agent on named ids, a theme or a question.")
    parser.add_argument("--ids", default="", help="EXCHANGE:TICKER ids, comma separated (bare tickers are qualified).")
    parser.add_argument("--theme", default=None, help="Add a config/generic_utils/themes.json theme's ids.")
    parser.add_argument("--question", default=None, help="A question the agent answers over the names.")
    parser.add_argument("--label", default=None)
    parser.add_argument("--pack", default=None, help="Briefing pack folder (default the newest).")
    parser.add_argument("--agent", action="store_true", help="Launch the headless agent after the data step.")
    parser.add_argument("--print-prompt", action="store_true", help="Print the agent prompt instead of launching it.")
    parser.add_argument("--model", default=None)
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args(argv)
    result = deep_dive_analysis_agent(args.ids, question=args.question, theme=args.theme, label=args.label, agent=args.agent,
                                      print_prompt=args.print_prompt, pack=args.pack, model=args.model, out_dir=args.out_dir)
    if result.get("prompt"):
        print(result["prompt"])
    print(json.dumps({k: v for k, v in result.items() if k != "prompt"}, indent=1, default=str))
    return result


def run_deep_dive_local_main() -> dict[str, Any]:
    """No-arg default: the data-only dossier on NYSE:DOW."""
    return deep_dive_analysis_agent("NYSE:DOW", agent=False)


if __name__ == "__main__":
    main()
