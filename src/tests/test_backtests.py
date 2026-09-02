from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from backtests.analysis import build_overlay_signal_rows, evaluate_signal_rows
from backtests.config import DEFAULT_CONFIG_PATH, load_backtest_config
from backtests.contracts import ArtifactDay, OutcomeRow, SignalRow
from backtests.signals import UpsideOpportunityAdapter
from backtests.timeline import (
    build_artifact_calendar,
    build_calendar_coverage_rows,
    build_outcome_rows,
)


def _duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _duckdb_available(), reason="duckdb is required for backtests tests"
)


def _write_config(tmp_path: Path, overrides: dict[str, object]) -> Path:
    payload = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    payload.update(overrides)
    config_path = tmp_path / "backtests_config.json"
    config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return config_path


def _init_all_fields_db(path: Path, *, run_id: str, run_date: str, close_by_symbol: dict[str, float]) -> None:
    import duckdb

    conn = duckdb.connect(path.as_posix())
    try:
        conn.execute(
            """
            CREATE TABLE run_metadata (
                run_id VARCHAR,
                created_at_utc VARCHAR,
                run_date_utc DATE,
                suite_name VARCHAR
            )
            """
        )
        conn.execute(
            "INSERT INTO run_metadata VALUES (?, ?, ?, ?)",
            [run_id, f"{run_date}T14:00:00+00:00", run_date, "tradingview_all_fields_export_duckdb"],
        )
        conn.execute(
            """
            CREATE TABLE all_fields_rows (
                run_id VARCHAR,
                row_number BIGINT,
                symbol VARCHAR,
                close DOUBLE,
                change DOUBLE,
                market_cap_basic DOUBLE,
                industry VARCHAR,
                "ATRP" DOUBLE,
                "ADRP" DOUBLE,
                "Perf.5D" DOUBLE,
                "Perf.1M" DOUBLE,
                "Perf.3M" DOUBLE,
                "Recommend.All" DOUBLE,
                RSI DOUBLE,
                price_target DOUBLE,
                total_revenue_yoy_growth_ttm DOUBLE,
                total_revenue_cagr_5y DOUBLE,
                relative_volume_10d_calc DOUBLE
            )
            """
        )
        for idx, (symbol, close_price) in enumerate(sorted(close_by_symbol.items()), start=1):
            conn.execute(
                """
                INSERT INTO all_fields_rows VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    run_id,
                    idx,
                    symbol,
                    close_price,
                    1.0,
                    1_500_000_000,
                    "Software",
                    5.0,
                    5.0 + idx,
                    2.0,
                    6.0,
                    8.0,
                    0.2,
                    55.0,
                    close_price * 1.2,
                    10.0,
                    12.0,
                    1.4,
                ],
            )
    finally:
        conn.close()


def _init_prediction_db(path: Path, *, run_id: str, run_date: str) -> None:
    import duckdb

    conn = duckdb.connect(path.as_posix())
    try:
        conn.execute(
            """
            CREATE TABLE run_metadata (
                run_id VARCHAR,
                run_date_utc DATE,
                created_at_utc TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO run_metadata VALUES (?, ?, ?)",
            [run_id, run_date, f"{run_date} 15:00:00"],
        )
        conn.execute(
            """
            CREATE TABLE conviction_rankings (
                run_id VARCHAR,
                symbol VARCHAR,
                conviction_score DOUBLE,
                rank_overall BIGINT,
                manager_action_signal VARCHAR,
                entry_readiness VARCHAR
            )
            """
        )
        conn.execute(
            "INSERT INTO conviction_rankings VALUES (?, 'NASDAQ:AAA', 0.9, 1, 'BUY', 'ready')",
            [run_id],
        )
        conn.execute(
            """
            CREATE TABLE consensus_horizon_scores (
                run_id VARCHAR,
                symbol VARCHAR,
                horizon_name VARCHAR,
                score DOUBLE,
                risk_adjusted_score DOUBLE
            )
            """
        )
        conn.execute(
            "INSERT INTO consensus_horizon_scores VALUES (?, 'NASDAQ:AAA', 'days', 0.8, 0.75)",
            [run_id],
        )
        conn.execute(
            """
            CREATE TABLE profile_horizon_scores (
                run_id VARCHAR,
                symbol VARCHAR,
                profile_name VARCHAR,
                horizon_name VARCHAR,
                score DOUBLE
            )
            """
        )
        conn.execute(
            "INSERT INTO profile_horizon_scores VALUES (?, 'NASDAQ:AAA', 'breakout_long_v1', 'days', 0.7)",
            [run_id],
        )
        conn.execute(
            """
            CREATE TABLE regime_context_scores (
                run_id VARCHAR,
                symbol VARCHAR,
                regime_fit_score DOUBLE
            )
            """
        )
        conn.execute(
            "INSERT INTO regime_context_scores VALUES (?, 'NASDAQ:AAA', 0.65)",
            [run_id],
        )
    finally:
        conn.close()


def test_calendar_alignment_iso_week_to_day(tmp_path: Path) -> None:
    all_fields_root = tmp_path / "logs" / "tradingview_analysis" / "trading_view_all_fields_data"
    prediction_root = tmp_path / "logs" / "tradingview_analysis" / "prediction_analysis" / "duckdb_runs"
    day_dir = all_fields_root / "01_07_2026"
    day_dir.mkdir(parents=True, exist_ok=True)
    all_fields_db = day_dir / "tradingview_all_fields_01_07_2026.duckdb"
    _init_all_fields_db(
        all_fields_db,
        run_id="allfields_20260701",
        run_date="2026-07-01",
        close_by_symbol={"NASDAQ:AAA": 10.0},
    )

    pred_dir = prediction_root / "iso_year=2026" / "week=27"
    pred_dir.mkdir(parents=True, exist_ok=True)
    prediction_db = pred_dir / "move_prediction_2026_W27.duckdb"
    _init_prediction_db(prediction_db, run_id="pred_20260701", run_date="2026-07-01")

    config_path = _write_config(
        tmp_path,
        {
            "paths": {
                "all_fields_root": all_fields_root.as_posix(),
                "prediction_root": prediction_root.as_posix(),
                "edge_runs_root": (tmp_path / "edge").as_posix(),
                "market_timing_root": (tmp_path / "timing").as_posix(),
                "financial_projection_root": (tmp_path / "projection").as_posix(),
                "output_root": (tmp_path / "out").as_posix(),
            }
        },
    )
    config = load_backtest_config(config_path)
    days = build_artifact_calendar(config)
    assert len(days) == 1
    assert days[0].day_label == "01_07_2026"
    assert days[0].prediction_database is not None
    assert days[0].prediction_run_id == "pred_20260701"


def test_labels_compute_multi_horizon_returns(tmp_path: Path) -> None:
    root = tmp_path / "logs" / "tradingview_analysis" / "trading_view_all_fields_data"
    day_labels = ["01_07_2026", "02_07_2026", "03_07_2026"]
    closes = [10.0, 11.0, 12.0]
    days: list[ArtifactDay] = []
    for label, close in zip(day_labels, closes, strict=True):
        day_dir = root / label
        day_dir.mkdir(parents=True, exist_ok=True)
        db_path = day_dir / f"tradingview_all_fields_{label}.duckdb"
        run_id = f"run_{label}"
        iso_day = date.fromisoformat(
            f"{label[6:10]}-{label[3:5]}-{label[0:2]}"
        ).isoformat()
        _init_all_fields_db(
            db_path,
            run_id=run_id,
            run_date=iso_day,
            close_by_symbol={"NASDAQ:AAA": close},
        )
        days.append(
            ArtifactDay(
                as_of_date=date.fromisoformat(iso_day),
                day_label=label,
                all_fields_database=db_path,
                all_fields_run_id=run_id,
            )
        )

    result = build_outcome_rows(days, horizons=[1, 2], output_dir=tmp_path / "labels_out")
    horizon1 = [
        row for row in result.rows if row.symbol == "NASDAQ:AAA" and row.horizon_days == 1
    ]
    horizon2 = [
        row for row in result.rows if row.symbol == "NASDAQ:AAA" and row.horizon_days == 2
    ]
    assert len(horizon1) == 2
    assert len(horizon2) == 1
    first_h1 = sorted(horizon1, key=lambda row: row.as_of_date)[0]
    assert pytest.approx(first_h1.forward_return_pct, rel=1e-6) == 10.0
    assert pytest.approx(horizon2[0].forward_return_pct, rel=1e-6) == 20.0


def test_overlay_filter_and_weight() -> None:
    config = load_backtest_config(DEFAULT_CONFIG_PATH)
    as_of = date(2026, 7, 1)
    signals = [
        SignalRow(as_of_date=as_of, symbol="NASDAQ:AAA", family="move_prediction", signal_name="conviction_score", score=1.0),
        SignalRow(as_of_date=as_of, symbol="NASDAQ:BBB", family="move_prediction", signal_name="conviction_score", score=0.5),
        SignalRow(as_of_date=as_of, symbol="NASDAQ:AAA", family="market_timing", signal_name="timing_action_reconstructed", score=80.0, action="ENTER_SMALL", weight=0.5),
        SignalRow(as_of_date=as_of, symbol="NASDAQ:BBB", family="market_timing", signal_name="timing_action_reconstructed", score=40.0, action="WATCH", weight=0.0),
        SignalRow(as_of_date=as_of, symbol="NASDAQ:AAA", family="financial_projection", signal_name="primary_upside_pct", score=25.0),
        SignalRow(as_of_date=as_of, symbol="NASDAQ:BBB", family="financial_projection", signal_name="primary_upside_pct", score=5.0),
    ]
    overlay_rows = build_overlay_signal_rows(signals, config=config)
    by_variant = {}
    for row in overlay_rows:
        by_variant.setdefault(row.signal_name, []).append(row)

    assert len(by_variant["picks_only"]) == 2
    assert len(by_variant["timing_filter"]) == 1
    assert by_variant["timing_filter"][0].symbol == "NASDAQ:AAA"
    weighted = by_variant["timing_weight"][0]
    assert weighted.symbol == "NASDAQ:AAA"
    assert pytest.approx(weighted.score, rel=1e-6) == 0.5


def test_upside_adapter_reads_fixture_csv(tmp_path: Path) -> None:
    config = load_backtest_config(DEFAULT_CONFIG_PATH)
    run_dir = tmp_path / "edge_parent" / "aggregate" / "upside_opportunity_scan"
    run_dir.mkdir(parents=True, exist_ok=True)
    candidates_csv = run_dir / "upside_opportunity_candidates.csv"
    with candidates_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "symbol",
                "upside_opportunity_score",
                "opp_momentum_component",
                "opp_historical_validation_component",
                "opp_valuation_component",
                "opp_safety_quality_component",
                "opp_binary_risk_score",
                "opp_downside_risk_score",
                "directional_lean",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "symbol": "NASDAQ:AAA",
                "upside_opportunity_score": 0.72,
                "opp_momentum_component": 0.8,
                "opp_historical_validation_component": 0.7,
                "opp_valuation_component": 0.75,
                "opp_safety_quality_component": 0.65,
                "opp_binary_risk_score": 0.2,
                "opp_downside_risk_score": 0.1,
                "directional_lean": "UPSIDE",
            }
        )
    manifest_path = run_dir / "upside_opportunity_scan_manifest.json"
    manifest_path.write_text(
        json.dumps({"candidates_csv": candidates_csv.as_posix()}), encoding="utf-8"
    )
    day = ArtifactDay(
        as_of_date=date(2026, 7, 1),
        day_label="01_07_2026",
        edge_parent_dir=tmp_path / "edge_parent",
        upside_opportunity_manifest=manifest_path,
    )
    result = UpsideOpportunityAdapter().load([day], config=config)
    scores = [row for row in result.signals if row.signal_name == "upside_opportunity_score"]
    assert len(scores) == 1
    assert scores[0].symbol == "NASDAQ:AAA"


def test_rank_stats_detects_monotonic_signal() -> None:
    as_of = date(2026, 7, 1)
    signals = [
        SignalRow(
            as_of_date=as_of,
            symbol=f"NASDAQ:S{i}",
            family="move_prediction",
            signal_name="conviction_score",
            score=float(i),
        )
        for i in range(1, 6)
    ]
    outcomes = [
        OutcomeRow(
            as_of_date=as_of,
            symbol=f"NASDAQ:S{i}",
            horizon_days=1,
            forward_return_pct=float(i),
        )
        for i in range(1, 6)
    ]
    result = evaluate_signal_rows(
        signals,
        outcomes,
        horizons=[1],
        top_ns=[2],
        quintile_count=5,
        min_pairs_for_metric=2,
    )
    metrics = result["family_metrics"]
    assert len(metrics) == 1
    assert pytest.approx(float(metrics[0]["spearman_ic"]), rel=1e-6) == 1.0
    assert float(metrics[0]["quintile_spread_pct"]) > 0


def test_coverage_ledger_flags_missing_families() -> None:
    rows = build_calendar_coverage_rows(
        [
            ArtifactDay(
                as_of_date=date(2026, 7, 1),
                day_label="01_07_2026",
                all_fields_database=Path("dummy.duckdb"),
                all_fields_run_id="run",
            )
        ]
    )
    assert len(rows) == 1
    assert rows[0]["has_prediction"] == 0
    assert rows[0]["has_timing_run"] == 0


def test_write_outputs_uses_csv_views_for_large_tables(tmp_path: Path) -> None:
    import duckdb

    from backtests.reporting import write_backtest_outputs

    out_dir = tmp_path / "out"
    config_path = _write_config(
        tmp_path,
        {
            "paths": {
                "all_fields_root": (tmp_path / "all_fields").as_posix(),
                "prediction_root": (tmp_path / "prediction").as_posix(),
                "edge_runs_root": (tmp_path / "edge").as_posix(),
                "market_timing_root": (tmp_path / "timing").as_posix(),
                "financial_projection_root": (tmp_path / "projection").as_posix(),
                "output_root": out_dir.as_posix(),
            },
            "output": {
                "root_dir": out_dir.as_posix(),
                "write_csv": True,
                "max_report_signals": 5,
                "duckdb_memory_limit": "256MB",
                "duckdb_materialize_max_bytes": 1,
            },
        },
    )
    config = load_backtest_config(config_path)
    result = write_backtest_outputs(
        config=config,
        tables={
            "coverage_ledger": [
                {
                    "as_of_date": "2026-07-01",
                    "has_prediction": 1,
                    "has_edge_parent": 0,
                    "has_upside_scan": 0,
                    "has_timing_run": 0,
                    "has_projection_growth": 0,
                    "has_projection_price": 0,
                }
            ],
            "family_metrics": [
                {
                    "family": "move_prediction",
                    "signal_name": "conviction_score",
                    "horizon_days": 1,
                    "spearman_ic": 0.2,
                    "quintile_spread_pct": 1.0,
                    "top_20_lift_pct": 0.5,
                }
            ],
            "overlay_comparison": [
                {
                    "variant": "picks_only",
                    "horizon_days": 1,
                    "spearman_ic_lift_vs_picks_only": 0.0,
                    "top_20_avg_return_pct_lift_vs_picks_only": 0.0,
                }
            ],
            "signal_rows": [
                {
                    "as_of_date": "2026-07-01",
                    "symbol": "NASDAQ:AAA",
                    "family": "move_prediction",
                    "signal_name": "conviction_score",
                    "score": 1.0,
                }
            ],
        },
        adapter_coverage={"move_prediction": {"signal_rows": 1}},
        label_metadata={"source": "test"},
    )
    assert Path(result["report_path"]).exists()
    assert Path(result["manifest_path"]).exists()
    assert result["duckdb_error"] is None
    conn = duckdb.connect(result["database_path"], read_only=True)
    try:
        types = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT table_name, table_type FROM information_schema.tables "
                "WHERE table_schema='main'"
            ).fetchall()
        }
        assert types["signal_rows"] == "VIEW"
        assert conn.execute("SELECT COUNT(*) FROM signal_rows").fetchone()[0] == 1
    finally:
        conn.close()


def test_finalize_run_writes_report_from_csv(tmp_path: Path) -> None:
    from backtests.reporting import finalize_backtest_run, write_backtest_outputs

    out_dir = tmp_path / "out"
    config_path = _write_config(
        tmp_path,
        {
            "paths": {
                "all_fields_root": (tmp_path / "all_fields").as_posix(),
                "prediction_root": (tmp_path / "prediction").as_posix(),
                "edge_runs_root": (tmp_path / "edge").as_posix(),
                "market_timing_root": (tmp_path / "timing").as_posix(),
                "financial_projection_root": (tmp_path / "projection").as_posix(),
                "output_root": out_dir.as_posix(),
            },
            "output": {"root_dir": out_dir.as_posix(), "write_csv": True},
        },
    )
    config = load_backtest_config(config_path)
    written = write_backtest_outputs(
        config=config,
        tables={
            "coverage_ledger": [{"as_of_date": "2026-07-01", "has_prediction": 1}],
            "family_metrics": [
                {
                    "family": "move_prediction",
                    "signal_name": "conviction_score",
                    "horizon_days": 1,
                    "spearman_ic": 0.15,
                    "quintile_spread_pct": 0.4,
                    "top_20_lift_pct": 0.2,
                }
            ],
            "overlay_comparison": [],
            "run_manifest": [
                {
                    "config_id": "default_v1",
                    "calendar_day_count": 1,
                    "signal_row_count": 3,
                    "outcome_row_count": 2,
                }
            ],
        },
        adapter_coverage={"move_prediction": {"signal_rows": 3}},
        label_metadata={},
    )
    run_dir = Path(written["run_dir"])
    (run_dir / "evidence_report.md").unlink()
    (run_dir / "run_manifest.json").unlink()
    (run_dir / "backtests.duckdb").unlink()
    finalized = finalize_backtest_run(run_dir, config_path=config_path)
    assert Path(finalized["report_path"]).exists()
    assert Path(finalized["manifest_path"]).exists()
    assert Path(finalized["database_path"]).exists()
    assert "conviction_score" in Path(finalized["report_path"]).read_text(encoding="utf-8")


def test_report_survives_duckdb_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backtests import reporting

    def _boom(*args: object, **kwargs: object) -> dict[str, str]:
        raise RuntimeError("simulated duckdb failure")

    monkeypatch.setattr(reporting, "_write_duckdb", _boom)
    out_dir = tmp_path / "out"
    config_path = _write_config(
        tmp_path,
        {
            "paths": {
                "all_fields_root": (tmp_path / "all_fields").as_posix(),
                "prediction_root": (tmp_path / "prediction").as_posix(),
                "edge_runs_root": (tmp_path / "edge").as_posix(),
                "market_timing_root": (tmp_path / "timing").as_posix(),
                "financial_projection_root": (tmp_path / "projection").as_posix(),
                "output_root": out_dir.as_posix(),
            },
            "output": {"root_dir": out_dir.as_posix(), "write_csv": True},
        },
    )
    config = load_backtest_config(config_path)
    result = reporting.write_backtest_outputs(
        config=config,
        tables={
            "coverage_ledger": [{"as_of_date": "2026-07-01", "has_prediction": 0}],
            "family_metrics": [],
            "overlay_comparison": [],
        },
        adapter_coverage={},
        label_metadata={},
    )
    assert Path(result["report_path"]).exists()
    assert Path(result["manifest_path"]).exists()
    assert result["duckdb_error"] is not None
    assert "simulated duckdb failure" in result["duckdb_error"]

