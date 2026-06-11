-- Move-prediction pattern discovery session
-- Join prediction DB (A), all-fields DB (B), and history DB (C).
-- Python runner: trading_view_move_prediction_pattern_discovery.py

-- @block q_pattern_join_preview
-- Latest-run leaders joined to all-fields confirmation columns.
ATTACH '<ALL_FIELDS_DB>' AS fields (READ_ONLY);

WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT h.symbol,
    h.company,
    h.profile_name,
    h.horizon_name,
    h.score,
    h.risk_adjusted_score,
    h.manager_action_signal,
    a."ChaikinMoneyFlow",
    a."BBPower",
    a."DonchCh20.Upper",
    a."DonchCh20.Lower",
    a."P.SAR",
    a."HullMA9",
    a."RSI",
    a."Stoch.RSI.K",
    a."total_debt_to_ebitda_fq"
FROM profile_horizon_scores h
JOIN latest_run lr ON h.run_id = lr.run_id
LEFT JOIN fields.all_fields_rows a
    ON (
        h.symbol = a.symbol
        OR h.symbol LIKE '%:' || a.symbol
        OR a.symbol LIKE '%:' || h.symbol
    )
WHERE h.profile_name = 'breakout_long'
  AND h.horizon_name = 'weeks'
ORDER BY h.risk_adjusted_score DESC NULLS LAST
LIMIT 50;

-- @block q_pattern_cohort_field_compare
-- Top-decile vs bottom-decile field comparison for a profile cohort.
ATTACH '<ALL_FIELDS_DB>' AS fields (READ_ONLY);

WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
),
leaders AS (
    SELECT h.symbol,
        h.score,
        NTILE(10) OVER (ORDER BY h.score DESC) AS score_decile
    FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    WHERE h.profile_name = 'breakout_long'
      AND h.horizon_name = 'weeks'
      AND h.score IS NOT NULL
),
cohort AS (
    SELECT CASE
            WHEN score_decile = 1 THEN 'top_decile'
            WHEN score_decile = 10 THEN 'bottom_decile'
        END AS cohort_label,
        l.symbol,
        l.score,
        TRY_CAST(a."ChaikinMoneyFlow" AS DOUBLE) AS field_value
    FROM leaders l
    LEFT JOIN fields.all_fields_rows a
        ON (
            l.symbol = a.symbol
            OR l.symbol LIKE '%:' || a.symbol
            OR a.symbol LIKE '%:' || l.symbol
        )
    WHERE cohort_label IS NOT NULL
)
SELECT cohort_label,
    COUNT(*) AS symbol_count,
    AVG(score) AS avg_profile_score,
    AVG(field_value) AS avg_field_value,
    MEDIAN(field_value) AS median_field_value
FROM cohort
GROUP BY cohort_label
ORDER BY cohort_label;

-- @block q_pattern_false_positive_autopsy
-- Score improved but price fell — joined to raw downside signatures.
ATTACH '<HISTORY_DB>' AS hist (READ_ONLY);
ATTACH '<ALL_FIELDS_DB>' AS fields (READ_ONLY);

WITH false_pos AS (
    SELECT profile_name,
        symbol,
        score_delta_total,
        close_return_pct_total
    FROM hist.vw_profile_horizon_progression_core
    WHERE score_delta_total > 0
      AND close_return_pct_total < 0
),
latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
)
SELECT fp.profile_name,
    fp.symbol,
    fp.score_delta_total,
    fp.close_return_pct_total,
    a."RSI",
    a."ChaikinMoneyFlow",
    a."BBPower",
    a."total_debt_to_ebitda_fq",
    a."price_target_high",
    a."price_target_low"
FROM false_pos fp
LEFT JOIN fields.all_fields_rows a
    ON (
        fp.symbol = a.symbol
        OR fp.symbol LIKE '%:' || a.symbol
        OR a.symbol LIKE '%:' || fp.symbol
    )
ORDER BY fp.score_delta_total DESC
LIMIT 100;

-- @block q_pattern_calibration_gates
-- Promotion gates from history alignment view.
ATTACH '<HISTORY_DB>' AS hist (READ_ONLY);

SELECT profile_name,
    horizon_name,
    symbol_count,
    aligned_positive_count,
    false_positive_count,
    ROUND(
        aligned_positive_count * 1.0 / NULLIF(symbol_count, 0),
        4
    ) AS aligned_positive_ratio,
    ROUND(score_price_corr, 4) AS score_price_corr,
    CASE
        WHEN aligned_positive_count * 1.0 / NULLIF(symbol_count, 0) > 0.55
            AND score_price_corr > 0.15 THEN 'pass'
        ELSE 'observation'
    END AS calibration_gate
FROM hist.vw_profile_horizon_alignment_stats
ORDER BY aligned_positive_ratio DESC;

-- @block q_pattern_post_breakout_exhaustion
-- Post-breakout exhaustion screen (Perf.1M extended + RSI + fading volume).
WITH latest_run AS (
    SELECT run_id
    FROM run_metadata
    ORDER BY created_at_utc DESC
    LIMIT 1
),
perf_rank AS (
    SELECT r.symbol,
        r."Perf.1M",
        NTILE(10) OVER (ORDER BY TRY_CAST(r."Perf.1M" AS DOUBLE) DESC) AS perf_decile
    FROM raw_scan_rows r
    JOIN latest_run lr ON r.run_id = lr.run_id
)
SELECT h.symbol,
    h.company,
    h.score,
    h.risk_adjusted_score,
    r."Perf.1M",
    r."RSI",
    r."Stoch.RSI.K",
    r.average_volume_10d_calc / NULLIF(r.average_volume_30d_calc, 0) AS volume_trend_raw
FROM profile_horizon_scores h
JOIN latest_run lr ON h.run_id = lr.run_id
JOIN raw_scan_rows r
    ON r.run_id = h.run_id
    AND (
        r.symbol = h.symbol
        OR r.symbol LIKE '%:' || h.symbol
        OR h.symbol LIKE '%:' || r.symbol
    )
JOIN perf_rank pr ON pr.symbol = r.symbol
WHERE h.profile_name = 'breakout_long'
  AND h.horizon_name = 'weeks'
  AND pr.perf_decile = 1
  AND TRY_CAST(r."RSI" AS DOUBLE) > 70
  AND (r.average_volume_10d_calc / NULLIF(r.average_volume_30d_calc, 0)) < 1.0
ORDER BY h.score DESC
LIMIT 50;
