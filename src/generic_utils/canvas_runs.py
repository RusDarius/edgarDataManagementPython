"""Folder layout for operator pages: one folder per scan day, one sub-folder per run.

    logs/canvases/
      index.html                  every day -> every run -> its pages, newest first
      YYYYMMDD/
        judgment.json             the day's live judgment (a full run writes it, UPDATE edits it)
        HHMM_<mode>/              one run (UTC start): full | update | manual | ide
          run.json                mode, pack, refresh.json, agent rc, review counts
          prompt.txt, agent.log   agent runs
          judgment.json           the judgment as this run's pages used it
          judgment.prev.json      the judgment the run started from (UPDATE, forced full)
          update.md, update.json  UPDATE delta report
          <page>.json / .html     pages rendered in this run
        <name>.json / .html       pages rendered outside a run (ad-hoc paste)

Paths, run records and the index only. No ranking, no judgment.
`TV_CANVAS_ROOT` moves the root (tests); `CANVAS_RUN_DIR` names the open run
for renders that do not pass an out dir (set for the headless agent).
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CANVAS_ROOT = REPO_ROOT / "logs" / "canvases"
RUN_ENV = "CANVAS_RUN_DIR"
_DAY = re.compile(r"^\d{8}$")
_RUN = re.compile(r"^(\d{4})_([a-z]+)(?:_\d+)?$")


def canvas_root() -> Path:
    return Path(os.environ.get("TV_CANVAS_ROOT") or DEFAULT_CANVAS_ROOT)


def day_dir(day: str) -> Path:
    return canvas_root() / day


def judgment_path(day: str) -> Path:
    return day_dir(day) / "judgment.json"


def previous_judgment(day: str) -> tuple[str, Path] | None:
    """(day, path) of the newest live judgment before ``day``, or None."""
    root = canvas_root()
    if not root.exists():
        return None
    days = sorted(p.name for p in root.iterdir() if _DAY.match(p.name) and p.name < day and (p / "judgment.json").exists())
    return (days[-1], root / days[-1] / "judgment.json") if days else None


def new_run_dir(day: str, mode: str, *, now: datetime | None = None) -> Path:
    """Create <day>/<HHMM>_<mode>; a second run in the same minute gets a _2 suffix."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%H%M")
    base = day_dir(day) / f"{stamp}_{mode}"
    path, n = base, 1
    while path.exists():
        n += 1
        path = base.with_name(f"{base.name}_{n}")
    path.mkdir(parents=True)
    write_run_record(path, day=day, mode=mode, opened_utc=(now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC"))
    return path


def open_run_dir() -> Path | None:
    value = os.environ.get(RUN_ENV)
    return Path(value) if value else None


def default_out_dir(day: str | None) -> Path:
    """Where a render with no out dir goes: the open run, else the day folder, else the root."""
    return open_run_dir() or (day_dir(day) if day else canvas_root())


def is_run_dir(path: str | Path) -> bool:
    p = Path(path)
    return bool(_RUN.match(p.name)) and bool(_DAY.match(p.parent.name))


def write_run_record(run_dir: str | Path, *, keep_existing: bool = False, **fields: Any) -> Path:
    """Merge fields into <run_dir>/run.json (None values ignored; keep_existing only fills missing keys)."""
    path = Path(run_dir) / "run.json"
    record = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    record.update({k: v for k, v in fields.items() if v is not None and not (keep_existing and k in record)})
    path.write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")
    return path


def snapshot(src: str | Path, run_dir: str | Path, name: str) -> Path | None:
    """Copy a file into the run folder (judgment.json / judgment.prev.json). Missing source -> None."""
    source, target = Path(src), Path(run_dir) / name
    if not source.exists() or source.resolve() == target.resolve():
        return None
    shutil.copyfile(source, target)
    return target


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _link(path: Path, root: Path, label: str | None = None) -> str:
    return f'<a href="{html.escape(_rel(path, root))}">{html.escape(label or path.stem)}</a>'


def _run_notes(record: Mapping[str, Any]) -> str:
    notes = []
    if record.get("pack"):
        notes.append(f"pack {Path(str(record['pack'])).name.replace('briefing_pack_', '')}")
    if record.get("n_review") is not None:
        notes.append(f"{record['n_review']} of {record.get('n_judged', '?')} to review")
    if record.get("agent_skipped"):
        notes.append("agent skipped")
    elif "agent_rc" in record:
        notes.append(f"agent rc {record['agent_rc']}")
    if record.get("origin"):
        notes.append(str(record["origin"]))
    return " · ".join(notes)


def write_index(root: str | Path | None = None) -> Path:
    """Master index: days newest first, each run with its pages and files."""
    from generic_utils.canvas_page import _CSS

    base = Path(root) if root else canvas_root()
    base.mkdir(parents=True, exist_ok=True)
    days = sorted((d for d in base.iterdir() if d.is_dir() and _DAY.match(d.name)), key=lambda d: d.name, reverse=True)
    sections, n_runs, n_pages = [], 0, 0
    for day in days:
        rows = []
        runs = sorted((r for r in day.iterdir() if r.is_dir() and _RUN.match(r.name)), key=lambda r: r.name, reverse=True)
        for run in runs:
            rec_path = run / "run.json"
            record = json.loads(rec_path.read_text(encoding="utf-8")) if rec_path.exists() else {}
            pages = sorted(run.glob("*.html"))
            files = [p for p in (run / "update.md", run / "agent.log", run / "prompt.txt", run / "judgment.json", rec_path) if p.exists()]
            n_runs += 1
            n_pages += len(pages)
            rows.append(f"<tr><td>{html.escape(run.name)}</td><td>{html.escape(str(record.get('mode') or _RUN.match(run.name).group(2)))}</td>"
                        f"<td>{' · '.join(_link(p, base) for p in pages) or '-'}</td>"
                        f"<td>{' · '.join(_link(p, base, p.name) for p in files)}</td>"
                        f"<td>{html.escape(_run_notes(record))}</td></tr>")
        loose = sorted(day.glob("*.html"))
        if loose:
            n_pages += len(loose)
            rows.append(f"<tr><td>(day folder)</td><td>ad-hoc</td><td>{' · '.join(_link(p, base) for p in loose)}</td><td></td><td></td></tr>")
        judgment = day / "judgment.json"
        title = datetime.strptime(day.name, "%Y%m%d").strftime("%d %b %Y")
        head = f"<h2>{title}</h2>" + (f'<div class="secondary">live judgment: {_link(judgment, base, "judgment.json")}</div>' if judgment.exists() else "")
        sections.append(head + '<div class="scroll"><table><thead><tr><th>Run (UTC)</th><th>Mode</th><th>Pages</th>'
                        f'<th>Files</th><th>Notes</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')
    unfiled = sorted((p for p in base.glob("*.html") if p.name != "index.html"), key=lambda p: p.stat().st_mtime, reverse=True)
    if unfiled:
        n_pages += len(unfiled)
        sections.append("<h2>Unfiled (old flat layout)</h2><ul>" + "".join(f"<li>{_link(p, base)}</li>" for p in unfiled) + "</ul>")
    doc = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Operator pages</title><style>{_CSS}</style></head>'
           f'<body><header><h1>Operator pages</h1><div class="secondary">{len(days)} days · {n_runs} runs · {n_pages} pages, newest first</div></header>'
           f'<main>{"".join(sections)}</main></body></html>')
    out = base / "index.html"
    out.write_text(doc, encoding="utf-8")
    return out
