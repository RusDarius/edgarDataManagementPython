"""Upside-focused trading-opportunity scanner.

Standalone complement to the main edge-research suite (see
``run_upside_opportunity_scan.py``). This module reuses the already-computed
per-symbol dataset from the unified edge highlights output (technical
percentiles, historical validation, forward valuation, safety scoring) and
adds:

- a handful of supplementary raw technical fields not already carried in
  unified highlights (beta, extended volatility/indicator fields, raw
  performance), pulled directly from the daily all-fields database;
- a component-weighted ``upside_opportunity_score`` focused on UPSIDE
  potential (momentum, historical validation, valuation, safety/quality);
- a continuous ``binary_risk_score`` that DOWN-WEIGHTS (never hard-excludes)
  names showing binary/speculative characteristics: sparse fundamental data,
  wide valuation bear/bull dispersion, extreme volatility percentile, an
  imminent earnings/catalyst date, or a biotech/clinical-stage sector match;
- a ``downside_risk_score`` and ``directional_lean`` that flag elevated
  downside risk as an explicit AVOID/CAUTION signal rather than a short
  trade idea (this suite does not generate short signals).

Design patterns (component weighting, risk-adjusted scoring, missing-data
penalties) are inspired by the separate move-prediction system's proven
scoring engine, but this module has NO runtime dependency on it -- every
input here comes from edge_research_tools' own outputs.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

DEFAULT_MISSING_SAFETY_PENALTY = 0.20
DEFAULT_TOP_COUNT = 30

# Starter keyword list for the binary-risk sector/industry signal. This is a
# tunable first pass (see plan "Further Considerations") -- it contributes a
# fraction of the binary-risk score, it never hard-excludes a name.
BINARY_RISK_SECTOR_KEYWORDS: tuple[str, ...] = (
    "biotechnology",
    "biotech",
    "pharmaceutical",
    "clinical-stage",
    "clinical stage",
    "drug manufacturer",
    "life sciences tools",
)

# Raw all_fields_rows columns not already carried by unified highlights.
# (raw column name, output alias, SQL cast type)
SUPPLEMENTARY_FIELD_SPECS: tuple[tuple[str, str, str], ...] = (
    ("beta_1_year", "beta_1_year", "DOUBLE"),
    ("Volatility.D", "volatility_d", "DOUBLE"),
    ("Volatility.W", "volatility_w", "DOUBLE"),
    ("Volatility.M", "volatility_m", "DOUBLE"),
    ("ADX", "adx", "DOUBLE"),
    ("RSI", "rsi", "DOUBLE"),
    ("Perf.5D", "perf_5d", "DOUBLE"),
    ("Perf.1M", "perf_1m", "DOUBLE"),
    ("Perf.3M", "perf_3m", "DOUBLE"),
    ("Recommend.All", "recommend_all_raw", "DOUBLE"),
    ("SMA20", "sma20", "DOUBLE"),
    ("SMA50", "sma50", "DOUBLE"),
    ("close", "close", "DOUBLE"),
)

OPPORTUNITY_TIER_PRIORITY: dict[str, int] = {
    "HIGH_CONVICTION_UPSIDE": 0,
    "MODERATE_UPSIDE": 1,
    "SPECULATIVE_UPSIDE_BINARY_RISK": 2,
    "CAUTION_DOWNSIDE_RISK": 3,
    "NEUTRAL_INSUFFICIENT_EDGE": 4,
}


def _import_duckdb():
    import duckdb

    return duckdb


def _q(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _qi(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _table_exists(connection: Any, *, database_name: str, table_name: str) -> bool:
    row = connection.execute(
        "SELECT COUNT(*) FROM duckdb_tables() WHERE database_name = ? AND table_name = ?",
        [database_name, table_name],
    ).fetchone()
    return bool(row and int(row[0]) > 0)


def _table_columns(connection: Any, *, database_name: str, table_name: str) -> set[str]:
    rows = connection.execute(
        "SELECT column_name FROM duckdb_columns() WHERE database_name = ? AND table_name = ?",
        [database_name, table_name],
    ).fetchall()
    return {str(row[0]) for row in rows}


def _fetch_dict_rows(
    connection: Any, query: str, params: Sequence[Any] = ()
) -> list[dict[str, Any]]:
    cursor = (
        connection.execute(query, list(params)) if params else connection.execute(query)
    )
    columns = [str(item[0]) for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _read_csv_rows(path: str | Path | None) -> list[dict[str, str]]:
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


def _index_rows_by_symbol(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if symbol and symbol not in indexed:
            indexed[symbol] = row
    return indexed


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _optional_select(
    *,
    field_name: str,
    alias: str,
    sql_type: str,
    available_columns: set[str],
) -> str:
    if field_name not in available_columns:
        return f"CAST(NULL AS {sql_type}) AS {_qi(alias)}"
    return f"TRY_CAST(r.{_qi(field_name)} AS {sql_type}) AS {_qi(alias)}"


def load_supplementary_technical_fields(
    *,
    source_database_path: str | Path,
    symbols: Sequence[str],
    duckdb_threads: int = 16,
) -> dict[str, dict[str, Any]]:
    """Pull raw technical fields not already carried by unified highlights.

    Writes a NEW, self-contained query (rather than modifying or reusing
    ``edge_trade_plan.load_latest_all_fields_for_symbols``) so this module has
    no coupling to that lens's contract.
    """
    unique_symbols = sorted(
        {str(symbol).strip() for symbol in symbols if str(symbol).strip()}
    )
    if not unique_symbols:
        return {}

    db_path = Path(source_database_path)
    if not db_path.exists():
        raise FileNotFoundError(
            f"Source daily all-fields database not found: {db_path.as_posix()}"
        )

    duckdb = _import_duckdb()
    conn = duckdb.connect(database=":memory:")
    conn.execute(f"SET threads TO {int(duckdb_threads)}")
    try:
        conn.execute(f"ATTACH {_q(db_path.as_posix())} AS src_daily (READ_ONLY)")
        available_columns = _table_columns(
            conn, database_name="src_daily", table_name="all_fields_rows"
        )
        if not available_columns:
            raise ValueError(
                "Table src_daily.all_fields_rows was not found or has no columns "
                f"in {db_path.as_posix()}."
            )

        base_select_sql = ",\n                    ".join(
            _optional_select(
                field_name=field_name,
                alias=alias,
                sql_type=sql_type,
                available_columns=available_columns,
            )
            for field_name, alias, sql_type in SUPPLEMENTARY_FIELD_SPECS
        )

        has_run_metadata = _table_exists(
            conn, database_name="src_daily", table_name="run_metadata"
        )
        if has_run_metadata:
            latest_run_rank_sql = (
                "ROW_NUMBER() OVER ("
                "PARTITION BY CAST(r.symbol AS VARCHAR) "
                "ORDER BY m.created_at_utc DESC NULLS LAST, r.run_id DESC, r.row_number DESC"
                ")"
            )
            join_sql = "LEFT JOIN src_daily.run_metadata AS m USING (run_id)"
        else:
            latest_run_rank_sql = (
                "ROW_NUMBER() OVER ("
                "PARTITION BY CAST(r.symbol AS VARCHAR) "
                "ORDER BY r.run_id DESC, r.row_number DESC"
                ")"
            )
            join_sql = ""

        symbol_literals = ", ".join(_q(symbol) for symbol in unique_symbols)
        query = f"""
            SELECT * EXCLUDE (row_num)
            FROM (
                SELECT
                    CAST(r.symbol AS VARCHAR) AS symbol,
                    {base_select_sql},
                    {latest_run_rank_sql} AS row_num
                FROM src_daily.all_fields_rows AS r
                {join_sql}
                WHERE CAST(r.symbol AS VARCHAR) IN ({symbol_literals})
            )
            WHERE row_num = 1
        """
        rows = _fetch_dict_rows(conn, query)
    finally:
        conn.close()

    return {str(row["symbol"]): row for row in rows if row.get("symbol")}


# ---------------------------------------------------------------------------
# Scoring engine (pure functions, unit-tested independently of any IO)
# ---------------------------------------------------------------------------


def compute_momentum_component(row: Mapping[str, Any]) -> float:
    adrp_pct = _clamp(_safe_float(row.get("adrp_pct_today")))
    relvol_pct = _clamp(_safe_float(row.get("relvol_pct_today")))
    mom_core_pct = _clamp(_safe_float(row.get("mom_core_pct_today")))
    recommend_all = _safe_float_or_none(row.get("recommend_all_raw"))
    recommend_norm = (
        _clamp((recommend_all + 1.0) / 2.0) if recommend_all is not None else 0.5
    )
    perf_1m = _safe_float_or_none(row.get("perf_1m"))
    perf_1m_norm = (
        _clamp((_clamp(perf_1m / 25.0, -1.0, 1.0) + 1.0) / 2.0)
        if perf_1m is not None
        else 0.5
    )
    return round(
        _clamp(
            (0.30 * adrp_pct)
            + (0.25 * relvol_pct)
            + (0.20 * mom_core_pct)
            + (0.15 * recommend_norm)
            + (0.10 * perf_1m_norm)
        ),
        4,
    )


def compute_historical_validation_component(row: Mapping[str, Any]) -> float:
    hist_win_rate = _clamp(_safe_float(row.get("upside_hist_win_rate")))
    hist_median_fwd_pct = _safe_float(row.get("upside_hist_median_fwd_pct"))
    return_score = _clamp(hist_median_fwd_pct / 10.0)
    edge_5d_win_rate = _clamp(_safe_float(row.get("historical_edge_hist_win_rate_5d")))
    occurrence_score = _clamp(
        _safe_int(row.get("hist_occurrence_count_in_setup")) / 8.0
    )

    base_score = _clamp(
        (0.35 * hist_win_rate)
        + (0.25 * return_score)
        + (0.20 * edge_5d_win_rate)
        + (0.20 * occurrence_score)
    )

    ci_low = _safe_float_or_none(row.get("lane_leader_median_fwd_5d_ci_low"))
    if ci_low is not None:
        base_score = _clamp(base_score + (0.05 if ci_low > 0 else -0.05))

    return round(base_score, 4)


def compute_valuation_component(row: Mapping[str, Any]) -> float:
    upside_pct = _safe_float_or_none(row.get("forward_valuation_upside_pct"))
    if upside_pct is None:
        return 0.0
    score = _clamp(upside_pct / 40.0)
    mode = str(row.get("forward_upside_mode") or "").strip().lower()
    if mode != "valuation":
        score *= 0.7
    return round(_clamp(score), 4)


def compute_safety_quality_component(row: Mapping[str, Any]) -> float:
    safety_data_available = _safe_int(row.get("safety_data_available")) == 1
    if not safety_data_available:
        return round(DEFAULT_MISSING_SAFETY_PENALTY, 4)

    safety_companion_score = _clamp(_safe_float(row.get("safety_companion_score")))
    indicator_pass_score = _clamp(_safe_int(row.get("indicator_pass_count")) / 11.0)
    return round(
        _clamp((0.65 * safety_companion_score) + (0.35 * indicator_pass_score)),
        4,
    )


def compute_binary_risk_score(
    row: Mapping[str, Any],
    *,
    earnings_days_until: int | None = None,
) -> float:
    safety_data_available = _safe_int(row.get("safety_data_available")) == 1
    forward_upside_mode = str(row.get("forward_upside_mode") or "").strip().lower()
    data_gap_score = (
        1.0 if (not safety_data_available) or forward_upside_mode == "fallback" else 0.0
    )

    bear_pct = _safe_float_or_none(row.get("forward_valuation_bear_upside_pct"))
    bull_pct = _safe_float_or_none(row.get("forward_valuation_bull_upside_pct"))
    if bear_pct is not None and bull_pct is not None:
        dispersion_score = _clamp((bull_pct - bear_pct) / 80.0)
    else:
        dispersion_score = 0.0

    volatility_extremity_score = _clamp(_safe_float(row.get("vol_core_pct_today")))

    if earnings_days_until is None:
        catalyst_proximity_score = 0.0
    elif 0 <= earnings_days_until <= 10:
        catalyst_proximity_score = 1.0
    elif 11 <= earnings_days_until <= 20:
        catalyst_proximity_score = 0.4
    else:
        catalyst_proximity_score = 0.0

    haystack = " ".join(
        str(row.get(key) or "").lower() for key in ("sector", "industry")
    )
    sector_keyword_score = (
        1.0
        if any(keyword in haystack for keyword in BINARY_RISK_SECTOR_KEYWORDS)
        else 0.0
    )

    return round(
        _clamp(
            (0.30 * data_gap_score)
            + (0.25 * dispersion_score)
            + (0.20 * volatility_extremity_score)
            + (0.15 * catalyst_proximity_score)
            + (0.10 * sector_keyword_score)
        ),
        4,
    )


def compute_downside_risk_component(
    row: Mapping[str, Any],
    *,
    momentum_component: float,
    safety_quality_component: float,
) -> float:
    bear_pct = _safe_float_or_none(row.get("forward_valuation_bear_upside_pct"))
    bear_penalty = (
        _clamp(-bear_pct / 40.0) if bear_pct is not None and bear_pct < 0 else 0.0
    )
    return round(
        _clamp(
            (0.40 * (1.0 - momentum_component))
            + (0.35 * (1.0 - safety_quality_component))
            + (0.25 * bear_penalty)
        ),
        4,
    )


def classify_directional_lean(
    *,
    opportunity_score: float,
    downside_risk_score: float,
) -> str:
    if downside_risk_score >= 0.65 and opportunity_score < 0.40:
        return "AVOID_DOWNSIDE_RISK"
    if downside_risk_score >= 0.50:
        return "CAUTION_DOWNSIDE"
    if opportunity_score >= 0.70:
        return "STRONG_UP"
    if opportunity_score >= 0.50:
        return "LEAN_UP"
    return "NEUTRAL"


def classify_opportunity_tier(
    *,
    opportunity_score: float,
    binary_risk_score: float,
    directional_lean: str,
) -> str:
    if directional_lean in ("AVOID_DOWNSIDE_RISK", "CAUTION_DOWNSIDE"):
        return "CAUTION_DOWNSIDE_RISK"
    if binary_risk_score >= 0.55 and opportunity_score >= 0.45:
        return "SPECULATIVE_UPSIDE_BINARY_RISK"
    if opportunity_score >= 0.65:
        return "HIGH_CONVICTION_UPSIDE"
    if opportunity_score >= 0.45:
        return "MODERATE_UPSIDE"
    return "NEUTRAL_INSUFFICIENT_EDGE"


def compute_opportunity_fields(
    row: Mapping[str, Any],
    *,
    earnings_days_until: int | None = None,
) -> dict[str, Any]:
    """Combine every component into the final upside-opportunity fields.

    Every input here is either copied verbatim from the unified highlights
    row or freshly computed by this function -- no upstream rank/score field
    is ever overwritten.
    """
    momentum = compute_momentum_component(row)
    historical_validation = compute_historical_validation_component(row)
    valuation = compute_valuation_component(row)
    safety_quality = compute_safety_quality_component(row)
    binary_risk = compute_binary_risk_score(
        row, earnings_days_until=earnings_days_until
    )
    downside_risk = compute_downside_risk_component(
        row,
        momentum_component=momentum,
        safety_quality_component=safety_quality,
    )

    base_score = (
        (0.30 * momentum)
        + (0.25 * historical_validation)
        + (0.20 * valuation)
        + (0.15 * safety_quality)
    )
    penalty = (0.35 * binary_risk) + (0.20 * downside_risk)
    bonus = (0.08 * safety_quality) + (0.05 * historical_validation)
    opportunity_score = round(
        _clamp(base_score * (1.0 + bonus) / (1.0 + penalty)),
        4,
    )

    directional_lean = classify_directional_lean(
        opportunity_score=opportunity_score,
        downside_risk_score=downside_risk,
    )
    opportunity_tier = classify_opportunity_tier(
        opportunity_score=opportunity_score,
        binary_risk_score=binary_risk,
        directional_lean=directional_lean,
    )

    return {
        "opp_momentum_component": momentum,
        "opp_historical_validation_component": historical_validation,
        "opp_valuation_component": valuation,
        "opp_safety_quality_component": safety_quality,
        "opp_binary_risk_score": binary_risk,
        "opp_downside_risk_score": downside_risk,
        "upside_opportunity_score": opportunity_score,
        "directional_lean": directional_lean,
        "opportunity_tier": opportunity_tier,
        "earnings_days_until": (
            earnings_days_until if earnings_days_until is not None else ""
        ),
    }


def rank_opportunity_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Order by opportunity score without rewriting any inherited rank field."""

    def sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            -_safe_float(row.get("upside_opportunity_score")),
            -_safe_float(row.get("opp_historical_validation_component")),
            str(row.get("symbol") or ""),
        )

    ordered = sorted((dict(row) for row in rows), key=sort_key)
    for position, row in enumerate(ordered, start=1):
        row["upside_opportunity_rank"] = position
    return ordered


# ---------------------------------------------------------------------------
# Orchestrator: build the standalone CSV/DuckDB store
# ---------------------------------------------------------------------------


def _write_report(
    path: Path,
    *,
    unified_csv_path: Path,
    source_database_path: Path,
    earnings_priority_csv_path: Path | None,
    candidate_count: int,
    tier_counts: Mapping[str, int],
    lean_counts: Mapping[str, int],
    candidates_csv: Path,
    top_csv: Path,
    database_path: Path,
    top_count: int,
) -> None:
    lines = [
        "# Upside Trading Opportunity Scanner",
        "",
        "## What This Output Does",
        "",
        "Standalone complement to the main edge-research suite. Every symbol from "
        "the unified edge highlights output is scored for UPSIDE potential, "
        "blending momentum, historical validation, forward valuation, and "
        "safety/quality -- with a continuous binary/speculative-risk down-weight "
        "(never a hard exclude) and a directional-lean classification that flags "
        "elevated downside risk as an explicit caution/avoid signal rather than a "
        "short trade idea (this suite does not generate short signals).",
        "",
        "## Score Construction",
        "",
        "- `opp_momentum_component`: current-state technical percentiles (ADRP, "
        "relative volume, momentum-context sleeve) plus TradingView recommendation "
        "and 1-month performance.",
        "- `opp_historical_validation_component`: historical win rate / median "
        "forward return / occurrence depth from the upside-prediction and "
        "historical-edge-progression lenses, with a small bootstrap-CI-lower-bound "
        "adjustment.",
        "- `opp_valuation_component`: forward valuation upside %, discounted when "
        "the valuation lens fell back to a non-valuation mode.",
        "- `opp_safety_quality_component`: safety companion score + indicator pass "
        "count; unscored names get a fixed missing-data penalty "
        f"({DEFAULT_MISSING_SAFETY_PENALTY}) rather than a neutral default.",
        "- `opp_binary_risk_score` (0-1, continuous, never a hard filter): data "
        "availability gaps, forward-valuation bear/bull scenario dispersion, "
        "volatility percentile extremity, earnings/catalyst proximity, and a "
        "biotech/clinical-stage sector-keyword signal.",
        "- `opp_downside_risk_score`: weak momentum + weak safety + negative "
        "bear-scenario valuation -- used only to drive the risk-flag axis below, "
        "never a short score.",
        "- `upside_opportunity_score` = (0.30 momentum + 0.25 historical + 0.20 "
        "valuation + 0.15 safety) risk-adjusted by binary-risk and downside-risk "
        "penalties, with a small safety/historical-validation bonus.",
        "",
        "## Directional Lean And Tiers",
        "",
        "- `directional_lean`: STRONG_UP / LEAN_UP / NEUTRAL / CAUTION_DOWNSIDE / "
        "AVOID_DOWNSIDE_RISK -- UP-focused, with downside framed as a risk flag.",
        "- `opportunity_tier`: HIGH_CONVICTION_UPSIDE / MODERATE_UPSIDE / "
        "SPECULATIVE_UPSIDE_BINARY_RISK / CAUTION_DOWNSIDE_RISK / "
        "NEUTRAL_INSUFFICIENT_EDGE.",
        "- `upside_opportunity_rank` orders this view; it never rewrites any "
        "inherited unified-highlights rank or score field.",
        "",
        "## Inputs",
        "",
        f"- unified edge highlights CSV: `{unified_csv_path.as_posix()}`",
        f"- source daily all-fields database: `{source_database_path.as_posix()}`",
        (
            f"- earnings-priority candidates CSV: `{earnings_priority_csv_path.as_posix()}`"
            if earnings_priority_csv_path is not None
            else "- earnings-priority candidates CSV: (not supplied -- catalyst "
            "proximity defaults to neutral)"
        ),
        "",
        f"- candidate rows: {candidate_count}",
        "",
        "## Opportunity Tier Counts",
        "",
    ]
    lines.extend(f"- {key}: {value}" for key, value in sorted(tier_counts.items()))
    lines.extend(["", "## Directional Lean Counts", ""])
    lines.extend(f"- {key}: {value}" for key, value in sorted(lean_counts.items()))
    lines.extend(
        [
            "",
            "## Output Files",
            "",
            f"- candidates CSV: `{candidates_csv.as_posix()}`",
            f"- top focus CSV: `{top_csv.as_posix()}` (top {int(max(1, top_count))} "
            "HIGH_CONVICTION_UPSIDE / MODERATE_UPSIDE rows)",
            f"- query database: `{database_path.as_posix()}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_database(
    database_path: Path,
    *,
    candidates_csv_path: Path,
    manifest_rows: Sequence[tuple[str, str]],
) -> None:
    duckdb = _import_duckdb()
    database_path.unlink(missing_ok=True)
    conn = duckdb.connect(database_path.as_posix())
    try:
        if candidates_csv_path.exists() and candidates_csv_path.stat().st_size > 0:
            conn.execute(f"""
                CREATE TABLE upside_opportunity_candidates AS
                SELECT * FROM read_csv_auto({_q(candidates_csv_path.as_posix())}, HEADER=TRUE)
                """)
        else:
            conn.execute("CREATE TABLE upside_opportunity_candidates(symbol VARCHAR)")
        conn.execute(
            "CREATE TABLE upside_opportunity_run_manifest(key VARCHAR, value VARCHAR)"
        )
        conn.executemany(
            "INSERT INTO upside_opportunity_run_manifest VALUES (?, ?)",
            list(manifest_rows),
        )
    finally:
        conn.close()


def build_upside_opportunity_scan(
    *,
    output_dir: str | Path,
    unified_csv_path: str | Path,
    source_database_path: str | Path,
    earnings_priority_csv_path: str | Path | None = None,
    top_count: int = DEFAULT_TOP_COUNT,
    duckdb_threads: int = 16,
) -> dict[str, Any]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    unified_rows = _read_csv_rows(unified_csv_path)
    symbols = [
        str(row.get("symbol") or "") for row in unified_rows if row.get("symbol")
    ]

    supplementary_by_symbol = load_supplementary_technical_fields(
        source_database_path=source_database_path,
        symbols=symbols,
        duckdb_threads=duckdb_threads,
    )
    earnings_by_symbol = _index_rows_by_symbol(
        _read_csv_rows(earnings_priority_csv_path)
    )

    scored_rows: list[dict[str, Any]] = []
    for unified_row in unified_rows:
        symbol = str(unified_row.get("symbol") or "")
        merged_row = dict(unified_row)
        supplementary_row = supplementary_by_symbol.get(symbol)
        if supplementary_row:
            for key, value in supplementary_row.items():
                if key != "symbol":
                    merged_row[key] = value

        earnings_row = earnings_by_symbol.get(symbol)
        earnings_days_until = (
            _safe_int_or_none(earnings_row.get("earnings_days_until"))
            if earnings_row
            else None
        )

        merged_row.update(
            compute_opportunity_fields(
                merged_row,
                earnings_days_until=earnings_days_until,
            )
        )
        scored_rows.append(merged_row)

    ordered_rows = rank_opportunity_rows(scored_rows)

    candidates_csv = output_path / "upside_opportunity_candidates.csv"
    top_csv = output_path / f"upside_opportunity_top{int(max(1, top_count))}.csv"
    database_path = output_path / "upside_opportunity_scan.duckdb"
    report_md = output_path / "upside_opportunity_scan_report.md"
    manifest_path = output_path / "upside_opportunity_scan_manifest.json"

    _write_csv_rows(candidates_csv, ordered_rows)

    top_tier_rows = [
        row
        for row in ordered_rows
        if row.get("opportunity_tier") in ("HIGH_CONVICTION_UPSIDE", "MODERATE_UPSIDE")
    ][: int(max(1, top_count))]
    _write_csv_rows(top_csv, top_tier_rows)

    tier_counts: dict[str, int] = {}
    lean_counts: dict[str, int] = {}
    for row in ordered_rows:
        tier = str(row.get("opportunity_tier") or "unknown")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        lean = str(row.get("directional_lean") or "unknown")
        lean_counts[lean] = lean_counts.get(lean, 0) + 1

    resolved_unified_csv_path = Path(unified_csv_path)
    resolved_source_database_path = Path(source_database_path)
    resolved_earnings_priority_csv_path = (
        Path(earnings_priority_csv_path) if earnings_priority_csv_path else None
    )

    _write_database(
        database_path,
        candidates_csv_path=candidates_csv,
        manifest_rows=[
            ("candidate_count", str(len(ordered_rows))),
            ("unified_csv_path", resolved_unified_csv_path.as_posix()),
            ("source_database_path", resolved_source_database_path.as_posix()),
            (
                "earnings_priority_csv_path",
                (
                    resolved_earnings_priority_csv_path.as_posix()
                    if resolved_earnings_priority_csv_path
                    else ""
                ),
            ),
            *[(f"tier_count:{key}", str(value)) for key, value in tier_counts.items()],
            *[(f"lean_count:{key}", str(value)) for key, value in lean_counts.items()],
        ],
    )
    _write_report(
        report_md,
        unified_csv_path=resolved_unified_csv_path,
        source_database_path=resolved_source_database_path,
        earnings_priority_csv_path=resolved_earnings_priority_csv_path,
        candidate_count=len(ordered_rows),
        tier_counts=tier_counts,
        lean_counts=lean_counts,
        candidates_csv=candidates_csv,
        top_csv=top_csv,
        database_path=database_path,
        top_count=int(max(1, top_count)),
    )

    manifest = {
        "command": "upside-opportunity-scan",
        "unified_csv_path": resolved_unified_csv_path.as_posix(),
        "source_database_path": resolved_source_database_path.as_posix(),
        "earnings_priority_csv_path": (
            resolved_earnings_priority_csv_path.as_posix()
            if resolved_earnings_priority_csv_path
            else None
        ),
        "output_dir": output_path.as_posix(),
        "candidates_csv": candidates_csv.as_posix(),
        "top_csv": top_csv.as_posix(),
        "database_path": database_path.as_posix(),
        "report_md": report_md.as_posix(),
        "row_count": len(ordered_rows),
        "top_count": int(max(1, top_count)),
        "tier_counts": tier_counts,
        "lean_counts": lean_counts,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return {
        "output_dir": output_path,
        "candidates_csv": candidates_csv,
        "top_csv": top_csv,
        "database_path": database_path,
        "report_md": report_md,
        "manifest_path": manifest_path,
        "row_count": len(ordered_rows),
        "tier_counts": tier_counts,
        "lean_counts": lean_counts,
    }
