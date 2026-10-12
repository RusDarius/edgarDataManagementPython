"""Post-event feedback for scan models, skills, and wisdom dumps.

Cases live in ``cases/*.json``. A case records what the pack and the
financial-projection model said before an event, what the tape did after,
and the rule that should change the next course. Skills read the open
cases before they treat a model terminal or a weeks score as a buy.
"""

from __future__ import annotations

import json
from pathlib import Path

CASES_DIR = Path(__file__).resolve().parent / "cases"


def load_cases() -> list[dict]:
    rows: list[dict] = []
    if not CASES_DIR.is_dir():
        return rows
    for path in sorted(CASES_DIR.glob("*.json")):
        rows.append(json.loads(path.read_text(encoding="utf-8")))
    return rows


def open_lessons() -> list[str]:
    """One sentence per case that is still a live rule, not a closed anecdote."""
    out: list[str] = []
    for case in load_cases():
        lesson = str(case.get("lesson") or "").strip()
        if lesson:
            out.append(lesson)
    return out
