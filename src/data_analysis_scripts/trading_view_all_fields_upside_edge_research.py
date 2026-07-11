from __future__ import annotations

import csv
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from data_analysis_scripts.trading_view_all_fields_metric_pattern_analyzer import (
    DEFAULT_ALL_FIELDS_ROOT,
    _extract_day_label_from_database_path,
    discover_all_fields_daily_databases,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCAN_PERIOD_RUN_ROOT = (
    PROJECT_ROOT
    / "logs"
    / "tradingview_analysis"
    / "trading_view_all_fields_data"
    / "pattern_analysis"
    / "runs"
    / "scan_period_close_forward_tracking_29mar_15jun2026_b108820e"
)
DEFAULT_TAXONOMY_REGISTRY = (
    PROJECT_ROOT
    / "savedData"
    / "trading_view_field_taxonomy"
    / "variants"
    / "master_registry.csv"
)
DEFAULT_PERFORMANCE_TARGET = "period_return_pct"
DEFAULT_EXCLUDED_SEMANTIC_BUCKETS: tuple[str, ...] = (
    "fixed_income",
    "fund_structure",
    "metadata_classification",
    "structural_calendar",
)
DEFAULT_MAR_JUN_UPSIDE_TOP_N = 50
DEFAULT_MOVE_PREDICTION_DUCKDB_ROOT = (
    PROJECT_ROOT
    / "logs"
    / "tradingview_analysis"
    / "prediction_analysis"
    / "duckdb_runs"
)
DEFAULT_EXISTING_BACKWARDS_DATABASE_PATH = (
    PROJECT_ROOT
    / "logs"
    / "tradingview_analysis"
    / "prediction_analysis"
    / "duckdb_runs"
    / "backwards_prediction_analysis"
    / "runs"
    / "backwards_prediction_analysis_20260625_1621_utc_b00e6c1d"
    / "backwards_prediction_analysis.duckdb"
)
DEFAULT_EXISTING_PROFILE_NAMES: tuple[str, ...] = (
    "breakout_long_v1",
    "early_momentum_inflection_v1",
    "fragility_short",
    "forward_edge_active_v2",
    "quality_value_compounder",
    "durable_value_compounder_v1",
    "asymmetric_value",
    "value_recovery_v3",
)
DEFAULT_BEST_TRADE_TOP_N = 150
DEFAULT_BEST_TRADE_MIN_MARKET_CAP_USD = 500_000_000.0
DEFAULT_BEST_TRADE_MIN_START_CLOSE = 1.0
DEFAULT_BEST_TRADE_MIN_AVERAGE_VOLUME_10D = 50_000.0
DEFAULT_BEST_TRADE_INDICATOR_PERCENTILE = 0.80
DEFAULT_BEST_TRADE_MAX_INDICATOR_FIELDS = 50
DEFAULT_TRADE_LADDER_MIN_MARKET_CAP_USD = 500_000_000.0
DEFAULT_TRADE_LADDER_MIN_START_CLOSE = 1.0
DEFAULT_TRADE_LADDER_MIN_AVERAGE_VOLUME_10D = 50_000.0
DEFAULT_TRADE_LADDER_REPORT_TOP_N = 30
DEFAULT_TRADE_LADDER_MIN_PULLBACK_PCT = 0.0
DEFAULT_TRADE_LADDER_TRANSACTION_COST_PCT_PER_SIDE = 0.0
DEFAULT_TRADE_LADDER_MIN_TRADE_RETURN_PCT = 0.0
DEFAULT_TRADE_LADDER_ENTRY_SCENARIO_NAME = "realistic_mid"
DEFAULT_TRADE_LADDER_ENTRY_MAX_RANK = 250
DEFAULT_TRADE_LADDER_ENTRY_FIELD_PERCENTILE = 0.80
DEFAULT_TRADE_LADDER_ENTRY_FIELD_MAX_INDICATORS = 25
DEFAULT_TRADE_LADDER_REALISM_SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "scenario_name": "perfect_hindsight",
        "min_pullback_pct_before_entry": 0.0,
        "transaction_cost_pct_per_side": 0.0,
        "min_trade_return_pct": 0.0,
    },
    {
        "scenario_name": "pullback_5pct",
        "min_pullback_pct_before_entry": 5.0,
        "transaction_cost_pct_per_side": 0.0,
        "min_trade_return_pct": 0.0,
    },
    {
        "scenario_name": "realistic_mid",
        "min_pullback_pct_before_entry": 5.0,
        "transaction_cost_pct_per_side": 0.25,
        "min_trade_return_pct": 7.0,
    },
    {
        "scenario_name": "realistic_tight",
        "min_pullback_pct_before_entry": 8.0,
        "transaction_cost_pct_per_side": 0.5,
        "min_trade_return_pct": 10.0,
    },
)

RAW_SCAN_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "relative_volume": ("relative_volume_10d_calc",),
    "relative_volume_10d_calc": ("relative_volume_10d_calc",),
    "close": ("close",),
    "volume": ("volume",),
    "average_volume_10d_calc": ("average_volume_10d_calc",),
    "average_volume_30d_calc": ("average_volume_30d_calc",),
}


@dataclass(frozen=True)
class UpsideFieldCandidate:
    predictor_field: str
    predictor_display_name: str | None
    semantic_bucket: str | None
    usage_role: str | None
    relevance_tier: str | None
    stability_split: str | None
    period_pattern_score: float | None
    period_quintile_spread_pp: float | None
    period_pearson: float | None
    period_top_quintile_avg_pct: float | None
    period_bottom_quintile_avg_pct: float | None
    pair_n: int | None
    predictor_fill_rate: float | None
    runs_seen: int | None
    sign_consistency_ratio: float | None
    median_pearson: float | None
    median_quintile_spread_pp: float | None
    rank_stability_score: float | None
    bullish_rank_direction: str
    joinable_in_move_prediction_raw_scan: bool | None
    raw_scan_join_field: str | None
    recommendation: str


def _import_duckdb():
    import duckdb

    return duckdb


def _quote_sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _field_window_label(field_name: str) -> str:
    if "|" not in field_name:
        return "base"
    return field_name.rsplit("|", 1)[-1]


def _fetch_dict_rows(conn: Any, query: str) -> list[dict[str, Any]]:
    result = conn.execute(query)
    columns = [column[0] for column in result.description]
    return [dict(zip(columns, row)) for row in result.fetchall()]


def _write_dict_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    headers = list(rows[0].keys()) if rows else []
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _parse_day_label(day_label: str) -> datetime:
    return datetime.strptime(day_label.strip(), "%d_%m_%Y")


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _load_taxonomy_rows(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {str(row.get("field_name") or ""): row for row in csv.DictReader(handle)}


def _load_raw_scan_columns(
    move_prediction_database_path: str | Path | None,
) -> set[str] | None:
    if move_prediction_database_path is None:
        return None
    path = Path(move_prediction_database_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Move-prediction database not found: {path.as_posix()}"
        )
    duckdb = _import_duckdb()
    conn = duckdb.connect(path.as_posix(), read_only=True)
    try:
        rows = conn.execute("PRAGMA table_info('raw_scan_rows')").fetchall()
    finally:
        conn.close()
    return {str(row[1]) for row in rows}


def _resolve_raw_scan_join_field(
    predictor_field: str,
    taxonomy_row: dict[str, str] | None,
    raw_scan_columns: set[str] | None,
) -> tuple[bool | None, str | None]:
    if raw_scan_columns is None:
        return None, None
    candidates = [predictor_field]
    if taxonomy_row:
        base_name = taxonomy_row.get("base_name")
        if base_name:
            candidates.append(base_name)
    for alias in RAW_SCAN_FIELD_ALIASES.get(
        predictor_field, ()
    ):  # explicit aliases first
        candidates.append(alias)
    for candidate in candidates:
        if candidate in raw_scan_columns:
            return True, candidate
    return False, None


def _candidate_recommendation(
    *,
    relevance_tier: str | None,
    stability_split: str | None,
    joinable: bool | None,
) -> str:
    if joinable is True and relevance_tier in {"promote", "watch"}:
        return "test_directly_in_execution_backtest"
    if joinable is True:
        return "test_as_raw_scan_overlay_with_caution"
    if joinable is False and stability_split == "stability_passed":
        return "requires_daily_all_fields_join"
    if joinable is None:
        return "check_move_prediction_joinability"
    return "research_only_for_now"


def _load_upside_rows(
    *,
    run_root: Path,
    performance_target: str,
    top_n: int,
    min_runs_seen: int,
    require_sign_consistency: float,
    min_quintile_spread_pct: float,
    min_pair_n: int,
) -> list[dict[str, Any]]:
    period_total_parquet = (
        run_root / "period_total" / "period_field_performance_patterns.parquet"
    )
    stability_parquet = run_root / "aggregates" / "cross_run_field_stability.parquet"
    if not period_total_parquet.exists():
        raise FileNotFoundError(
            f"Missing period-total parquet: {period_total_parquet.as_posix()}"
        )
    if not stability_parquet.exists():
        raise FileNotFoundError(
            f"Missing stability parquet: {stability_parquet.as_posix()}"
        )

    duckdb = _import_duckdb()
    conn = duckdb.connect()
    try:
        result = conn.execute(
            """
            WITH period AS (
                SELECT predictor_field,
                    predictor_display_name,
                    pattern_score,
                    quintile_spread_adjusted,
                    pearson_corr_adjusted,
                    top_quintile_avg_perf,
                    bottom_quintile_avg_perf,
                    pair_n,
                    predictor_fill_rate
                FROM read_parquet(?)
                WHERE performance_field = ?
            ),
            stability AS (
                SELECT predictor_field,
                    runs_seen,
                    sign_consistency_ratio,
                    median_pearson,
                    median_quintile_spread,
                    rank_stability_score
                FROM read_parquet(?)
                WHERE performance_field = ?
            )
            SELECT period.predictor_field,
                period.predictor_display_name,
                period.pattern_score,
                period.quintile_spread_adjusted,
                period.pearson_corr_adjusted,
                period.top_quintile_avg_perf,
                period.bottom_quintile_avg_perf,
                period.pair_n,
                period.predictor_fill_rate,
                stability.runs_seen,
                stability.sign_consistency_ratio,
                stability.median_pearson,
                stability.median_quintile_spread,
                stability.rank_stability_score
            FROM period
            INNER JOIN stability USING (predictor_field)
            WHERE stability.runs_seen >= ?
              AND stability.sign_consistency_ratio >= ?
              AND period.pair_n >= ?
              AND period.quintile_spread_adjusted >= ?
              AND stability.rank_stability_score > 0
            ORDER BY stability.rank_stability_score DESC NULLS LAST,
                period.pattern_score DESC NULLS LAST,
                period.quintile_spread_adjusted DESC NULLS LAST
            LIMIT ?
            """,
            [
                period_total_parquet.as_posix(),
                performance_target,
                stability_parquet.as_posix(),
                performance_target,
                min_runs_seen,
                require_sign_consistency,
                min_pair_n,
                min_quintile_spread_pct,
                top_n,
            ],
        )
        columns = [column[0] for column in result.description]
        return [dict(zip(columns, row)) for row in result.fetchall()]
    finally:
        conn.close()


def _build_candidates(
    rows: list[dict[str, Any]],
    *,
    taxonomy_rows: dict[str, dict[str, str]],
    raw_scan_columns: set[str] | None,
) -> list[UpsideFieldCandidate]:
    candidates: list[UpsideFieldCandidate] = []
    for row in rows:
        predictor_field = str(row["predictor_field"])
        taxonomy_row = taxonomy_rows.get(predictor_field)
        joinable, join_field = _resolve_raw_scan_join_field(
            predictor_field,
            taxonomy_row,
            raw_scan_columns,
        )
        relevance_tier = taxonomy_row.get("relevance_tier") if taxonomy_row else None
        stability_split = taxonomy_row.get("stability_split") if taxonomy_row else None
        candidates.append(
            UpsideFieldCandidate(
                predictor_field=predictor_field,
                predictor_display_name=row.get("predictor_display_name"),
                semantic_bucket=(
                    taxonomy_row.get("semantic_bucket") if taxonomy_row else None
                ),
                usage_role=taxonomy_row.get("usage_role") if taxonomy_row else None,
                relevance_tier=relevance_tier,
                stability_split=stability_split,
                period_pattern_score=_as_float(row.get("pattern_score")),
                period_quintile_spread_pp=_as_float(
                    row.get("quintile_spread_adjusted")
                ),
                period_pearson=_as_float(row.get("pearson_corr_adjusted")),
                period_top_quintile_avg_pct=_as_float(row.get("top_quintile_avg_perf")),
                period_bottom_quintile_avg_pct=_as_float(
                    row.get("bottom_quintile_avg_perf")
                ),
                pair_n=_as_int(row.get("pair_n")),
                predictor_fill_rate=_as_float(row.get("predictor_fill_rate")),
                runs_seen=_as_int(row.get("runs_seen")),
                sign_consistency_ratio=_as_float(row.get("sign_consistency_ratio")),
                median_pearson=_as_float(row.get("median_pearson")),
                median_quintile_spread_pp=_as_float(row.get("median_quintile_spread")),
                rank_stability_score=_as_float(row.get("rank_stability_score")),
                bullish_rank_direction="high_values_bullish",
                joinable_in_move_prediction_raw_scan=joinable,
                raw_scan_join_field=join_field,
                recommendation=_candidate_recommendation(
                    relevance_tier=relevance_tier,
                    stability_split=stability_split,
                    joinable=joinable,
                ),
            )
        )
    return candidates


def _candidate_is_default_tradable_predictor(
    candidate: UpsideFieldCandidate,
    *,
    excluded_semantic_buckets: set[str],
) -> bool:
    if candidate.semantic_bucket in excluded_semantic_buckets:
        return False
    if candidate.usage_role not in {None, "predictor_feature"}:
        return False
    if candidate.relevance_tier == "ignore":
        return False
    return True


def _write_candidate_csv(path: Path, candidates: list[UpsideFieldCandidate]) -> None:
    headers = (
        list(asdict(candidates[0]).keys())
        if candidates
        else list(UpsideFieldCandidate.__dataclass_fields__)
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(asdict(candidate))


def _write_markdown_report(path: Path, candidates: list[UpsideFieldCandidate]) -> None:
    recommendation_counts = {
        recommendation: sum(
            1 for candidate in candidates if candidate.recommendation == recommendation
        )
        for recommendation in sorted(
            {candidate.recommendation for candidate in candidates}
        )
    }
    direct_candidates = [
        candidate
        for candidate in candidates
        if candidate.recommendation == "test_directly_in_execution_backtest"
    ]
    daily_join_candidates = [
        candidate
        for candidate in candidates
        if candidate.recommendation == "requires_daily_all_fields_join"
    ]
    lines = [
        "# Upside all-fields candidate shortlist",
        "",
        "This report reuses the existing scan-period tracking outputs and does not run a fresh all-fields scan.",
        "",
        "## Small Conclusions",
        "",
        f"- Candidate count: {len(candidates)}.",
        f"- Recommendation counts: `{json.dumps(recommendation_counts, sort_keys=True)}`.",
        f"- Direct move-prediction raw-scan candidates: {len(direct_candidates)}.",
        f"- Daily all-fields join candidates: {len(daily_join_candidates)}.",
        "- Treat this as a shortlist, not a system: these predictors still need to be joined into execution backtests and checked against acted trades.",
        "",
        "| Field | Bucket | Spread | Stability | Runs | Join | Recommendation |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for candidate in candidates:
        spread = (
            ""
            if candidate.period_quintile_spread_pp is None
            else f"{candidate.period_quintile_spread_pp:.2f}"
        )
        stability = (
            ""
            if candidate.rank_stability_score is None
            else f"{candidate.rank_stability_score:.3f}"
        )
        join = candidate.raw_scan_join_field or (
            "unknown"
            if candidate.joinable_in_move_prediction_raw_scan is None
            else "daily_join_needed"
        )
        lines.append(
            "| "
            f"{candidate.predictor_field} | "
            f"{candidate.semantic_bucket or ''} | "
            f"{spread} | "
            f"{stability} | "
            f"{candidate.runs_seen or ''} | "
            f"{join} | "
            f"{candidate.recommendation} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _find_latest_move_prediction_database(
    root: str | Path = DEFAULT_MOVE_PREDICTION_DUCKDB_ROOT,
) -> Path | None:
    resolved_root = Path(root)
    if not resolved_root.exists():
        return None
    candidates = sorted(
        resolved_root.glob("iso_year=*/week=*/move_prediction_*.duckdb"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def analyze_all_fields_upside_edge_candidates(
    *,
    run_root: str | Path = DEFAULT_SCAN_PERIOD_RUN_ROOT,
    taxonomy_registry_path: str | Path = DEFAULT_TAXONOMY_REGISTRY,
    output_dir: str | Path | None = None,
    performance_target: str = DEFAULT_PERFORMANCE_TARGET,
    top_n: int = 50,
    min_runs_seen: int = 10,
    require_sign_consistency: float = 0.65,
    min_quintile_spread_pct: float = 0.30,
    min_pair_n: int = 100,
    move_prediction_database_path: str | Path | None = None,
    excluded_semantic_buckets: tuple[str, ...] = DEFAULT_EXCLUDED_SEMANTIC_BUCKETS,
    candidate_pool_multiplier: int = 5,
) -> dict[str, Any]:
    started = time.perf_counter()
    resolved_run_root = Path(run_root)
    resolved_output_dir = (
        Path(output_dir)
        if output_dir is not None
        else resolved_run_root / "upside_edge_research"
    )
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    taxonomy_rows = _load_taxonomy_rows(Path(taxonomy_registry_path))
    raw_scan_columns = _load_raw_scan_columns(move_prediction_database_path)
    rows = _load_upside_rows(
        run_root=resolved_run_root,
        performance_target=performance_target,
        top_n=max(top_n, top_n * max(1, candidate_pool_multiplier)),
        min_runs_seen=min_runs_seen,
        require_sign_consistency=require_sign_consistency,
        min_quintile_spread_pct=min_quintile_spread_pct,
        min_pair_n=min_pair_n,
    )
    candidates = _build_candidates(
        rows,
        taxonomy_rows=taxonomy_rows,
        raw_scan_columns=raw_scan_columns,
    )
    excluded_buckets = {
        bucket.strip() for bucket in excluded_semantic_buckets if bucket.strip()
    }
    candidates = [
        candidate
        for candidate in candidates
        if _candidate_is_default_tradable_predictor(
            candidate,
            excluded_semantic_buckets=excluded_buckets,
        )
    ][:top_n]

    candidate_csv = resolved_output_dir / "upside_field_candidates.csv"
    report_md = resolved_output_dir / "upside_field_candidates.md"
    manifest_path = resolved_output_dir / "upside_field_candidates_manifest.json"
    _write_candidate_csv(candidate_csv, candidates)
    _write_markdown_report(report_md, candidates)
    manifest_path.write_text(
        json.dumps(
            {
                "run_root": resolved_run_root.as_posix(),
                "taxonomy_registry_path": Path(taxonomy_registry_path).as_posix(),
                "performance_target": performance_target,
                "top_n": top_n,
                "min_runs_seen": min_runs_seen,
                "require_sign_consistency": require_sign_consistency,
                "min_quintile_spread_pct": min_quintile_spread_pct,
                "min_pair_n": min_pair_n,
                "excluded_semantic_buckets": list(excluded_semantic_buckets),
                "candidate_pool_multiplier": candidate_pool_multiplier,
                "move_prediction_database_path": (
                    str(move_prediction_database_path)
                    if move_prediction_database_path is not None
                    else None
                ),
                "candidate_count": len(candidates),
                "recommendation_counts": {
                    recommendation: sum(
                        1
                        for candidate in candidates
                        if candidate.recommendation == recommendation
                    )
                    for recommendation in sorted(
                        {candidate.recommendation for candidate in candidates}
                    )
                },
                "candidate_csv": candidate_csv.as_posix(),
                "report_md": report_md.as_posix(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "output_dir": resolved_output_dir.as_posix(),
        "candidate_csv": candidate_csv.as_posix(),
        "report_md": report_md.as_posix(),
        "manifest_path": manifest_path.as_posix(),
        "candidates": candidates,
        "elapsed_seconds": round(time.perf_counter() - started, 4),
    }


def _load_candidate_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _median_float(values: Sequence[float]) -> float | None:
    cleaned = sorted(value for value in values if value is not None)
    if not cleaned:
        return None
    mid = len(cleaned) // 2
    if len(cleaned) % 2 == 1:
        return cleaned[mid]
    return (cleaned[mid - 1] + cleaned[mid]) / 2.0


def _avg_float(values: Sequence[float]) -> float | None:
    cleaned = [value for value in values if value is not None]
    if not cleaned:
        return None
    return sum(cleaned) / len(cleaned)


def _round_optional(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _default_trade_ladder_realism_suite_output_dir(run_root: Path) -> Path:
    return run_root / "upside_edge_research" / "mar_jun_best_trade_ladder_realism_suite"


def _trade_ladder_scenario_output_dir(run_root: Path, scenario_name: str) -> Path:
    return _default_trade_ladder_realism_suite_output_dir(run_root) / scenario_name


def _default_trade_ladder_entry_trades_csv(
    run_root: Path,
    *,
    scenario_name: str = DEFAULT_TRADE_LADDER_ENTRY_SCENARIO_NAME,
) -> Path:
    return (
        _trade_ladder_scenario_output_dir(run_root, scenario_name)
        / "ticker_trade_ladder_trades.csv"
    )


def _load_trade_ladder_rows(
    path: Path,
    *,
    max_ladder_rank: int | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            ladder_rank = _as_int(row.get("ladder_rank"))
            if max_ladder_rank is not None and ladder_rank is not None:
                if ladder_rank > max_ladder_rank:
                    continue
            trade_number = _as_int(row.get("trade_number"))
            entry_day_label = str(row.get("entry_day_label") or "").strip()
            exit_day_label = str(row.get("exit_day_label") or "").strip()
            symbol = str(row.get("symbol") or "").strip()
            rows.append(
                {
                    **row,
                    "ladder_rank": ladder_rank,
                    "trade_number": trade_number,
                    "trade_return_pct": _as_float(row.get("trade_return_pct")),
                    "trade_multiple": _as_float(row.get("trade_multiple")),
                    "gross_trade_return_pct": _as_float(
                        row.get("gross_trade_return_pct") or row.get("trade_return_pct")
                    ),
                    "entry_close": _as_float(row.get("entry_close")),
                    "exit_close": _as_float(row.get("exit_close")),
                    "holding_calendar_days": _as_int(row.get("holding_calendar_days")),
                    "symbol": symbol,
                    "bare_ticker": str(row.get("bare_ticker") or "").strip(),
                    "entry_day_label": entry_day_label,
                    "exit_day_label": exit_day_label,
                    "trade_id": (
                        f"{symbol}|{trade_number or ''}|{entry_day_label}|{exit_day_label}"
                    ),
                }
            )
    return rows


def _resolve_daily_all_fields_database_map(
    *,
    all_fields_root: str | Path = DEFAULT_ALL_FIELDS_ROOT,
    start_day_label: str | None = None,
    end_day_label: str | None = None,
) -> dict[str, Path]:
    return {
        _extract_day_label_from_database_path(path): path
        for path in discover_all_fields_daily_databases(
            all_fields_root=all_fields_root,
            start_day_label=start_day_label,
            end_day_label=end_day_label,
        )
    }


def _resolve_trade_ladder_scenario_config(scenario_name: str) -> dict[str, Any]:
    normalized = scenario_name.strip().lower()
    for scenario in DEFAULT_TRADE_LADDER_REALISM_SCENARIOS:
        if str(scenario.get("scenario_name") or "").strip().lower() == normalized:
            return dict(scenario)
    raise ValueError(
        f"Unknown trade ladder scenario {scenario_name!r}. "
        f"Supported: {[row['scenario_name'] for row in DEFAULT_TRADE_LADDER_REALISM_SCENARIOS]}"
    )


def _extract_optimal_long_trade_ladder(
    progression_rows: Sequence[dict[str, Any]],
    *,
    min_pullback_pct_before_entry: float = DEFAULT_TRADE_LADDER_MIN_PULLBACK_PCT,
    transaction_cost_pct_per_side: float = DEFAULT_TRADE_LADDER_TRANSACTION_COST_PCT_PER_SIDE,
    min_trade_return_pct: float = DEFAULT_TRADE_LADDER_MIN_TRADE_RETURN_PCT,
) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for row in progression_rows:
        day_label = str(row.get("source_day_label") or "").strip()
        close_price = _as_float(row.get("close_price"))
        if not day_label or close_price is None:
            continue
        points.append(
            {
                "source_day_label": day_label,
                "point_date": _parse_day_label(day_label),
                "close_price": close_price,
            }
        )
    if len(points) < 2:
        return []

    points.sort(key=lambda row: (row["point_date"], row["source_day_label"]))
    trades: list[dict[str, Any]] = []
    cumulative_multiple = 1.0
    last_exit_close: float | None = None
    resolved_min_pullback = max(0.0, float(min_pullback_pct_before_entry))
    resolved_cost_per_side = max(0.0, float(transaction_cost_pct_per_side))
    resolved_min_trade_return = max(0.0, float(min_trade_return_pct))
    cost_multiplier_per_side = max(0.0, 1.0 - (resolved_cost_per_side / 100.0))
    index = 0
    last_index = len(points) - 1
    while index < last_index:
        while (
            index < last_index
            and points[index + 1]["close_price"] <= points[index]["close_price"]
        ):
            index += 1
        if index >= last_index:
            break
        entry_point = points[index]
        entry_close = float(entry_point["close_price"])
        pullback_from_last_exit_pct: float | None = None
        if last_exit_close is not None and last_exit_close > 0:
            pullback_from_last_exit_pct = (
                (last_exit_close - entry_close) / last_exit_close
            ) * 100.0
            if pullback_from_last_exit_pct < resolved_min_pullback:
                index += 1
                continue
        index += 1
        while (
            index < len(points)
            and points[index]["close_price"] >= points[index - 1]["close_price"]
        ):
            index += 1
        exit_point = points[index - 1]
        exit_close = float(exit_point["close_price"])
        if exit_close <= entry_close:
            continue
        gross_trade_multiple = exit_close / entry_close
        gross_trade_return_pct = (gross_trade_multiple - 1.0) * 100.0
        if gross_trade_return_pct < resolved_min_trade_return:
            continue
        trade_multiple = (
            gross_trade_multiple * cost_multiplier_per_side * cost_multiplier_per_side
        )
        if trade_multiple <= 1.0:
            continue
        cumulative_multiple *= trade_multiple
        last_exit_close = exit_close
        holding_days = (exit_point["point_date"] - entry_point["point_date"]).days
        trades.append(
            {
                "trade_number": len(trades) + 1,
                "entry_day_label": entry_point["source_day_label"],
                "exit_day_label": exit_point["source_day_label"],
                "entry_close": round(entry_close, 4),
                "exit_close": round(exit_close, 4),
                "holding_calendar_days": holding_days,
                "gross_trade_return_pct": round(gross_trade_return_pct, 4),
                "trade_return_pct": round((trade_multiple - 1.0) * 100.0, 4),
                "gross_trade_multiple": round(gross_trade_multiple, 6),
                "trade_multiple": round(trade_multiple, 6),
                "entry_pullback_from_last_exit_pct": _round_optional(
                    pullback_from_last_exit_pct, 4
                ),
                "transaction_cost_pct_per_side": round(resolved_cost_per_side, 4),
                "min_pullback_pct_before_entry": round(resolved_min_pullback, 4),
                "min_trade_return_pct_threshold": round(resolved_min_trade_return, 4),
                "cumulative_multiple_after_trade": round(cumulative_multiple, 6),
                "cumulative_return_pct_after_trade": round(
                    (cumulative_multiple - 1.0) * 100.0, 4
                ),
            }
        )
    return trades


def _format_trade_ladder_path(
    trades: Sequence[dict[str, Any]], *, max_segments: int = 6
) -> str:
    segments = [
        (
            f"{trade['entry_day_label']}@{trade['entry_close']}"
            f"->{trade['exit_day_label']}@{trade['exit_close']}"
            f" ({trade['trade_return_pct']}%)"
        )
        for trade in list(trades)[:max_segments]
    ]
    if len(trades) > max_segments:
        segments.append(f"... +{len(trades) - max_segments} more")
    return " | ".join(segments)


def _write_trade_ladder_report(
    path: Path,
    *,
    run_root: Path,
    output_dir: Path,
    scenario_name: str,
    eligible_symbol_count: int,
    summary_rows: Sequence[dict[str, Any]],
    report_top_n: int,
    min_market_cap_usd: float | None,
    min_start_close: float | None,
    min_average_volume_10d: float | None,
    min_pullback_pct_before_entry: float,
    transaction_cost_pct_per_side: float,
    min_trade_return_pct: float,
) -> None:
    top_rows = list(summary_rows)[:report_top_n]
    positive_trade_count = sum(1 for row in summary_rows if int(row["trade_count"]) > 0)
    multi_trade_count = sum(1 for row in summary_rows if int(row["trade_count"]) > 1)
    lines = [
        "# Mar-Jun best trade ladders by ticker",
        "",
        f"Scan-period run: `{run_root.as_posix()}`",
        f"Scenario: `{scenario_name}`",
        "",
        "This runner differs from `best_trades_indicator_profile_tracking.csv`.",
        "It does not score one start-to-end trade per ticker. Instead, it computes the perfect-hindsight long-only close-to-close trade ladder for each ticker over the whole period: buy local trough, sell next local peak, then re-enter after the next pullback.",
        "",
        "## Assumptions",
        "",
        "- Uses daily close progression only, not intraday highs/lows.",
        f"- Entry constraint: minimum pullback before re-entry = {min_pullback_pct_before_entry}%.",
        f"- Trading friction: transaction cost per side = {transaction_cost_pct_per_side}%.",
        f"- Trade filter: minimum gross trade return = {min_trade_return_pct}%.",
        "- This is still a hindsight upper bound on trade selection, even when the constraints reduce noise and micro-swings.",
        f"- Filters: market cap >= {min_market_cap_usd if min_market_cap_usd is not None else 'none'}, start close >= {min_start_close if min_start_close is not None else 'none'}, avg 10d volume >= {min_average_volume_10d if min_average_volume_10d is not None else 'none'}.",
        "",
        "## Small Conclusions",
        "",
        f"- Eligible tickers after filters: {eligible_symbol_count}.",
        f"- Ranked tickers with at least one positive swing: {positive_trade_count}.",
        f"- Tickers with more than one swing trade: {multi_trade_count}.",
    ]
    if top_rows:
        best_row = top_rows[0]
        lines.append(
            "- Best cumulative ladder: "
            f"`{best_row['symbol']}` with {best_row['trade_count']} trades, "
            f"ladder return {best_row['cumulative_return_pct']}%, "
            f"buy/hold {best_row['buy_hold_return_pct']}%."
        )
    lines.extend(
        [
            "",
            "## Top Ladder Rankings",
            "",
            "| Rank | Symbol | Trades | Ladder Return | Buy/Hold | Excess | Avg Trade | Max Trade |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in top_rows:
        lines.append(
            "| "
            f"{row['ladder_rank']} | "
            f"{row['symbol']} | "
            f"{row['trade_count']} | "
            f"{row['cumulative_return_pct']} | "
            f"{row['buy_hold_return_pct']} | "
            f"{row['excess_vs_buy_hold_pct']} | "
            f"{row['avg_trade_return_pct']} | "
            f"{row['max_trade_return_pct']} |"
        )
    lines.extend(
        [
            "",
            "## Output Locations",
            "",
            f"- Ladder ranking CSV: `{(output_dir / 'ticker_trade_ladder_ranking.csv').as_posix()}`",
            f"- Ladder trades CSV: `{(output_dir / 'ticker_trade_ladder_trades.csv').as_posix()}`",
            f"- Manifest: `{(output_dir / 'ticker_trade_ladder_manifest.json').as_posix()}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _candidate_indicator_specs(
    *,
    candidate_rows: Sequence[dict[str, str]],
    available_columns: set[str] | None,
    max_indicator_fields: int,
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in candidate_rows:
        field_name = str(row.get("predictor_field") or "").strip()
        if not field_name or field_name in seen:
            continue
        if available_columns is not None and field_name not in available_columns:
            continue
        seen.add(field_name)
        index = len(specs) + 1
        specs.append(
            {
                "predictor_field": field_name,
                "window_label": _field_window_label(field_name),
                "recommendation": row.get("recommendation") or "",
                "raw_scan_join_field": row.get("raw_scan_join_field") or "",
                "period_quintile_spread_pp": _as_float(
                    row.get("period_quintile_spread_pp")
                ),
                "rank_stability_score": _as_float(row.get("rank_stability_score")),
                "value_alias": f"indicator_{index:02d}_value",
                "percentile_alias": f"indicator_{index:02d}_percentile",
            }
        )
        if len(specs) >= max_indicator_fields:
            break
    return specs


def _attach_readonly(conn: Any, alias: str, database_path: Path) -> None:
    conn.execute(
        f"ATTACH {_quote_sql_literal(database_path.as_posix())} "
        f"AS {_quote_identifier(alias)} (READ_ONLY)"
    )


def _optional_numeric_select(
    *,
    available_columns: set[str],
    column_name: str,
    alias: str,
    table_alias: str = "psf",
) -> str:
    if column_name not in available_columns:
        return f"CAST(NULL AS DOUBLE) AS {_quote_identifier(alias)}"
    return (
        f"TRY_CAST({table_alias}.{_quote_identifier(column_name)} AS DOUBLE) "
        f"AS {_quote_identifier(alias)}"
    )


def _write_trade_ladder_realism_suite_report(
    path: Path,
    *,
    run_root: Path,
    output_dir: Path,
    scenario_rows: Sequence[dict[str, Any]],
) -> None:
    lines = [
        "# Mar-Jun trade ladder realism suite",
        "",
        f"Scan-period run: `{run_root.as_posix()}`",
        "",
        "This suite compares the same hindsight ladder logic under progressively tighter constraints so the output is less dominated by tiny re-entry wiggles.",
        "",
        "## Scenario Comparison",
        "",
        "| Scenario | Pullback % | Cost / side % | Min gross trade % | Positive Symbols | Multi-trade Symbols | Best Symbol | Best Ladder Return | Median Ladder Return |",
        "|---|---:|---:|---:|---:|---:|---|---:|---:|",
    ]
    for row in scenario_rows:
        lines.append(
            "| "
            f"{row['scenario_name']} | "
            f"{row['min_pullback_pct_before_entry']} | "
            f"{row['transaction_cost_pct_per_side']} | "
            f"{row['min_trade_return_pct']} | "
            f"{row['positive_trade_symbol_count']} | "
            f"{row['multi_trade_symbol_count']} | "
            f"{row['top_symbol'] or ''} | "
            f"{row['top_cumulative_return_pct'] or ''} | "
            f"{row['median_cumulative_return_pct'] or ''} |"
        )
    lines.extend(
        [
            "",
            "## Output Locations",
            "",
            f"- Scenario comparison CSV: `{(output_dir / 'trade_ladder_realism_suite_summary.csv').as_posix()}`",
            f"- Manifest: `{(output_dir / 'trade_ladder_realism_suite_manifest.json').as_posix()}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_trade_ladder_profile_entry_report(
    path: Path,
    *,
    run_root: Path,
    output_dir: Path,
    trades_csv_path: Path,
    max_ladder_rank: int | None,
    trade_count: int,
    summary_rows: Sequence[dict[str, Any]],
) -> None:
    top_rows = list(summary_rows)[: min(8, len(summary_rows))]
    lines = [
        "# Trade ladder profile entry capture",
        "",
        f"Scan-period run: `{run_root.as_posix()}`",
        f"Trades source: `{trades_csv_path.as_posix()}`",
        "",
        "This diagnostic still selects the trade set with hindsight, but profile ranks are measured only from anchors available on or before each trade entry date.",
        "",
        "## Scope",
        "",
        f"- Ladder trades analyzed: {trade_count}.",
        f"- Maximum ladder rank included: {max_ladder_rank if max_ladder_rank is not None else 'all'}.",
        "- Signals are measured two ways: latest anchor before entry, and best rank achieved before entry.",
        "",
        "## Profile Summary",
        "",
        "| Profile | Trades | Latest Top100 Rate | Best Top100 Rate | Median Latest Rank | Median Best Rank | Median Lead Days |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in top_rows:
        lines.append(
            "| "
            f"{row['profile_name']} | "
            f"{row['trade_count']} | "
            f"{float(row['latest_top100_rate']) * 100:.1f}% | "
            f"{float(row['best_top100_rate']) * 100:.1f}% | "
            f"{row['median_latest_rank'] or ''} | "
            f"{row['median_best_rank'] or ''} | "
            f"{row['median_latest_lead_days'] or ''} |"
        )
    lines.extend(
        [
            "",
            "## Output Locations",
            "",
            f"- Trade/profile detail CSV: `{(output_dir / 'trade_ladder_entry_profile_capture.csv').as_posix()}`",
            f"- Profile summary CSV: `{(output_dir / 'trade_ladder_profile_entry_summary.csv').as_posix()}`",
            f"- Manifest: `{(output_dir / 'trade_ladder_profile_entry_manifest.json').as_posix()}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_trade_ladder_entry_field_report(
    path: Path,
    *,
    run_root: Path,
    output_dir: Path,
    trades_csv_path: Path,
    max_ladder_rank: int | None,
    entry_trade_count: int,
    percentile_threshold: float,
    summary_rows: Sequence[dict[str, Any]],
) -> None:
    top_rows = list(summary_rows)[: min(10, len(summary_rows))]
    lines = [
        "# Trade ladder entry-day field diagnostics",
        "",
        f"Scan-period run: `{run_root.as_posix()}`",
        f"Trades source: `{trades_csv_path.as_posix()}`",
        "",
        "This suite uses the real daily `export_all_tradingview_fields_duckdb` outputs for each ladder entry day. The trade set is still hindsight-selected, but each field observation is taken strictly from the actual entry date.",
        "",
        "## Scope",
        "",
        f"- Entry trades analyzed: {entry_trade_count}.",
        f"- Maximum ladder rank included: {max_ladder_rank if max_ladder_rank is not None else 'all'}.",
        f"- Bullish hit threshold: top {int((1.0 - percentile_threshold) * 100)}% of same-day universe.",
        "",
        "## Field Summary",
        "",
        "| Field | Window | Coverage | Hit Rate | Median Entry Percentile | Median Return When Hit | Recommendation |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for row in top_rows:
        lines.append(
            "| "
            f"{row['predictor_field']} | "
            f"{row['window_label']} | "
            f"{float(row['observation_coverage_rate']) * 100:.1f}% | "
            f"{float(row['bullish_entry_hit_rate']) * 100:.1f}% | "
            f"{row['median_entry_percentile'] or ''} | "
            f"{row['median_trade_return_hit_pct'] or ''} | "
            f"{row['recommendation']} |"
        )
    lines.extend(
        [
            "",
            "## Output Locations",
            "",
            f"- Entry field detail CSV: `{(output_dir / 'trade_ladder_entry_field_details.csv').as_posix()}`",
            f"- Entry field summary CSV: `{(output_dir / 'trade_ladder_entry_field_summary.csv').as_posix()}`",
            f"- Manifest: `{(output_dir / 'trade_ladder_entry_field_manifest.json').as_posix()}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_best_trade_tracking_report(
    path: Path,
    *,
    output_dir: Path,
    run_root: Path,
    backwards_database_path: Path,
    best_trade_rows: Sequence[dict[str, Any]],
    profile_summary_rows: Sequence[dict[str, Any]],
    indicator_summary_rows: Sequence[dict[str, Any]],
    indicator_percentile_threshold: float,
    candidate_field_count: int,
) -> None:
    top_profiles = list(profile_summary_rows)[:8]
    top_indicators = list(indicator_summary_rows)[:12]
    lines = [
        "# Mar-Jun best-trade indicator/profile tracking",
        "",
        f"Scan-period run: `{run_root.as_posix()}`",
        f"Backwards profile DB: `{backwards_database_path.as_posix()}`",
        "",
        "This is a hindsight diagnostic: it finds the best realized period winners, then checks whether edge-field indicators and existing scoring profiles were pointing at those symbols during the same sparse Mar-Jun window.",
        "",
        "## How To Read This",
        "",
        f"- A candidate field is treated as bullish when the symbol is at or above percentile `{indicator_percentile_threshold:.2f}` for that field at the period-start snapshot.",
        "- Windowed fields such as `volume|60`, `ATRP|1M`, or `ADRP|30` are kept as separate indicators; the suffix is the suggested lookback/window from the all-fields export.",
        "- Profile tracking uses the existing sparse backwards snapshots and records whether each winner ever reached top-50, top-100, or top-250 in the selected profile set.",
        "- This does not prove causality. It checks whether the indicator candidates and scoring profiles would have helped surface the winners early enough to investigate.",
        "",
        "## Small Conclusions",
        "",
        f"- Hindsight winners checked: {len(best_trade_rows)}.",
        f"- Candidate indicator fields checked: {candidate_field_count}.",
    ]
    if profile_summary_rows:
        best_profile = profile_summary_rows[0]
        lines.append(
            "- Best profile capture by top-100 rate: "
            f"`{best_profile['profile_name']}` captured "
            f"{best_profile['captured_top100_count']}/{best_profile['best_trade_count']} "
            f"({float(best_profile['captured_top100_rate']) * 100:.1f}%)."
        )
    if indicator_summary_rows:
        best_indicator = indicator_summary_rows[0]
        lines.append(
            "- Best field hit-rate among winners: "
            f"`{best_indicator['predictor_field']}` hit "
            f"{float(best_indicator['best_trade_hit_rate']) * 100:.1f}% of winners "
            f"at the bullish percentile gate."
        )
    lines.extend(
        [
            "- Treat high hit-rate indicators as overlays or entry filters, then verify with forward-only execution rules before promoting them into scoring.",
            "",
            "## Profile Capture Summary",
            "",
            "| Profile | Top-50 | Top-100 | Top-250 | Avg Best Rank | Median Return Top-100 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in top_profiles:
        lines.append(
            "| "
            f"{row['profile_name']} | "
            f"{float(row['captured_top50_rate']) * 100:.1f}% | "
            f"{float(row['captured_top100_rate']) * 100:.1f}% | "
            f"{float(row['captured_top250_rate']) * 100:.1f}% | "
            f"{row['avg_best_rank'] or ''} | "
            f"{row['median_return_top100_pct'] or ''} |"
        )
    lines.extend(
        [
            "",
            "## Indicator Hindsight Summary",
            "",
            "| Field | Window | Hit Rate | Median Winner Percentile | Top-Gate Median Return | Recommendation |",
            "|---|---|---:|---:|---:|---|",
        ]
    )
    for row in top_indicators:
        lines.append(
            "| "
            f"{row['predictor_field']} | "
            f"{row['window_label']} | "
            f"{float(row['best_trade_hit_rate']) * 100:.1f}% | "
            f"{row['best_trade_median_percentile'] or ''} | "
            f"{row['top_indicator_median_return_pct'] or ''} | "
            f"{row['recommendation']} |"
        )
    lines.extend(
        [
            "",
            "## Output Locations",
            "",
            f"- Best-trade tracking CSV: `{(output_dir / 'best_trades_indicator_profile_tracking.csv').as_posix()}`",
            f"- Indicator detail CSV: `{(output_dir / 'best_trade_indicator_details.csv').as_posix()}`",
            f"- Indicator summary CSV: `{(output_dir / 'indicator_hindsight_summary.csv').as_posix()}`",
            f"- Profile capture CSV: `{(output_dir / 'profile_capture_summary.csv').as_posix()}`",
            f"- Manifest: `{(output_dir / 'best_trade_profile_tracking_manifest.json').as_posix()}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def analyze_best_trade_profile_tracking(
    *,
    run_root: str | Path = DEFAULT_SCAN_PERIOD_RUN_ROOT,
    candidate_csv_path: str | Path | None = None,
    backwards_database_path: str | Path = DEFAULT_EXISTING_BACKWARDS_DATABASE_PATH,
    output_dir: str | Path | None = None,
    profile_names: Sequence[str] = DEFAULT_EXISTING_PROFILE_NAMES,
    top_trade_count: int = DEFAULT_BEST_TRADE_TOP_N,
    min_market_cap_usd: float | None = DEFAULT_BEST_TRADE_MIN_MARKET_CAP_USD,
    min_start_close: float | None = DEFAULT_BEST_TRADE_MIN_START_CLOSE,
    min_average_volume_10d: float | None = DEFAULT_BEST_TRADE_MIN_AVERAGE_VOLUME_10D,
    indicator_percentile_threshold: float = DEFAULT_BEST_TRADE_INDICATOR_PERCENTILE,
    max_indicator_fields: int = DEFAULT_BEST_TRADE_MAX_INDICATOR_FIELDS,
) -> dict[str, Any]:
    """Find best realized period trades and check indicator/profile coverage.

    This is intentionally a hindsight diagnostic. It does not create a trading
    rule; it asks whether the edge-field candidates and existing scoring
    profiles were capable of surfacing the winners during the same scan period.
    """
    started = time.perf_counter()
    resolved_run_root = Path(run_root)
    resolved_candidate_csv = Path(
        candidate_csv_path
        if candidate_csv_path is not None
        else resolved_run_root
        / "upside_edge_research"
        / "mar_jun_default"
        / "upside_field_candidates.csv"
    )
    resolved_backwards_db = Path(backwards_database_path)
    resolved_output_dir = (
        Path(output_dir)
        if output_dir is not None
        else resolved_run_root / "upside_edge_research" / "mar_jun_best_trade_tracking"
    )
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    period_input_path = (
        resolved_run_root / "period_total" / "period_analysis_input.duckdb"
    )
    period_returns_path = (
        resolved_run_root
        / "period_total"
        / "period_returns"
        / "period_boundary_returns.duckdb"
    )
    for required_path in (
        resolved_candidate_csv,
        resolved_backwards_db,
        period_input_path,
        period_returns_path,
    ):
        if not required_path.exists():
            raise FileNotFoundError(
                f"Missing required input: {required_path.as_posix()}"
            )

    candidate_rows = _load_candidate_csv_rows(resolved_candidate_csv)
    duckdb = _import_duckdb()
    conn = duckdb.connect()
    try:
        _attach_readonly(conn, "pi", period_input_path)
        _attach_readonly(conn, "pr", period_returns_path)
        _attach_readonly(conn, "bw", resolved_backwards_db)

        available_columns = {
            row[0]
            for row in conn.execute("DESCRIBE pi.period_start_features").fetchall()
        }
        indicator_specs = _candidate_indicator_specs(
            candidate_rows=candidate_rows,
            available_columns=available_columns,
            max_indicator_fields=max(1, max_indicator_fields),
        )
        if not indicator_specs:
            raise ValueError(
                "No candidate indicator fields exist in period_start_features."
            )

        value_select_sql = ",\n            ".join(
            f"TRY_CAST(psf.{_quote_identifier(spec['predictor_field'])} AS DOUBLE) AS {_quote_identifier(spec['value_alias'])}"
            for spec in indicator_specs
        )
        percentile_select_sql = ",\n            ".join(
            "CASE WHEN "
            f"{_quote_identifier(spec['value_alias'])} IS NULL THEN NULL ELSE "
            f"PERCENT_RANK() OVER (ORDER BY {_quote_identifier(spec['value_alias'])} ASC NULLS FIRST) END "
            f"AS {_quote_identifier(spec['percentile_alias'])}"
            for spec in indicator_specs
        )
        filters = ["TRY_CAST(r.period_return_pct AS DOUBLE) IS NOT NULL"]
        if min_start_close is not None:
            filters.append(
                f"TRY_CAST(r.start_close_price AS DOUBLE) >= {float(min_start_close)}"
            )
        if min_market_cap_usd is not None and "market_cap_basic" in available_columns:
            filters.append(
                "TRY_CAST(psf.market_cap_basic AS DOUBLE) "
                f">= {float(min_market_cap_usd)}"
            )
        if (
            min_average_volume_10d is not None
            and "average_volume_10d_calc" in available_columns
        ):
            filters.append(
                "TRY_CAST(psf.average_volume_10d_calc AS DOUBLE) "
                f">= {float(min_average_volume_10d)}"
            )
        filter_sql = " AND\n              ".join(filters)

        conn.execute(f"""
            CREATE TEMP TABLE candidate_universe_base AS
            SELECT r.symbol,
                COALESCE(
                    NULLIF(split_part(CAST(r.symbol AS VARCHAR), ':', 2), ''),
                    CAST(r.symbol AS VARCHAR)
                ) AS bare_ticker,
                r.start_day_label,
                r.end_day_label,
                TRY_CAST(r.start_close_price AS DOUBLE) AS start_close_price,
                TRY_CAST(r.end_close_price AS DOUBLE) AS end_close_price,
                TRY_CAST(r.holding_calendar_days AS DOUBLE) AS holding_calendar_days,
                TRY_CAST(r.period_return_pct AS DOUBLE) AS period_return_pct,
                {_optional_numeric_select(available_columns=available_columns, column_name='market_cap_basic', alias='market_cap_basic')},
                {_optional_numeric_select(available_columns=available_columns, column_name='volume', alias='volume')},
                {_optional_numeric_select(available_columns=available_columns, column_name='average_volume_10d_calc', alias='average_volume_10d_calc')},
                {value_select_sql}
            FROM pr.period_boundary_returns AS r
            INNER JOIN pi.period_start_features AS psf
                ON psf.symbol = r.symbol
            WHERE {filter_sql}
            """)
        conn.execute(f"""
            CREATE TEMP TABLE candidate_universe AS
            SELECT *,
                {percentile_select_sql}
            FROM candidate_universe_base
            """)
        conn.execute(f"""
            CREATE TEMP TABLE best_trades AS
            SELECT *
            FROM (
                SELECT ROW_NUMBER() OVER (
                        ORDER BY period_return_pct DESC NULLS LAST, symbol
                    ) AS hindsight_rank,
                    *
                FROM candidate_universe
            )
            WHERE hindsight_rank <= {int(top_trade_count)}
            """)

        profile_values = ", ".join(
            f"({_quote_sql_literal(profile_name)})" for profile_name in profile_names
        )
        conn.execute(f"""
            CREATE TEMP TABLE included_profiles AS
            SELECT * FROM (VALUES {profile_values}) AS t(profile_name)
            """)
        conn.execute("""
            CREATE TEMP TABLE profile_observation_rows AS
            SELECT b.hindsight_rank,
                b.symbol AS period_symbol,
                b.bare_ticker,
                p.profile_name,
                s.anchor_name,
                s.symbol AS snapshot_symbol,
                TRY_CAST(s.score AS DOUBLE) AS score,
                TRY_CAST(s.profile_rank AS INTEGER) AS profile_rank,
                COALESCE(a.anchor_created_at_utc, runs.current_created_at_utc) AS point_time
            FROM best_trades AS b
            CROSS JOIN included_profiles AS p
            LEFT JOIN bw.backwards_anchor_snapshots AS s
                ON LOWER(TRIM(s.profile_name)) = LOWER(TRIM(p.profile_name))
                AND s.horizon_name = 'weeks'
                AND s.close IS NOT NULL
                AND NOT s.is_current
                AND (
                    s.symbol = b.symbol
                    OR COALESCE(
                        NULLIF(split_part(CAST(s.symbol AS VARCHAR), ':', 2), ''),
                        CAST(s.symbol AS VARCHAR)
                    ) = b.bare_ticker
                )
            LEFT JOIN bw.backwards_analysis_runs AS runs
                ON s.backwards_analysis_id = runs.backwards_analysis_id
            LEFT JOIN bw.backwards_analysis_anchors AS a
                ON s.backwards_analysis_id = a.backwards_analysis_id
                AND s.anchor_name = a.anchor_name
            """)
        conn.execute("""
            CREATE TEMP TABLE profile_trade_summary AS
            SELECT hindsight_rank,
                period_symbol,
                bare_ticker,
                profile_name,
                COUNT(profile_rank) AS scored_anchor_count,
                MIN(profile_rank) AS best_rank,
                MAX(score) AS best_score,
                SUM(CASE WHEN profile_rank <= 50 THEN 1 ELSE 0 END) AS top50_anchor_count,
                SUM(CASE WHEN profile_rank <= 100 THEN 1 ELSE 0 END) AS top100_anchor_count,
                SUM(CASE WHEN profile_rank <= 250 THEN 1 ELSE 0 END) AS top250_anchor_count,
                MIN(CASE WHEN profile_rank <= 50 THEN CAST(point_time AS VARCHAR) ELSE NULL END) AS first_top50_time,
                MIN(CASE WHEN profile_rank <= 100 THEN CAST(point_time AS VARCHAR) ELSE NULL END) AS first_top100_time,
                MIN(CASE WHEN profile_rank <= 250 THEN CAST(point_time AS VARCHAR) ELSE NULL END) AS first_top250_time
            FROM profile_observation_rows
            GROUP BY hindsight_rank, period_symbol, bare_ticker, profile_name
            """)

        best_rows = _fetch_dict_rows(
            conn,
            "SELECT * FROM best_trades ORDER BY hindsight_rank",
        )
        universe_rows = _fetch_dict_rows(
            conn,
            """
            SELECT u.*,
                CASE WHEN b.hindsight_rank IS NULL THEN 0 ELSE 1 END AS is_best_trade
            FROM candidate_universe AS u
            LEFT JOIN best_trades AS b USING (symbol)
            """,
        )
        profile_trade_rows = _fetch_dict_rows(
            conn,
            "SELECT * FROM profile_trade_summary ORDER BY hindsight_rank, profile_name",
        )
    finally:
        conn.close()

    profile_rows_by_trade: dict[int, list[dict[str, Any]]] = {}
    profile_summary_map: dict[str, dict[str, Any]] = {
        profile_name: {
            "profile_name": profile_name,
            "best_trade_count": len(best_rows),
            "scored_trade_count": 0,
            "captured_top50_count": 0,
            "captured_top100_count": 0,
            "captured_top250_count": 0,
            "best_ranks": [],
            "top100_returns": [],
        }
        for profile_name in profile_names
    }
    return_by_rank = {
        int(row["hindsight_rank"]): _as_float(row.get("period_return_pct"))
        for row in best_rows
    }
    for row in profile_trade_rows:
        rank_key = int(row["hindsight_rank"])
        profile_rows_by_trade.setdefault(rank_key, []).append(row)
        profile_name = str(row["profile_name"])
        summary = profile_summary_map.setdefault(
            profile_name,
            {
                "profile_name": profile_name,
                "best_trade_count": len(best_rows),
                "scored_trade_count": 0,
                "captured_top50_count": 0,
                "captured_top100_count": 0,
                "captured_top250_count": 0,
                "best_ranks": [],
                "top100_returns": [],
            },
        )
        best_rank = _as_int(row.get("best_rank"))
        if best_rank is None:
            continue
        summary["scored_trade_count"] += 1
        summary["best_ranks"].append(float(best_rank))
        period_return = return_by_rank.get(rank_key)
        if best_rank <= 50:
            summary["captured_top50_count"] += 1
        if best_rank <= 100:
            summary["captured_top100_count"] += 1
            if period_return is not None:
                summary["top100_returns"].append(period_return)
        if best_rank <= 250:
            summary["captured_top250_count"] += 1

    profile_summary_rows: list[dict[str, Any]] = []
    denominator = max(1, len(best_rows))
    for summary in profile_summary_map.values():
        profile_summary_rows.append(
            {
                "profile_name": summary["profile_name"],
                "best_trade_count": len(best_rows),
                "scored_trade_count": summary["scored_trade_count"],
                "captured_top50_count": summary["captured_top50_count"],
                "captured_top50_rate": round(
                    summary["captured_top50_count"] / denominator, 4
                ),
                "captured_top100_count": summary["captured_top100_count"],
                "captured_top100_rate": round(
                    summary["captured_top100_count"] / denominator, 4
                ),
                "captured_top250_count": summary["captured_top250_count"],
                "captured_top250_rate": round(
                    summary["captured_top250_count"] / denominator, 4
                ),
                "avg_best_rank": _round_optional(_avg_float(summary["best_ranks"]), 2),
                "median_best_rank": _round_optional(
                    _median_float(summary["best_ranks"]), 2
                ),
                "median_return_top100_pct": _round_optional(
                    _median_float(summary["top100_returns"]), 4
                ),
            }
        )
    profile_summary_rows.sort(
        key=lambda row: (
            float(row["captured_top100_rate"]),
            float(row["captured_top50_rate"]),
            -(float(row["avg_best_rank"]) if row["avg_best_rank"] else 999999.0),
        ),
        reverse=True,
    )

    best_trade_output_rows: list[dict[str, Any]] = []
    indicator_detail_rows: list[dict[str, Any]] = []
    for row in best_rows:
        rank_key = int(row["hindsight_rank"])
        hit_specs = [
            spec
            for spec in indicator_specs
            if (_as_float(row.get(spec["percentile_alias"])) or 0.0)
            >= indicator_percentile_threshold
        ]
        direct_hit_specs = [
            spec
            for spec in hit_specs
            if spec["recommendation"] == "test_directly_in_execution_backtest"
        ]
        profile_rows = profile_rows_by_trade.get(rank_key, [])
        ranked_profile_rows = [
            profile_row
            for profile_row in profile_rows
            if _as_int(profile_row.get("best_rank")) is not None
        ]
        ranked_profile_rows.sort(
            key=lambda profile_row: _as_int(profile_row.get("best_rank")) or 999999
        )
        best_profile_row = ranked_profile_rows[0] if ranked_profile_rows else None
        top100_profiles = [
            str(profile_row["profile_name"])
            for profile_row in ranked_profile_rows
            if (_as_int(profile_row.get("best_rank")) or 999999) <= 100
        ]
        best_trade_output_rows.append(
            {
                "hindsight_rank": rank_key,
                "symbol": row["symbol"],
                "bare_ticker": row["bare_ticker"],
                "period_return_pct": _round_optional(
                    _as_float(row.get("period_return_pct")), 4
                ),
                "start_day_label": row.get("start_day_label"),
                "end_day_label": row.get("end_day_label"),
                "start_close_price": _round_optional(
                    _as_float(row.get("start_close_price")), 4
                ),
                "end_close_price": _round_optional(
                    _as_float(row.get("end_close_price")), 4
                ),
                "market_cap_basic": _round_optional(
                    _as_float(row.get("market_cap_basic")), 2
                ),
                "average_volume_10d_calc": _round_optional(
                    _as_float(row.get("average_volume_10d_calc")), 2
                ),
                "indicator_hit_count": len(hit_specs),
                "direct_indicator_hit_count": len(direct_hit_specs),
                "indicator_hit_fields": ";".join(
                    spec["predictor_field"] for spec in hit_specs
                ),
                "direct_indicator_hit_fields": ";".join(
                    spec["predictor_field"] for spec in direct_hit_specs
                ),
                "best_profile_name": (
                    best_profile_row.get("profile_name") if best_profile_row else None
                ),
                "best_profile_rank": (
                    _as_int(best_profile_row.get("best_rank"))
                    if best_profile_row
                    else None
                ),
                "best_profile_score": (
                    _round_optional(_as_float(best_profile_row.get("best_score")), 4)
                    if best_profile_row
                    else None
                ),
                "profiles_top50_count": sum(
                    1
                    for profile_row in ranked_profile_rows
                    if (_as_int(profile_row.get("best_rank")) or 999999) <= 50
                ),
                "profiles_top100_count": len(top100_profiles),
                "profiles_top250_count": sum(
                    1
                    for profile_row in ranked_profile_rows
                    if (_as_int(profile_row.get("best_rank")) or 999999) <= 250
                ),
                "profiles_top100": ";".join(top100_profiles),
            }
        )
        for spec in indicator_specs:
            percentile = _as_float(row.get(spec["percentile_alias"]))
            indicator_detail_rows.append(
                {
                    "hindsight_rank": rank_key,
                    "symbol": row["symbol"],
                    "period_return_pct": _round_optional(
                        _as_float(row.get("period_return_pct")), 4
                    ),
                    "predictor_field": spec["predictor_field"],
                    "window_label": spec["window_label"],
                    "recommendation": spec["recommendation"],
                    "raw_scan_join_field": spec["raw_scan_join_field"],
                    "indicator_value": _round_optional(
                        _as_float(row.get(spec["value_alias"])), 6
                    ),
                    "indicator_percentile": _round_optional(percentile, 4),
                    "bullish_indicator_hit": (
                        percentile is not None
                        and percentile >= indicator_percentile_threshold
                    ),
                }
            )

    indicator_summary_rows: list[dict[str, Any]] = []
    best_count = max(1, len(best_rows))
    for spec in indicator_specs:
        top_gate_returns = [
            _as_float(row.get("period_return_pct"))
            for row in universe_rows
            if (_as_float(row.get(spec["percentile_alias"])) or 0.0)
            >= indicator_percentile_threshold
        ]
        non_top_gate_returns = [
            _as_float(row.get("period_return_pct"))
            for row in universe_rows
            if (_as_float(row.get(spec["percentile_alias"])) or 0.0)
            < indicator_percentile_threshold
        ]
        best_percentiles = [
            _as_float(row.get(spec["percentile_alias"]))
            for row in universe_rows
            if int(row.get("is_best_trade") or 0) == 1
        ]
        best_hits = [
            percentile
            for percentile in best_percentiles
            if percentile is not None and percentile >= indicator_percentile_threshold
        ]
        indicator_summary_rows.append(
            {
                "predictor_field": spec["predictor_field"],
                "window_label": spec["window_label"],
                "recommendation": spec["recommendation"],
                "raw_scan_join_field": spec["raw_scan_join_field"],
                "period_quintile_spread_pp": _round_optional(
                    spec["period_quintile_spread_pp"], 4
                ),
                "rank_stability_score": _round_optional(
                    spec["rank_stability_score"], 4
                ),
                "best_trade_count": len(best_rows),
                "best_trade_indicator_hits": len(best_hits),
                "best_trade_hit_rate": round(len(best_hits) / best_count, 4),
                "best_trade_median_percentile": _round_optional(
                    _median_float([p for p in best_percentiles if p is not None]), 4
                ),
                "top_indicator_universe_count": len(
                    [value for value in top_gate_returns if value is not None]
                ),
                "top_indicator_avg_return_pct": _round_optional(
                    _avg_float(top_gate_returns), 4
                ),
                "top_indicator_median_return_pct": _round_optional(
                    _median_float(top_gate_returns), 4
                ),
                "non_top_indicator_avg_return_pct": _round_optional(
                    _avg_float(non_top_gate_returns), 4
                ),
                "non_top_indicator_median_return_pct": _round_optional(
                    _median_float(non_top_gate_returns), 4
                ),
            }
        )
    indicator_summary_rows.sort(
        key=lambda row: (
            float(row["best_trade_hit_rate"]),
            float(row["best_trade_median_percentile"] or 0.0),
            float(row["rank_stability_score"] or 0.0),
        ),
        reverse=True,
    )

    best_trades_csv = resolved_output_dir / "best_trades_indicator_profile_tracking.csv"
    indicator_details_csv = resolved_output_dir / "best_trade_indicator_details.csv"
    indicator_summary_csv = resolved_output_dir / "indicator_hindsight_summary.csv"
    profile_summary_csv = resolved_output_dir / "profile_capture_summary.csv"
    report_md = resolved_output_dir / "best_trade_profile_tracking_report.md"
    manifest_path = resolved_output_dir / "best_trade_profile_tracking_manifest.json"
    _write_dict_csv(best_trades_csv, best_trade_output_rows)
    _write_dict_csv(indicator_details_csv, indicator_detail_rows)
    _write_dict_csv(indicator_summary_csv, indicator_summary_rows)
    _write_dict_csv(profile_summary_csv, profile_summary_rows)
    _write_best_trade_tracking_report(
        report_md,
        output_dir=resolved_output_dir,
        run_root=resolved_run_root,
        backwards_database_path=resolved_backwards_db,
        best_trade_rows=best_trade_output_rows,
        profile_summary_rows=profile_summary_rows,
        indicator_summary_rows=indicator_summary_rows,
        indicator_percentile_threshold=indicator_percentile_threshold,
        candidate_field_count=len(indicator_specs),
    )
    manifest_path.write_text(
        json.dumps(
            {
                "run_root": resolved_run_root.as_posix(),
                "candidate_csv_path": resolved_candidate_csv.as_posix(),
                "backwards_database_path": resolved_backwards_db.as_posix(),
                "output_dir": resolved_output_dir.as_posix(),
                "profile_names": list(profile_names),
                "top_trade_count": top_trade_count,
                "min_market_cap_usd": min_market_cap_usd,
                "min_start_close": min_start_close,
                "min_average_volume_10d": min_average_volume_10d,
                "indicator_percentile_threshold": indicator_percentile_threshold,
                "max_indicator_fields": max_indicator_fields,
                "candidate_indicator_fields": [
                    spec["predictor_field"] for spec in indicator_specs
                ],
                "best_trade_count": len(best_trade_output_rows),
                "best_trades_csv": best_trades_csv.as_posix(),
                "indicator_details_csv": indicator_details_csv.as_posix(),
                "indicator_summary_csv": indicator_summary_csv.as_posix(),
                "profile_summary_csv": profile_summary_csv.as_posix(),
                "report_md": report_md.as_posix(),
                "elapsed_seconds": round(time.perf_counter() - started, 4),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "output_dir": resolved_output_dir.as_posix(),
        "best_trades_csv": best_trades_csv.as_posix(),
        "indicator_details_csv": indicator_details_csv.as_posix(),
        "indicator_summary_csv": indicator_summary_csv.as_posix(),
        "profile_summary_csv": profile_summary_csv.as_posix(),
        "report_md": report_md.as_posix(),
        "manifest_path": manifest_path.as_posix(),
        "best_trade_count": len(best_trade_output_rows),
        "candidate_indicator_count": len(indicator_specs),
        "elapsed_seconds": round(time.perf_counter() - started, 4),
    }


def analyze_best_trade_ladders(
    *,
    run_root: str | Path = DEFAULT_SCAN_PERIOD_RUN_ROOT,
    output_dir: str | Path | None = None,
    scenario_name: str = "perfect_hindsight",
    min_market_cap_usd: float | None = DEFAULT_TRADE_LADDER_MIN_MARKET_CAP_USD,
    min_start_close: float | None = DEFAULT_TRADE_LADDER_MIN_START_CLOSE,
    min_average_volume_10d: float | None = DEFAULT_TRADE_LADDER_MIN_AVERAGE_VOLUME_10D,
    report_top_n: int = DEFAULT_TRADE_LADDER_REPORT_TOP_N,
    min_pullback_pct_before_entry: float = DEFAULT_TRADE_LADDER_MIN_PULLBACK_PCT,
    transaction_cost_pct_per_side: float = DEFAULT_TRADE_LADDER_TRANSACTION_COST_PCT_PER_SIDE,
    min_trade_return_pct: float = DEFAULT_TRADE_LADDER_MIN_TRADE_RETURN_PCT,
) -> dict[str, Any]:
    """Rank tickers by perfect-hindsight multi-trade long ladders.

    This uses the stored daily close progression for the scan period and extracts
    every positive close-to-close upswing per ticker: local trough to the next
    local peak, then re-enter after the next pullback. The result is a hindsight
    upper bound for long-only swing capture over the period.
    """
    started = time.perf_counter()
    resolved_run_root = Path(run_root)
    resolved_output_dir = (
        Path(output_dir)
        if output_dir is not None
        else resolved_run_root / "upside_edge_research" / "mar_jun_best_trade_ladders"
    )
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    period_input_path = (
        resolved_run_root / "period_total" / "period_analysis_input.duckdb"
    )
    progression_path = (
        resolved_run_root / "progression" / "period_symbol_progression.parquet"
    )
    for required_path in (period_input_path, progression_path):
        if not required_path.exists():
            raise FileNotFoundError(
                f"Missing required input: {required_path.as_posix()}"
            )

    duckdb = _import_duckdb()
    conn = duckdb.connect()
    try:
        _attach_readonly(conn, "pi", period_input_path)
        available_columns = {
            row[0]
            for row in conn.execute("DESCRIBE pi.period_start_features").fetchall()
        }
        filters = [
            "TRY_CAST(p.close_price AS DOUBLE) IS NOT NULL",
            "NULLIF(TRIM(CAST(p.source_day_label AS VARCHAR)), '') IS NOT NULL",
        ]
        if min_start_close is not None:
            filters.append(
                f"TRY_CAST(p.start_close_price AS DOUBLE) >= {float(min_start_close)}"
            )
        if min_market_cap_usd is not None and "market_cap_basic" in available_columns:
            filters.append(
                "TRY_CAST(psf.market_cap_basic AS DOUBLE) "
                f">= {float(min_market_cap_usd)}"
            )
        if (
            min_average_volume_10d is not None
            and "average_volume_10d_calc" in available_columns
        ):
            filters.append(
                "TRY_CAST(psf.average_volume_10d_calc AS DOUBLE) "
                f">= {float(min_average_volume_10d)}"
            )
        filter_sql = " AND\n              ".join(filters)

        conn.execute(
            f"""
            CREATE TEMP TABLE ladder_progression_base AS
            SELECT p.symbol,
                COALESCE(
                    NULLIF(split_part(CAST(p.symbol AS VARCHAR), ':', 2), ''),
                    CAST(p.symbol AS VARCHAR)
                ) AS bare_ticker,
                CAST(p.source_day_label AS VARCHAR) AS source_day_label,
                CAST(p.run_created_at_utc AS TIMESTAMP) AS run_created_at_utc,
                TRY_CAST(p.close_price AS DOUBLE) AS close_price,
                TRY_CAST(p.start_close_price AS DOUBLE) AS start_close_price,
                TRY_CAST(p.end_close_price AS DOUBLE) AS end_close_price,
                {_optional_numeric_select(available_columns=available_columns, column_name='market_cap_basic', alias='market_cap_basic')},
                {_optional_numeric_select(available_columns=available_columns, column_name='average_volume_10d_calc', alias='average_volume_10d_calc')},
                ROW_NUMBER() OVER (
                    PARTITION BY p.symbol, CAST(p.source_day_label AS VARCHAR)
                    ORDER BY p.run_created_at_utc DESC NULLS LAST,
                        TRY_CAST(p.close_price AS DOUBLE) DESC NULLS LAST
                ) AS row_num
            FROM read_parquet(?) AS p
            LEFT JOIN pi.period_start_features AS psf
                ON psf.symbol = p.symbol
            WHERE {filter_sql}
            """,
            [progression_path.as_posix()],
        )
        conn.execute("""
            CREATE TEMP TABLE ladder_progression AS
            SELECT * EXCLUDE (row_num)
            FROM ladder_progression_base
            WHERE row_num = 1
            """)
        progression_rows = _fetch_dict_rows(
            conn,
            """
            SELECT *
            FROM ladder_progression
            ORDER BY symbol, run_created_at_utc, source_day_label
            """,
        )
    finally:
        conn.close()

    progression_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in progression_rows:
        progression_by_symbol.setdefault(str(row["symbol"]), []).append(row)

    summary_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    for symbol, symbol_rows in progression_by_symbol.items():
        ordered_points: list[dict[str, Any]] = []
        for row in symbol_rows:
            day_label = str(row.get("source_day_label") or "").strip()
            close_price = _as_float(row.get("close_price"))
            if not day_label or close_price is None:
                continue
            ordered_points.append(
                {
                    "source_day_label": day_label,
                    "point_date": _parse_day_label(day_label),
                    "close_price": close_price,
                    "row": row,
                }
            )
        if not ordered_points:
            continue

        ordered_points.sort(
            key=lambda point: (point["point_date"], point["source_day_label"])
        )
        first_point = ordered_points[0]
        last_point = ordered_points[-1]
        start_close = float(first_point["close_price"])
        end_close = float(last_point["close_price"])
        buy_hold_multiple = end_close / start_close if start_close > 0 else 1.0
        buy_hold_return_pct = (buy_hold_multiple - 1.0) * 100.0

        trades = _extract_optimal_long_trade_ladder(
            symbol_rows,
            min_pullback_pct_before_entry=min_pullback_pct_before_entry,
            transaction_cost_pct_per_side=transaction_cost_pct_per_side,
            min_trade_return_pct=min_trade_return_pct,
        )
        trade_returns = [
            _as_float(trade.get("trade_return_pct")) for trade in trades if trade
        ]
        trade_returns = [value for value in trade_returns if value is not None]
        holding_days = [
            _as_int(trade.get("holding_calendar_days")) for trade in trades if trade
        ]
        holding_days = [value for value in holding_days if value is not None]
        cumulative_multiple = (
            float(trades[-1]["cumulative_multiple_after_trade"]) if trades else 1.0
        )
        cumulative_return_pct = (cumulative_multiple - 1.0) * 100.0
        first_trade = trades[0] if trades else None
        last_trade = trades[-1] if trades else None

        summary_rows.append(
            {
                "scenario_name": scenario_name,
                "symbol": symbol,
                "bare_ticker": str(first_point["row"].get("bare_ticker") or ""),
                "trade_count": len(trades),
                "cumulative_multiple": round(cumulative_multiple, 6),
                "cumulative_return_pct": round(cumulative_return_pct, 4),
                "buy_hold_multiple": round(buy_hold_multiple, 6),
                "buy_hold_return_pct": round(buy_hold_return_pct, 4),
                "excess_vs_buy_hold_pct": round(
                    cumulative_return_pct - buy_hold_return_pct, 4
                ),
                "avg_trade_return_pct": _round_optional(_avg_float(trade_returns), 4),
                "median_trade_return_pct": _round_optional(
                    _median_float(trade_returns), 4
                ),
                "max_trade_return_pct": (
                    _round_optional(max(trade_returns), 4) if trade_returns else None
                ),
                "min_trade_return_pct": (
                    _round_optional(min(trade_returns), 4) if trade_returns else None
                ),
                "total_holding_calendar_days": sum(holding_days) if holding_days else 0,
                "avg_holding_calendar_days": _round_optional(
                    _avg_float(holding_days), 2
                ),
                "period_point_count": len(ordered_points),
                "period_calendar_days": (
                    last_point["point_date"] - first_point["point_date"]
                ).days,
                "start_day_label": first_point["source_day_label"],
                "end_day_label": last_point["source_day_label"],
                "start_close_price": round(start_close, 4),
                "end_close_price": round(end_close, 4),
                "market_cap_basic": _round_optional(
                    _as_float(first_point["row"].get("market_cap_basic")), 2
                ),
                "average_volume_10d_calc": _round_optional(
                    _as_float(first_point["row"].get("average_volume_10d_calc")), 2
                ),
                "first_trade_entry_day_label": (
                    first_trade.get("entry_day_label") if first_trade else None
                ),
                "last_trade_exit_day_label": (
                    last_trade.get("exit_day_label") if last_trade else None
                ),
                "ladder_path": _format_trade_ladder_path(trades),
            }
        )

        for trade in trades:
            trade_rows.append(
                {
                    "scenario_name": scenario_name,
                    "symbol": symbol,
                    "bare_ticker": str(first_point["row"].get("bare_ticker") or ""),
                    "buy_hold_return_pct": round(buy_hold_return_pct, 4),
                    **trade,
                }
            )

    summary_rows.sort(
        key=lambda row: (
            float(row["cumulative_multiple"]),
            float(row["buy_hold_multiple"]),
            int(row["trade_count"]),
            row["symbol"],
        ),
        reverse=True,
    )
    for index, row in enumerate(summary_rows, start=1):
        row["ladder_rank"] = index

    trade_rows_by_symbol: dict[str, int] = {
        str(row["symbol"]): int(row["ladder_rank"]) for row in summary_rows
    }
    for trade_row in trade_rows:
        trade_row["ladder_rank"] = trade_rows_by_symbol[str(trade_row["symbol"])]
    trade_rows.sort(key=lambda row: (int(row["ladder_rank"]), int(row["trade_number"])))

    ranking_csv = resolved_output_dir / "ticker_trade_ladder_ranking.csv"
    trades_csv = resolved_output_dir / "ticker_trade_ladder_trades.csv"
    report_md = resolved_output_dir / "ticker_trade_ladder_report.md"
    manifest_path = resolved_output_dir / "ticker_trade_ladder_manifest.json"
    _write_dict_csv(ranking_csv, summary_rows)
    _write_dict_csv(trades_csv, trade_rows)
    _write_trade_ladder_report(
        report_md,
        run_root=resolved_run_root,
        output_dir=resolved_output_dir,
        scenario_name=scenario_name,
        eligible_symbol_count=len(summary_rows),
        summary_rows=summary_rows,
        report_top_n=max(1, report_top_n),
        min_market_cap_usd=min_market_cap_usd,
        min_start_close=min_start_close,
        min_average_volume_10d=min_average_volume_10d,
        min_pullback_pct_before_entry=min_pullback_pct_before_entry,
        transaction_cost_pct_per_side=transaction_cost_pct_per_side,
        min_trade_return_pct=min_trade_return_pct,
    )
    manifest_path.write_text(
        json.dumps(
            {
                "run_root": resolved_run_root.as_posix(),
                "period_input_path": period_input_path.as_posix(),
                "progression_path": progression_path.as_posix(),
                "output_dir": resolved_output_dir.as_posix(),
                "scenario_name": scenario_name,
                "min_market_cap_usd": min_market_cap_usd,
                "min_start_close": min_start_close,
                "min_average_volume_10d": min_average_volume_10d,
                "report_top_n": report_top_n,
                "min_pullback_pct_before_entry": min_pullback_pct_before_entry,
                "transaction_cost_pct_per_side": transaction_cost_pct_per_side,
                "min_trade_return_pct": min_trade_return_pct,
                "eligible_symbol_count": len(summary_rows),
                "positive_trade_symbol_count": sum(
                    1 for row in summary_rows if int(row["trade_count"]) > 0
                ),
                "multi_trade_symbol_count": sum(
                    1 for row in summary_rows if int(row["trade_count"]) > 1
                ),
                "ranking_csv": ranking_csv.as_posix(),
                "trades_csv": trades_csv.as_posix(),
                "report_md": report_md.as_posix(),
                "elapsed_seconds": round(time.perf_counter() - started, 4),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "output_dir": resolved_output_dir.as_posix(),
        "ranking_csv": ranking_csv.as_posix(),
        "trades_csv": trades_csv.as_posix(),
        "report_md": report_md.as_posix(),
        "manifest_path": manifest_path.as_posix(),
        "scenario_name": scenario_name,
        "min_pullback_pct_before_entry": min_pullback_pct_before_entry,
        "transaction_cost_pct_per_side": transaction_cost_pct_per_side,
        "min_trade_return_pct": min_trade_return_pct,
        "eligible_symbol_count": len(summary_rows),
        "positive_trade_symbol_count": sum(
            1 for row in summary_rows if int(row["trade_count"]) > 0
        ),
        "multi_trade_symbol_count": sum(
            1 for row in summary_rows if int(row["trade_count"]) > 1
        ),
        "top_symbol": summary_rows[0]["symbol"] if summary_rows else None,
        "top_cumulative_return_pct": (
            summary_rows[0]["cumulative_return_pct"] if summary_rows else None
        ),
        "median_cumulative_return_pct": _round_optional(
            _median_float(
                [
                    float(row["cumulative_return_pct"])
                    for row in summary_rows
                    if row.get("cumulative_return_pct") is not None
                ]
            ),
            4,
        ),
        "elapsed_seconds": round(time.perf_counter() - started, 4),
    }


def analyze_best_trade_ladder_realism_suite(
    *,
    run_root: str | Path = DEFAULT_SCAN_PERIOD_RUN_ROOT,
    output_dir: str | Path | None = None,
    min_market_cap_usd: float | None = DEFAULT_TRADE_LADDER_MIN_MARKET_CAP_USD,
    min_start_close: float | None = DEFAULT_TRADE_LADDER_MIN_START_CLOSE,
    min_average_volume_10d: float | None = DEFAULT_TRADE_LADDER_MIN_AVERAGE_VOLUME_10D,
    report_top_n: int = DEFAULT_TRADE_LADDER_REPORT_TOP_N,
    scenario_configs: Sequence[dict[str, Any]] = DEFAULT_TRADE_LADDER_REALISM_SCENARIOS,
) -> dict[str, Any]:
    started = time.perf_counter()
    resolved_run_root = Path(run_root)
    resolved_output_dir = (
        Path(output_dir)
        if output_dir is not None
        else _default_trade_ladder_realism_suite_output_dir(resolved_run_root)
    )
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    scenario_rows: list[dict[str, Any]] = []
    scenario_outputs: dict[str, dict[str, Any]] = {}
    for scenario in scenario_configs:
        scenario_name = str(scenario.get("scenario_name") or "").strip()
        if not scenario_name:
            continue
        scenario_result = analyze_best_trade_ladders(
            run_root=resolved_run_root,
            output_dir=resolved_output_dir / scenario_name,
            scenario_name=scenario_name,
            min_market_cap_usd=min_market_cap_usd,
            min_start_close=min_start_close,
            min_average_volume_10d=min_average_volume_10d,
            report_top_n=report_top_n,
            min_pullback_pct_before_entry=float(
                scenario.get("min_pullback_pct_before_entry") or 0.0
            ),
            transaction_cost_pct_per_side=float(
                scenario.get("transaction_cost_pct_per_side") or 0.0
            ),
            min_trade_return_pct=float(scenario.get("min_trade_return_pct") or 0.0),
        )
        scenario_outputs[scenario_name] = scenario_result
        scenario_rows.append(
            {
                "scenario_name": scenario_name,
                "min_pullback_pct_before_entry": scenario_result[
                    "min_pullback_pct_before_entry"
                ],
                "transaction_cost_pct_per_side": scenario_result[
                    "transaction_cost_pct_per_side"
                ],
                "min_trade_return_pct": scenario_result["min_trade_return_pct"],
                "eligible_symbol_count": scenario_result["eligible_symbol_count"],
                "positive_trade_symbol_count": scenario_result[
                    "positive_trade_symbol_count"
                ],
                "multi_trade_symbol_count": scenario_result["multi_trade_symbol_count"],
                "top_symbol": scenario_result.get("top_symbol"),
                "top_cumulative_return_pct": scenario_result.get(
                    "top_cumulative_return_pct"
                ),
                "median_cumulative_return_pct": scenario_result.get(
                    "median_cumulative_return_pct"
                ),
                "ranking_csv": scenario_result["ranking_csv"],
                "trades_csv": scenario_result["trades_csv"],
                "report_md": scenario_result["report_md"],
            }
        )

    summary_csv = resolved_output_dir / "trade_ladder_realism_suite_summary.csv"
    report_md = resolved_output_dir / "trade_ladder_realism_suite_report.md"
    manifest_path = resolved_output_dir / "trade_ladder_realism_suite_manifest.json"
    _write_dict_csv(summary_csv, scenario_rows)
    _write_trade_ladder_realism_suite_report(
        report_md,
        run_root=resolved_run_root,
        output_dir=resolved_output_dir,
        scenario_rows=scenario_rows,
    )
    manifest_path.write_text(
        json.dumps(
            {
                "run_root": resolved_run_root.as_posix(),
                "output_dir": resolved_output_dir.as_posix(),
                "scenario_count": len(scenario_rows),
                "scenario_names": [row["scenario_name"] for row in scenario_rows],
                "summary_csv": summary_csv.as_posix(),
                "report_md": report_md.as_posix(),
                "scenario_outputs": scenario_outputs,
                "elapsed_seconds": round(time.perf_counter() - started, 4),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "output_dir": resolved_output_dir.as_posix(),
        "summary_csv": summary_csv.as_posix(),
        "report_md": report_md.as_posix(),
        "manifest_path": manifest_path.as_posix(),
        "scenario_count": len(scenario_rows),
        "elapsed_seconds": round(time.perf_counter() - started, 4),
    }


def analyze_trade_ladder_entry_profile_capture(
    *,
    run_root: str | Path = DEFAULT_SCAN_PERIOD_RUN_ROOT,
    trades_csv_path: str | Path | None = None,
    backwards_database_path: str | Path = DEFAULT_EXISTING_BACKWARDS_DATABASE_PATH,
    output_dir: str | Path | None = None,
    profile_names: Sequence[str] = DEFAULT_EXISTING_PROFILE_NAMES,
    max_ladder_rank: int | None = DEFAULT_TRADE_LADDER_ENTRY_MAX_RANK,
    scenario_name: str = DEFAULT_TRADE_LADDER_ENTRY_SCENARIO_NAME,
    horizon_name: str = "weeks",
) -> dict[str, Any]:
    started = time.perf_counter()
    resolved_run_root = Path(run_root)
    resolved_backwards_db = Path(backwards_database_path)
    resolved_output_dir = (
        Path(output_dir)
        if output_dir is not None
        else resolved_run_root
        / "upside_edge_research"
        / "mar_jun_trade_ladder_entry_profile_capture"
    )
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    resolved_trades_csv = (
        Path(trades_csv_path)
        if trades_csv_path is not None
        else _default_trade_ladder_entry_trades_csv(
            resolved_run_root,
            scenario_name=scenario_name,
        )
    )
    if not resolved_trades_csv.exists():
        scenario = _resolve_trade_ladder_scenario_config(scenario_name)
        analyze_best_trade_ladders(
            run_root=resolved_run_root,
            output_dir=resolved_trades_csv.parent,
            scenario_name=scenario_name,
            min_pullback_pct_before_entry=float(
                scenario.get("min_pullback_pct_before_entry") or 0.0
            ),
            transaction_cost_pct_per_side=float(
                scenario.get("transaction_cost_pct_per_side") or 0.0
            ),
            min_trade_return_pct=float(scenario.get("min_trade_return_pct") or 0.0),
        )
    if not resolved_trades_csv.exists():
        raise FileNotFoundError(
            f"Missing required trade ladder CSV: {resolved_trades_csv.as_posix()}"
        )
    if not resolved_backwards_db.exists():
        raise FileNotFoundError(
            f"Missing backwards database: {resolved_backwards_db.as_posix()}"
        )

    trade_rows = _load_trade_ladder_rows(
        resolved_trades_csv,
        max_ladder_rank=max_ladder_rank,
    )
    if not trade_rows:
        raise ValueError("No trade ladder rows available for profile entry capture.")

    duckdb = _import_duckdb()
    conn = duckdb.connect()
    try:
        _attach_readonly(conn, "bw", resolved_backwards_db)
        conn.execute("""
            CREATE TEMP TABLE trade_entries (
                trade_id VARCHAR,
                ladder_rank INTEGER,
                trade_number INTEGER,
                symbol VARCHAR,
                bare_ticker VARCHAR,
                entry_day_label VARCHAR,
                entry_date VARCHAR,
                exit_day_label VARCHAR,
                trade_return_pct DOUBLE,
                trade_multiple DOUBLE,
                holding_calendar_days INTEGER
            )
            """)
        conn.executemany(
            "INSERT INTO trade_entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    str(row["trade_id"]),
                    _as_int(row.get("ladder_rank")),
                    _as_int(row.get("trade_number")),
                    str(row.get("symbol") or ""),
                    str(row.get("bare_ticker") or ""),
                    str(row.get("entry_day_label") or ""),
                    _parse_day_label(str(row.get("entry_day_label") or ""))
                    .date()
                    .isoformat(),
                    str(row.get("exit_day_label") or ""),
                    _as_float(row.get("trade_return_pct")),
                    _as_float(row.get("trade_multiple")),
                    _as_int(row.get("holding_calendar_days")),
                )
                for row in trade_rows
            ],
        )
        profile_values = ", ".join(
            f"({_quote_sql_literal(profile_name)})" for profile_name in profile_names
        )
        conn.execute(f"""
            CREATE TEMP TABLE included_profiles AS
            SELECT * FROM (VALUES {profile_values}) AS t(profile_name)
            """)
        conn.execute(f"""
            CREATE TEMP TABLE profile_entry_candidates AS
            SELECT t.trade_id,
                t.ladder_rank,
                t.trade_number,
                t.symbol,
                t.bare_ticker,
                t.entry_day_label,
                t.entry_date,
                t.exit_day_label,
                t.trade_return_pct,
                t.trade_multiple,
                t.holding_calendar_days,
                p.profile_name,
                s.anchor_name,
                TRY_CAST(s.profile_rank AS INTEGER) AS profile_rank,
                TRY_CAST(s.score AS DOUBLE) AS score,
                COALESCE(a.anchor_created_at_utc, runs.current_created_at_utc) AS point_time
            FROM trade_entries AS t
            CROSS JOIN included_profiles AS p
            LEFT JOIN bw.backwards_anchor_snapshots AS s
                ON LOWER(TRIM(s.profile_name)) = LOWER(TRIM(p.profile_name))
                AND s.horizon_name = {_quote_sql_literal(horizon_name)}
                AND NOT s.is_current
                AND (
                    s.symbol = t.symbol
                    OR COALESCE(
                        NULLIF(split_part(CAST(s.symbol AS VARCHAR), ':', 2), ''),
                        CAST(s.symbol AS VARCHAR)
                    ) = t.bare_ticker
                )
            LEFT JOIN bw.backwards_analysis_runs AS runs
                ON s.backwards_analysis_id = runs.backwards_analysis_id
            LEFT JOIN bw.backwards_analysis_anchors AS a
                ON s.backwards_analysis_id = a.backwards_analysis_id
                AND s.anchor_name = a.anchor_name
            WHERE COALESCE(a.anchor_created_at_utc, runs.current_created_at_utc) IS NOT NULL
              AND CAST(COALESCE(a.anchor_created_at_utc, runs.current_created_at_utc) AS DATE)
                    <= CAST(t.entry_date AS DATE)
            """)
        conn.execute("""
            CREATE TEMP TABLE profile_entry_latest AS
            SELECT * EXCLUDE (row_num)
            FROM (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY trade_id, profile_name
                        ORDER BY point_time DESC NULLS LAST,
                            profile_rank ASC NULLS LAST
                    ) AS row_num
                FROM profile_entry_candidates
            )
            WHERE row_num = 1
            """)
        conn.execute("""
            CREATE TEMP TABLE profile_entry_best AS
            SELECT * EXCLUDE (row_num)
            FROM (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY trade_id, profile_name
                        ORDER BY profile_rank ASC NULLS LAST,
                            point_time DESC NULLS LAST
                    ) AS row_num
                FROM profile_entry_candidates
            )
            WHERE row_num = 1
            """)
        detail_rows = _fetch_dict_rows(
            conn,
            """
            SELECT t.trade_id,
                t.ladder_rank,
                t.trade_number,
                t.symbol,
                t.bare_ticker,
                t.entry_day_label,
                t.exit_day_label,
                t.trade_return_pct,
                t.trade_multiple,
                t.holding_calendar_days,
                p.profile_name,
                l.anchor_name AS latest_anchor_name,
                CAST(l.point_time AS VARCHAR) AS latest_anchor_time,
                l.profile_rank AS latest_rank,
                l.score AS latest_score,
                b.anchor_name AS best_anchor_name,
                CAST(b.point_time AS VARCHAR) AS best_anchor_time,
                b.profile_rank AS best_rank,
                b.score AS best_score
            FROM trade_entries AS t
            CROSS JOIN included_profiles AS p
            LEFT JOIN profile_entry_latest AS l
                ON l.trade_id = t.trade_id
                AND l.profile_name = p.profile_name
            LEFT JOIN profile_entry_best AS b
                ON b.trade_id = t.trade_id
                AND b.profile_name = p.profile_name
            ORDER BY t.ladder_rank, t.trade_number, p.profile_name
            """,
        )
    finally:
        conn.close()

    total_trade_count = len(trade_rows)
    profile_summary_map: dict[str, dict[str, Any]] = {
        profile_name: {
            "profile_name": profile_name,
            "trade_count": total_trade_count,
            "latest_ranks": [],
            "best_ranks": [],
            "latest_lead_days": [],
            "best_lead_days": [],
            "latest_top50_count": 0,
            "latest_top100_count": 0,
            "latest_top250_count": 0,
            "best_top50_count": 0,
            "best_top100_count": 0,
            "best_top250_count": 0,
            "latest_top100_returns": [],
            "best_top100_returns": [],
        }
        for profile_name in profile_names
    }

    for row in detail_rows:
        latest_time_text = str(row.get("latest_anchor_time") or "").strip()
        best_time_text = str(row.get("best_anchor_time") or "").strip()
        entry_day_label = str(row.get("entry_day_label") or "").strip()
        entry_date = _parse_day_label(entry_day_label).date()
        latest_rank = _as_int(row.get("latest_rank"))
        best_rank = _as_int(row.get("best_rank"))
        latest_lead_days: int | None = None
        best_lead_days: int | None = None
        if latest_time_text:
            latest_date = datetime.fromisoformat(latest_time_text).date()
            latest_lead_days = (entry_date - latest_date).days
        if best_time_text:
            best_date = datetime.fromisoformat(best_time_text).date()
            best_lead_days = (entry_date - best_date).days
        row["latest_lead_days"] = latest_lead_days
        row["best_lead_days"] = best_lead_days
        row["latest_top50"] = latest_rank is not None and latest_rank <= 50
        row["latest_top100"] = latest_rank is not None and latest_rank <= 100
        row["latest_top250"] = latest_rank is not None and latest_rank <= 250
        row["best_top50"] = best_rank is not None and best_rank <= 50
        row["best_top100"] = best_rank is not None and best_rank <= 100
        row["best_top250"] = best_rank is not None and best_rank <= 250

        summary = profile_summary_map[str(row["profile_name"])]
        trade_return = _as_float(row.get("trade_return_pct"))
        if latest_rank is not None:
            summary["latest_ranks"].append(float(latest_rank))
            if latest_lead_days is not None:
                summary["latest_lead_days"].append(float(latest_lead_days))
            if latest_rank <= 50:
                summary["latest_top50_count"] += 1
            if latest_rank <= 100:
                summary["latest_top100_count"] += 1
                if trade_return is not None:
                    summary["latest_top100_returns"].append(trade_return)
            if latest_rank <= 250:
                summary["latest_top250_count"] += 1
        if best_rank is not None:
            summary["best_ranks"].append(float(best_rank))
            if best_lead_days is not None:
                summary["best_lead_days"].append(float(best_lead_days))
            if best_rank <= 50:
                summary["best_top50_count"] += 1
            if best_rank <= 100:
                summary["best_top100_count"] += 1
                if trade_return is not None:
                    summary["best_top100_returns"].append(trade_return)
            if best_rank <= 250:
                summary["best_top250_count"] += 1

    summary_rows: list[dict[str, Any]] = []
    for summary in profile_summary_map.values():
        latest_count = len(summary["latest_ranks"])
        best_count = len(summary["best_ranks"])
        summary_rows.append(
            {
                "profile_name": summary["profile_name"],
                "trade_count": total_trade_count,
                "latest_scored_trade_count": latest_count,
                "best_scored_trade_count": best_count,
                "latest_top50_count": summary["latest_top50_count"],
                "latest_top50_rate": round(
                    summary["latest_top50_count"] / total_trade_count, 4
                ),
                "latest_top100_count": summary["latest_top100_count"],
                "latest_top100_rate": round(
                    summary["latest_top100_count"] / total_trade_count, 4
                ),
                "latest_top250_count": summary["latest_top250_count"],
                "latest_top250_rate": round(
                    summary["latest_top250_count"] / total_trade_count, 4
                ),
                "best_top50_count": summary["best_top50_count"],
                "best_top50_rate": round(
                    summary["best_top50_count"] / total_trade_count, 4
                ),
                "best_top100_count": summary["best_top100_count"],
                "best_top100_rate": round(
                    summary["best_top100_count"] / total_trade_count, 4
                ),
                "best_top250_count": summary["best_top250_count"],
                "best_top250_rate": round(
                    summary["best_top250_count"] / total_trade_count, 4
                ),
                "median_latest_rank": _round_optional(
                    _median_float(summary["latest_ranks"]), 2
                ),
                "median_best_rank": _round_optional(
                    _median_float(summary["best_ranks"]), 2
                ),
                "median_latest_lead_days": _round_optional(
                    _median_float(summary["latest_lead_days"]), 2
                ),
                "median_best_lead_days": _round_optional(
                    _median_float(summary["best_lead_days"]), 2
                ),
                "median_trade_return_latest_top100_pct": _round_optional(
                    _median_float(summary["latest_top100_returns"]), 4
                ),
                "median_trade_return_best_top100_pct": _round_optional(
                    _median_float(summary["best_top100_returns"]), 4
                ),
            }
        )
    summary_rows.sort(
        key=lambda row: (
            float(row["latest_top100_rate"]),
            float(row["best_top100_rate"]),
            -(
                float(row["median_latest_rank"])
                if row["median_latest_rank"]
                else 999999.0
            ),
        ),
        reverse=True,
    )

    detail_csv = resolved_output_dir / "trade_ladder_entry_profile_capture.csv"
    summary_csv = resolved_output_dir / "trade_ladder_profile_entry_summary.csv"
    report_md = resolved_output_dir / "trade_ladder_profile_entry_report.md"
    manifest_path = resolved_output_dir / "trade_ladder_profile_entry_manifest.json"
    _write_dict_csv(detail_csv, detail_rows)
    _write_dict_csv(summary_csv, summary_rows)
    _write_trade_ladder_profile_entry_report(
        report_md,
        run_root=resolved_run_root,
        output_dir=resolved_output_dir,
        trades_csv_path=resolved_trades_csv,
        max_ladder_rank=max_ladder_rank,
        trade_count=total_trade_count,
        summary_rows=summary_rows,
    )
    manifest_path.write_text(
        json.dumps(
            {
                "run_root": resolved_run_root.as_posix(),
                "trades_csv_path": resolved_trades_csv.as_posix(),
                "backwards_database_path": resolved_backwards_db.as_posix(),
                "output_dir": resolved_output_dir.as_posix(),
                "profile_names": list(profile_names),
                "max_ladder_rank": max_ladder_rank,
                "scenario_name": scenario_name,
                "horizon_name": horizon_name,
                "trade_count": total_trade_count,
                "detail_csv": detail_csv.as_posix(),
                "summary_csv": summary_csv.as_posix(),
                "report_md": report_md.as_posix(),
                "elapsed_seconds": round(time.perf_counter() - started, 4),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "output_dir": resolved_output_dir.as_posix(),
        "detail_csv": detail_csv.as_posix(),
        "summary_csv": summary_csv.as_posix(),
        "report_md": report_md.as_posix(),
        "manifest_path": manifest_path.as_posix(),
        "trade_count": total_trade_count,
        "elapsed_seconds": round(time.perf_counter() - started, 4),
    }


def analyze_trade_ladder_entry_field_diagnostics(
    *,
    run_root: str | Path = DEFAULT_SCAN_PERIOD_RUN_ROOT,
    candidate_csv_path: str | Path | None = None,
    trades_csv_path: str | Path | None = None,
    all_fields_root: str | Path = DEFAULT_ALL_FIELDS_ROOT,
    output_dir: str | Path | None = None,
    max_ladder_rank: int | None = DEFAULT_TRADE_LADDER_ENTRY_MAX_RANK,
    scenario_name: str = DEFAULT_TRADE_LADDER_ENTRY_SCENARIO_NAME,
    indicator_percentile_threshold: float = DEFAULT_TRADE_LADDER_ENTRY_FIELD_PERCENTILE,
    max_indicator_fields: int = DEFAULT_TRADE_LADDER_ENTRY_FIELD_MAX_INDICATORS,
) -> dict[str, Any]:
    started = time.perf_counter()
    resolved_run_root = Path(run_root)
    resolved_output_dir = (
        Path(output_dir)
        if output_dir is not None
        else resolved_run_root
        / "upside_edge_research"
        / "mar_jun_trade_ladder_entry_field_diagnostics"
    )
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    resolved_candidate_csv = Path(
        candidate_csv_path
        if candidate_csv_path is not None
        else resolved_run_root
        / "upside_edge_research"
        / "mar_jun_default"
        / "upside_field_candidates.csv"
    )
    if not resolved_candidate_csv.exists():
        run_mar_jun_upside_edge_research()
    if not resolved_candidate_csv.exists():
        raise FileNotFoundError(
            f"Missing candidate CSV: {resolved_candidate_csv.as_posix()}"
        )

    resolved_trades_csv = (
        Path(trades_csv_path)
        if trades_csv_path is not None
        else _default_trade_ladder_entry_trades_csv(
            resolved_run_root,
            scenario_name=scenario_name,
        )
    )
    if not resolved_trades_csv.exists():
        scenario = _resolve_trade_ladder_scenario_config(scenario_name)
        analyze_best_trade_ladders(
            run_root=resolved_run_root,
            output_dir=resolved_trades_csv.parent,
            scenario_name=scenario_name,
            min_pullback_pct_before_entry=float(
                scenario.get("min_pullback_pct_before_entry") or 0.0
            ),
            transaction_cost_pct_per_side=float(
                scenario.get("transaction_cost_pct_per_side") or 0.0
            ),
            min_trade_return_pct=float(scenario.get("min_trade_return_pct") or 0.0),
        )
    if not resolved_trades_csv.exists():
        raise FileNotFoundError(
            f"Missing required trade ladder CSV: {resolved_trades_csv.as_posix()}"
        )

    trade_rows = _load_trade_ladder_rows(
        resolved_trades_csv,
        max_ladder_rank=max_ladder_rank,
    )
    if not trade_rows:
        raise ValueError(
            "No trade ladder rows available for entry-day field diagnostics."
        )
    candidate_rows = _load_candidate_csv_rows(resolved_candidate_csv)

    parsed_entry_dates = [
        _parse_day_label(str(row["entry_day_label"])) for row in trade_rows
    ]
    start_day_label = min(parsed_entry_dates).strftime("%d_%m_%Y")
    end_day_label = max(parsed_entry_dates).strftime("%d_%m_%Y")
    daily_db_map = _resolve_daily_all_fields_database_map(
        all_fields_root=all_fields_root,
        start_day_label=start_day_label,
        end_day_label=end_day_label,
    )

    trade_rows_by_day: dict[str, list[dict[str, Any]]] = {}
    for row in trade_rows:
        trade_rows_by_day.setdefault(str(row["entry_day_label"]), []).append(row)

    detail_rows: list[dict[str, Any]] = []
    for day_label, day_trade_rows in sorted(trade_rows_by_day.items()):
        daily_db_path = daily_db_map.get(day_label)
        if daily_db_path is None:
            continue
        conn = _import_duckdb().connect(daily_db_path.as_posix(), read_only=True)
        try:
            available_columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info('all_fields_rows')"
                ).fetchall()
            }
            indicator_specs = _candidate_indicator_specs(
                candidate_rows=candidate_rows,
                available_columns=available_columns,
                max_indicator_fields=max(1, max_indicator_fields),
            )
            if not indicator_specs:
                continue
            value_select_sql = ",\n                    ".join(
                f"TRY_CAST(r.{_quote_identifier(spec['predictor_field'])} AS DOUBLE) AS {_quote_identifier(spec['value_alias'])}"
                for spec in indicator_specs
            )
            percentile_select_sql = ",\n                    ".join(
                "CASE WHEN "
                f"{_quote_identifier(spec['value_alias'])} IS NULL THEN NULL ELSE "
                f"PERCENT_RANK() OVER (ORDER BY {_quote_identifier(spec['value_alias'])} ASC NULLS FIRST) END "
                f"AS {_quote_identifier(spec['percentile_alias'])}"
                for spec in indicator_specs
            )
            conn.execute(f"""
                CREATE TEMP TABLE entry_day_universe_base AS
                SELECT r.symbol,
                    COALESCE(
                        NULLIF(split_part(CAST(r.symbol AS VARCHAR), ':', 2), ''),
                        CAST(r.symbol AS VARCHAR)
                    ) AS bare_ticker,
                    {value_select_sql},
                    ROW_NUMBER() OVER (
                        PARTITION BY r.symbol
                        ORDER BY m.created_at_utc DESC NULLS LAST, r.run_id DESC
                    ) AS row_num
                FROM all_fields_rows AS r
                LEFT JOIN run_metadata AS m USING (run_id)
                """)
            conn.execute(f"""
                CREATE TEMP TABLE entry_day_universe AS
                SELECT * EXCLUDE (row_num),
                    {percentile_select_sql}
                FROM entry_day_universe_base
                WHERE row_num = 1
                """)
            day_rows = _fetch_dict_rows(conn, "SELECT * FROM entry_day_universe")
        finally:
            conn.close()

        exact_symbol_map = {str(row["symbol"]): row for row in day_rows}
        bare_symbol_map = {str(row["bare_ticker"]): row for row in day_rows}
        for trade_row in day_trade_rows:
            matched_row = exact_symbol_map.get(
                str(trade_row["symbol"])
            ) or bare_symbol_map.get(str(trade_row["bare_ticker"]))
            if matched_row is None:
                continue
            for spec in indicator_specs:
                percentile = _as_float(matched_row.get(spec["percentile_alias"]))
                detail_rows.append(
                    {
                        "trade_id": trade_row["trade_id"],
                        "ladder_rank": trade_row["ladder_rank"],
                        "trade_number": trade_row["trade_number"],
                        "symbol": trade_row["symbol"],
                        "bare_ticker": trade_row["bare_ticker"],
                        "entry_day_label": trade_row["entry_day_label"],
                        "exit_day_label": trade_row["exit_day_label"],
                        "trade_return_pct": trade_row["trade_return_pct"],
                        "holding_calendar_days": trade_row["holding_calendar_days"],
                        "predictor_field": spec["predictor_field"],
                        "window_label": spec["window_label"],
                        "recommendation": spec["recommendation"],
                        "raw_scan_join_field": spec["raw_scan_join_field"],
                        "indicator_value": _round_optional(
                            _as_float(matched_row.get(spec["value_alias"])), 6
                        ),
                        "indicator_percentile": _round_optional(percentile, 4),
                        "bullish_entry_hit": (
                            percentile is not None
                            and percentile >= indicator_percentile_threshold
                        ),
                    }
                )

    total_trade_count = len(trade_rows)
    summary_map: dict[tuple[str, str], dict[str, Any]] = {}
    for row in detail_rows:
        key = (str(row["predictor_field"]), str(row["window_label"]))
        summary = summary_map.setdefault(
            key,
            {
                "predictor_field": row["predictor_field"],
                "window_label": row["window_label"],
                "recommendation": row["recommendation"],
                "raw_scan_join_field": row["raw_scan_join_field"],
                "percentiles": [],
                "hit_returns": [],
                "non_hit_returns": [],
                "observation_count": 0,
                "hit_count": 0,
            },
        )
        percentile = _as_float(row.get("indicator_percentile"))
        trade_return = _as_float(row.get("trade_return_pct"))
        if percentile is None:
            continue
        summary["observation_count"] += 1
        summary["percentiles"].append(percentile)
        if bool(row.get("bullish_entry_hit")):
            summary["hit_count"] += 1
            if trade_return is not None:
                summary["hit_returns"].append(trade_return)
        else:
            if trade_return is not None:
                summary["non_hit_returns"].append(trade_return)

    summary_rows: list[dict[str, Any]] = []
    for summary in summary_map.values():
        observation_count = int(summary["observation_count"])
        hit_count = int(summary["hit_count"])
        summary_rows.append(
            {
                "predictor_field": summary["predictor_field"],
                "window_label": summary["window_label"],
                "recommendation": summary["recommendation"],
                "raw_scan_join_field": summary["raw_scan_join_field"],
                "entry_trade_count": total_trade_count,
                "observation_count": observation_count,
                "observation_coverage_rate": round(
                    observation_count / total_trade_count, 4
                ),
                "bullish_entry_hit_count": hit_count,
                "bullish_entry_hit_rate": (
                    round(hit_count / observation_count, 4)
                    if observation_count
                    else 0.0
                ),
                "median_entry_percentile": _round_optional(
                    _median_float(summary["percentiles"]), 4
                ),
                "median_trade_return_hit_pct": _round_optional(
                    _median_float(summary["hit_returns"]), 4
                ),
                "median_trade_return_non_hit_pct": _round_optional(
                    _median_float(summary["non_hit_returns"]), 4
                ),
            }
        )
    summary_rows.sort(
        key=lambda row: (
            float(row["bullish_entry_hit_rate"]),
            float(row["median_trade_return_hit_pct"] or 0.0),
            float(row["observation_coverage_rate"]),
        ),
        reverse=True,
    )

    detail_csv = resolved_output_dir / "trade_ladder_entry_field_details.csv"
    summary_csv = resolved_output_dir / "trade_ladder_entry_field_summary.csv"
    report_md = resolved_output_dir / "trade_ladder_entry_field_report.md"
    manifest_path = resolved_output_dir / "trade_ladder_entry_field_manifest.json"
    _write_dict_csv(detail_csv, detail_rows)
    _write_dict_csv(summary_csv, summary_rows)
    _write_trade_ladder_entry_field_report(
        report_md,
        run_root=resolved_run_root,
        output_dir=resolved_output_dir,
        trades_csv_path=resolved_trades_csv,
        max_ladder_rank=max_ladder_rank,
        entry_trade_count=total_trade_count,
        percentile_threshold=indicator_percentile_threshold,
        summary_rows=summary_rows,
    )
    manifest_path.write_text(
        json.dumps(
            {
                "run_root": resolved_run_root.as_posix(),
                "candidate_csv_path": resolved_candidate_csv.as_posix(),
                "trades_csv_path": resolved_trades_csv.as_posix(),
                "all_fields_root": Path(all_fields_root).as_posix(),
                "output_dir": resolved_output_dir.as_posix(),
                "max_ladder_rank": max_ladder_rank,
                "scenario_name": scenario_name,
                "indicator_percentile_threshold": indicator_percentile_threshold,
                "max_indicator_fields": max_indicator_fields,
                "entry_trade_count": total_trade_count,
                "detail_csv": detail_csv.as_posix(),
                "summary_csv": summary_csv.as_posix(),
                "report_md": report_md.as_posix(),
                "elapsed_seconds": round(time.perf_counter() - started, 4),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "output_dir": resolved_output_dir.as_posix(),
        "detail_csv": detail_csv.as_posix(),
        "summary_csv": summary_csv.as_posix(),
        "report_md": report_md.as_posix(),
        "manifest_path": manifest_path.as_posix(),
        "entry_trade_count": total_trade_count,
        "elapsed_seconds": round(time.perf_counter() - started, 4),
    }


def run_mar_jun_upside_edge_research() -> dict[str, Any]:
    """Run the default Mar-Jun upside-edge shortlist from existing scan outputs."""
    latest_move_prediction_database = _find_latest_move_prediction_database()
    return analyze_all_fields_upside_edge_candidates(
        run_root=DEFAULT_SCAN_PERIOD_RUN_ROOT,
        output_dir=DEFAULT_SCAN_PERIOD_RUN_ROOT
        / "upside_edge_research"
        / "mar_jun_default",
        top_n=DEFAULT_MAR_JUN_UPSIDE_TOP_N,
        move_prediction_database_path=latest_move_prediction_database,
    )


def run_mar_jun_best_trade_profile_tracking() -> dict[str, Any]:
    """Run the default hindsight best-trade/profile tracking diagnostic."""
    candidate_csv = (
        DEFAULT_SCAN_PERIOD_RUN_ROOT
        / "upside_edge_research"
        / "mar_jun_default"
        / "upside_field_candidates.csv"
    )
    if not candidate_csv.exists():
        run_mar_jun_upside_edge_research()
    return analyze_best_trade_profile_tracking(
        run_root=DEFAULT_SCAN_PERIOD_RUN_ROOT,
        candidate_csv_path=candidate_csv,
        backwards_database_path=DEFAULT_EXISTING_BACKWARDS_DATABASE_PATH,
        output_dir=DEFAULT_SCAN_PERIOD_RUN_ROOT
        / "upside_edge_research"
        / "mar_jun_best_trade_tracking",
    )


def run_mar_jun_best_trade_ladders() -> dict[str, Any]:
    """Run the default Mar-Jun perfect-hindsight multi-trade ladder ranking."""
    return analyze_best_trade_ladders(
        run_root=DEFAULT_SCAN_PERIOD_RUN_ROOT,
        scenario_name="perfect_hindsight",
        output_dir=DEFAULT_SCAN_PERIOD_RUN_ROOT
        / "upside_edge_research"
        / "mar_jun_best_trade_ladders",
    )


def run_mar_jun_best_trade_ladder_realism_suite() -> dict[str, Any]:
    """Run the default Mar-Jun constrained ladder scenario comparison suite."""
    return analyze_best_trade_ladder_realism_suite(
        run_root=DEFAULT_SCAN_PERIOD_RUN_ROOT,
        output_dir=DEFAULT_SCAN_PERIOD_RUN_ROOT
        / "upside_edge_research"
        / "mar_jun_best_trade_ladder_realism_suite",
    )


def run_mar_jun_trade_ladder_entry_profile_capture() -> dict[str, Any]:
    """Run profile capture diagnostics for realistic ladder entry dates."""
    return analyze_trade_ladder_entry_profile_capture(
        run_root=DEFAULT_SCAN_PERIOD_RUN_ROOT,
        trades_csv_path=_default_trade_ladder_entry_trades_csv(
            DEFAULT_SCAN_PERIOD_RUN_ROOT,
            scenario_name=DEFAULT_TRADE_LADDER_ENTRY_SCENARIO_NAME,
        ),
        backwards_database_path=DEFAULT_EXISTING_BACKWARDS_DATABASE_PATH,
        output_dir=DEFAULT_SCAN_PERIOD_RUN_ROOT
        / "upside_edge_research"
        / "mar_jun_trade_ladder_entry_profile_capture",
        scenario_name=DEFAULT_TRADE_LADDER_ENTRY_SCENARIO_NAME,
    )


def run_mar_jun_trade_ladder_entry_field_diagnostics() -> dict[str, Any]:
    """Run entry-day all-fields diagnostics for realistic ladder trades."""
    return analyze_trade_ladder_entry_field_diagnostics(
        run_root=DEFAULT_SCAN_PERIOD_RUN_ROOT,
        trades_csv_path=_default_trade_ladder_entry_trades_csv(
            DEFAULT_SCAN_PERIOD_RUN_ROOT,
            scenario_name=DEFAULT_TRADE_LADDER_ENTRY_SCENARIO_NAME,
        ),
        candidate_csv_path=DEFAULT_SCAN_PERIOD_RUN_ROOT
        / "upside_edge_research"
        / "mar_jun_default"
        / "upside_field_candidates.csv",
        output_dir=DEFAULT_SCAN_PERIOD_RUN_ROOT
        / "upside_edge_research"
        / "mar_jun_trade_ladder_entry_field_diagnostics",
        scenario_name=DEFAULT_TRADE_LADDER_ENTRY_SCENARIO_NAME,
    )


__all__ = [
    "UpsideFieldCandidate",
    "analyze_all_fields_upside_edge_candidates",
    "analyze_best_trade_ladders",
    "analyze_best_trade_ladder_realism_suite",
    "analyze_best_trade_profile_tracking",
    "analyze_trade_ladder_entry_field_diagnostics",
    "analyze_trade_ladder_entry_profile_capture",
    "run_mar_jun_best_trade_ladders",
    "run_mar_jun_best_trade_ladder_realism_suite",
    "run_mar_jun_best_trade_profile_tracking",
    "run_mar_jun_trade_ladder_entry_field_diagnostics",
    "run_mar_jun_trade_ladder_entry_profile_capture",
    "run_mar_jun_upside_edge_research",
]
