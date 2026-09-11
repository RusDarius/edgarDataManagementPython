"""Street / TradingView leftover targets joined to financial_projection dumps.

Field attach only. No eligibility cutoff and no 0–100 composite.

Street leftover, pack leftover, and model terminals stay separate columns.
Empty ``pe_fwd`` is a data gap — do not fill it with EV/Rev.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.derive import attach_implied_targets
from generic_utils.ranking import ticker_of, to_float

DEFAULT_RECIPE_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "generic_utils"
    / "forward_value.json"
)

DEFAULT_COVER = 1000
MAX_COVER = 1000

FORWARD_KEEP = (
    "fwd_street_pct",
    "fwd_street_px",
    "fwd_pack_pct",
    "fwd_pack_px",
    "fwd_pe",
    "fwd_peg",
    "fp_terminal_px",
    "fp_terminal_upside_pct",
    "fp_primary_upside_pct",
    "fp_primary_source",
    "fp_street_upside_pct",
    "fp_y1_upside_pct",
    "fp_fy_eps_upside_pct",
    "fp_lens",
    "fp_regime",
    "fp_scenario",
    "fp_rev_cagr_own",
    "fp_ebitda_cagr_own",
    "fp_y1_rev_own",
    "fp_growth_source",
    "fp_growth_lane",
    "fp_price_run",
    "fp_growth_run",
    "fp_joined",
)


def load_forward_recipe(path: str | Path | None = None) -> dict[str, Any]:
    from generic_utils.run_export import load_json_recipe

    return load_json_recipe(path or DEFAULT_RECIPE_PATH)


def clamp_cover(n: int | None, *, default: int = DEFAULT_COVER, ceiling: int = MAX_COVER) -> int:
    """Suite size. Not leftover/RSI eligibility and not an industry cap."""
    if n is None:
        return int(default)
    return max(1, min(int(n), int(ceiling)))


def _round(value: Any, digits: int = 2) -> float | None:
    number = to_float(value)
    if number is None:
        return None
    return round(number, digits)


def keep_with_forward(keep: Sequence[str]) -> list[str]:
    out = list(keep)
    for extra in FORWARD_KEEP:
        if extra not in out:
            out.append(extra)
    return out


def discover_latest_projection_db(
    root: str | Path | None = None,
    *,
    prefix: str = "finproj_",
) -> Path | None:
    """Newest DuckDB under financial_projection whose stem starts with ``prefix``."""
    from financial_projection.config import DEFAULT_OUTPUT_ROOT

    base = Path(root) if root else DEFAULT_OUTPUT_ROOT
    if not base.exists():
        return None
    matches = [
        path
        for path in base.rglob("*.duckdb")
        if path.name.startswith(prefix)
    ]
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_mtime)


def _table_names(db: str | Path) -> set[str]:
    from generic_utils.scan_sources import rows_from_duckdb

    names: set[str] = set()
    for row in rows_from_duckdb(db, "SHOW TABLES"):
        name = row.get("name") or row.get("table_name")
        if name:
            names.add(str(name))
    return names


def _column_names(db: str | Path, table: str) -> set[str]:
    from generic_utils.scan_sources import rows_from_duckdb

    ident = '"' + str(table).replace('"', '""') + '"'
    return {
        str(row.get("name"))
        for row in rows_from_duckdb(db, f"PRAGMA table_info({ident})")
        if row.get("name")
    }


def load_projection_rows(
    db: str | Path | None,
    *,
    table: str,
    scenario: str = "base",
    extra_equals: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Load a financial_projection summary table. Missing db/table -> []."""
    if db is None:
        return []
    path = Path(db)
    if not path.exists():
        return []
    from generic_utils.scan_sources import rows_from_duckdb

    tables = _table_names(path)
    if table not in tables:
        return []
    ident = '"' + str(table).replace('"', '""') + '"'
    columns = _column_names(path, table)
    clauses: list[str] = []
    if "scenario" in columns and scenario:
        clauses.append(f"scenario = '{str(scenario).replace(chr(39), chr(39)*2)}'")
    for field, value in (extra_equals or {}).items():
        if field in columns and value is not None:
            clauses.append(
                f'"{field}" = \'{str(value).replace(chr(39), chr(39)*2)}\''
            )
    sql = f"SELECT * FROM {ident}"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    return rows_from_duckdb(path, sql)


def index_projection_by_ticker(
    rows: Sequence[Mapping[str, Any]],
    *,
    id_field: str = "symbol",
) -> dict[str, dict[str, Any]]:
    """One row per bare ticker. Later duplicates do not replace a valid row."""
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = ticker_of(row.get(id_field))
        if not ticker:
            continue
        current = out.get(ticker)
        if current is None:
            out[ticker] = dict(row)
            continue
        current_valid = bool(current.get("valid"))
        incoming_valid = bool(row.get("valid"))
        if incoming_valid and not current_valid:
            out[ticker] = dict(row)
    return out


def _copy_mapped(
    dest: dict[str, Any],
    source: Mapping[str, Any] | None,
    mapping: Mapping[str, str],
) -> bool:
    if not source:
        return False
    hit = False
    for dest_key, src_key in mapping.items():
        if src_key not in source:
            continue
        value = source.get(src_key)
        if value is None or value == "":
            continue
        number = to_float(value)
        if number is not None and (
            dest_key.endswith("_pct")
            or dest_key.endswith("_px")
            or dest_key.endswith("_own")
        ):
            dest[dest_key] = round(number, 4)
        else:
            dest[dest_key] = value
        hit = True
    return hit


def attach_street_forward(
    rows: Sequence[Mapping[str, Any]],
    *,
    close_field: str = "close",
    pt_field: str = "pt",
) -> list[dict[str, Any]]:
    """Street PT leftover and pack leftover as explicit forward columns."""
    out: list[dict[str, Any]] = []
    for rec in attach_implied_targets(rows, close_field=close_field, pt_field=pt_field):
        rec["fwd_street_pct"] = _round(rec.get("street_left"))
        rec["fwd_street_px"] = _round(rec.get("street_px"))
        rec["fwd_pack_pct"] = _round(rec.get("left"))
        rec["fwd_pack_px"] = _round(rec.get("target_px"))
        rec["fwd_pe"] = _round(rec.get("pe_fwd"))
        rec["fwd_peg"] = _round(rec.get("peg"), 3)
        out.append(rec)
    return out


def attach_forward_value(
    rows: Sequence[Mapping[str, Any]],
    recipe: Mapping[str, Any] | None = None,
    *,
    price_rows: Sequence[Mapping[str, Any]] | None = None,
    growth_rows: Sequence[Mapping[str, Any]] | None = None,
    price_run: str | None = None,
    growth_run: str | None = None,
    recipe_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Attach street/TV forward columns and optional financial_projection joins."""
    cfg = dict(recipe or load_forward_recipe(recipe_path))
    close_field = str(cfg.get("close_field") or "close")
    pt_field = str(cfg.get("pt_field") or "pt")
    id_field = str(cfg.get("id_field") or "symbol")
    price_map = dict(cfg.get("price_map") or {})
    growth_map = dict(cfg.get("growth_map") or {})
    price_by = index_projection_by_ticker(price_rows or (), id_field=id_field)
    growth_by = index_projection_by_ticker(growth_rows or (), id_field=id_field)
    out: list[dict[str, Any]] = []
    for rec in attach_street_forward(
        rows, close_field=close_field, pt_field=pt_field
    ):
        ticker = ticker_of(rec.get(id_field))
        joined: list[str] = ["street_tv"]
        if _copy_mapped(rec, price_by.get(ticker), price_map):
            joined.append("price")
            rec["fp_price_run"] = price_run
        if _copy_mapped(rec, growth_by.get(ticker), growth_map):
            joined.append("growth")
            rec["fp_growth_run"] = growth_run
        rec["fp_joined"] = "+".join(joined)
        out.append(rec)
    return out


def load_price_join_rows(
    db: str | Path | None,
    recipe: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cfg = dict(recipe or {})
    return load_projection_rows(
        db,
        table=str(cfg.get("price_table") or "projection_summary"),
        scenario=str(cfg.get("scenario") or "base"),
    )


def load_growth_join_rows(
    db: str | Path | None,
    recipe: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cfg = dict(recipe or {})
    return load_projection_rows(
        db,
        table=str(cfg.get("growth_table") or "growth_summary"),
        scenario=str(cfg.get("scenario") or "base"),
        extra_equals={"growth_lane": str(cfg.get("growth_lane") or "own")},
    )


def build_forward_suite_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    recipe: Mapping[str, Any] | None = None,
    setup_recipe: Mapping[str, Any] | None = None,
    peer_rows: Sequence[Mapping[str, Any]] | None = None,
    price_rows: Sequence[Mapping[str, Any]] | None = None,
    growth_rows: Sequence[Mapping[str, Any]] | None = None,
    price_run: str | None = None,
    growth_run: str | None = None,
    with_setup: bool = True,
    recipe_path: str | Path | None = None,
    setup_recipe_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Value/tech (optional) plus street/TV/finproj forward columns."""
    from generic_utils.setup import attach_setup, load_setup_recipe

    work: Sequence[Mapping[str, Any]] = rows
    if with_setup:
        setup_cfg = setup_recipe
        if setup_cfg is None and setup_recipe_path is not None:
            setup_cfg = load_setup_recipe(setup_recipe_path)
        work = attach_setup(work, setup_cfg, peer_rows=peer_rows)
    return attach_forward_value(
        work,
        recipe,
        price_rows=price_rows,
        growth_rows=growth_rows,
        price_run=price_run,
        growth_run=growth_run,
        recipe_path=recipe_path,
    )
