"""Day-judgment scaffolding: block shapes from the templates, a skeleton file, carried courses.

The agent still writes every course and every block. This module only removes the
mechanical part: which block ids a template reads and in which shape (a stats block is a
list of {value, label, tone}, a callout a {title, text, tone}, ...), the playbook id lists
(`ids_from`), and carrying the prior day's courses and recorded scans forward so the
polar rule and the template's scan-fed tables keep working.

No course is chosen here. A carried course keeps its original Reason, prefixed with the
day it was written.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

_CARRIED = re.compile(r"^\d{1,2} [A-Z][a-z]{2} \(carried\): ")

SHAPES = {
    "stats": "list of {value, label, tone}",
    "list": "list of strings (markdown **bold** allowed)",
    "callout": "{title, text, tone}",
    "text": "string",
    "h2": "string",
    "h3": "string",
}


def template_shapes(template: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """{page_key: {block_id or list key: expected shape}} for every judgment hook in one template."""
    key = str(template.get("page_key") or "")
    out: dict[str, str] = {}
    for tab in template.get("tabs") or []:
        for block in tab.get("blocks") or []:
            source = str(block.get("from") or "")
            if source.startswith(f"pages.{key}.blocks."):
                kind = str(block.get("type") or "")
                shape = SHAPES.get(kind, kind)
                if kind == "table":
                    headers = block.get("headers") or []
                    shape = "{title?, rows: [[" + ", ".join(map(str, headers)) + "]], tones?, note?}"
                out[source.rsplit(".", 1)[1]] = f"{kind}: {shape} (tab {tab.get('label')})"
            ids_key = str(block.get("ids_from") or "")
            if ids_key.startswith(f"pages.{key}."):
                out[ids_key.split(".", 2)[2]] = f"ids: list of EXCHANGE:TICKER, row order (table {block.get('title')})"
    return {key: out} if key else {}


def _day_label(day: str) -> str:
    d = date(int(day[:4]), int(day[4:6]), int(day[6:8]))
    return f"{d.day} {d.strftime('%b')}"


def carry_courses(prev: Mapping[str, Any], *, prev_day: str) -> dict[str, dict[str, Any]]:
    """Prior courses with the Reason prefixed '<d Mon> (carried): ' (an already-carried Reason keeps its first date)."""
    label = _day_label(prev_day)
    out: dict[str, dict[str, Any]] = {}
    for symbol, course in (prev.get("courses") or {}).items():
        rec = dict(course or {})
        reason = str(rec.get("Reason") or "")
        if not _CARRIED.match(reason):
            rec["Reason"] = f"{label} (carried): {reason}"
        out[symbol] = rec
    return out


def build_skeleton(
    day: str,
    *,
    pack: str,
    templates: Sequence[Mapping[str, Any]],
    prev: Mapping[str, Any] | None = None,
    prev_day: str | None = None,
) -> dict[str, Any]:
    """Judgment skeleton: carried courses and scans, empty blocks, and `_shapes` per page (ignored by the renderer)."""
    pages: dict[str, Any] = {}
    for template in templates:
        for key, shapes in template_shapes(template).items():
            pages.setdefault(key, {"blocks": {}, "_shapes": {}})["_shapes"].update(shapes)
    return {
        "day": day,
        "pack": Path(pack).name,
        "courses": carry_courses(prev, prev_day=prev_day) if prev and prev_day else {},
        "pages": pages,
        "capital_plan": {"capital_plan": [], "capital_plan_queue": []},
        "scans": list((prev or {}).get("scans") or []),
    }


def unfilled(judgment: Mapping[str, Any]) -> dict[str, list[str]]:
    """Block ids a page's `_shapes` lists that the judgment has not filled yet (a missing block is dropped from the page)."""
    out: dict[str, list[str]] = {}
    for key, page in (judgment.get("pages") or {}).items():
        shapes = (page or {}).get("_shapes") or {}
        blocks = (page or {}).get("blocks") or {}
        missing = [b for b, shape in shapes.items() if (blocks.get(b) if not shape.startswith("ids:") else page.get(b)) in (None, "", [], {})]
        if missing:
            out[key] = missing
    return out


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
