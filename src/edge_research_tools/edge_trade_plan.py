from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any, Callable, Mapping, Sequence


@dataclass(frozen=True)
class TradePlanVariant:
    name: str
    description: str
    max_holding_days: int
    target_pct: float | None
    stop_pct: float | None


DEFAULT_TRADE_PLAN_VARIANTS: tuple[TradePlanVariant, ...] = (
    TradePlanVariant(
        name="baseline_5d",
        description="Unfiltered cohort baseline held for five trading observations.",
        max_holding_days=5,
        target_pct=None,
        stop_pct=None,
    ),
    TradePlanVariant(
        name="momentum_confirmed_5d",
        description=(
            "Positive 5D/1M trend, relative volume confirmation, non-negative "
            "recommendation, and no more than two ATRP units of 5D extension."
        ),
        max_holding_days=5,
        target_pct=10.0,
        stop_pct=7.0,
    ),
    TradePlanVariant(
        name="controlled_pullback_10d",
        description=(
            "Positive 1M trend with 5D performance between -1.0 and +0.75 ATRP "
            "units, moderate participation, and non-negative recommendation."
        ),
        max_holding_days=10,
        target_pct=8.0,
        stop_pct=5.0,
    ),
    TradePlanVariant(
        name="risk_controlled_10d",
        description=(
            "Low-to-moderate ATRP, intact 1M trend, restrained 5D extension, "
            "moderate participation, and non-negative recommendation."
        ),
        max_holding_days=10,
        target_pct=6.0,
        stop_pct=4.0,
    ),
)


def _import_duckdb():
    import duckdb

    return duckdb


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    number = _safe_float(value)
    return int(number) if number is not None else None


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _round_or_blank(value: float | None, digits: int = 4) -> float | str:
    return "" if value is None else round(float(value), digits)


def _read_csv_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _quote_sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _variant_signal(name: str, row: Mapping[str, Any]) -> bool:
    perf_5d = _safe_float(row.get("perf_5d"))
    perf_1m = _safe_float(row.get("perf_1m"))
    atrp = _safe_float(row.get("atrp"))
    relative_volume = _safe_float(row.get("relative_volume_10d_calc"))
    recommendation = _safe_float(row.get("recommend_all"))
    if name == "baseline_5d":
        return True
    if None in (perf_5d, perf_1m, atrp, relative_volume, recommendation):
        return False
    assert perf_5d is not None
    assert perf_1m is not None
    assert atrp is not None
    assert relative_volume is not None
    assert recommendation is not None
    if atrp <= 0:
        return False
    extension_atr = perf_5d / atrp
    if name == "momentum_confirmed_5d":
        return (
            perf_5d > 0
            and perf_1m > 0
            and 1.0 <= relative_volume <= 3.0
            and recommendation >= 0
            and extension_atr <= 2.0
        )
    if name == "controlled_pullback_10d":
        return (
            perf_1m > 0
            and -1.0 <= extension_atr <= 0.75
            and 0.6 <= relative_volume <= 1.8
            and recommendation >= 0
        )
    if name == "risk_controlled_10d":
        return (
            atrp <= 6.0
            and perf_1m >= -2.0
            and -0.5 <= extension_atr <= 1.5
            and 0.7 <= relative_volume <= 2.0
            and recommendation >= 0
        )
    raise ValueError(f"Unsupported trade-plan variant: {name}")


def _current_quality_checks(row: Mapping[str, Any]) -> dict[str, bool]:
    occurrence_count = _safe_int(row.get("hist_occurrence_count_in_setup")) or 0
    historical_bucket = str(row.get("historical_validation_bucket") or "").lower()
    safety_bucket = str(row.get("safety_bucket") or "").lower()
    lane_ci_low = _safe_float(row.get("lane_leader_median_fwd_5d_ci_low"))
    return {
        "check_evidence_depth": occurrence_count >= 5,
        "check_historical_support": historical_bucket in {"supported", "strong"},
        "check_lane_lower_bound_positive": lane_ci_low is not None and lane_ci_low > 0,
        "check_safety_not_speculative": safety_bucket in {"balanced", "safer"},
        "check_valuation_not_negative": (
            (_safe_float(row.get("forward_valuation_upside_pct")) or 0.0) >= 0
        ),
    }


def _timing_checks(row: Mapping[str, Any]) -> dict[str, bool]:
    perf_5d = _safe_float(row.get("perf_5d"))
    perf_1m = _safe_float(row.get("perf_1m"))
    atrp = _safe_float(row.get("atrp"))
    relative_volume = _safe_float(row.get("relative_volume_10d_calc"))
    recommendation = _safe_float(row.get("recommend_all"))
    extension_atr = (
        perf_5d / atrp
        if perf_5d is not None and atrp is not None and atrp > 0
        else None
    )
    return {
        "check_timing_data_complete": None
        not in (perf_5d, perf_1m, atrp, relative_volume, recommendation),
        "check_trend_intact": perf_1m is not None and perf_1m >= -2.0,
        "check_extension_controlled": (
            extension_atr is not None and -1.0 <= extension_atr <= 2.0
        ),
        "check_participation_tradeable": (
            relative_volume is not None and 0.6 <= relative_volume <= 3.0
        ),
        "check_recommendation_non_negative": (
            recommendation is not None and recommendation >= 0
        ),
    }


def _recommendation_state(
    row: Mapping[str, Any],
    *,
    matched_variants: Sequence[str],
    quality_checks: Mapping[str, bool],
    timing_checks: Mapping[str, bool],
) -> str:
    safety_bucket = str(row.get("safety_bucket") or "").lower()
    if safety_bucket == "speculative" and not matched_variants:
        return "REJECT"
    if matched_variants and all(timing_checks.values()):
        if (
            sum(quality_checks.values()) >= 3
            and quality_checks["check_historical_support"]
            and quality_checks["check_safety_not_speculative"]
        ):
            return "ENTER_STARTER"
        return "ARMED"
    if matched_variants:
        return "ARMED"
    if sum(timing_checks.values()) >= 3:
        return "WATCH"
    return "REJECT"


def build_current_trade_plan_rows(
    unified_rows: Sequence[Mapping[str, Any]],
    latest_fields_by_symbol: Mapping[str, Mapping[str, Any]],
    *,
    variants: Sequence[TradePlanVariant] = DEFAULT_TRADE_PLAN_VARIANTS,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    actionable_variants = [item for item in variants if item.name != "baseline_5d"]
    population = max(1, len(unified_rows))
    for source_row in unified_rows:
        row = dict(source_row)
        symbol = str(row.get("symbol") or "").strip()
        latest = dict(latest_fields_by_symbol.get(symbol) or {})
        row.update(latest)
        atrp = _safe_float(row.get("atrp"))
        perf_5d = _safe_float(row.get("perf_5d"))
        extension_atr = (
            perf_5d / atrp
            if perf_5d is not None and atrp is not None and atrp > 0
            else None
        )
        quality_checks = _current_quality_checks(row)
        timing_checks = _timing_checks(row)
        matched_variants = [
            item.name for item in actionable_variants if _variant_signal(item.name, row)
        ]
        quality_score = sum(quality_checks.values()) / len(quality_checks)
        timing_score = sum(timing_checks.values()) / len(timing_checks)
        unified_rank = _safe_int(row.get("unified_edge_highlight_rank")) or population
        rank_score = _clamp(1.0 - ((unified_rank - 1) / population))
        plan_score = (
            (0.45 * rank_score) + (0.30 * quality_score) + (0.25 * timing_score)
        )
        state = _recommendation_state(
            row,
            matched_variants=matched_variants,
            quality_checks=quality_checks,
            timing_checks=timing_checks,
        )
        output.append(
            {
                **row,
                "entry_state": state,
                "matched_entry_variants": "|".join(matched_variants),
                "entry_variant_count": len(matched_variants),
                "trade_plan_score": round(plan_score, 4),
                "trade_plan_quality_score": round(quality_score, 4),
                "trade_plan_timing_score": round(timing_score, 4),
                "extension_atr_units": _round_or_blank(extension_atr),
                **{key: int(value) for key, value in quality_checks.items()},
                **{key: int(value) for key, value in timing_checks.items()},
            }
        )
    state_priority = {"ENTER_STARTER": 4, "ARMED": 3, "WATCH": 2, "REJECT": 1}
    output.sort(
        key=lambda item: (
            state_priority.get(str(item.get("entry_state")), 0),
            _safe_float(item.get("trade_plan_score")) or 0.0,
            -(_safe_int(item.get("unified_edge_highlight_rank")) or population),
        ),
        reverse=True,
    )
    for rank, row in enumerate(output, start=1):
        row["trade_plan_rank"] = rank
    return output


TRADE_PLAN_TOP_COLUMNS: tuple[str, ...] = (
    "trade_plan_rank",
    "entry_state",
    "symbol",
    "company_name",
    "sector",
    "industry",
    "matched_entry_variants",
    "trade_plan_score",
    "trade_plan_quality_score",
    "trade_plan_timing_score",
    "unified_edge_highlight_rank",
    "upside_prediction_rank",
    "big_mover_rank",
    "tradeable_safety_rank",
    "forward_upside_rank",
    "historical_validation_bucket",
    "hist_occurrence_count_in_setup",
    "safety_bucket",
    "safety_companion_score",
    "forward_valuation_upside_pct",
    "latest_close",
    "perf_5d",
    "perf_1m",
    "perf_3m",
    "atrp",
    "extension_atr_units",
    "relative_volume_10d_calc",
    "recommend_all",
    "sma20",
    "sma50",
    "next_earnings_at",
    "check_evidence_depth",
    "check_historical_support",
    "check_lane_lower_bound_positive",
    "check_safety_not_speculative",
    "check_valuation_not_negative",
)


def project_trade_plan_top_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: row.get(key, "") for key in TRADE_PLAN_TOP_COLUMNS}


def load_latest_all_fields_for_symbols(
    database_path: str | Path,
    symbols: Sequence[str],
) -> dict[str, dict[str, Any]]:
    if not symbols:
        return {}
    duckdb = _import_duckdb()
    conn = duckdb.connect(Path(database_path).as_posix(), read_only=True)
    try:
        conn.execute("CREATE TEMP TABLE requested_symbols(symbol VARCHAR)")
        conn.executemany(
            "INSERT INTO requested_symbols VALUES (?)",
            [(symbol,) for symbol in symbols],
        )
        rows = conn.execute("""
            WITH latest_run AS (
                SELECT run_id
                FROM run_metadata
                ORDER BY created_at_utc DESC
                LIMIT 1
            )
            SELECT raw.symbol,
                TRY_CAST(raw.close AS DOUBLE) AS latest_close,
                TRY_CAST(raw."Perf.5D" AS DOUBLE) AS perf_5d,
                TRY_CAST(raw."Perf.1M" AS DOUBLE) AS perf_1m,
                TRY_CAST(raw."Perf.3M" AS DOUBLE) AS perf_3m,
                TRY_CAST(raw.ATRP AS DOUBLE) AS atrp,
                TRY_CAST(raw.relative_volume_10d_calc AS DOUBLE) AS relative_volume_10d_calc,
                TRY_CAST(raw."Recommend.All" AS DOUBLE) AS recommend_all,
                TRY_CAST(raw.SMA20 AS DOUBLE) AS sma20,
                TRY_CAST(raw.SMA50 AS DOUBLE) AS sma50,
                TRY_CAST(raw.EMA20 AS DOUBLE) AS ema20,
                TRY_CAST(raw.EMA50 AS DOUBLE) AS ema50,
                TRY_CAST(raw.earnings_release_next_trading_date_fq AS TIMESTAMP) AS next_earnings_at
            FROM all_fields_rows AS raw
            JOIN latest_run USING (run_id)
            JOIN requested_symbols AS requested USING (symbol)
            """).fetchall()
        columns = [item[0] for item in conn.description]
    finally:
        conn.close()
    return {str(row[0]): dict(zip(columns, row, strict=True)) for row in rows if row[0]}


def load_historical_timing_rows(
    snapshot_database_path: str | Path,
    symbols: Sequence[str],
) -> list[dict[str, Any]]:
    if not symbols:
        return []
    duckdb = _import_duckdb()
    conn = duckdb.connect(Path(snapshot_database_path).as_posix(), read_only=True)
    try:
        conn.execute("CREATE TEMP TABLE requested_symbols(symbol VARCHAR)")
        conn.executemany(
            "INSERT INTO requested_symbols VALUES (?)",
            [(symbol,) for symbol in symbols],
        )
        result = conn.execute("""
            WITH optional_fields AS (
                SELECT source_date, symbol,
                    MAX(CASE WHEN field_name = 'ATRP' THEN raw_value END) AS atrp,
                    MAX(CASE WHEN field_name = 'Recommend.All' THEN raw_value END) AS recommend_all
                FROM symbol_day_feature_values
                JOIN requested_symbols USING (symbol)
                WHERE field_name IN ('ATRP', 'Recommend.All')
                GROUP BY source_date, symbol
            )
            SELECT snap.source_date,
                snap.symbol,
                snap.close_price,
                snap.perf_5d,
                snap.perf_1m,
                snap.perf_3m,
                snap.relative_volume_10d_calc,
                fields.atrp,
                fields.recommend_all
            FROM symbol_day_feature_snapshot AS snap
            JOIN requested_symbols USING (symbol)
            LEFT JOIN optional_fields AS fields USING (source_date, symbol)
            WHERE snap.close_price IS NOT NULL AND snap.close_price > 0
            ORDER BY snap.symbol, snap.source_date
            """)
        rows = result.fetchall()
        columns = [item[0] for item in result.description]
    finally:
        conn.close()
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _simulate_exit(
    rows: Sequence[Mapping[str, Any]],
    entry_index: int,
    variant: TradePlanVariant,
) -> dict[str, Any] | None:
    exit_index = min(entry_index + variant.max_holding_days, len(rows) - 1)
    if exit_index <= entry_index:
        return None
    entry_close = _safe_float(rows[entry_index].get("close_price"))
    if entry_close is None or entry_close <= 0:
        return None
    exit_reason = "time_exit"
    for index in range(entry_index + 1, exit_index + 1):
        close = _safe_float(rows[index].get("close_price"))
        if close is None:
            continue
        return_pct = 100.0 * ((close / entry_close) - 1.0)
        if variant.stop_pct is not None and return_pct <= -abs(variant.stop_pct):
            exit_index = index
            exit_reason = "stop_exit"
            break
        if variant.target_pct is not None and return_pct >= variant.target_pct:
            exit_index = index
            exit_reason = "target_exit"
            break
    exit_close = _safe_float(rows[exit_index].get("close_price"))
    if exit_close is None:
        return None
    path_returns = [
        100.0
        * (((_safe_float(item.get("close_price")) or entry_close) / entry_close) - 1.0)
        for item in rows[entry_index + 1 : exit_index + 1]
    ]
    return {
        "exit_index": exit_index,
        "exit_date": rows[exit_index].get("source_date"),
        "exit_close": round(exit_close, 6),
        "exit_reason": exit_reason,
        "holding_observations": exit_index - entry_index,
        "return_pct": round(100.0 * ((exit_close / entry_close) - 1.0), 4),
        "mfe_pct": round(max(path_returns), 4) if path_returns else 0.0,
        "mae_pct": round(min(path_returns), 4) if path_returns else 0.0,
    }


def backtest_trade_plan_variants(
    historical_rows: Sequence[Mapping[str, Any]],
    *,
    variants: Sequence[TradePlanVariant] = DEFAULT_TRADE_PLAN_VARIANTS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for source_row in historical_rows:
        symbol = str(source_row.get("symbol") or "")
        if symbol:
            rows_by_symbol.setdefault(symbol, []).append(dict(source_row))
    for rows in rows_by_symbol.values():
        rows.sort(key=lambda item: str(item.get("source_date") or ""))

    trades: list[dict[str, Any]] = []
    for variant in variants:
        for symbol, rows in rows_by_symbol.items():
            index = 0
            while index < len(rows) - 1:
                entry_row = rows[index]
                if not _variant_signal(variant.name, entry_row):
                    index += 1
                    continue
                simulated = _simulate_exit(rows, index, variant)
                if simulated is None:
                    break
                trades.append(
                    {
                        "variant": variant.name,
                        "symbol": symbol,
                        "entry_date": entry_row.get("source_date"),
                        "entry_close": round(
                            _safe_float(entry_row.get("close_price")) or 0.0, 6
                        ),
                        "entry_perf_5d": _round_or_blank(
                            _safe_float(entry_row.get("perf_5d"))
                        ),
                        "entry_perf_1m": _round_or_blank(
                            _safe_float(entry_row.get("perf_1m"))
                        ),
                        "entry_atrp": _round_or_blank(
                            _safe_float(entry_row.get("atrp"))
                        ),
                        "entry_relative_volume": _round_or_blank(
                            _safe_float(entry_row.get("relative_volume_10d_calc"))
                        ),
                        "entry_recommend_all": _round_or_blank(
                            _safe_float(entry_row.get("recommend_all"))
                        ),
                        **{
                            key: value
                            for key, value in simulated.items()
                            if key != "exit_index"
                        },
                    }
                )
                index = int(simulated["exit_index"]) + 1

    summaries: list[dict[str, Any]] = []
    for variant in variants:
        selected = [row for row in trades if row["variant"] == variant.name]
        returns = [float(row["return_pct"]) for row in selected]
        maes = [float(row["mae_pct"]) for row in selected]
        mfes = [float(row["mfe_pct"]) for row in selected]
        summaries.append(
            {
                "variant": variant.name,
                "description": variant.description,
                "max_holding_days": variant.max_holding_days,
                "target_pct": (
                    variant.target_pct if variant.target_pct is not None else ""
                ),
                "stop_pct": variant.stop_pct if variant.stop_pct is not None else "",
                "trade_count": len(selected),
                "symbol_count": len({str(row["symbol"]) for row in selected}),
                "win_rate": (
                    round(sum(value > 0 for value in returns) / len(returns), 4)
                    if returns
                    else ""
                ),
                "avg_return_pct": (
                    round(sum(returns) / len(returns), 4) if returns else ""
                ),
                "median_return_pct": round(median(returns), 4) if returns else "",
                "median_mfe_pct": round(median(mfes), 4) if mfes else "",
                "median_mae_pct": round(median(maes), 4) if maes else "",
                "target_exit_rate": (
                    round(
                        sum(row["exit_reason"] == "target_exit" for row in selected)
                        / len(selected),
                        4,
                    )
                    if selected
                    else ""
                ),
                "stop_exit_rate": (
                    round(
                        sum(row["exit_reason"] == "stop_exit" for row in selected)
                        / len(selected),
                        4,
                    )
                    if selected
                    else ""
                ),
            }
        )
    return summaries, trades


def _write_trade_plan_duckdb(
    database_path: Path,
    *,
    plan_rows: Sequence[Mapping[str, Any]],
    summary_rows: Sequence[Mapping[str, Any]],
    trade_rows: Sequence[Mapping[str, Any]],
) -> None:
    duckdb = _import_duckdb()
    if database_path.exists():
        database_path.unlink()
    conn = duckdb.connect(database_path.as_posix())
    try:
        for table_name, rows in (
            ("edge_trade_plan", plan_rows),
            ("edge_trade_plan_backtest_summary", summary_rows),
            ("edge_trade_plan_backtest_trades", trade_rows),
        ):
            if not rows:
                conn.execute(f"CREATE TABLE {table_name}(symbol VARCHAR)")
                continue
            csv_path = database_path.with_name(f".{table_name}.csv")
            _write_csv_rows(csv_path, rows)
            conn.execute(
                f"CREATE TABLE {table_name} AS SELECT * FROM "
                f"read_csv({_quote_sql_literal(csv_path.as_posix())}, header=true, auto_detect=true)"
            )
            csv_path.unlink(missing_ok=True)
    finally:
        conn.close()


def build_edge_trade_plan_store(
    *,
    output_dir: str | Path,
    unified_csv_path: str | Path,
    latest_all_fields_database_path: str | Path,
    snapshot_database_path: str | Path,
    top_count: int = 30,
    variants: Sequence[TradePlanVariant] = DEFAULT_TRADE_PLAN_VARIANTS,
) -> dict[str, Any]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    unified_rows = _read_csv_rows(unified_csv_path)
    symbols = [
        str(row.get("symbol") or "") for row in unified_rows if row.get("symbol")
    ]
    latest_by_symbol = load_latest_all_fields_for_symbols(
        latest_all_fields_database_path,
        symbols,
    )
    plan_rows = build_current_trade_plan_rows(
        unified_rows,
        latest_by_symbol,
        variants=variants,
    )
    recommendation_rows = [
        row for row in plan_rows if row.get("entry_state") in {"ENTER_STARTER", "ARMED"}
    ][: max(1, int(top_count))]
    historical_rows = load_historical_timing_rows(snapshot_database_path, symbols)
    summary_rows, trade_rows = backtest_trade_plan_variants(
        historical_rows,
        variants=variants,
    )

    ranked_csv = output_path / "edge_trade_plan_ranked.csv"
    top_csv = output_path / f"edge_trade_plan_top{max(1, int(top_count))}.csv"
    summary_csv = output_path / "edge_trade_plan_backtest_summary.csv"
    trades_csv = output_path / "edge_trade_plan_backtest_trades.csv"
    database_path = output_path / "edge_trade_plan.duckdb"
    report_md = output_path / "edge_trade_plan_methodology.md"
    manifest_path = output_path / "edge_trade_plan_manifest.json"
    _write_csv_rows(ranked_csv, plan_rows)
    _write_csv_rows(
        top_csv,
        [project_trade_plan_top_row(row) for row in recommendation_rows],
    )
    _write_csv_rows(summary_csv, summary_rows)
    _write_csv_rows(trades_csv, trade_rows)
    _write_trade_plan_duckdb(
        database_path,
        plan_rows=plan_rows,
        summary_rows=summary_rows,
        trade_rows=trade_rows,
    )

    state_counts: dict[str, int] = {}
    for row in plan_rows:
        state = str(row.get("entry_state") or "UNKNOWN")
        state_counts[state] = state_counts.get(state, 0) + 1
    report_lines = [
        "# Edge Trade Plan Methodology",
        "",
        "## Purpose",
        "",
        "This augmentation converts the existing unified edge shortlist into explicit entry states and compares several daily-close entry/exit variants. It does not change any upstream edge score.",
        "",
        "## Current States",
        "",
        "- `ENTER_STARTER`: timing variant matches and at least three quality checks pass.",
        "- `ARMED`: an entry variant matches, but evidence or safety support is incomplete.",
        "- `WATCH`: timing is close but no complete entry variant matches.",
        "- `REJECT`: timing is weak or a speculative name has no matching entry variant.",
        "",
        "## Checks",
        "",
        "Quality checks cover setup depth, historical-validation bucket, positive lane bootstrap lower bound, non-speculative safety, and non-negative valuation upside.",
        "Timing checks cover data completeness, intact 1M trend, 5D extension measured in ATRP units, tradeable participation, and non-negative TradingView recommendation.",
        "",
        "## Backtest Variants",
        "",
    ]
    for variant in variants:
        report_lines.append(
            f"- `{variant.name}`: {variant.description} Max hold={variant.max_holding_days}; target={variant.target_pct}; stop={variant.stop_pct}."
        )
    report_lines.extend(
        [
            "",
            "## Backtest Results",
            "",
            "| Variant | Trades | Win Rate | Avg Return % | Median Return % | Median MFE % | Median MAE % | Target Exit Rate | Stop Exit Rate |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in summary_rows:
        report_lines.append(
            "| "
            f"{row['variant']} | {row['trade_count']} | {row['win_rate']} | "
            f"{row['avg_return_pct']} | {row['median_return_pct']} | "
            f"{row['median_mfe_pct']} | {row['median_mae_pct']} | "
            f"{row['target_exit_rate']} | {row['stop_exit_rate']} |"
        )
    report_lines.extend(
        [
            "",
            "## Interpretation Limits",
            "",
            "- The backtest is conditioned on symbols in the current unified shortlist. It tests timing overlays on that cohort; it is not a point-in-time recreation of historical unified ranks.",
            "- Entry fields are point-in-time daily values and exits use subsequent daily closes, avoiding future data in signal construction.",
            "- Target and stop crossings are evaluated at daily close. Intraday crossings, gaps, spread, fees, slippage, taxes, and FX are not modeled.",
            "- Trades are non-overlapping per symbol and variant to reduce repeated-signal inflation.",
            "- Use the baseline to judge incremental timing value. Do not select a variant from average return alone; require adequate trade count, win rate, median return, and MAE improvement.",
            "",
            "## Outputs",
            "",
            f"- Current ranked plan: `{ranked_csv.as_posix()}`",
            f"- Top recommendations: `{top_csv.as_posix()}`",
            f"- Backtest summary: `{summary_csv.as_posix()}`",
            f"- Trade detail: `{trades_csv.as_posix()}`",
            f"- Query database: `{database_path.as_posix()}`",
            "",
            "## Current State Counts",
            "",
        ]
    )
    report_lines.extend(
        f"- {key}: {value}" for key, value in sorted(state_counts.items())
    )
    report_md.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    manifest = {
        "command": "edge-trade-plan",
        "unified_csv_path": Path(unified_csv_path).as_posix(),
        "latest_all_fields_database_path": Path(
            latest_all_fields_database_path
        ).as_posix(),
        "snapshot_database_path": Path(snapshot_database_path).as_posix(),
        "output_dir": output_path.as_posix(),
        "ranked_csv": ranked_csv.as_posix(),
        "top_csv": top_csv.as_posix(),
        "backtest_summary_csv": summary_csv.as_posix(),
        "backtest_trades_csv": trades_csv.as_posix(),
        "database_path": database_path.as_posix(),
        "report_md": report_md.as_posix(),
        "row_count": len(plan_rows),
        "recommendation_count": len(recommendation_rows),
        "backtest_trade_count": len(trade_rows),
        "state_counts": state_counts,
        "variants": [asdict(item) for item in variants],
        "backtest_scope": "current_unified_cohort_timing_overlay",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        "output_dir": output_path,
        "ranked_csv": ranked_csv,
        "top_csv": top_csv,
        "backtest_summary_csv": summary_csv,
        "backtest_trades_csv": trades_csv,
        "database_path": database_path,
        "report_md": report_md,
        "manifest_path": manifest_path,
        "row_count": len(plan_rows),
        "recommendation_count": len(recommendation_rows),
        "backtest_trade_count": len(trade_rows),
        "state_counts": state_counts,
    }
