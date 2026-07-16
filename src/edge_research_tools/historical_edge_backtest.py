from __future__ import annotations

import csv
import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from .config import DEFAULT_EDGE_RESEARCH_ROOT
from .setup_engine import DEFAULT_VOLATILITY_LIQUIDITY_SETUP_VARIANTS

DEFAULT_HISTORICAL_EDGE_ROOT = DEFAULT_EDGE_RESEARCH_ROOT / "historical_backtests"


@dataclass(frozen=True)
class HistoricalEdgeExecutionRule:
    rule_id: str
    entry_rank: int
    exit_rank: int
    max_holding_observations: int
    require_fresh_entry: bool = False
    fresh_rank: int = 100


DEFAULT_HISTORICAL_EDGE_EXECUTION_RULES: tuple[HistoricalEdgeExecutionRule, ...] = (
    HistoricalEdgeExecutionRule(
        rule_id="top20_exit100_max10",
        entry_rank=20,
        exit_rank=100,
        max_holding_observations=10,
    ),
    HistoricalEdgeExecutionRule(
        rule_id="fresh_top20_exit100_max10",
        entry_rank=20,
        exit_rank=100,
        max_holding_observations=10,
        require_fresh_entry=True,
        fresh_rank=100,
    ),
    HistoricalEdgeExecutionRule(
        rule_id="top50_exit200_max20",
        entry_rank=50,
        exit_rank=200,
        max_holding_observations=20,
    ),
)


def _import_duckdb():
    import duckdb

    return duckdb


def _q(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


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


def _pct_return(exit_close: float, entry_close: float) -> float:
    if entry_close == 0:
        return 0.0
    return 100.0 * ((exit_close / entry_close) - 1.0)


def _parse_date(value: str | date | None) -> date | None:
    if value is None or isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d_%m_%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported date value: {value!r}")


def select_anchor_dates(
    available_dates: Sequence[date],
    *,
    mode: str = "twice_weekly",
    spacing_trading_days: int = 3,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[date]:
    dates = sorted(
        value
        for value in set(available_dates)
        if (start_date is None or value >= start_date)
        and (end_date is None or value <= end_date)
    )
    if mode == "every_n_trading_days":
        if spacing_trading_days < 1:
            raise ValueError("spacing_trading_days must be at least 1.")
        return dates[::spacing_trading_days]
    if mode != "twice_weekly":
        raise ValueError(
            "anchor mode must be 'twice_weekly' or 'every_n_trading_days'."
        )
    by_week: dict[tuple[int, int], list[date]] = {}
    for value in dates:
        iso = value.isocalendar()
        by_week.setdefault((iso.year, iso.week), []).append(value)
    selected: list[date] = []
    for key in sorted(by_week):
        week_dates = by_week[key]
        selected.append(week_dates[0])
        if len(week_dates) > 1:
            selected.append(week_dates[-1])
    return selected


def _variant_sql() -> tuple[str, str]:
    flag_columns: list[str] = []
    names: list[str] = []
    for variant in DEFAULT_VOLATILITY_LIQUIDITY_SETUP_VARIANTS:
        condition = (
            f"COALESCE(adrp_pct, 0) >= {variant.min_adrp_pct} "
            f"AND COALESCE(relvol_pct, 0) >= {variant.min_relative_volume_pct} "
            f"AND COALESCE(value_traded_pct, 0) >= {variant.min_value_traded_pct} "
            f"AND COALESCE(liquidity_core_pct, 0) >= {variant.min_liquidity_core_pct} "
            f"AND COALESCE(momentum_context_pct, 0) >= {variant.min_momentum_context_pct} "
            f"AND COALESCE(volatility_core_pct, 0) >= {variant.min_volatility_core_pct}"
        )
        flag_columns.append(
            f"CASE WHEN {condition} THEN 1 ELSE 0 END AS in_{variant.name}"
        )
        names.append(f"in_{variant.name}")
    return ",\n                ".join(flag_columns), " OR ".join(
        f"{name} = 1" for name in names
    )


def _config_hash(config: Mapping[str, Any]) -> str:
    payload = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _rows(
    conn: Any, sql: str, params: Sequence[Any] | None = None
) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, params or [])
    columns = [str(item[0]) for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _initialize_metadata_tables(conn: Any) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS edge_historical_dataset_metadata (
            config_hash VARCHAR PRIMARY KEY,
            config_json VARCHAR,
            created_at_utc TIMESTAMP,
            updated_at_utc TIMESTAMP,
            source_snapshot_path VARCHAR
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS edge_anchor_plan (
            config_hash VARCHAR,
            anchor_date DATE,
            maturity_cutoff_5d DATE,
            anchor_mode VARCHAR,
            status VARCHAR,
            candidate_count BIGINT,
            created_at_utc TIMESTAMP,
            PRIMARY KEY (config_hash, anchor_date)
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS edge_refresh_log (
            refresh_id VARCHAR PRIMARY KEY,
            config_hash VARCHAR,
            created_at_utc TIMESTAMP,
            refresh_mode VARCHAR,
            requested_anchor_count BIGINT,
            processed_anchor_count BIGINT,
            candidate_count BIGINT,
            status VARCHAR,
            message VARCHAR
        )
        """)


def _create_daily_state(
    conn: Any,
    max_anchor_date: date,
    *,
    markets: Sequence[str] = (),
) -> None:
    flags_sql, any_setup_sql = _variant_sql()
    market_filter_sql = ""
    if markets:
        market_literals = ", ".join(_q(str(value).lower()) for value in markets)
        market_filter_sql = (
            f"AND LOWER(COALESCE(snap.market, '')) IN ({market_literals})"
        )
    conn.execute("""
        CREATE OR REPLACE TEMP TABLE historical_edge_label_maturity AS
        SELECT source_date, symbol,
            LEAD(source_date, 5) OVER (
                PARTITION BY symbol ORDER BY source_date
            ) AS maturity_date_5d
        FROM source_db.symbol_day_feature_snapshot
        """)
    conn.execute(f"""
        CREATE OR REPLACE TEMP TABLE historical_edge_daily_state AS
        WITH field_pivot AS (
            SELECT source_date, symbol,
                MAX(CASE WHEN field_name = 'ADRP' THEN raw_value END) AS adrp_raw,
                MAX(CASE WHEN field_name = 'ATRP' THEN raw_value END) AS atrp_raw,
                MAX(CASE WHEN field_name = 'relative_volume_10d_calc' THEN raw_value END) AS relvol_raw,
                MAX(CASE WHEN field_name = 'ADRP' THEN directional_universe_percentile END) AS adrp_pct,
                MAX(CASE WHEN field_name = 'relative_volume_10d_calc' THEN directional_universe_percentile END) AS relvol_pct,
                MAX(CASE WHEN field_name = 'Value.Traded' THEN directional_universe_percentile END) AS value_traded_pct
            FROM source_db.symbol_day_feature_values
            WHERE source_date <= {_q(max_anchor_date.isoformat())}::DATE
            GROUP BY source_date, symbol
        ),
        sleeve_pivot AS (
            SELECT source_date, symbol,
                MAX(CASE WHEN sleeve_name = 'volatility_core' THEN avg_directional_universe_percentile END) AS volatility_core_pct,
                MAX(CASE WHEN sleeve_name = 'liquidity_core' THEN avg_directional_universe_percentile END) AS liquidity_core_pct,
                MAX(CASE WHEN sleeve_name = 'momentum_context' THEN avg_directional_universe_percentile END) AS momentum_context_pct
            FROM source_db.symbol_day_sleeve_scores
            WHERE source_date <= {_q(max_anchor_date.isoformat())}::DATE
            GROUP BY source_date, symbol
        ),
        base AS (
            SELECT snap.source_date, snap.symbol, snap.bare_ticker,
                snap.company_name, snap.exchange, snap.country, snap.sector,
                snap.industry, snap.close_price, snap.market_cap_basic,
                snap.perf_5d, snap.perf_1m, snap.perf_3m,
                fields.adrp_raw, fields.atrp_raw, fields.relvol_raw,
                fields.adrp_pct, fields.relvol_pct, fields.value_traded_pct,
                sleeves.volatility_core_pct, sleeves.liquidity_core_pct,
                sleeves.momentum_context_pct,
                (
                    COALESCE(fields.adrp_pct, 0) + COALESCE(fields.relvol_pct, 0) +
                    COALESCE(fields.value_traded_pct, 0) +
                    COALESCE(sleeves.volatility_core_pct, 0) +
                    COALESCE(sleeves.liquidity_core_pct, 0) +
                    COALESCE(sleeves.momentum_context_pct, 0)
                ) / 6.0 AS composite_score,
                {flags_sql}
            FROM source_db.symbol_day_feature_snapshot AS snap
            LEFT JOIN field_pivot AS fields USING (source_date, symbol)
            LEFT JOIN sleeve_pivot AS sleeves USING (source_date, symbol)
            WHERE snap.source_date <= {_q(max_anchor_date.isoformat())}::DATE
                            {market_filter_sql}
        )
        SELECT *, CASE WHEN {any_setup_sql} THEN 1 ELSE 0 END AS in_any_setup
        FROM base
        """)


def _compute_candidates(
    conn: Any,
    *,
    top_n: int,
    min_composite: float,
) -> None:
    conn.execute(f"""
        CREATE OR REPLACE TEMP TABLE computed_edge_candidates AS
        WITH historical AS (
            SELECT anchors.anchor_date, state.symbol,
                COUNT(labels.forward_return_5d_pct) AS hist_sample_count_5d,
                MEDIAN(labels.forward_return_5d_pct) AS hist_median_return_5d_pct,
                AVG(CASE WHEN labels.forward_return_5d_pct > 0 THEN 1.0 ELSE 0.0 END) AS hist_win_rate_5d,
                MEDIAN(labels.max_favorable_excursion_5d_pct) AS hist_median_mfe_5d_pct,
                MEDIAN(labels.max_adverse_excursion_5d_pct) AS hist_median_mae_5d_pct,
                AVG(CASE WHEN labels.target_before_stop_5d_flag THEN 1.0 ELSE 0.0 END) AS hist_target_before_stop_rate_5d
            FROM requested_anchors AS anchors
            JOIN historical_edge_daily_state AS state
                            ON state.source_date <= anchors.anchor_date
             AND state.in_any_setup = 1
            JOIN source_db.symbol_day_forward_labels AS labels
              ON labels.source_date = state.source_date AND labels.symbol = state.symbol
                        JOIN historical_edge_label_maturity AS maturity
                            ON maturity.source_date = state.source_date AND maturity.symbol = state.symbol
                        WHERE maturity.maturity_date_5d <= anchors.anchor_date
                            AND labels.available_forward_days_5d >= 5
            GROUP BY anchors.anchor_date, state.symbol
        ),
        persistence AS (
            SELECT anchors.anchor_date, state.symbol,
                COUNT(*) AS observed_days,
                SUM(state.in_any_setup) AS any_setup_days,
                AVG(CAST(state.in_any_setup AS DOUBLE)) AS any_setup_rate,
                AVG(state.composite_score) AS avg_composite_score
            FROM requested_anchors AS anchors
            JOIN historical_edge_daily_state AS state
              ON state.source_date <= anchors.anchor_date
            GROUP BY anchors.anchor_date, state.symbol
        ),
        scored AS (
            SELECT anchors.anchor_date, current.*,
                COALESCE(hist.hist_sample_count_5d, 0) AS hist_sample_count_5d,
                hist.hist_median_return_5d_pct,
                hist.hist_win_rate_5d,
                hist.hist_median_mfe_5d_pct,
                hist.hist_median_mae_5d_pct,
                hist.hist_target_before_stop_rate_5d,
                COALESCE(persistence.observed_days, 0) AS observed_days,
                COALESCE(persistence.any_setup_days, 0) AS any_setup_days,
                COALESCE(persistence.any_setup_rate, 0) AS any_setup_rate,
                persistence.avg_composite_score,
                GREATEST(0.0, LEAST(1.0,
                    0.45 * current.composite_score +
                    0.15 * COALESCE(hist.hist_win_rate_5d, 0.5) +
                    0.12 * GREATEST(0.0, LEAST(1.0, COALESCE(hist.hist_median_return_5d_pct, 0) / 10.0)) +
                    0.10 * COALESCE(hist.hist_target_before_stop_rate_5d, 0) +
                    0.10 * GREATEST(0.0, LEAST(1.0, COALESCE(persistence.any_setup_rate, 0) / 0.15)) +
                    0.08 * GREATEST(0.0, LEAST(1.0, COALESCE(hist.hist_sample_count_5d, 0) / 10.0)) +
                    CASE WHEN current.in_any_setup = 1 THEN 0.03 ELSE 0.0 END
                )) AS historical_edge_score
            FROM requested_anchors AS anchors
            JOIN historical_edge_daily_state AS current
              ON current.source_date = anchors.anchor_date
            LEFT JOIN historical AS hist
              ON hist.anchor_date = anchors.anchor_date AND hist.symbol = current.symbol
            LEFT JOIN persistence
              ON persistence.anchor_date = anchors.anchor_date AND persistence.symbol = current.symbol
            WHERE current.composite_score >= {float(min_composite)} OR current.in_any_setup = 1
        ),
        ranked AS (
            SELECT scored.*,
                ROW_NUMBER() OVER (
                    PARTITION BY anchor_date
                    ORDER BY historical_edge_score DESC, composite_score DESC,
                        hist_sample_count_5d DESC, market_cap_basic DESC NULLS LAST
                ) AS historical_edge_rank
            FROM scored
        )
        SELECT ranked.*,
            labels.forward_return_3d_pct,
            labels.forward_return_5d_pct,
            labels.forward_return_10d_pct,
            labels.forward_return_20d_pct,
            labels.max_favorable_excursion_5d_pct,
            labels.max_adverse_excursion_5d_pct,
            labels.max_favorable_excursion_10d_pct,
            labels.max_adverse_excursion_10d_pct,
            labels.max_favorable_excursion_20d_pct,
            labels.max_adverse_excursion_20d_pct,
            labels.target_before_stop_5d_flag,
            labels.target_before_stop_10d_flag,
            labels.target_before_stop_20d_flag
        FROM ranked
        LEFT JOIN source_db.symbol_day_forward_labels AS labels
          ON labels.source_date = ranked.anchor_date AND labels.symbol = ranked.symbol
        WHERE historical_edge_rank <= {int(top_n)}
        """)


def _persist_candidates(
    conn: Any,
    *,
    config_hash: str,
    anchor_mode: str,
    created_at: datetime,
) -> int:
    table_exists = bool(
        conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'edge_candidate_snapshots'"
        ).fetchone()[0]
    )
    if not table_exists:
        conn.execute("""
            CREATE TABLE edge_candidate_snapshots AS
            SELECT CAST('' AS VARCHAR) AS config_hash, computed.*
            FROM computed_edge_candidates AS computed
            WHERE FALSE
            """)
    conn.execute(
        """
        DELETE FROM edge_candidate_snapshots
        WHERE config_hash = ?
          AND anchor_date IN (SELECT anchor_date FROM requested_anchors)
        """,
        [config_hash],
    )
    conn.execute(
        """
        INSERT INTO edge_candidate_snapshots
        SELECT ? AS config_hash, computed.*
        FROM computed_edge_candidates AS computed
        """,
        [config_hash],
    )
    count = int(
        conn.execute("SELECT COUNT(*) FROM computed_edge_candidates").fetchone()[0]
    )
    conn.execute(
        """
        DELETE FROM edge_anchor_plan
        WHERE config_hash = ?
          AND anchor_date IN (SELECT anchor_date FROM requested_anchors)
        """,
        [config_hash],
    )
    conn.execute(
        """
        INSERT INTO edge_anchor_plan
        SELECT ?, anchor_date, maturity_cutoff_5d, ?, 'complete',
            (SELECT COUNT(*) FROM computed_edge_candidates AS candidates
             WHERE candidates.anchor_date = anchors.anchor_date), ?
        FROM requested_anchors AS anchors
        """,
        [config_hash, anchor_mode, created_at],
    )
    return count


def _refresh_candidate_outcomes(conn: Any, config_hash: str) -> None:
    conn.execute(
        """
        UPDATE edge_candidate_snapshots AS candidates
        SET forward_return_3d_pct = labels.forward_return_3d_pct,
            forward_return_5d_pct = labels.forward_return_5d_pct,
            forward_return_10d_pct = labels.forward_return_10d_pct,
            forward_return_20d_pct = labels.forward_return_20d_pct,
            max_favorable_excursion_5d_pct = labels.max_favorable_excursion_5d_pct,
            max_adverse_excursion_5d_pct = labels.max_adverse_excursion_5d_pct,
            max_favorable_excursion_10d_pct = labels.max_favorable_excursion_10d_pct,
            max_adverse_excursion_10d_pct = labels.max_adverse_excursion_10d_pct,
            max_favorable_excursion_20d_pct = labels.max_favorable_excursion_20d_pct,
            max_adverse_excursion_20d_pct = labels.max_adverse_excursion_20d_pct,
            target_before_stop_5d_flag = labels.target_before_stop_5d_flag,
            target_before_stop_10d_flag = labels.target_before_stop_10d_flag,
            target_before_stop_20d_flag = labels.target_before_stop_20d_flag
        FROM source_db.symbol_day_forward_labels AS labels
        WHERE candidates.config_hash = ?
          AND labels.source_date = candidates.anchor_date
          AND labels.symbol = candidates.symbol
        """,
        [config_hash],
    )


def _build_horizon_summaries(conn: Any, config_hash: str) -> list[dict[str, Any]]:
    conn.execute("DROP TABLE IF EXISTS edge_horizon_backtest_summary")
    conn.execute(
        """
        CREATE TABLE edge_horizon_backtest_summary AS
        WITH rank_bands AS (
            SELECT * FROM (VALUES (10), (20), (50), (100)) AS bands(top_n)
        ), horizons AS (
            SELECT * FROM (VALUES (3), (5), (10), (20)) AS values_table(horizon_days)
        ), expanded AS (
            SELECT candidates.*, bands.top_n, horizons.horizon_days,
                CASE horizons.horizon_days
                    WHEN 3 THEN forward_return_3d_pct
                    WHEN 5 THEN forward_return_5d_pct
                    WHEN 10 THEN forward_return_10d_pct
                    WHEN 20 THEN forward_return_20d_pct
                END AS realized_return_pct
            FROM edge_candidate_snapshots AS candidates
            CROSS JOIN rank_bands AS bands
            CROSS JOIN horizons
            WHERE candidates.config_hash = ?
              AND candidates.historical_edge_rank <= bands.top_n
        )
        SELECT ? AS config_hash, top_n, horizon_days,
            COUNT(realized_return_pct) AS observation_count,
            COUNT(DISTINCT anchor_date) AS anchor_count,
            COUNT(DISTINCT symbol) AS symbol_count,
            AVG(realized_return_pct) AS avg_return_pct,
            MEDIAN(realized_return_pct) AS median_return_pct,
            AVG(CASE WHEN realized_return_pct > 0 THEN 1.0 ELSE 0.0 END) AS win_rate,
            QUANTILE_CONT(realized_return_pct, 0.10) AS return_p10_pct,
            QUANTILE_CONT(realized_return_pct, 0.90) AS return_p90_pct
        FROM expanded
        WHERE realized_return_pct IS NOT NULL
        GROUP BY top_n, horizon_days
        ORDER BY top_n, horizon_days
        """,
        [config_hash, config_hash],
    )
    return _rows(
        conn,
        "SELECT * FROM edge_horizon_backtest_summary WHERE config_hash = ? ORDER BY top_n, horizon_days",
        [config_hash],
    )


def simulate_historical_edge_execution(
    candidate_rows: Sequence[Mapping[str, Any]],
    *,
    rules: Sequence[
        HistoricalEdgeExecutionRule
    ] = DEFAULT_HISTORICAL_EDGE_EXECUTION_RULES,
    price_rows: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    anchors = sorted({str(row["anchor_date"]) for row in candidate_rows})
    by_anchor: dict[str, dict[str, Mapping[str, Any]]] = {}
    for row in candidate_rows:
        by_anchor.setdefault(str(row["anchor_date"]), {})[str(row["symbol"])] = row
    price_by_anchor_symbol = {
        (str(row["anchor_date"]), str(row["symbol"])): float(row["close_price"])
        for row in (price_rows or candidate_rows)
        if _safe_float(row.get("close_price")) is not None
    }
    trades: list[dict[str, Any]] = []
    for rule in rules:
        open_positions: dict[str, dict[str, Any]] = {}
        prior_ranks: dict[str, int] = {}
        for anchor_index, anchor in enumerate(anchors):
            current = by_anchor.get(anchor, {})
            for symbol, position in list(open_positions.items()):
                row = current.get(symbol)
                rank = _safe_int(row.get("historical_edge_rank")) if row else None
                close = price_by_anchor_symbol.get((anchor, symbol))
                age = anchor_index - int(position["entry_anchor_index"])
                exit_reason: str | None = None
                if rank is None or rank > rule.exit_rank:
                    exit_reason = "rank_exit"
                elif age >= rule.max_holding_observations:
                    exit_reason = "max_hold_exit"
                if exit_reason and close is not None:
                    entry_close = float(position["entry_close"])
                    trades.append(
                        {
                            "rule_id": rule.rule_id,
                            "symbol": symbol,
                            "entry_date": position["entry_date"],
                            "entry_close": entry_close,
                            "entry_rank": position["entry_rank"],
                            "exit_date": anchor,
                            "exit_close": close,
                            "exit_rank": rank if rank is not None else "",
                            "exit_reason": exit_reason,
                            "holding_anchor_observations": age,
                            "return_pct": round(_pct_return(close, entry_close), 4),
                        }
                    )
                    del open_positions[symbol]
            for symbol, row in current.items():
                if symbol in open_positions:
                    continue
                rank = _safe_int(row.get("historical_edge_rank"))
                close = _safe_float(row.get("close_price"))
                prior_rank = prior_ranks.get(symbol)
                fresh = prior_rank is None or prior_rank > rule.fresh_rank
                if (
                    rank is not None
                    and rank <= rule.entry_rank
                    and close is not None
                    and (not rule.require_fresh_entry or fresh)
                ):
                    open_positions[symbol] = {
                        "entry_date": anchor,
                        "entry_close": close,
                        "entry_rank": rank,
                        "entry_anchor_index": anchor_index,
                    }
            prior_ranks = {
                symbol: int(row["historical_edge_rank"])
                for symbol, row in current.items()
                if _safe_int(row.get("historical_edge_rank")) is not None
            }
        if anchors:
            final_anchor = anchors[-1]
            final_rows = by_anchor.get(final_anchor, {})
            for symbol, position in open_positions.items():
                row = final_rows.get(symbol)
                close = price_by_anchor_symbol.get((final_anchor, symbol))
                if close is None:
                    continue
                entry_close = float(position["entry_close"])
                trades.append(
                    {
                        "rule_id": rule.rule_id,
                        "symbol": symbol,
                        "entry_date": position["entry_date"],
                        "entry_close": entry_close,
                        "entry_rank": position["entry_rank"],
                        "exit_date": final_anchor,
                        "exit_close": close,
                        "exit_rank": row.get("historical_edge_rank", "") if row else "",
                        "exit_reason": "window_end",
                        "holding_anchor_observations": len(anchors)
                        - 1
                        - int(position["entry_anchor_index"]),
                        "return_pct": round(_pct_return(close, entry_close), 4),
                    }
                )
    summaries: list[dict[str, Any]] = []
    for rule in rules:
        selected = [row for row in trades if row["rule_id"] == rule.rule_id]
        returns = [float(row["return_pct"]) for row in selected]
        summaries.append(
            {
                "rule_id": rule.rule_id,
                "entry_rank": rule.entry_rank,
                "exit_rank": rule.exit_rank,
                "max_holding_observations": rule.max_holding_observations,
                "require_fresh_entry": int(rule.require_fresh_entry),
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
                "avg_holding_anchor_observations": (
                    round(
                        sum(
                            float(row["holding_anchor_observations"])
                            for row in selected
                        )
                        / len(selected),
                        2,
                    )
                    if selected
                    else ""
                ),
            }
        )
    return summaries, trades


def run_historical_edge_backtest(
    *,
    snapshot_database_path: str | Path,
    output_database_path: str | Path | None = None,
    anchor_mode: str = "twice_weekly",
    spacing_trading_days: int = 3,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
    top_n: int = 250,
    min_composite: float = 0.45,
    markets: Sequence[str] | None = None,
    incremental: bool = True,
    duckdb_threads: int = 16,
    memory_limit_gb: float = 24.0,
    artifact_output_dir: str | Path | None = None,
    export_candidate_csv: bool = True,
) -> dict[str, Any]:
    snapshot_path = Path(snapshot_database_path).resolve()
    if not snapshot_path.exists():
        raise FileNotFoundError(f"Snapshot database not found: {snapshot_path}")
    output_path = (
        Path(output_database_path)
        if output_database_path is not None
        else DEFAULT_HISTORICAL_EDGE_ROOT / "historical_edge_backtest.duckdb"
    ).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not incremental and output_path.exists():
        output_path.unlink()

    config = {
        "anchor_mode": anchor_mode,
        "spacing_trading_days": spacing_trading_days,
        "top_n": top_n,
        "min_composite": min_composite,
        "markets": sorted(str(value).lower() for value in (markets or ())),
        "setup_variants": [
            asdict(item) for item in DEFAULT_VOLATILITY_LIQUIDITY_SETUP_VARIANTS
        ],
        "score_version": "historical_edge_score_v1",
    }
    config_hash = _config_hash(config)
    created_at = datetime.now(tz=timezone.utc)
    refresh_id = (
        f"edge_refresh_{created_at.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    )
    duckdb = _import_duckdb()
    conn = duckdb.connect(output_path.as_posix())
    conn.execute(f"SET threads TO {int(duckdb_threads)}")
    conn.execute(f"SET memory_limit = '{float(memory_limit_gb):.1f}GB'")
    conn.execute(f"ATTACH {_q(snapshot_path)} AS source_db (READ_ONLY)")
    try:
        _initialize_metadata_tables(conn)
        available_dates = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT source_date FROM source_db.symbol_day_feature_snapshot ORDER BY source_date"
            ).fetchall()
        ]
        selected_dates = select_anchor_dates(
            available_dates,
            mode=anchor_mode,
            spacing_trading_days=spacing_trading_days,
            start_date=_parse_date(start_date),
            end_date=_parse_date(end_date),
        )
        existing_dates = {
            row[0]
            for row in conn.execute(
                "SELECT anchor_date FROM edge_anchor_plan WHERE config_hash = ? AND status = 'complete'",
                [config_hash],
            ).fetchall()
        }
        requested_dates = [
            value
            for value in selected_dates
            if not incremental or value not in existing_dates
        ]
        date_index = {value: index for index, value in enumerate(available_dates)}
        anchor_rows = [
            (
                value,
                (
                    available_dates[date_index[value] - 5]
                    if date_index[value] >= 5
                    else None
                ),
            )
            for value in requested_dates
        ]
        conn.execute(
            "CREATE TEMP TABLE requested_anchors(anchor_date DATE, maturity_cutoff_5d DATE)"
        )
        if anchor_rows:
            conn.executemany("INSERT INTO requested_anchors VALUES (?, ?)", anchor_rows)
            _create_daily_state(
                conn,
                max(requested_dates),
                markets=tuple(markets or ()),
            )
            _compute_candidates(conn, top_n=top_n, min_composite=min_composite)
            candidate_count = _persist_candidates(
                conn,
                config_hash=config_hash,
                anchor_mode=anchor_mode,
                created_at=created_at,
            )
        else:
            candidate_count = 0
        if bool(
            conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'edge_candidate_snapshots'"
            ).fetchone()[0]
        ):
            _refresh_candidate_outcomes(conn, config_hash)
            horizon_summaries = _build_horizon_summaries(conn, config_hash)
            candidate_rows = _rows(
                conn,
                "SELECT * FROM edge_candidate_snapshots WHERE config_hash = ? ORDER BY anchor_date, historical_edge_rank",
                [config_hash],
            )
            execution_price_rows = _rows(
                conn,
                """
                SELECT snapshot.source_date AS anchor_date, snapshot.symbol, snapshot.close_price
                FROM source_db.symbol_day_feature_snapshot AS snapshot
                WHERE snapshot.source_date IN (
                    SELECT anchor_date FROM edge_anchor_plan WHERE config_hash = ?
                )
                  AND snapshot.symbol IN (
                    SELECT DISTINCT symbol FROM edge_candidate_snapshots WHERE config_hash = ?
                )
                  AND snapshot.close_price IS NOT NULL
                """,
                [config_hash, config_hash],
            )
        else:
            horizon_summaries = []
            candidate_rows = []
            execution_price_rows = []
        execution_summaries, execution_trades = simulate_historical_edge_execution(
            candidate_rows,
            price_rows=execution_price_rows,
        )

        conn.execute(
            "DELETE FROM edge_historical_dataset_metadata WHERE config_hash = ?",
            [config_hash],
        )
        conn.execute(
            "INSERT INTO edge_historical_dataset_metadata VALUES (?, ?, ?, ?, ?)",
            [
                config_hash,
                json.dumps(config, sort_keys=True),
                created_at,
                created_at,
                snapshot_path.as_posix(),
            ],
        )
        conn.execute(
            "INSERT INTO edge_refresh_log VALUES (?, ?, ?, ?, ?, ?, ?, 'complete', ?)",
            [
                refresh_id,
                config_hash,
                created_at,
                "incremental" if incremental else "rebuild",
                len(selected_dates),
                len(requested_dates),
                candidate_count,
                (
                    "No new anchors"
                    if not requested_dates
                    else "Anchor candidates refreshed"
                ),
            ],
        )
        conn.execute("DROP TABLE IF EXISTS edge_execution_backtest_summary")
        conn.execute("DROP TABLE IF EXISTS edge_execution_backtest_trades")
        if execution_summaries:
            summary_csv_temp = output_path.with_suffix(".execution_summary.tmp.csv")
            _write_csv(summary_csv_temp, execution_summaries)
            conn.execute(
                f"CREATE TABLE edge_execution_backtest_summary AS SELECT * FROM read_csv_auto({_q(summary_csv_temp)}, HEADER=TRUE)"
            )
            summary_csv_temp.unlink(missing_ok=True)
        else:
            conn.execute(
                "CREATE TABLE edge_execution_backtest_summary(rule_id VARCHAR)"
            )
        if execution_trades:
            trades_csv_temp = output_path.with_suffix(".execution_trades.tmp.csv")
            _write_csv(trades_csv_temp, execution_trades)
            conn.execute(
                f"CREATE TABLE edge_execution_backtest_trades AS SELECT * FROM read_csv_auto({_q(trades_csv_temp)}, HEADER=TRUE)"
            )
            trades_csv_temp.unlink(missing_ok=True)
        else:
            conn.execute("CREATE TABLE edge_execution_backtest_trades(rule_id VARCHAR)")
    finally:
        conn.close()

    output_dir = (
        Path(artifact_output_dir).resolve()
        if artifact_output_dir is not None
        else output_path.parent
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    anchor_csv = output_dir / "edge_anchor_plan.csv"
    candidates_csv = output_dir / "edge_candidate_snapshots.csv"
    horizon_csv = output_dir / "edge_horizon_backtest_summary.csv"
    execution_summary_csv = output_dir / "edge_execution_backtest_summary.csv"
    execution_trades_csv = output_dir / "edge_execution_backtest_trades.csv"
    report_md = output_dir / "historical_edge_backtest_report.md"
    manifest_path = output_dir / "historical_edge_backtest_manifest.json"
    conn = duckdb.connect(output_path.as_posix(), read_only=True)
    try:
        anchor_output = _rows(
            conn,
            "SELECT * FROM edge_anchor_plan WHERE config_hash = ? ORDER BY anchor_date",
            [config_hash],
        )
        total_candidate_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM edge_candidate_snapshots WHERE config_hash = ?",
                [config_hash],
            ).fetchone()[0]
        )
        candidate_output = (
            _rows(
                conn,
                "SELECT * FROM edge_candidate_snapshots WHERE config_hash = ? ORDER BY anchor_date, historical_edge_rank",
                [config_hash],
            )
            if export_candidate_csv
            else []
        )
    finally:
        conn.close()
    _write_csv(anchor_csv, anchor_output)
    if export_candidate_csv:
        _write_csv(candidates_csv, candidate_output)
    _write_csv(horizon_csv, horizon_summaries)
    _write_csv(execution_summary_csv, execution_summaries)
    _write_csv(execution_trades_csv, execution_trades)

    lines = [
        "# Historical Edge Backtest",
        "",
        "## Scope",
        "",
        f"- source snapshot: `{snapshot_path.as_posix()}`",
        f"- persistent database: `{output_path.as_posix()}`",
        f"- anchor mode: `{anchor_mode}`",
        f"- selected anchors: {len(selected_dates)}",
        f"- newly processed anchors: {len(requested_dates)}",
        f"- candidate rows: {total_candidate_count}",
        f"- config hash: `{config_hash}`",
        "",
        "Historical setup statistics at each anchor use only label rows mature by that anchor. Current-anchor forward returns are attached only as outcomes and never enter the score.",
        "",
        "## Buy And Hold Rank Cohorts",
        "",
        "| Top N | Horizon | Observations | Win Rate | Average Return % | Median Return % | P10 % | P90 % |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in horizon_summaries:
        lines.append(
            f"| {row['top_n']} | {row['horizon_days']}d | {row['observation_count']} | "
            f"{round(float(row['win_rate']), 4)} | {round(float(row['avg_return_pct']), 4)} | "
            f"{round(float(row['median_return_pct']), 4)} | {round(float(row['return_p10_pct']), 4)} | "
            f"{round(float(row['return_p90_pct']), 4)} |"
        )
    lines.extend(
        [
            "",
            "## Rank Entry And Exit Rules",
            "",
            "| Rule | Trades | Symbols | Win Rate | Average Return % | Median Return % | Average Anchor Holds |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in execution_summaries:
        lines.append(
            f"| {row['rule_id']} | {row['trade_count']} | {row['symbol_count']} | {row['win_rate']} | "
            f"{row['avg_return_pct']} | {row['median_return_pct']} | {row['avg_holding_anchor_observations']} |"
        )
    lines.extend(
        [
            "",
            "## Persistence And Refresh",
            "",
            "Rerun with the same output database and `incremental=True`. Existing complete anchors are retained, only newly selected dates are scored, and outcomes for older candidates are refreshed from the expanded snapshot.",
            "A changed scoring or anchor configuration receives a different config hash and coexists in the same database.",
            "",
            "## Limits",
            "",
            "- Rank rules execute at selected anchor closes, not intraday.",
            "- Fixed 3/5/10/20-day cohorts use close-to-close labels and do not model fees, spread, slippage, taxes, FX, or capacity.",
            "- The historical score reconstructs edge setup and persistence only. Current safety and forward-valuation overlays are excluded because point-in-time historical copies are not guaranteed.",
            "- Window-end positions are marked separately and should not be compared blindly with completed rank exits.",
        ]
    )
    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = {
        "refresh_id": refresh_id,
        "config_hash": config_hash,
        "config": config,
        "snapshot_database_path": snapshot_path.as_posix(),
        "database_path": output_path.as_posix(),
        "anchor_count": len(anchor_output),
        "new_anchor_count": len(requested_dates),
        "candidate_count": total_candidate_count,
        "anchor_csv": anchor_csv.as_posix(),
        "candidates_csv": candidates_csv.as_posix() if export_candidate_csv else "",
        "horizon_summary_csv": horizon_csv.as_posix(),
        "execution_summary_csv": execution_summary_csv.as_posix(),
        "execution_trades_csv": execution_trades_csv.as_posix(),
        "report_md": report_md.as_posix(),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        **manifest,
        "manifest_path": manifest_path,
        "anchor_rows": anchor_output,
        "horizon_summaries": horizon_summaries,
        "execution_summaries": execution_summaries,
    }
