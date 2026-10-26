"""Mesh letters (F C P D T) for a playbook base list. Labels, not a score.

Each letter is on or off by the rule written on the canvas Ledger
(config/generic_utils/mesh.json holds the same numbers). `mesh` is the
letters that are on, in order F C P D T, or `-` when none are. `nmesh` is
the count. Nothing here ranks, filters, or drops a row.

- F: pack forming sleeve, or weeks `early` >= early_min with `rng` < forming_rng_max
- C: earnings `dte` <= catalyst_dte_max
- P: 25-week `bo_chg` >= progression_bo_min with at least progression_min_weeks weeks
- D: setup peer discount, `val_vs_ind` <= discount_max
- T: `tech_sma` ABOVE and `rng` < trend_rng_max
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.ranking import to_float

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE_PATH = REPO_ROOT / "config" / "generic_utils" / "mesh.json"
LETTERS = ("F", "C", "P", "D", "T")


def load_mesh_recipe(path: str | Path | None = None) -> dict[str, Any]:
    recipe_path = Path(path) if path else DEFAULT_RECIPE_PATH
    return json.loads(recipe_path.read_text(encoding="utf-8"))


def _forming_tag(row: Mapping[str, Any]) -> bool:
    tags = row.get("sleeve_tags")
    if isinstance(tags, (list, tuple)):
        return "forming" in tags
    return "forming" in str(tags or "")


def mesh_flags(
    row: Mapping[str, Any],
    *,
    span: Mapping[str, Any] | None = None,
    forming_ids: set[str] | None = None,
    recipe: Mapping[str, Any] | None = None,
) -> dict[str, bool]:
    used = recipe or load_mesh_recipe()
    sym = str(row.get("symbol") or "")
    rng = to_float(row.get("rng"))
    early = to_float(row.get("early"))
    dte = to_float(row.get("dte"))
    peer = to_float(row.get("val_vs_ind"))
    span = span or {}
    bo_chg = to_float(span.get("bo_chg"))
    n_weeks = to_float(span.get("n"))
    forming = _forming_tag(row) or (forming_ids is not None and sym in forming_ids)
    return {
        "F": forming or (early is not None and early >= used["early_min"] and rng is not None and rng < used["forming_rng_max"]),
        "C": dte is not None and 0 <= dte <= used["catalyst_dte_max"],
        "P": bo_chg is not None and bo_chg >= used["progression_bo_min"] and (n_weeks or 0) >= used["progression_min_weeks"],
        "D": peer is not None and peer <= used["discount_max"],
        "T": str(row.get("tech_sma") or "").upper() == "ABOVE" and rng is not None and rng < used["trend_rng_max"],
    }


def attach_mesh(
    rows: Sequence[Mapping[str, Any]],
    *,
    spans: Mapping[str, Mapping[str, Any]] | None = None,
    forming_ids: set[str] | None = None,
    recipe: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Copy each row and add mesh, nmesh, mesh_<letter>, and bo_chg / span_n from the span."""
    used = recipe or load_mesh_recipe()
    out: list[dict[str, Any]] = []
    for row in rows:
        span = (spans or {}).get(str(row.get("symbol") or ""), {})
        flags = mesh_flags(row, span=span, forming_ids=forming_ids, recipe=used)
        letters = "".join(letter for letter in LETTERS if flags[letter])
        rec = dict(row)
        rec["mesh"] = letters or "-"
        rec["nmesh"] = len(letters)
        rec["bo_chg"] = to_float(span.get("bo_chg"))
        rec["span_n"] = to_float(span.get("n"))
        for letter in LETTERS:
            rec[f"mesh_{letter}"] = flags[letter]
        out.append(rec)
    return out


def write_mesh(
    rows: Sequence[Mapping[str, Any]],
    out_dir: str | Path,
    *,
    spans: Mapping[str, Mapping[str, Any]] | None = None,
    forming_ids: set[str] | None = None,
    recipe: Mapping[str, Any] | None = None,
    source_note: str | None = None,
) -> dict[str, Any]:
    """Write mesh.csv, mesh.duckdb, queries.txt, overview.log."""
    from generic_utils.run_export import write_run_export

    used = recipe or load_mesh_recipe()
    attached = attach_mesh(rows, spans=spans, forming_ids=forming_ids, recipe=used)
    notes = [
        "mesh is the letters that are on, in order F C P D T. '-' means none. nmesh is the count. Not a score.",
        f"F forming sleeve, or early >= {used['early_min']} with rng < {used['forming_rng_max']}.",
        f"C dte <= {used['catalyst_dte_max']}. P 25-week bo_chg >= {used['progression_bo_min']} with >= {used['progression_min_weeks']} weeks.",
        f"D val_vs_ind <= {used['discount_max']} (setup peer only). T tech_sma ABOVE and rng < {used['trend_rng_max']}.",
        "Mesh 3+ is the filter nmesh >= 3. Best 25 is still judgment.",
    ]
    if source_note:
        notes.insert(0, source_note)
    return write_run_export(
        out_dir,
        tool="generic_utils.mesh",
        tables={"mesh": attached},
        sources={"recipe": str(used.get("name") or "mesh"), "n": len(attached)},
        notes=notes,
        duckdb_name="mesh.duckdb",
    )
