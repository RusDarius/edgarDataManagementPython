from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .forward_upside_valuation import (
    FORWARD_UPSIDE_VALUATION_OVERLAY_COLUMNS,
    extract_forward_upside_valuation_overlay_fields,
)
from .upside_prediction import compute_upside_prediction_fields


def _import_duckdb():
    import duckdb

    return duckdb


def _normalize_symbol_field(row: dict[str, Any]) -> dict[str, Any]:
    """Backfill a missing symbol from exchange:bare_ticker when available.

    The symbol is the primary join key across all edge outlook lenses. Some
    upstream rows arrive with a blank ``symbol`` but still carry ``exchange``
    and ``bare_ticker``. Reconstruct the fully-qualified ``EXCHANGE:TICKER``
    form so lookups and CSV output do not silently drop rows.
    """
    symbol = str(row.get("symbol") or "").strip()
    if symbol:
        return row
    exchange = str(row.get("exchange") or "").strip()
    bare_ticker = str(row.get("bare_ticker") or "").strip()
    if ":" in bare_ticker and not exchange:
        row["symbol"] = bare_ticker
    elif exchange and bare_ticker:
        row["symbol"] = f"{exchange}:{bare_ticker}"
    elif bare_ticker:
        row["symbol"] = bare_ticker
    else:
        row["symbol"] = ""
    return row


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _read_csv_rows(path: str | Path) -> list[dict[str, str]]:
    if not path:
        return []
    csv_path = Path(path)
    if not csv_path.exists() or csv_path.is_dir():
        return []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _round_or_blank(value: float | None, digits: int = 4) -> float | str:
    if value is None:
        return ""
    return round(float(value), digits)


OUTLOOK_DEFINITIONS: tuple[dict[str, str], ...] = (
    {
        "name": "edge_big_mover",
        "score_key": "big_mover_score",
        "rank_key": "big_mover_rank",
    },
    {
        "name": "edge_confidence",
        "score_key": "confidence_score",
        "rank_key": "confidence_rank",
    },
    {
        "name": "upside_prediction",
        "score_key": "upside_prediction_score",
        "rank_key": "upside_prediction_rank",
    },
    {
        "name": "forward_valuation",
        "score_key": "forward_upside_score",
        "rank_key": "forward_upside_rank",
    },
    {
        "name": "tradeable_safety",
        "score_key": "tradeable_safety_blend_score",
        "rank_key": "tradeable_safety_rank",
    },
    {
        "name": "safety_companion",
        "score_key": "safety_companion_score",
        "rank_key": "safety_rank_within_tradeables",
    },
)

OUTLOOK_SCORE_WEIGHTS: dict[str, float] = {
    "edge_big_mover": 0.18,
    "edge_confidence": 0.16,
    "upside_prediction": 0.20,
    "forward_valuation": 0.20,
    "tradeable_safety": 0.14,
    "safety_companion": 0.12,
}

SAFETY_DETAIL_PREFIX = "safety_detail_"
LANE_LEADER_PREFIX = "lane_leader_"
EDGE_GROUP_PREFIX = "edge_group_"


def _normalize_unit_score(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return _clamp(_safe_float(value))


def _normalize_pct_score(value: Any, *, full_score_pct: float = 80.0) -> float | None:
    if value is None or value == "":
        return None
    if full_score_pct <= 0:
        return None
    return _clamp(_safe_float(value) / float(full_score_pct))


def _rank_to_score(rank: Any, *, population: int) -> float | None:
    if rank is None or rank == "" or population <= 0:
        return None
    rank_value = _safe_int(rank, default=0)
    if rank_value <= 0:
        return None
    return _clamp(1.0 - ((float(rank_value) - 1.0) / float(population)))


def _forward_valuation_outlook_score(row: Mapping[str, Any]) -> float | None:
    mode = str(row.get("forward_upside_mode") or "").strip().lower()
    if mode == "valuation":
        pct_score = _normalize_pct_score(row.get("forward_valuation_upside_pct"))
        if pct_score is not None:
            return pct_score
    fallback = _normalize_unit_score(
        row.get("forward_supporting_upside_prediction_score")
    )
    if fallback is not None:
        return fallback
    return _normalize_unit_score(row.get("forward_upside_score"))


def compute_probabilistic_edge_fit_score(
    row: Mapping[str, Any],
    *,
    ranking_horizon: int,
) -> float:
    horizon = int(ranking_horizon)
    ci_low = _safe_float_or_none(
        row.get(f"{LANE_LEADER_PREFIX}median_fwd_{horizon}d_ci_low")
    )
    ci_width = _safe_float_or_none(
        row.get(f"{LANE_LEADER_PREFIX}median_fwd_{horizon}d_ci_width")
    )
    bootstrap_n = _safe_float_or_none(
        row.get(f"{LANE_LEADER_PREFIX}median_fwd_{horizon}d_bootstrap_sample_count")
    )
    lane_median = _safe_float_or_none(row.get(f"lane_median_fwd_{horizon}d"))
    hist_median = _safe_float_or_none(row.get(f"median_fwd_{horizon}d_in_setup"))
    hist_win = _safe_float_or_none(row.get(f"win_rate_{horizon}d_in_setup"))

    if ci_low is None and lane_median is None and hist_median is None:
        return 0.0

    lower_norm = _clamp((ci_low or 0.0) / 10.0) if ci_low is not None else 0.0
    width_penalty = _clamp(1.0 - (ci_width / 20.0)) if ci_width is not None else 0.45
    depth = _clamp((bootstrap_n or 0.0) / 50.0) if bootstrap_n is not None else 0.0
    lane_norm = _clamp((lane_median or 0.0) / 10.0) if lane_median is not None else 0.0
    hist_norm = _clamp((hist_median or 0.0) / 12.0) if hist_median is not None else 0.0
    win_component = _clamp(hist_win) if hist_win is not None else 0.0

    return _clamp(
        (0.30 * lower_norm)
        + (0.20 * width_penalty)
        + (0.15 * depth)
        + (0.15 * lane_norm)
        + (0.10 * hist_norm)
        + (0.10 * win_component)
    )


def _safe_float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compute_unified_outlook_fields(
    row: Mapping[str, Any],
    *,
    population: int,
    ranking_horizon: int,
) -> dict[str, Any]:
    outlook_scores: dict[str, float] = {}
    for outlook in OUTLOOK_DEFINITIONS:
        name = str(outlook["name"])
        score_key = str(outlook["score_key"])
        rank_key = str(outlook["rank_key"])
        normalized: float | None
        if name == "forward_valuation":
            normalized = _forward_valuation_outlook_score(row)
        else:
            normalized = _normalize_unit_score(row.get(score_key))
            if normalized is None:
                normalized = _rank_to_score(row.get(rank_key), population=population)
        if normalized is not None:
            outlook_scores[name] = normalized

    probabil_score = compute_probabilistic_edge_fit_score(
        row,
        ranking_horizon=ranking_horizon,
    )

    if not outlook_scores:
        consensus = 0.0
        best_name = ""
        best_score = 0.0
    else:
        weighted_total = 0.0
        weight_sum = 0.0
        for name, score in outlook_scores.items():
            weight = OUTLOOK_SCORE_WEIGHTS.get(name, 0.0)
            if weight <= 0:
                continue
            weighted_total += weight * score
            weight_sum += weight
        consensus = weighted_total / weight_sum if weight_sum > 0 else 0.0
        best_name, best_score = max(outlook_scores.items(), key=lambda item: item[1])

    highlight_score = _clamp((0.72 * consensus) + (0.28 * probabil_score))

    fields: dict[str, Any] = {
        "unified_consensus_score": round(consensus, 4),
        "unified_best_outlook_name": best_name,
        "unified_best_outlook_score": round(best_score, 4),
        "unified_probabil_edge_fit_score": round(probabil_score, 4),
        "unified_edge_highlight_score": round(highlight_score, 4),
    }
    for name, score in outlook_scores.items():
        fields[f"outlook_{name}_score"] = round(score, 4)
    for outlook in OUTLOOK_DEFINITIONS:
        name = str(outlook["name"])
        fields.setdefault(f"outlook_{name}_score", "")
    return fields


def _prefix_fields(
    row: Mapping[str, Any],
    *,
    prefix: str,
    exclude: set[str] | None = None,
) -> dict[str, Any]:
    excluded = exclude or set()
    return {
        f"{prefix}{key}": value
        for key, value in row.items()
        if key not in excluded and value not in (None,)
    }


def _lane_leader_join_keys(row: Mapping[str, Any]) -> tuple[str, str]:
    group_by = str(row.get("lane_group_by") or "industry")
    group_value = str(row.get("lane_group_value") or row.get("industry") or "Unknown")
    return group_by, group_value


def _merge_tradeable_safety_fields(
    row: dict[str, Any],
    *,
    tradeable_row: Mapping[str, Any] | None,
) -> None:
    if not tradeable_row:
        return
    for key in (
        "tradeable_core_score",
        "tradeable_safety_blend_score",
        "tradeable_safety_rank",
        "safety_rank_within_tradeables",
        "risk_rank_within_tradeables",
        "historical_validation_score",
        "historical_validation_bucket",
        "historical_validation_pass",
        "historical_validation_occurrence_count",
        "historical_validation_win_rate",
        "historical_validation_median_fwd_pct",
        "historical_validation_any_setup_rate",
        "historical_validation_stability_score",
        "safety_bucket",
        "safety_shortlist_flag",
        "safety_focus_flag",
        "safety_data_available",
        "balance_sheet_safety_score",
        "cash_generation_value_score",
        "safety_companion_score",
        "indicator_pass_count",
        "safety_rank_global",
    ):
        if key in tradeable_row:
            row[key] = tradeable_row.get(key, "")


def build_unified_edge_highlight_rows(
    *,
    highlight_rows: Sequence[Mapping[str, Any]],
    safety_by_symbol: Mapping[str, Mapping[str, Any]],
    upside_by_symbol: Mapping[str, Mapping[str, Any]],
    forward_by_symbol: Mapping[str, Mapping[str, Any]],
    tradeable_by_symbol: Mapping[str, Mapping[str, Any]],
    lane_leader_by_group: Mapping[str, Mapping[str, Any]],
    edge_group_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    ranking_horizon: int,
) -> list[dict[str, Any]]:
    population = max(1, len(highlight_rows))
    unified_rows: list[dict[str, Any]] = []

    for highlight_row in highlight_rows:
        row = _normalize_symbol_field(dict(highlight_row))
        symbol = str(row.get("symbol") or "").strip()

        upside_row = upside_by_symbol.get(symbol)
        if upside_row:
            for key, value in upside_row.items():
                if key not in row or row.get(key) in ("", None):
                    row[key] = value
        else:
            row.update(
                compute_upside_prediction_fields(
                    row,
                    ranking_horizon=int(ranking_horizon),
                )
            )

        row.update(
            extract_forward_upside_valuation_overlay_fields(
                forward_by_symbol.get(symbol)
            )
        )
        forward_full = forward_by_symbol.get(symbol) or {}
        for key in FORWARD_UPSIDE_VALUATION_OVERLAY_COLUMNS:
            if key in forward_full:
                row[key] = forward_full.get(key, "")

        _merge_tradeable_safety_fields(
            row, tradeable_row=tradeable_by_symbol.get(symbol)
        )

        safety_row = safety_by_symbol.get(symbol)
        if safety_row:
            row.update(
                _prefix_fields(
                    safety_row,
                    prefix=SAFETY_DETAIL_PREFIX,
                    exclude={"symbol", "bare_ticker", "company_name"},
                )
            )
            for key in (
                "balance_sheet_safety_score",
                "cash_generation_value_score",
                "safety_companion_score",
                "indicator_pass_count",
                "safety_rank",
            ):
                if row.get(key) in ("", None) and safety_row.get(key) not in ("", None):
                    row[key] = safety_row.get(key)
            if row.get("safety_rank_global") in ("", None) and safety_row.get(
                "safety_rank"
            ):
                row["safety_rank_global"] = safety_row.get("safety_rank")

        _, group_value = _lane_leader_join_keys(row)
        lane_row = lane_leader_by_group.get(group_value)
        if lane_row:
            row.update(
                _prefix_fields(
                    lane_row,
                    prefix=LANE_LEADER_PREFIX,
                    exclude={"group_by", "group_value", "setup_name"},
                )
            )

        edge_group_row = edge_group_by_key.get(("industry", group_value))
        if edge_group_row is None:
            edge_group_row = edge_group_by_key.get(
                (str(row.get("lane_group_by") or "industry"), group_value)
            )
        if edge_group_row:
            row.update(
                _prefix_fields(
                    edge_group_row,
                    prefix=EDGE_GROUP_PREFIX,
                    exclude={"industry", "sector", "universe", "group_value"},
                )
            )

        row.update(
            compute_unified_outlook_fields(
                row,
                population=population,
                ranking_horizon=int(ranking_horizon),
            )
        )
        unified_rows.append(row)

    unified_rows.sort(
        key=lambda item: (
            _safe_float(item.get("unified_edge_highlight_score")),
            _safe_float(item.get("unified_best_outlook_score")),
            _safe_float(item.get("unified_probabil_edge_fit_score")),
            _safe_float(item.get("forward_valuation_upside_pct")),
            _safe_float(item.get("upside_prediction_score")),
        ),
        reverse=True,
    )
    top_n = min(30, len(unified_rows))
    for index, row in enumerate(unified_rows, start=1):
        row["unified_edge_highlight_rank"] = index
        row["unified_best_across_outlooks_flag"] = int(index <= top_n)
    return unified_rows


def _load_indexed_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    key: str,
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_dict = dict(row)
        if key == "symbol":
            row_dict = _normalize_symbol_field(row_dict)
        row_key = str(row_dict.get(key) or "").strip()
        if row_key:
            indexed[row_key] = row_dict
    return indexed


def _load_lane_leader_by_group(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        group_value = str(row.get("group_value") or "Unknown")
        indexed[group_value] = dict(row)
    return indexed


def _load_edge_group_by_key(
    rows: Sequence[Mapping[str, Any]],
    *,
    group_by: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        group_value = str(
            row.get(group_by)
            or row.get("group_value")
            or row.get("industry")
            or "Unknown"
        )
        indexed[(group_by, group_value)] = dict(row)
    return indexed


def write_unified_edge_highlights_duckdb(
    *,
    database_path: Path,
    symbol_rows: Sequence[Mapping[str, Any]],
    lane_leader_rows: Sequence[Mapping[str, Any]],
    edge_group_rows: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    safety_scored_rows: Sequence[Mapping[str, Any]] = (),
    safety_group_summary_rows: Sequence[Mapping[str, Any]] = (),
) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    if database_path.exists():
        database_path.unlink()

    duckdb = _import_duckdb()
    conn = duckdb.connect(database_path.as_posix())
    try:
        if symbol_rows:
            symbol_csv = database_path.with_suffix(".symbol_rows.csv")
            _write_csv_rows(symbol_csv, symbol_rows)
            conn.execute(f"""
                CREATE TABLE symbol_unified_highlights AS
                SELECT * FROM read_csv({_q(symbol_csv.as_posix())}, header = true, auto_detect = true)
                """)
            symbol_csv.unlink(missing_ok=True)
        else:
            conn.execute("CREATE TABLE symbol_unified_highlights(symbol VARCHAR)")

        if lane_leader_rows:
            lane_csv = database_path.with_suffix(".lane_rows.csv")
            _write_csv_rows(lane_csv, lane_leader_rows)
            conn.execute(f"""
                CREATE TABLE lane_bootstrap_leaders AS
                SELECT * FROM read_csv({_q(lane_csv.as_posix())}, header = true, auto_detect = true)
                """)
            lane_csv.unlink(missing_ok=True)
        else:
            conn.execute("CREATE TABLE lane_bootstrap_leaders(group_value VARCHAR)")

        if edge_group_rows:
            edge_csv = database_path.with_suffix(".edge_group_rows.csv")
            _write_csv_rows(edge_csv, edge_group_rows)
            conn.execute(f"""
                CREATE TABLE edge_group_bootstrap_summary AS
                SELECT * FROM read_csv({_q(edge_csv.as_posix())}, header = true, auto_detect = true)
                """)
            edge_csv.unlink(missing_ok=True)
        else:
            conn.execute(
                "CREATE TABLE edge_group_bootstrap_summary(group_value VARCHAR)"
            )

        if safety_scored_rows:
            safety_scored_csv = database_path.with_suffix(".safety_scored_rows.csv")
            _write_csv_rows(safety_scored_csv, safety_scored_rows)
            conn.execute(f"""
                CREATE TABLE safety_scored_universe AS
                SELECT * FROM read_csv({_q(safety_scored_csv.as_posix())}, header = true, auto_detect = true)
                """)
            safety_scored_csv.unlink(missing_ok=True)
        else:
            conn.execute("CREATE TABLE safety_scored_universe(symbol VARCHAR)")

        if safety_group_summary_rows:
            safety_group_csv = database_path.with_suffix(".safety_group_rows.csv")
            _write_csv_rows(safety_group_csv, safety_group_summary_rows)
            conn.execute(f"""
                CREATE TABLE safety_group_summary AS
                SELECT * FROM read_csv({_q(safety_group_csv.as_posix())}, header = true, auto_detect = true)
                """)
            safety_group_csv.unlink(missing_ok=True)
        else:
            conn.execute("CREATE TABLE safety_group_summary(group_value VARCHAR)")

        conn.execute("""
            CREATE TABLE unified_run_manifest (
                key VARCHAR,
                value VARCHAR
            )
            """)
        manifest_rows = [
            (str(key), json.dumps(value) if not isinstance(value, str) else value)
            for key, value in manifest.items()
        ]
        if manifest_rows:
            conn.executemany(
                "INSERT INTO unified_run_manifest VALUES (?, ?)",
                manifest_rows,
            )
    finally:
        conn.close()


def _write_unified_edge_highlights_report(
    path: Path,
    *,
    database_path: Path,
    csv_path: Path,
    row_count: int,
    ranking_horizon: int,
    safety_scored_universe_csv: Path,
    safety_group_summary_csv: Path,
    safety_scored_row_count: int,
    safety_group_row_count: int,
) -> None:
    lines = [
        "# Unified Edge Highlights Store",
        "",
        "Query-ready DuckDB bundle that merges edge setup, probabilistic lane bootstrap",
        "context, upside prediction, forward valuation, and safety companion fields.",
        "",
        "## Primary table: `symbol_unified_highlights`",
        "",
        "- One row per highlights shortlist symbol with all merged outlook scores and ranks.",
        "- `unified_edge_highlight_score` blends weighted cross-outlook consensus with probabilistic lane-fit score.",
        "- `unified_best_outlook_name` / `unified_best_outlook_score` identify the strongest single outlook per symbol.",
        "- `outlook_*_score` columns hold normalized 0-1 scores for each outlook lens.",
        "- `lane_leader_*` columns carry bootstrap median-forward CI context for the symbol's lane group.",
        "- `edge_group_*` columns carry grouped edge-summary bootstrap context.",
        "- `safety_detail_*` columns carry the full safety scored universe metrics for shortlist symbols.",
        "",
        "## Supporting tables",
        "",
        "- `lane_bootstrap_leaders`: lane-level bootstrap leaders from highlights.",
        "- `edge_group_bootstrap_summary`: grouped edge summary with bootstrap CIs.",
        "- `safety_scored_universe`: the full balance-sheet-safety / cash-generation-value",
        "  scored universe (every scanned symbol, not just the highlights shortlist). This",
        "  is the safety companion scan folded directly into the unified aggregate output",
        "  instead of a separate top-level `safety_highlights/` folder.",
        "- `safety_group_summary`: industry/sector rollup of the safety scores above --",
        "  useful for spotting durable, well-capitalized lanes even when they are not on",
        "  the volatility-liquidity radar.",
        "- `unified_run_manifest`: run metadata key/value pairs.",
        "",
        "## Example queries",
        "",
        "```sql",
        "SELECT symbol, unified_edge_highlight_rank, unified_edge_highlight_score,",
        "       unified_best_outlook_name, forward_valuation_upside_pct, safety_companion_score",
        "FROM symbol_unified_highlights",
        "ORDER BY unified_edge_highlight_rank",
        "LIMIT 20;",
        "",
        "SELECT group_value, median_safety_companion_score, shortlist_count",
        "FROM safety_group_summary",
        "ORDER BY median_safety_companion_score DESC",
        "LIMIT 20;",
        "```",
        "",
        f"- ranking_horizon: {int(ranking_horizon)}d",
        f"- symbol rows: {int(row_count)}",
        f"- safety scored universe rows: {int(safety_scored_row_count)} "
        f"-> `{safety_scored_universe_csv.as_posix()}`",
        f"- safety group summary rows: {int(safety_group_row_count)} "
        f"-> `{safety_group_summary_csv.as_posix()}`",
        f"- duckdb: `{database_path.as_posix()}`",
        f"- csv mirror: `{csv_path.as_posix()}`",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_unified_edge_highlights_store(
    *,
    output_dir: str | Path,
    highlights_result: Mapping[str, Any],
    safety_result: Mapping[str, Any],
    scan_edge_result: Mapping[str, Any] | None,
    tradeable_safety_result: Mapping[str, Any] | None,
    upside_prediction_lens_result: Mapping[str, Any],
    forward_upside_valuation_lens_result: Mapping[str, Any],
) -> dict[str, Any]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    ranking_horizon = int(highlights_result.get("ranking_horizon") or 5)
    highlight_rows = [
        _normalize_symbol_field(dict(row))
        for row in _read_csv_rows(highlights_result["shortlist_csv"])
    ]
    lane_leader_rows = _read_csv_rows(highlights_result.get("lane_leaders_csv", ""))
    safety_rows = _read_csv_rows(safety_result["scored_csv"])
    safety_group_rows = _read_csv_rows(safety_result.get("group_summary_csv", ""))
    upside_rows = _read_csv_rows(upside_prediction_lens_result["ranked_csv"])
    tradeable_rows = (
        _read_csv_rows(tradeable_safety_result["overlay_csv"])
        if tradeable_safety_result
        else []
    )

    forward_by_symbol = forward_upside_valuation_lens_result.get("rows_by_symbol") or {}
    if not forward_by_symbol:
        forward_rows = _read_csv_rows(
            forward_upside_valuation_lens_result["ranked_csv"]
        )
        forward_by_symbol = _load_indexed_rows(forward_rows, key="symbol")
    else:
        normalized_forward: dict[str, dict[str, Any]] = {}
        for raw_key, raw_value in forward_by_symbol.items():
            normalized_value = _normalize_symbol_field(dict(raw_value))
            normalized_key = str(normalized_value.get("symbol") or raw_key).strip()
            if normalized_key:
                normalized_forward[normalized_key] = normalized_value
        forward_by_symbol = normalized_forward

    edge_group_rows: list[dict[str, Any]] = []
    edge_group_by = "industry"
    if scan_edge_result:
        edge_group_by = str(scan_edge_result.get("group_by") or "industry")
        edge_group_rows = _read_csv_rows(scan_edge_result.get("summary_csv", ""))

    unified_rows = build_unified_edge_highlight_rows(
        highlight_rows=highlight_rows,
        safety_by_symbol=_load_indexed_rows(safety_rows, key="symbol"),
        upside_by_symbol=_load_indexed_rows(upside_rows, key="symbol"),
        forward_by_symbol={
            str(key): dict(value) for key, value in forward_by_symbol.items()
        },
        tradeable_by_symbol=_load_indexed_rows(tradeable_rows, key="symbol"),
        lane_leader_by_group=_load_lane_leader_by_group(lane_leader_rows),
        edge_group_by_key=_load_edge_group_by_key(
            edge_group_rows,
            group_by=edge_group_by,
        ),
        ranking_horizon=ranking_horizon,
    )

    database_path = output_path / "edge_unified_highlights.duckdb"
    csv_path = output_path / "edge_unified_highlights.csv"
    report_md = output_path / "edge_unified_highlights_report.md"
    manifest_path = output_path / "edge_unified_highlights_manifest.json"
    safety_scored_universe_csv = (
        output_path / "edge_unified_highlights_safety_scored_universe.csv"
    )
    safety_group_summary_csv = (
        output_path / "edge_unified_highlights_safety_group_summary.csv"
    )

    _write_csv_rows(csv_path, unified_rows)
    _write_csv_rows(safety_scored_universe_csv, safety_rows)
    _write_csv_rows(safety_group_summary_csv, safety_group_rows)
    manifest = {
        "command": "unified-edge-highlights",
        "ranking_horizon": ranking_horizon,
        "row_count": len(unified_rows),
        "database_path": database_path.as_posix(),
        "csv_path": csv_path.as_posix(),
        "highlights_shortlist_csv": str(highlights_result.get("shortlist_csv")),
        "lane_leaders_csv": str(highlights_result.get("lane_leaders_csv")),
        "safety_scored_csv": str(safety_result.get("scored_csv")),
        "upside_prediction_ranked_csv": str(
            upside_prediction_lens_result.get("ranked_csv")
        ),
        "forward_upside_ranked_csv": str(
            forward_upside_valuation_lens_result.get("ranked_csv")
        ),
        "tradeable_safety_overlay_csv": (
            str(tradeable_safety_result.get("overlay_csv"))
            if tradeable_safety_result
            else ""
        ),
        "edge_group_summary_csv": (
            str(scan_edge_result.get("summary_csv")) if scan_edge_result else ""
        ),
        "outlook_names": [item["name"] for item in OUTLOOK_DEFINITIONS],
        "safety_scored_universe_csv": safety_scored_universe_csv.as_posix(),
        "safety_group_summary_csv": safety_group_summary_csv.as_posix(),
        "safety_scored_universe_row_count": len(safety_rows),
        "safety_group_summary_row_count": len(safety_group_rows),
    }
    write_unified_edge_highlights_duckdb(
        database_path=database_path,
        symbol_rows=unified_rows,
        lane_leader_rows=lane_leader_rows,
        edge_group_rows=edge_group_rows,
        manifest=manifest,
        safety_scored_rows=safety_rows,
        safety_group_summary_rows=safety_group_rows,
    )
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _write_unified_edge_highlights_report(
        report_md,
        database_path=database_path,
        csv_path=csv_path,
        row_count=len(unified_rows),
        ranking_horizon=ranking_horizon,
        safety_scored_universe_csv=safety_scored_universe_csv,
        safety_group_summary_csv=safety_group_summary_csv,
        safety_scored_row_count=len(safety_rows),
        safety_group_row_count=len(safety_group_rows),
    )

    return {
        "output_dir": output_path,
        "database_path": database_path,
        "csv_path": csv_path,
        "report_md": report_md,
        "manifest_path": manifest_path,
        "row_count": len(unified_rows),
        "ranking_horizon": ranking_horizon,
        "safety_scored_universe_csv": safety_scored_universe_csv,
        "safety_group_summary_csv": safety_group_summary_csv,
        "safety_scored_universe_row_count": len(safety_rows),
        "safety_group_summary_row_count": len(safety_group_rows),
    }
