#!/usr/bin/env python3
"""Export top-N stock rankings from a whole-period tracking run.

Rankings follow advised conclusions in:
  pattern_analysis/advised_conclusions/close_forward_*_advised_conclusions.md

Outputs land in:
  {run_root}/predictor_stock_rankings/
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUN_ROOT = (
    PROJECT_ROOT
    / "logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/runs"
    / "scan_period_close_forward_tracking_25may_12jun2026_dd898100"
)
DEFAULT_DAILY_DB = (
    PROJECT_ROOT
    / "logs/tradingview_analysis/trading_view_all_fields_data/12_06_2026"
    / "tradingview_all_fields_12_06_2026.duckdb"
)
DEFAULT_DAILY_RUN_ID = "tradingview_all_fields_20260612_2008_utc_028e0f86"
DEFAULT_LATEST_SCORED_DAY = "11_06_2026"
MIN_MCAP = 500_000_000.0
MIN_CLOSE = 10.0
DEFAULT_TOP_N = 100
DEFAULT_PERFORMANCE_TARGET = "period_return_pct"

BULLISH_PREDICTORS = (
    "ATRP|1W",
    "ATRP",
    "ADRP|15",
    "ADRP|1W",
    "ADX-DI|1M",
    "ADX-DI_50|1M",
    "relative_volume",
)

BEARISH_WARNING_PREDICTORS = (
    "RSI21[1]|1M",
    "Stoch.K_14_1_3|1M",
    "W.R|1M",
    "Recommend.MA|1M",
    "oper_income_ttm",
    "ebitda_ttm",
)

# Advice buckets mirror close_forward_*_advised_conclusions.md playbooks.
ADVISED_PREDICTOR_ADVICE: dict[str, tuple[str, str]] = {
    "ATRP|1W": ("volatility_bullish", "Playbook A primary — rank high"),
    "ATRP": ("volatility_bullish", "Playbook A — rank high"),
    "ADRP|15": ("volatility_bullish", "Volatility tilt — rank high"),
    "ADRP|1W": ("volatility_bullish", "Volatility tilt — rank high"),
    "ADX-DI|1M": ("adx_pressure_bullish", "Playbook A optional — rank high"),
    "ADX-DI_50|1M": ("adx_pressure_bullish", "Confirmation filter — rank high"),
    "relative_volume": ("volatility_bullish", "Playbook A optional — rank high"),
    "RSI21[1]|1M": ("oversold_warning", "Playbook C — rank low (avoid oversold long)"),
    "Stoch.K_14_1_3|1M": ("oversold_warning", "Playbook C — bottom quintile avoid"),
    "W.R|1M": ("oversold_warning", "Playbook C — bottom quintile avoid"),
    "Recommend.MA|1M": ("crowded_consensus_warning", "Playbook C — rank low (bullish MA = headwind)"),
    "oper_income_ttm": ("mega_cap_warning", "Playbook C — rank low (size headwind)"),
    "ebitda_ttm": ("mega_cap_warning", "Playbook C — rank low (size headwind)"),
}

PREFERRED_MARKETS = (
    "america",
    "canada",
    "mexico",
    "austria",
    "belgium",
    "cyprus",
    "czech",
    "denmark",
    "estonia",
    "finland",
    "france",
    "germany",
    "greece",
    "hungary",
    "iceland",
    "ireland",
    "italy",
    "latvia",
    "lithuania",
    "luxembourg",
    "netherlands",
    "norway",
    "poland",
    "portugal",
    "romania",
    "slovakia",
    "spain",
    "sweden",
    "switzerland",
    "uk",
)


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _preferred_markets_values_sql() -> str:
    rows = ",\n                ".join(f"('{market}')" for market in PREFERRED_MARKETS)
    return f"VALUES {rows}"


def _attach(con: duckdb.DuckDBPyConnection, alias: str, path: Path) -> None:
    con.execute(
        f"ATTACH '{path.as_posix()}' AS {alias} (READ_ONLY)"
    )


def _require_path(path: Path, *, label: str) -> None:
    if path.exists():
        return
    raise FileNotFoundError(
        f"{label} not found at {path.as_posix()}. "
        "Run scan-period tracking again to generate period_total outputs."
    )


def _fetch_predictor_weights(
    con: duckdb.DuckDBPyConnection,
    predictor_fields: tuple[str, ...],
    performance_target: str,
) -> list[tuple[str, float, float]]:
    placeholders = ", ".join("?" for _ in predictor_fields)
    rows = con.execute(
        f"""
        SELECT predictor_field,
               rank_stability_score,
               ABS(rank_stability_score) AS weight
        FROM agg.cross_run_field_stability
        WHERE performance_field = ?
          AND predictor_field IN ({placeholders})
        ORDER BY ABS(rank_stability_score) DESC
        """,
        [performance_target, *list(predictor_fields)],
    ).fetchall()
    return [(str(field), float(rss), float(weight)) for field, rss, weight in rows]


def _build_enr_unpivot_sql(
    predictor_fields: list[str],
    *,
    performance_field: str,
) -> str:
    parts: list[str] = []
    for field in predictor_fields:
        col = _quote_identifier(field)
        escaped = field.replace("'", "''")
        parts.append(
            f"""
    SELECT run_id,
           symbol,
           '{escaped}' AS predictor_field,
           TRY_CAST({col} AS DOUBLE) AS pred_value,
           TRY_CAST({_quote_identifier(performance_field)} AS DOUBLE) AS period_ret
    FROM filtered
    WHERE TRY_CAST({col} AS DOUBLE) IS NOT NULL
      AND isfinite(TRY_CAST({col} AS DOUBLE))
            """.strip()
        )
    return "\n    UNION ALL\n".join(parts)


def _top_stable_predictors_sql(*, top_n: int, performance_target: str) -> str:
    return f"""
SELECT predictor_field,
       runs_seen,
       ROUND(sign_consistency_ratio, 3) AS sign_consistency,
       ROUND(median_pearson, 4) AS median_pearson,
       ROUND(mean_pearson, 4) AS mean_pearson,
       ROUND(median_quintile_spread, 2) AS median_q_spread_pp,
       ROUND(mean_quintile_spread, 2) AS mean_q_spread_pp,
       ROUND(rank_stability_score, 3) AS rank_stability_score,
       CASE
           WHEN rank_stability_score >= 0 THEN 'rank_high_in_universe'
           ELSE 'rank_low_in_universe'
       END AS scout_direction,
       CASE
           WHEN median_quintile_spread >= 0.4
                AND sign_consistency_ratio >= 0.65 THEN 'bullish_tilt'
           WHEN median_quintile_spread <= -0.3
                AND sign_consistency_ratio >= 0.65 THEN 'avoid_warning'
           WHEN ABS(median_quintile_spread) >= 0.3 THEN 'moderate_mixed'
           ELSE 'weak_sparse'
       END AS advice_bucket
FROM agg.cross_run_field_stability
WHERE performance_field = '{performance_target}'
  AND runs_seen >= 10
  AND sign_consistency_ratio >= 0.65
  AND predictor_field NOT ILIKE '%gap%'
  AND predictor_field NOT ILIKE '%change%'
  AND predictor_field NOT ILIKE '%Mom%'
  AND predictor_field NOT ILIKE '%ROC%'
  AND ABS(median_quintile_spread) >= 0.3
ORDER BY ABS(rank_stability_score) DESC NULLS LAST
LIMIT {top_n}
"""


def _advised_predictor_profile_sql(
    weights: list[tuple[str, float, float]],
    *,
    performance_target: str,
) -> str:
    rows = ",\n                ".join(
        f"('{field.replace(chr(39), chr(39)+chr(39))}', '{ADVISED_PREDICTOR_ADVICE.get(field, ('unknown', ''))[0]}', "
        f"'{ADVISED_PREDICTOR_ADVICE.get(field, ('', 'unknown'))[1].replace(chr(39), chr(39)+chr(39))}')"
        for field, _, _ in weights
    )
    return f"""
WITH advised_meta AS (
    SELECT predictor_field, advice_category, active_mgmt_note
    FROM (
            VALUES
                {rows}
        ) AS t(predictor_field, advice_category, active_mgmt_note)
),
stability AS (
    SELECT predictor_field,
           runs_seen,
           ROUND(sign_consistency_ratio, 3) AS sign_consistency,
           ROUND(median_pearson, 4) AS median_pearson,
           ROUND(median_quintile_spread, 2) AS median_q_spread_pp,
           ROUND(rank_stability_score, 3) AS rank_stability_score,
           CASE
               WHEN rank_stability_score >= 0 THEN 'rank_high_in_universe'
               ELSE 'rank_low_in_universe'
           END AS scout_direction
    FROM agg.cross_run_field_stability
    WHERE performance_field = '{performance_target}'
)
SELECT m.predictor_field,
       m.advice_category,
       m.active_mgmt_note,
       s.runs_seen,
       s.sign_consistency,
       s.median_pearson,
       s.median_q_spread_pp,
       s.rank_stability_score,
       s.scout_direction,
       ABS(s.rank_stability_score) AS composite_weight
FROM advised_meta m
    LEFT JOIN stability s USING (predictor_field)
ORDER BY ABS(COALESCE(s.rank_stability_score, 0)) DESC NULLS LAST,
    m.predictor_field
"""


def _quintile_fwd_perf_pooled_sql(
    predictor_fields: list[str],
    *,
    performance_field: str,
) -> str:
    return f"""
WITH filtered AS (
    SELECT a.run_id,
           a.symbol,
           a.{_quote_identifier(performance_field)},
           a.*
    FROM enr.all_fields_rows a
        INNER JOIN pr.period_boundary_returns pr USING (symbol)
    WHERE TRY_CAST(a.{_quote_identifier(performance_field)} AS DOUBLE) BETWEEN -100 AND 100
      AND pr.start_close_price >= {MIN_CLOSE}
),
long_vals AS (
{_build_enr_unpivot_sql(predictor_fields, performance_field=performance_field)}
),
quintiled AS (
    SELECT run_id,
           symbol,
           predictor_field,
           pred_value,
           period_ret,
           NTILE(5) OVER (
               PARTITION BY predictor_field
               ORDER BY pred_value
           ) AS pred_quintile
    FROM long_vals
    WHERE period_ret IS NOT NULL
),
pooled AS (
    SELECT predictor_field,
           pred_quintile,
           COUNT(*) AS n_symbols,
           COUNT(DISTINCT run_id) AS n_runs,
           ROUND(AVG(period_ret), 3) AS avg_period_ret_pct,
           ROUND(MEDIAN(period_ret), 3) AS median_period_ret_pct,
           ROUND(STDDEV(period_ret), 3) AS stdev_period_ret_pct,
           ROUND(AVG(CASE WHEN period_ret > 0 THEN 1.0 ELSE 0.0 END), 3) AS win_rate
    FROM quintiled
    GROUP BY predictor_field, pred_quintile
)
SELECT predictor_field,
       pred_quintile,
       n_symbols,
       n_runs,
       avg_period_ret_pct,
       median_period_ret_pct,
       stdev_period_ret_pct,
       win_rate
FROM pooled
ORDER BY predictor_field, pred_quintile
"""


def _quintile_spread_summary_sql(
    predictor_fields: list[str],
    *,
    performance_field: str,
) -> str:
    return f"""
WITH filtered AS (
    SELECT a.run_id,
           a.symbol,
           a.{_quote_identifier(performance_field)},
           a.*
    FROM enr.all_fields_rows a
        INNER JOIN pr.period_boundary_returns pr USING (symbol)
    WHERE TRY_CAST(a.{_quote_identifier(performance_field)} AS DOUBLE) BETWEEN -100 AND 100
      AND pr.start_close_price >= {MIN_CLOSE}
),
long_vals AS (
{_build_enr_unpivot_sql(predictor_fields, performance_field=performance_field)}
),
quintiled AS (
    SELECT run_id,
           symbol,
           predictor_field,
           pred_value,
           period_ret,
           NTILE(5) OVER (
               PARTITION BY predictor_field
               ORDER BY pred_value
           ) AS pred_quintile
    FROM long_vals
    WHERE period_ret IS NOT NULL
),
pooled AS (
    SELECT predictor_field,
           pred_quintile,
           ROUND(AVG(period_ret), 3) AS avg_period_ret_pct
    FROM quintiled
    GROUP BY predictor_field, pred_quintile
),
pooled_wide AS (
    SELECT predictor_field,
           MAX(CASE WHEN pred_quintile = 1 THEN avg_period_ret_pct END) AS q1_avg_period_ret_pct,
           MAX(CASE WHEN pred_quintile = 2 THEN avg_period_ret_pct END) AS q2_avg_period_ret_pct,
           MAX(CASE WHEN pred_quintile = 3 THEN avg_period_ret_pct END) AS q3_avg_period_ret_pct,
           MAX(CASE WHEN pred_quintile = 4 THEN avg_period_ret_pct END) AS q4_avg_period_ret_pct,
           MAX(CASE WHEN pred_quintile = 5 THEN avg_period_ret_pct END) AS q5_avg_period_ret_pct,
           MAX(CASE WHEN pred_quintile = 5 THEN avg_period_ret_pct END)
               - MAX(CASE WHEN pred_quintile = 1 THEN avg_period_ret_pct END) AS pooled_q5_minus_q1_pp
    FROM pooled
    GROUP BY predictor_field
)
SELECT pw.predictor_field,
       pw.q1_avg_period_ret_pct,
       pw.q2_avg_period_ret_pct,
       pw.q3_avg_period_ret_pct,
       pw.q4_avg_period_ret_pct,
       pw.q5_avg_period_ret_pct,
       pw.pooled_q5_minus_q1_pp,
       st.runs_seen AS aggregate_runs_seen,
       ROUND(st.sign_consistency_ratio, 3) AS aggregate_sign_consistency,
       ROUND(st.median_quintile_spread, 2) AS aggregate_median_q_spread_pp,
       ROUND(st.median_pearson, 4) AS aggregate_median_pearson,
       CASE
           WHEN st.rank_stability_score >= 0 THEN 'rank_high_in_universe'
           ELSE 'rank_low_in_universe'
       END AS scout_direction
FROM pooled_wide pw
    LEFT JOIN agg.cross_run_field_stability st
        ON st.predictor_field = pw.predictor_field
        AND st.performance_field = '{performance_field}'
ORDER BY ABS(COALESCE(st.rank_stability_score, pw.pooled_q5_minus_q1_pp)) DESC NULLS LAST,
    pw.predictor_field
"""


def _per_run_quintile_spread_sql() -> str:
    return f"""
SELECT predictor_field,
       runs_seen,
       ROUND(sign_consistency_ratio, 3) AS sign_consistency_ratio,
       ROUND(median_quintile_spread, 3) AS median_quintile_spread,
       ROUND(mean_quintile_spread, 3) AS mean_quintile_spread,
       ROUND(median_pearson, 4) AS median_pearson,
       ROUND(rank_stability_score, 4) AS rank_stability_score
FROM agg.cross_run_field_stability
WHERE performance_field = '{DEFAULT_PERFORMANCE_TARGET}'
ORDER BY ABS(rank_stability_score) DESC NULLS LAST,
    predictor_field
"""


def _active_management_candidates_sql(
    *,
    weights: list[tuple[str, float, float]],
    min_signals: int,
    daily_run_id: str,
    performance_field: str,
) -> str:
    escaped_run_id = daily_run_id.replace("'", "''")
    composite_sql = _bind_run_id(
        _composite_score_sql(weights=weights, min_signals=min_signals).strip(),
        escaped_run_id,
    )
    playbook_a_sql = _bind_run_id(_playbook_a_sql().strip(), escaped_run_id)
    warning_sql = _bind_run_id(_warning_overlay_sql().strip(), escaped_run_id)
    return f"""
WITH composite AS (
{composite_sql}
),
hist AS (
    SELECT symbol,
           COUNT(*) AS n_days,
           ROUND(AVG(ret), 2) AS avg_ret_pct,
           ROUND(AVG(CASE WHEN ret > 0 THEN 1.0 ELSE 0.0 END), 2) AS win_rate
    FROM (
            SELECT pr.symbol,
                   TRY_CAST(a.{_quote_identifier(performance_field)} AS DOUBLE) AS ret
            FROM pr.period_boundary_returns pr
                INNER JOIN enr.all_fields_rows a USING (symbol)
            WHERE TRY_CAST(a.market_cap_basic AS DOUBLE) >= {MIN_MCAP}
              AND TRY_CAST(a.{_quote_identifier(performance_field)} AS DOUBLE) BETWEEN -100 AND 100
              AND pr.start_close_price >= {MIN_CLOSE}
        ) x
    GROUP BY symbol
),
trim AS (
    SELECT symbol
    FROM hist
    WHERE n_days >= 8
      AND avg_ret_pct < -2
),
playbook_a AS (
    SELECT symbol
    FROM (
            {playbook_a_sql}
        ) pa
),
warnings AS (
    SELECT symbol,
           warning_flag_count
    FROM (
            {warning_sql}
        ) w
)
SELECT c.symbol,
       c.name,
       c.market,
       c.sector,
       c.close,
       c.mcap_b_usd,
       c.atrp_1w,
       c.relative_volume,
       c.close_fwd_fit_score,
       c.rank AS composite_rank,
       h.n_days,
       h.avg_ret_pct,
       h.win_rate,
       CASE WHEN pa.symbol IS NOT NULL THEN 1 ELSE 0 END AS playbook_a_fit,
       COALESCE(w.warning_flag_count, 0) AS warning_flag_count,
       CASE
           WHEN trim.symbol IS NOT NULL THEN 'trim_avoid'
           WHEN COALESCE(w.warning_flag_count, 0) >= 2 THEN 'warning_overlay'
           WHEN pa.symbol IS NOT NULL
                AND COALESCE(h.avg_ret_pct, 0) >= 1.5
                AND COALESCE(h.win_rate, 0) >= 0.60 THEN 'playbook_b_quality_drift'
           WHEN pa.symbol IS NOT NULL THEN 'playbook_a_vol_continuation'
           WHEN COALESCE(h.avg_ret_pct, 0) >= 1.5
                AND COALESCE(h.win_rate, 0) >= 0.60 THEN 'playbook_b_watch'
           ELSE 'composite_only'
       END AS active_mgmt_tier
FROM composite c
    LEFT JOIN hist h USING (symbol)
    LEFT JOIN playbook_a pa USING (symbol)
    LEFT JOIN warnings w USING (symbol)
    LEFT JOIN trim USING (symbol)
WHERE trim.symbol IS NULL
  AND COALESCE(w.warning_flag_count, 0) < 2
  AND (
      pa.symbol IS NOT NULL
      OR COALESCE(h.avg_ret_pct, 0) >= 1.5
  )
ORDER BY c.close_fwd_fit_score DESC,
    COALESCE(h.avg_ret_pct, -999) DESC
"""


def _build_long_vals_sql(predictor_fields: list[str], source_alias: str = "day") -> str:
    parts: list[str] = []
    for field in predictor_fields:
        col = _quote_identifier(field)
        parts.append(
            f"""
    SELECT symbol,
           '{field.replace("'", "''")}' AS predictor_field,
           TRY_CAST({source_alias}.{_quote_identifier(field)} AS DOUBLE) AS pred_value
    FROM run_rows_preferred {source_alias}
            """.strip()
        )
    return "\n    UNION ALL\n".join(parts)


def _build_pattern_weights_values(weights: list[tuple[str, float, float]]) -> str:
    rows = ",\n                ".join(
        f"('{field.replace(chr(39), chr(39)+chr(39))}', {rss})"
        for field, rss, _ in weights
    )
    return f"VALUES {rows}"


def _composite_score_sql(
    *,
    weights: list[tuple[str, float, float]],
    min_signals: int,
    source_alias: str = "day",
) -> str:
    predictor_fields = [field for field, _, _ in weights]
    return f"""
WITH preferred_market_codes AS (
    SELECT market_code
    FROM (
            {_preferred_markets_values_sql()}
        ) AS preferred_markets(market_code)
),
run_rows_preferred AS (
    SELECT r.*
    FROM day.all_fields_rows r
        INNER JOIN preferred_market_codes pm
            ON LOWER(TRIM(COALESCE(r.market, ''))) = pm.market_code
    WHERE r.run_id = ?
      AND TRY_CAST(r.market_cap_basic AS DOUBLE) >= {MIN_MCAP}
      AND TRY_CAST(r.close AS DOUBLE) >= {MIN_CLOSE}
),
pattern_weights AS (
    SELECT predictor_field,
           rank_stability_score AS rss,
           ABS(rank_stability_score) AS weight
    FROM (
            {_build_pattern_weights_values(weights)}
        ) AS t(predictor_field, rank_stability_score)
),
long_vals AS (
{_build_long_vals_sql(predictor_fields, source_alias="r")}
),
ranked AS (
    SELECT lv.symbol,
           lv.predictor_field,
           pw.rss,
           pw.weight,
           PERCENT_RANK() OVER (
               PARTITION BY lv.predictor_field
               ORDER BY lv.pred_value
           ) AS pred_pct_rank
    FROM long_vals lv
        INNER JOIN pattern_weights pw USING (predictor_field)
    WHERE lv.pred_value IS NOT NULL
      AND isfinite(lv.pred_value)
),
aligned AS (
    SELECT symbol,
           predictor_field,
           weight,
           CASE
               WHEN rss >= 0 THEN pred_pct_rank
               ELSE 1.0 - pred_pct_rank
           END AS aligned_rank
    FROM ranked
),
stock_scores AS (
    SELECT symbol,
           COUNT(*) AS signals_matched,
           SUM(weight) AS total_weight,
           SUM(weight * aligned_rank) / NULLIF(SUM(weight), 0) AS raw_score
    FROM aligned
    GROUP BY symbol
),
meta AS (
    SELECT symbol,
           name,
           market,
           country,
           sector,
           industry,
           TRY_CAST(close AS DOUBLE) AS close,
           TRY_CAST(market_cap_basic AS DOUBLE) AS market_cap_basic,
           TRY_CAST("ATRP|1W" AS DOUBLE) AS atrp_1w,
           TRY_CAST(relative_volume AS DOUBLE) AS relative_volume,
           TRY_CAST("W.R|1M" AS DOUBLE) AS wr_1m,
           TRY_CAST("Recommend.MA|1M" AS DOUBLE) AS rec_ma_1m
    FROM run_rows_preferred
)
SELECT m.symbol,
       m.name,
       m.market,
       m.country,
       m.sector,
       m.industry,
       ROUND(m.close, 4) AS close,
       ROUND(m.market_cap_basic / 1e9, 2) AS mcap_b_usd,
       ROUND(m.atrp_1w, 2) AS atrp_1w,
       ROUND(m.relative_volume, 2) AS relative_volume,
       ROUND(m.wr_1m, 1) AS wr_1m,
       ROUND(m.rec_ma_1m, 2) AS rec_ma_1m,
       s.signals_matched,
       ROUND(100.0 * s.raw_score, 2) AS close_fwd_fit_score,
       RANK() OVER (ORDER BY s.raw_score DESC) AS rank
FROM stock_scores s
    INNER JOIN meta m USING (symbol)
WHERE s.signals_matched >= {min_signals}
ORDER BY s.raw_score DESC
"""


def _playbook_a_sql() -> str:
    return f"""
WITH preferred_market_codes AS (
    SELECT market_code
    FROM (
            {_preferred_markets_values_sql()}
        ) AS preferred_markets(market_code)
),
base AS (
    SELECT r.symbol,
           r.name,
           r.market,
           r.country,
           r.sector,
           TRY_CAST(r.close AS DOUBLE) AS close,
           TRY_CAST(r.market_cap_basic AS DOUBLE) AS market_cap_basic,
           TRY_CAST(r."ATRP|1W" AS DOUBLE) AS atrp_1w,
           TRY_CAST(r.ATRP AS DOUBLE) AS atrp,
           TRY_CAST(r.relative_volume AS DOUBLE) AS relvol,
           TRY_CAST(r."ADX-DI|5" AS DOUBLE) AS adx_di5,
           TRY_CAST(r."Stoch.K_14_1_3|1M" AS DOUBLE) AS stoch_k_1m,
           TRY_CAST(r."Recommend.MA|1M" AS DOUBLE) AS rec_ma_1m
    FROM day.all_fields_rows r
        INNER JOIN preferred_market_codes pm
            ON LOWER(TRIM(COALESCE(r.market, ''))) = pm.market_code
    WHERE r.run_id = ?
      AND TRY_CAST(r.market_cap_basic AS DOUBLE) >= {MIN_MCAP}
      AND TRY_CAST(r.close AS DOUBLE) >= {MIN_CLOSE}
),
med AS (
    SELECT MEDIAN(atrp_1w) AS atrp_median
    FROM base
    WHERE atrp_1w IS NOT NULL
),
stoch_med AS (
    SELECT MEDIAN(stoch_k_1m) AS stoch_median
    FROM base
    WHERE stoch_k_1m IS NOT NULL
)
SELECT b.symbol,
       b.name,
       b.market,
       b.country,
       b.sector,
       ROUND(b.close, 2) AS close,
       ROUND(b.market_cap_basic / 1e9, 2) AS mcap_b_usd,
       ROUND(b.atrp_1w, 2) AS atrp_1w,
       ROUND(b.relvol, 2) AS relative_volume,
       ROUND(b.adx_di5, 1) AS adx_di5,
       ROUND(b.stoch_k_1m, 1) AS stoch_k_1m,
       ROUND(b.rec_ma_1m, 2) AS rec_ma_1m,
       RANK() OVER (
           ORDER BY b.atrp_1w DESC,
               b.relvol DESC,
               b.adx_di5 DESC NULLS LAST
       ) AS rank
FROM base b
    CROSS JOIN med
    CROSS JOIN stoch_med sm
WHERE b.atrp_1w > med.atrp_median
  AND b.relvol > 1.5
  AND (
      b.stoch_k_1m IS NULL
      OR b.stoch_k_1m > sm.stoch_median
  )
ORDER BY b.atrp_1w DESC,
    b.relvol DESC,
    b.adx_di5 DESC NULLS LAST
"""


def _playbook_b_sql() -> str:
    return f"""
WITH joined AS (
    SELECT pr.symbol,
           TRY_CAST(a.{_quote_identifier(DEFAULT_PERFORMANCE_TARGET)} AS DOUBLE) AS ret
    FROM pr.period_boundary_returns pr
        INNER JOIN enr.all_fields_rows a USING (symbol)
    WHERE TRY_CAST(a.market_cap_basic AS DOUBLE) >= {MIN_MCAP}
      AND TRY_CAST(a.{_quote_identifier(DEFAULT_PERFORMANCE_TARGET)} AS DOUBLE) BETWEEN -100 AND 100
      AND pr.start_close_price >= {MIN_CLOSE}
)
SELECT symbol,
       COUNT(*) AS n_days,
       ROUND(AVG(ret), 2) AS avg_period_ret_pct,
       ROUND(STDDEV(ret), 2) AS stdev_pct,
       ROUND(AVG(CASE WHEN ret > 0 THEN 1.0 ELSE 0 END), 2) AS win_rate,
       RANK() OVER (ORDER BY AVG(ret) DESC) AS rank
FROM joined
GROUP BY symbol
HAVING COUNT(*) >= 10
   AND AVG(ret) > 1.5
   AND AVG(CASE WHEN ret > 0 THEN 1.0 ELSE 0 END) >= 0.60
ORDER BY avg_period_ret_pct DESC
"""


def _trim_list_sql() -> str:
    return f"""
WITH joined AS (
    SELECT pr.symbol,
           TRY_CAST(a.{_quote_identifier(DEFAULT_PERFORMANCE_TARGET)} AS DOUBLE) AS ret
    FROM pr.period_boundary_returns pr
        INNER JOIN enr.all_fields_rows a USING (symbol)
    WHERE TRY_CAST(a.market_cap_basic AS DOUBLE) >= {MIN_MCAP}
      AND TRY_CAST(a.{_quote_identifier(DEFAULT_PERFORMANCE_TARGET)} AS DOUBLE) BETWEEN -100 AND 100
      AND pr.start_close_price >= {MIN_CLOSE}
)
SELECT symbol,
       COUNT(*) AS n_days,
       ROUND(AVG(ret), 2) AS avg_period_ret_pct,
       ROUND(STDDEV(ret), 2) AS stdev_pct,
       ROUND(AVG(CASE WHEN ret > 0 THEN 1.0 ELSE 0 END), 2) AS win_rate,
       RANK() OVER (ORDER BY AVG(ret) ASC) AS rank
FROM joined
GROUP BY symbol
HAVING COUNT(*) >= 8
   AND AVG(ret) < -2
ORDER BY avg_period_ret_pct ASC
"""


def _warning_overlay_sql() -> str:
    return f"""
WITH preferred_market_codes AS (
    SELECT market_code
    FROM (
            {_preferred_markets_values_sql()}
        ) AS preferred_markets(market_code)
),
base AS (
    SELECT r.symbol,
           r.name,
           r.market,
           TRY_CAST(r.close AS DOUBLE) AS close,
           TRY_CAST(r."Stoch.K_14_1_3|1M" AS DOUBLE) AS stoch_k_1m,
           TRY_CAST(r."W.R|1M" AS DOUBLE) AS wr_1m,
           TRY_CAST(r."Recommend.MA|1M" AS DOUBLE) AS rec_ma_1m,
           TRY_CAST(r.oper_income_ttm AS DOUBLE) AS oper_income_ttm,
           TRY_CAST(r.ebitda_ttm AS DOUBLE) AS ebitda_ttm
    FROM day.all_fields_rows r
        INNER JOIN preferred_market_codes pm
            ON LOWER(TRIM(COALESCE(r.market, ''))) = pm.market_code
    WHERE r.run_id = ?
      AND TRY_CAST(r.market_cap_basic AS DOUBLE) >= {MIN_MCAP}
      AND TRY_CAST(r.close AS DOUBLE) >= {MIN_CLOSE}
),
hist AS (
    SELECT symbol,
           ROUND(AVG(ret), 2) AS avg_ret_pct
    FROM (
            SELECT pr.symbol,
                   TRY_CAST(a.{_quote_identifier(DEFAULT_PERFORMANCE_TARGET)} AS DOUBLE) AS ret
            FROM pr.period_boundary_returns pr
                INNER JOIN enr.all_fields_rows a USING (symbol)
            WHERE TRY_CAST(a.market_cap_basic AS DOUBLE) >= {MIN_MCAP}
              AND TRY_CAST(a.{_quote_identifier(DEFAULT_PERFORMANCE_TARGET)} AS DOUBLE) BETWEEN -100 AND 100
              AND pr.start_close_price >= {MIN_CLOSE}
        ) x
    GROUP BY symbol
    HAVING COUNT(*) >= 5
),
quintiles AS (
    SELECT symbol,
           NTILE(5) OVER (ORDER BY stoch_k_1m) AS stoch_q,
           NTILE(5) OVER (ORDER BY wr_1m) AS wr_q,
           NTILE(5) OVER (ORDER BY rec_ma_1m) AS rec_ma_q,
           NTILE(5) OVER (ORDER BY oper_income_ttm) AS oper_q,
           NTILE(5) OVER (ORDER BY ebitda_ttm) AS ebitda_q,
           stoch_k_1m,
           wr_1m,
           rec_ma_1m,
           oper_income_ttm,
           ebitda_ttm,
           name,
           market,
           close
    FROM base
)
SELECT q.symbol,
       q.name,
       q.market,
       ROUND(q.close, 2) AS close,
       h.avg_ret_pct,
       q.stoch_k_1m,
       q.wr_1m,
       q.rec_ma_1m,
       (
           CASE WHEN q.stoch_q = 1 THEN 1 ELSE 0 END
           + CASE WHEN q.wr_q = 1 THEN 1 ELSE 0 END
           + CASE WHEN q.rec_ma_q = 5 AND COALESCE(h.avg_ret_pct, 0) < 0 THEN 1 ELSE 0 END
           + CASE WHEN q.oper_q = 5 THEN 1 ELSE 0 END
           + CASE WHEN q.ebitda_q = 5 THEN 1 ELSE 0 END
       ) AS warning_flag_count,
       RANK() OVER (
           ORDER BY (
               CASE WHEN q.stoch_q = 1 THEN 1 ELSE 0 END
               + CASE WHEN q.wr_q = 1 THEN 1 ELSE 0 END
               + CASE WHEN q.rec_ma_q = 5 AND COALESCE(h.avg_ret_pct, 0) < 0 THEN 1 ELSE 0 END
               + CASE WHEN q.oper_q = 5 THEN 1 ELSE 0 END
               + CASE WHEN q.ebitda_q = 5 THEN 1 ELSE 0 END
           ) DESC,
           COALESCE(h.avg_ret_pct, 999) ASC
       ) AS rank
FROM quintiles q
    LEFT JOIN hist h USING (symbol)
WHERE (
    CASE WHEN q.stoch_q = 1 THEN 1 ELSE 0 END
    + CASE WHEN q.wr_q = 1 THEN 1 ELSE 0 END
    + CASE WHEN q.rec_ma_q = 5 AND COALESCE(h.avg_ret_pct, 0) < 0 THEN 1 ELSE 0 END
    + CASE WHEN q.oper_q = 5 THEN 1 ELSE 0 END
    + CASE WHEN q.ebitda_q = 5 THEN 1 ELSE 0 END
) >= 2
ORDER BY warning_flag_count DESC,
    COALESCE(h.avg_ret_pct, 999) ASC
"""


def _single_predictor_sql(field: str, *, bullish: bool) -> str:
    col = _quote_identifier(field)
    order = "DESC" if bullish else "ASC"
    return f"""
WITH preferred_market_codes AS (
    SELECT market_code
    FROM (
            {_preferred_markets_values_sql()}
        ) AS preferred_markets(market_code)
),
base AS (
    SELECT r.symbol,
           r.name,
           r.market,
           r.country,
           r.sector,
           TRY_CAST(r.close AS DOUBLE) AS close,
           TRY_CAST(r.market_cap_basic AS DOUBLE) AS market_cap_basic,
           TRY_CAST(r.{col} AS DOUBLE) AS predictor_value,
           PERCENT_RANK() OVER (
               ORDER BY TRY_CAST(r.{col} AS DOUBLE)
           ) AS predictor_pct_rank
    FROM day.all_fields_rows r
        INNER JOIN preferred_market_codes pm
            ON LOWER(TRIM(COALESCE(r.market, ''))) = pm.market_code
    WHERE r.run_id = ?
      AND TRY_CAST(r.market_cap_basic AS DOUBLE) >= {MIN_MCAP}
      AND TRY_CAST(r.close AS DOUBLE) >= {MIN_CLOSE}
      AND TRY_CAST(r.{col} AS DOUBLE) IS NOT NULL
      AND isfinite(TRY_CAST(r.{col} AS DOUBLE))
)
SELECT symbol,
       name,
       market,
       country,
       sector,
       ROUND(close, 2) AS close,
       ROUND(market_cap_basic / 1e9, 2) AS mcap_b_usd,
       ROUND(predictor_value, 4) AS predictor_value,
       ROUND(100 * predictor_pct_rank, 1) AS pct_in_universe,
       RANK() OVER (ORDER BY predictor_value {order}) AS rank
FROM base
ORDER BY predictor_value {order}
"""


def _playbook_a_realized_sql(source_day_label: str) -> str:
    return f"""
WITH base AS (
    SELECT pr.symbol,
           pr.start_close_price AS close_price,
           TRY_CAST(a.{_quote_identifier(DEFAULT_PERFORMANCE_TARGET)} AS DOUBLE) AS ret,
           TRY_CAST(a."ATRP|1W" AS DOUBLE) AS atrp_1w,
           TRY_CAST(a.relative_volume AS DOUBLE) AS relvol,
           TRY_CAST(a."ADX-DI|5" AS DOUBLE) AS adx_di5
    FROM pr.period_boundary_returns pr
        INNER JOIN enr.all_fields_rows a USING (symbol)
    WHERE pr.start_day_label = '{source_day_label}'
      AND TRY_CAST(a.market_cap_basic AS DOUBLE) >= {MIN_MCAP}
      AND pr.start_close_price >= {MIN_CLOSE}
),
med AS (SELECT MEDIAN(atrp_1w) AS m FROM base)
SELECT b.symbol,
       ROUND(b.close_price, 2) AS close_px,
       ROUND(b.ret, 2) AS realized_period_ret_pct,
       ROUND(b.atrp_1w, 2) AS atrp_1w,
       ROUND(b.relvol, 2) AS relative_volume,
       ROUND(b.adx_di5, 1) AS adx_di5,
       RANK() OVER (ORDER BY b.ret DESC) AS rank
FROM base b,
    med
WHERE b.atrp_1w > med.m
  AND b.relvol > 1.5
ORDER BY b.ret DESC
"""


def _bind_run_id(sql: str, escaped_run_id: str) -> str:
    return sql.replace("?", f"'{escaped_run_id}'")


def _query_all(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    params: list | None = None,
) -> tuple[list[str], list[tuple]]:
    result = con.execute(sql, list(params or []))
    columns = [col[0] for col in result.description]
    rows = result.fetchall()
    return columns, rows


def _write_csv(path: Path, rows: list[tuple], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)


def _safe_csv_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower() or "unknown"


def _query_limited(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    params: list | None,
    top_n: int,
) -> tuple[list[str], list[tuple]]:
    bound = list(params or [])
    bound.append(top_n)
    result = con.execute(f"SELECT * FROM ({sql}) q LIMIT ?", bound)
    columns = [col[0] for col in result.description]
    rows = result.fetchall()
    return columns, rows


def _write_readme(
    path: Path,
    *,
    run_root: Path,
    daily_db: Path,
    daily_run_id: str,
    performance_target: str,
    outputs: list[str],
) -> None:
    target_slug = _safe_csv_name(performance_target)
    top_stable_csv = f"00_top_stable_{target_slug}_predictors.csv"
    quintile_perf_csv = f"00_quintile_perf_by_advised_predictor_{target_slug}.csv"
    quintile_spread_csv = f"00_quintile_spread_by_advised_predictor_{target_slug}.csv"
    rolling_spread_csv = (
        f"00_rolling_stability_by_advised_predictor_{target_slug}.csv"
    )
    text = f"""# Predictor stock rankings — dd898100

Generated by `scripts/run_close_forward_predictor_stock_rankings.py`.

## Which DuckDB to connect

| Question | Target database | Alias / table |
|----------|-----------------|---------------|
| Cross-run predictor stability (which fields matter) | `{run_root.as_posix()}/aggregates/all_fields_pattern_aggregate.duckdb` | `cross_run_field_stability`, `per_run_patterns` |
| Whole-period boundary returns (start/end close) | `{run_root.as_posix()}/period_total/period_returns/period_boundary_returns.duckdb` | `period_boundary_returns` |
| Enriched period rows joined to performance labels | `{run_root.as_posix()}/period_total/period_analysis_input.duckdb` | `all_fields_rows` (join on `symbol`) |
| **Latest positioning (12 Jun — no forward label yet)** | `{daily_db.as_posix()}` | `all_fields_rows` where `run_id = '{daily_run_id}'` |
| Realized Playbook A on a scored day | Attach `period_boundary_returns` + `period_analysis_input` | filter `start_day_label = '11_06_2026'` |

**Do not** use `period_analysis_input.duckdb` alone for live scouting — it is a pooled period snapshot.
For current screens use the **12 Jun daily DuckDB**. For drift / trim lists use **period_boundary_returns**.

Unlimited SQL replays live in:
`sql_connections_space/tradingview_all_fields_pattern_analysis.session.sql`
section `[PERIOD DD898100]` (remove `LIMIT` clauses as needed).

## Indicator / correlation CSVs (`indicator_perf/`)

Use these to chart top predictors and verify quintile period-return behavior:

- `{top_stable_csv}` — ranked stable fields (no gap/change noise)
- `00_advised_active_manager_predictor_profile.csv` — composite weights + Playbook A/B/C advice tags
- `{quintile_perf_csv}` — pooled average period return by quintile (long format; pivot in Excel)
- `{quintile_spread_csv}` — Q5−Q1 spread vs aggregate stability metrics
- `{rolling_spread_csv}` — rolling-window stability spread summary

Performance target used by this run: `{performance_target}`.

Active-management stock overlay: `07_active_management_candidates_top*.csv`
(composite fit + Playbook A/B filters, excluding trim list and warning overlay).

## Output files

{chr(10).join(f'- `{name}`' for name in outputs)}

## Re-run

```bash
python scripts/run_close_forward_predictor_stock_rankings.py --top-n 100
```
"""
    path.write_text(text, encoding="utf-8")


def run_rankings(
    *,
    run_root: Path,
    daily_db: Path,
    daily_run_id: str,
    latest_scored_day: str,
    top_n: int,
    output_dir: Path | None = None,
) -> dict[str, Path]:
    out_dir = output_dir or (run_root / "predictor_stock_rankings")
    by_predictor_dir = out_dir / "by_predictor"
    out_dir.mkdir(parents=True, exist_ok=True)
    by_predictor_dir.mkdir(parents=True, exist_ok=True)

    tracking_json_path = run_root / "_scan_period_close_forward_tracking.json"
    tracking_payload = {}
    if tracking_json_path.exists():
        tracking_payload = json.loads(tracking_json_path.read_text(encoding="utf-8"))
    performance_target = str(
        tracking_payload.get("performance_target") or DEFAULT_PERFORMANCE_TARGET
    )
    period_total_root = run_root / "period_total"
    agg_path = run_root / "aggregates/all_fields_pattern_aggregate.duckdb"
    period_total_result = tracking_payload.get("period_total_result") or {}
    period_input_result = period_total_result.get("period_input_result") or {}
    period_returns_result = period_total_result.get("period_returns_result") or {}
    enr_path = Path(
        period_input_result.get("database_path")
        or period_total_root / "period_analysis_input.duckdb"
    )
    pr_path = Path(
        period_returns_result.get("database_path")
        or period_total_root / "period_returns" / "period_boundary_returns.duckdb"
    )

    _require_path(agg_path, label="Aggregate stability database")
    _require_path(enr_path, label="Period analysis input database")
    _require_path(pr_path, label="Period boundary returns database")
    _require_path(daily_db, label="Daily all-fields database")

    con = duckdb.connect()
    _attach(con, "agg", agg_path)
    _attach(con, "enr", enr_path)
    _attach(con, "pr", pr_path)
    _attach(con, "day", daily_db)

    all_predictors = BULLISH_PREDICTORS + BEARISH_WARNING_PREDICTORS
    weights = _fetch_predictor_weights(con, all_predictors, performance_target)
    if not weights:
        raise RuntimeError("No predictor weights found in aggregate DB.")

    min_signals = max(3, len(weights) // 2)
    outputs: dict[str, Path] = {}
    indicator_dir = out_dir / "indicator_perf"
    indicator_dir.mkdir(parents=True, exist_ok=True)

    columns, rows = _query_all(
        con,
        _top_stable_predictors_sql(
            top_n=max(top_n, 60), performance_target=performance_target
        ),
        None,
    )
    path = indicator_dir / f"00_top_stable_{_safe_csv_name(performance_target)}_predictors.csv"
    _write_csv(path, rows, columns)
    outputs["top_stable_predictors"] = path

    columns, rows = _query_all(
        con,
        _advised_predictor_profile_sql(
            weights, performance_target=performance_target
        ),
        None,
    )
    path = indicator_dir / "00_advised_active_manager_predictor_profile.csv"
    _write_csv(path, rows, columns)
    outputs["advised_predictor_profile"] = path

    advised_fields = [field for field, _, _ in weights]
    columns, rows = _query_all(
        con,
        _quintile_fwd_perf_pooled_sql(
            advised_fields,
            performance_field=performance_target,
        ),
        None,
    )
    path = indicator_dir / f"00_quintile_perf_by_advised_predictor_{_safe_csv_name(performance_target)}.csv"
    _write_csv(path, rows, columns)
    outputs["quintile_fwd_perf"] = path

    columns, rows = _query_all(
        con,
        _quintile_spread_summary_sql(
            advised_fields,
            performance_field=performance_target,
        ),
        None,
    )
    path = indicator_dir / f"00_quintile_spread_by_advised_predictor_{_safe_csv_name(performance_target)}.csv"
    _write_csv(path, rows, columns)
    outputs["quintile_spread_summary"] = path

    columns, rows = _query_all(con, _per_run_quintile_spread_sql(), None)
    path = indicator_dir / f"00_rolling_stability_by_advised_predictor_{_safe_csv_name(performance_target)}.csv"
    _write_csv(path, rows, columns)
    outputs["per_run_quintile_spread"] = path

    composite_sql = _composite_score_sql(
        weights=weights,
        min_signals=min_signals,
    )
    columns, rows = _query_limited(
        con, composite_sql, [daily_run_id], top_n
    )
    path = out_dir / f"01_composite_bullish_fit_top{top_n}.csv"
    _write_csv(path, rows, columns)
    outputs["composite"] = path

    columns, rows = _query_limited(con, _playbook_a_sql(), [daily_run_id], top_n)
    path = out_dir / f"02_playbook_a_volatility_continuation_top{top_n}.csv"
    _write_csv(path, rows, columns)
    outputs["playbook_a"] = path

    columns, rows = _query_limited(con, _playbook_b_sql(), None, top_n)
    path = out_dir / f"03_playbook_b_quality_drift_top{top_n}.csv"
    _write_csv(path, rows, columns)
    outputs["playbook_b"] = path

    columns, rows = _query_limited(con, _trim_list_sql(), None, top_n)
    path = out_dir / f"04_trim_negative_drift_top{top_n}.csv"
    _write_csv(path, rows, columns)
    outputs["trim"] = path

    columns, rows = _query_limited(con, _warning_overlay_sql(), [daily_run_id], top_n)
    path = out_dir / f"05_warning_overlay_avoid_top{top_n}.csv"
    _write_csv(path, rows, columns)
    outputs["warning"] = path

    columns, rows = _query_limited(
        con, _playbook_a_realized_sql(latest_scored_day), None, top_n
    )
    path = out_dir / f"06_playbook_a_realized_{latest_scored_day}_top{top_n}.csv"
    _write_csv(path, rows, columns)
    outputs["playbook_a_realized"] = path

    columns, rows = _query_limited(
        con,
        _active_management_candidates_sql(
            weights=weights,
            min_signals=min_signals,
            daily_run_id=daily_run_id,
            performance_field=performance_target,
        ),
        None,
        top_n,
    )
    path = out_dir / f"07_active_management_candidates_top{top_n}.csv"
    _write_csv(path, rows, columns)
    outputs["active_management"] = path

    for field in BULLISH_PREDICTORS:
        columns, rows = _query_limited(
            con, _single_predictor_sql(field, bullish=True), [daily_run_id], top_n
        )
        path = by_predictor_dir / f"bullish_{_slug(field)}_top{top_n}.csv"
        _write_csv(path, rows, columns)
        outputs[f"bullish_{field}"] = path

    for field in BEARISH_WARNING_PREDICTORS:
        columns, rows = _query_limited(
            con,
            _single_predictor_sql(field, bullish=False),
            [daily_run_id],
            top_n,
        )
        path = by_predictor_dir / f"warning_{_slug(field)}_top{top_n}.csv"
        _write_csv(path, rows, columns)
        outputs[f"warning_{field}"] = path

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_root": run_root.as_posix(),
        "daily_db": daily_db.as_posix(),
        "daily_run_id": daily_run_id,
        "latest_scored_day": latest_scored_day,
        "top_n": top_n,
        "predictor_weights": [
            {"field": field, "rank_stability_score": rss, "weight": weight}
            for field, rss, weight in weights
        ],
        "outputs": {key: value.as_posix() for key, value in outputs.items()},
    }
    manifest_path = out_dir / "_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    _write_readme(
        out_dir / "_README.md",
        run_root=run_root,
        daily_db=daily_db,
        daily_run_id=daily_run_id,
        performance_target=performance_target,
        outputs=[p.name for p in outputs.values()] + ["_manifest.json"],
    )

    con.close()
    return outputs


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export period-total predictor stock rankings for a tracking run."
    )
    parser.add_argument(
        "--run-root",
        type=Path,
        default=DEFAULT_RUN_ROOT,
        help="scan_period_close_forward_tracking run folder",
    )
    parser.add_argument(
        "--daily-db",
        type=Path,
        default=DEFAULT_DAILY_DB,
        help="Latest daily all-fields DuckDB for positioning screens",
    )
    parser.add_argument(
        "--daily-run-id",
        default=DEFAULT_DAILY_RUN_ID,
        help="run_id inside the daily DuckDB",
    )
    parser.add_argument(
        "--latest-scored-day",
        default=DEFAULT_LATEST_SCORED_DAY,
        help="Last source_day_label with realized period labels",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=DEFAULT_TOP_N,
        help="Row limit per ranking export",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Defaults to {run_root}/predictor_stock_rankings",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    outputs = run_rankings(
        run_root=args.run_root,
        daily_db=args.daily_db,
        daily_run_id=args.daily_run_id,
        latest_scored_day=args.latest_scored_day,
        top_n=args.top_n,
        output_dir=args.output_dir,
    )
    print(f"Wrote {len(outputs)} ranking files to {args.output_dir or args.run_root / 'predictor_stock_rankings'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
