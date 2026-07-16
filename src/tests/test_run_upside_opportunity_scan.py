from __future__ import annotations

import json

import duckdb

from run_upside_opportunity_scan import (
    run_upside_opportunity_scan_method,
)


def _build_all_fields_db(db_path) -> None:
    conn = duckdb.connect(db_path.as_posix())
    try:
        conn.execute("""
            CREATE TABLE all_fields_rows (
                run_id VARCHAR,
                row_number BIGINT,
                symbol VARCHAR,
                beta_1_year DOUBLE,
                "Volatility.D" DOUBLE,
                "Volatility.W" DOUBLE,
                "Volatility.M" DOUBLE,
                "ADX" DOUBLE,
                "RSI" DOUBLE,
                "Perf.5D" DOUBLE,
                "Perf.1M" DOUBLE,
                "Perf.3M" DOUBLE,
                "Recommend.All" DOUBLE,
                "SMA20" DOUBLE,
                "SMA50" DOUBLE,
                close DOUBLE
            )
        """)
        conn.execute("""
            INSERT INTO all_fields_rows VALUES
            ('run_1', 0, 'NASDAQ:GOOD', 1.1, 2.0, 3.0, 4.0, 25.0, 60.0, 1.0, 6.0,
             9.0, 0.4, 96.0, 91.0, 101.0)
            """)
    finally:
        conn.close()


def _build_parent_run(tmp_path, *, lens_subdir_name: str | None = None):
    parent_run_dir = tmp_path / "edge_integrated_parent_run"
    parent_run_dir.mkdir(parents=True)
    (parent_run_dir / "parent_run_manifest.json").write_text(
        json.dumps({"suite": "integrated_extension"}), encoding="utf-8"
    )
    lens_root = (
        parent_run_dir / lens_subdir_name if lens_subdir_name else parent_run_dir
    )
    lens_root.mkdir(parents=True, exist_ok=True)

    db_path = parent_run_dir / "all_fields.duckdb"
    _build_all_fields_db(db_path)

    unified_csv = lens_root / "edge_unified_highlights" / "unified.csv"
    unified_csv.parent.mkdir(parents=True)
    unified_csv.write_text(
        "symbol,unified_edge_highlight_rank,adrp_pct_today,relvol_pct_today,"
        "mom_core_pct_today,vol_core_pct_today,upside_hist_win_rate,"
        "upside_hist_median_fwd_pct,forward_valuation_upside_pct,"
        "forward_upside_mode,safety_data_available,safety_companion_score,"
        "indicator_pass_count,sector,industry\n"
        "NASDAQ:GOOD,1,0.85,0.8,0.75,0.3,0.7,6.0,22.0,valuation,1,0.75,9,"
        "Technology,Software\n",
        encoding="utf-8",
    )

    earnings_dir = lens_root / "earnings_priority_lens"
    earnings_dir.mkdir(parents=True)
    (earnings_dir / "edge_earnings_priority_candidates.csv").write_text(
        "symbol,earnings_days_until\nNASDAQ:GOOD,15\n",
        encoding="utf-8",
    )
    (earnings_dir / "edge_earnings_priority_manifest.json").write_text(
        json.dumps(
            {
                "source_database_path": db_path.as_posix(),
                "unified_csv_path": unified_csv.as_posix(),
            }
        ),
        encoding="utf-8",
    )

    return parent_run_dir


def test_run_upside_opportunity_scan_method_resolves_prior_parent_run(tmp_path) -> None:
    parent_run_dir = _build_parent_run(tmp_path)

    result = run_upside_opportunity_scan_method(
        run_ref=parent_run_dir,
        auto_discover_latest=False,
    )

    assert result["parent_run_dir"] == parent_run_dir
    assert result["row_count"] == 1
    assert result["output_dir"] == parent_run_dir / "upside_opportunity_scan"
    assert result["candidates_csv"].exists()

    conn = duckdb.connect(result["database_path"].as_posix(), read_only=True)
    try:
        rows = conn.execute(
            "SELECT symbol, beta_1_year, rsi, earnings_days_until "
            "FROM upside_opportunity_candidates"
        ).fetchall()
    finally:
        conn.close()

    assert rows[0][0] == "NASDAQ:GOOD"
    assert rows[0][1] == 1.1
    assert rows[0][2] == 60.0
    assert rows[0][3] == 15


def test_run_upside_opportunity_scan_method_raises_without_earnings_priority_manifest(
    tmp_path,
) -> None:
    parent_run_dir = tmp_path / "bare_parent_run"
    parent_run_dir.mkdir(parents=True)
    (parent_run_dir / "parent_run_manifest.json").write_text(
        json.dumps({"suite": "daily_scan"}), encoding="utf-8"
    )

    try:
        run_upside_opportunity_scan_method(
            run_ref=parent_run_dir,
            auto_discover_latest=False,
        )
        raised = False
    except FileNotFoundError:
        raised = True
    assert raised


def test_run_upside_opportunity_scan_method_resolves_nested_aggregate_wrapper(
    tmp_path,
) -> None:
    """Covers the `latest_500m_full_edge_research` suite's wrapper layout,
    where the top-level parent run dir only has its own `parent_run_manifest.json`
    and the actual lenses (earnings_priority_lens, edge_unified_highlights, ...)
    live one level down under an `aggregate` child folder.
    """
    parent_run_dir = _build_parent_run(tmp_path, lens_subdir_name="aggregate")

    result = run_upside_opportunity_scan_method(
        run_ref=parent_run_dir,
        auto_discover_latest=False,
    )

    assert result["parent_run_dir"] == parent_run_dir
    assert result["row_count"] == 1
    assert (
        result["output_dir"] == parent_run_dir / "aggregate" / "upside_opportunity_scan"
    )
    assert result["candidates_csv"].exists()
