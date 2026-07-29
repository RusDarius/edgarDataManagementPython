import csv
import json
import os
from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from edge_research_tools.market_timing_policy import (
    TimingPolicyConfig,
    build_timing_policy_rows,
    evaluate_timing_policy_row,
)
from run_market_timing_policy import (
    _calibration_rows,
    _history_features,
    _latest_all_fields_database,
    _latest_prediction_database,
    _load_current_all_fields,
    _write_duckdb,
)


def test_drawdown_bounce_authorizes_small_risk_despite_survival_reject() -> None:
    timing, policy = evaluate_timing_policy_row(
        {
            "symbol": "NASDAQ:ALAB",
            "upside_prediction_rank": 1,
            "adrp_percentile": 98,
            "drawdown_from_high_pct": -24.0,
            "consecutive_down_days": 2,
            "perf_5d": -18.0,
            "perf_1m": 6.0,
            "safety_bucket": "speculative",
            "conviction_state": "reject",
        },
        checkpoint="premarket",
    )

    assert timing["setup_drawdown_bounce"] == 1
    assert policy["action"] == "ENTER_SMALL"
    assert policy["normalized_risk_units"] == 0.75
    assert policy["existing_safety_state"] == "speculative"
    assert policy["max_hold_days"] == 2


def test_screen_coverage_glue_keeps_missing_upside_name_in_timing_universe() -> None:
    timing, policy = evaluate_timing_policy_row(
        {
            "symbol": "NASDAQ:WDC",
            "screen_rank": 31,
            "atrp": 6.5,
            "drawdown_from_high_pct": -17.0,
            "consecutive_down_days": 2,
            "gap_pct": 8.5,
            "industry_breadth_up_3pct": 69.0,
            "perf_5d": -9.3,
            "perf_1m": -12.0,
        },
        checkpoint="after_open",
    )

    audit = json.loads(timing["audit_trail_json"])
    assert audit["coverage_glue"] is True
    assert timing["setup_gap_breadth_thrust"] == 1
    assert policy["action"] == "ENTER_SMALL"
    assert policy["primary_setup"] == "drawdown_bounce"


def test_prior_five_percent_day_blocks_day_after_chase() -> None:
    timing, policy = evaluate_timing_policy_row(
        {
            "symbol": "NASDAQ:CHASE",
            "upside_prediction_rank": 3,
            "atrp": 7.0,
            "drawdown_from_high_pct": -20.0,
            "consecutive_down_days": 0,
            "prior_day_pct": 9.0,
            "gap_pct": 4.0,
            "industry_breadth_up_3pct": 70.0,
            "industry_breadth_expanding_flag": False,
            "perf_1m": 12.0,
        },
        checkpoint="premarket",
    )

    assert timing["anti_continuation_block"] == 1
    assert timing["setup_breakout_continuation"] == 0
    assert policy["action"] == "AVOID_CHASE"
    assert policy["normalized_risk_units"] == 0.0


def test_policy_rows_are_ranked_separately_from_timing_rows() -> None:
    timing_rows, policy_rows = build_timing_policy_rows(
        [
            {"symbol": "NYSE:OUT"},
            {
                "symbol": "NASDAQ:IN",
                "upside_prediction_rank": 2,
                "atrp": 5.0,
                "drawdown_from_high_pct": -15.0,
                "consecutive_down_days": 2,
            },
        ],
        checkpoint="midday",
    )

    assert timing_rows[0]["symbol"] == "NASDAQ:IN"
    assert policy_rows[0]["policy_rank"] == 1
    assert policy_rows[0]["action"] == "ENTER_SMALL"


def test_unknown_checkpoint_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported checkpoint"):
        evaluate_timing_policy_row({}, checkpoint="close")


def test_default_coverage_includes_near_cutoff_opportunities() -> None:
    config = TimingPolicyConfig()

    assert config.sticky_upside_rank_max == 50
    assert config.coverage_screen_rank_max == 100


def test_history_features_use_exported_daily_change_not_close_delta() -> None:
    features = _history_features(
        [
            ("2026-07-16", 190.64, -4.4),
            ("2026-07-17", 182.78, 6.4),
            ("2026-07-20", 181.26, 2.0),
        ],
        current_close=197.75,
    )

    assert features["consecutive_down_days"] == 0
    assert features["prior_day_pct"] == 2.0
    assert features["drawdown_from_high_pct"] == 0.0


def test_calibration_is_reported_separately_from_live_score() -> None:
    rows = _calibration_rows(
        {
            "NASDAQ:TEST": [
                ("2026-07-13", 100.0, -3.0),
                ("2026-07-14", 96.0, -4.0),
                ("2026-07-15", 101.0, 5.2),
                ("2026-07-16", 95.0, -5.9),
                ("2026-07-17", 97.0, 2.1),
            ]
        }
    )

    by_rule = {row["rule_name"]: row for row in rows}
    assert by_rule["after_2_consecutive_down_days"]["sample_count"] == 1
    assert by_rule["after_2_consecutive_down_days"]["next_day_mean_pct"] == 5.2
    assert by_rule["after_day_ge_5pct"]["next_day_mean_pct"] == -5.9
    assert all(row["scoring_role"] == "calibration_only" for row in rows)


def test_latest_artifact_selection_prefers_date_and_partition_over_mtime(
    tmp_path: Path,
) -> None:
    older_day = tmp_path / "all_fields" / "20_07_2026" / "older.duckdb"
    latest_day = tmp_path / "all_fields" / "21_07_2026" / "latest.duckdb"
    older_week = (
        tmp_path
        / "prediction"
        / "iso_year=2026"
        / "week=29"
        / "move_prediction_old.duckdb"
    )
    latest_week = (
        tmp_path
        / "prediction"
        / "iso_year=2026"
        / "week=30"
        / "move_prediction_new.duckdb"
    )
    for path in (older_day, latest_day, older_week, latest_week):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    os.utime(older_day, (2_000_000_000, 2_000_000_000))
    os.utime(older_week, (2_000_000_000, 2_000_000_000))

    assert _latest_all_fields_database(tmp_path / "all_fields") == latest_day
    assert _latest_prediction_database(tmp_path / "prediction") == latest_week


def test_same_day_scans_are_optional_trend_evidence(tmp_path: Path) -> None:
    database_path = tmp_path / "all_fields.duckdb"
    connection = duckdb.connect(str(database_path))
    connection.execute(
        "CREATE TABLE run_metadata "
        "(run_id VARCHAR, created_at_utc TIMESTAMP, run_date_utc DATE)"
    )
    connection.execute(
        "CREATE TABLE all_fields_rows (run_id VARCHAR, symbol VARCHAR, industry VARCHAR, "
        'close VARCHAR, change VARCHAR, "Perf.5D" VARCHAR, "Perf.1M" VARCHAR, '
        "gap VARCHAR, premarket_gap VARCHAR, premarket_change VARCHAR, "
        '"ADRP" VARCHAR, "ATRP" VARCHAR, market_cap_basic VARCHAR)'
    )
    runs = (
        ("scan_1", datetime(2026, 7, 21, 13, 30), 1.0),
        ("scan_2", datetime(2026, 7, 21, 15, 0), 3.0),
        ("scan_3", datetime(2026, 7, 21, 17, 0), 5.0),
    )
    for run_id, created_at, change in runs:
        connection.execute(
            "INSERT INTO run_metadata VALUES (?, ?, DATE '2026-07-21')",
            [run_id, created_at],
        )
        for symbol in ("NASDAQ:TEST", "NASDAQ:PEER"):
            connection.execute(
                "INSERT INTO all_fields_rows VALUES "
                "(?, ?, 'Semiconductors', '100', ?, '1', '2', '4', '4', '4', "
                "'8', '6', '1000000000')",
                [run_id, symbol, str(change)],
            )
    connection.close()

    rows, run_ids, scan_day, _ = _load_current_all_fields(
        database_path, checkpoint="latest", same_day_scan_limit=3
    )
    latest = rows["NASDAQ:TEST"]
    assert scan_day == "2026-07-21"
    assert run_ids == ["scan_3", "scan_2", "scan_1"]
    assert latest["same_day_scan_count"] == 3
    assert latest["same_day_scan_change_delta_pct"] == 4.0
    assert latest["same_day_scan_breadth_delta_pct"] == 100.0
    assert latest["industry_breadth_expanding_flag"] is True

    one_scan_rows, one_run_id, _, _ = _load_current_all_fields(
        database_path, checkpoint="latest", same_day_scan_limit=1
    )
    one_scan = one_scan_rows["NASDAQ:TEST"]
    assert one_run_id == ["scan_3"]
    assert one_scan["same_day_scan_count"] == 1
    assert one_scan["same_day_scan_change_delta_pct"] is None
    assert one_scan["industry_breadth_expanding_flag"] is False



def test_duckdb_writer_includes_catalyst_tables(tmp_path: Path) -> None:
    def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
        if not rows:
            raise AssertionError("expected non-empty rows in test fixture")
        fieldnames = list(rows[0].keys())
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    timing_csv = tmp_path / "timing.csv"
    policy_csv = tmp_path / "policy.csv"
    calibration_csv = tmp_path / "calibration.csv"
    catalyst_scores_csv = tmp_path / "catalyst_scores.csv"
    catalyst_policy_csv = tmp_path / "catalyst_policy.csv"
    database_path = tmp_path / "market_timing_policy.duckdb"

    write_csv(
        timing_csv,
        [
            {
                "symbol": "NASDAQ:AAA",
                "checkpoint": "latest",
                "timing_score": 70.0,
                "matched_setups": "drawdown_bounce",
                "setup_count": 1,
                "audit_trail_json": "{}",
            }
        ],
    )
    write_csv(
        policy_csv,
        [
            {
                "symbol": "NASDAQ:AAA",
                "checkpoint": "latest",
                "action": "ENTER_SMALL",
                "primary_setup": "drawdown_bounce",
                "timing_score": 70.0,
                "normalized_risk_units": 0.5,
            }
        ],
    )
    write_csv(
        calibration_csv,
        [
            {
                "rule_name": "after_2_consecutive_down_days",
                "sample_count": 1,
                "next_day_mean_pct": 0.1,
                "next_day_median_pct": 0.1,
                "next_day_win_rate": 1.0,
                "scoring_role": "calibration_only",
            }
        ],
    )
    write_csv(
        catalyst_scores_csv,
        [
            {
                "symbol": "NASDAQ:BBB",
                "checkpoint": "latest",
                "event_policy_score": 62.0,
                "event_action": "BOOK_A_DERISK_INTO_PRINT",
                "event_book": "BOOK_A",
                "matched_gates": "near_earnings|quality_entry_state",
                "gate_count": 2,
                "audit_trail_json": "{}",
            }
        ],
    )
    write_csv(
        catalyst_policy_csv,
        [
            {
                "symbol": "NASDAQ:BBB",
                "checkpoint": "latest",
                "event_action": "BOOK_A_DERISK_INTO_PRINT",
                "event_book": "BOOK_A",
                "event_policy_score": 62.0,
                "normalized_risk_units": 0.5,
                "size_note": "Reduce exposure",
            }
        ],
    )

    _write_duckdb(
        database_path,
        timing_csv=timing_csv,
        policy_csv=policy_csv,
        calibration_csv=calibration_csv,
        catalyst_score_csv=catalyst_scores_csv,
        catalyst_policy_csv=catalyst_policy_csv,
    )

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        tables = {
            row[0]
            for row in connection.execute("SHOW TABLES").fetchall()
        }
        assert {
            "timing_checkpoint_scores",
            "action_policy_recommendations",
            "timing_rule_calibration",
            "catalyst_event_policy_scores",
            "catalyst_event_action_recommendations",
        }.issubset(tables)
    finally:
        connection.close()
