from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from backtracking.config import DEFAULT_CONFIG_PATH, load_backtracking_config
from backtracking.score import score_run
from backtracking.select import select_program_rows
from backtracking.timeline import build_decision_timeline
from db.trading_view_move_prediction_duckdb import query_move_prediction_duckdb
from generic_utils.scan_sources import rows_from_csv


def _duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _duckdb_available(), reason="duckdb is required for backtracking tests"
)


def _write_config(tmp_path: Path, overrides: dict[str, object]) -> Path:
    payload = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    payload.update(overrides)
    config_path = tmp_path / "backtracking_config.json"
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
            [run_id, f"{run_date}T15:00:00+00:00", run_date, "tradingview_all_fields_export_duckdb"],
        )
        conn.execute(
            """
            CREATE TABLE all_fields_rows (
                run_id VARCHAR,
                row_number BIGINT,
                symbol VARCHAR,
                close DOUBLE,
                industry VARCHAR,
                RSI DOUBLE,
                ATRP DOUBLE,
                price_target_average DOUBLE
            )
            """
        )
        for idx, (symbol, close) in enumerate(sorted(close_by_symbol.items()), start=1):
            conn.execute(
                """
                INSERT INTO all_fields_rows VALUES
                (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [run_id, idx, symbol, close, "Software", 55.0, 6.0, close * 1.25],
            )
    finally:
        conn.close()


def _init_prediction_db(path: Path, *, rows: list[tuple[str, str, str]]) -> None:
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
        for run_id, run_day, created_at in rows:
            conn.execute("INSERT INTO run_metadata VALUES (?, ?, ?)", [run_id, run_day, created_at])
    finally:
        conn.close()


def _init_backtracking_run_db(path: Path) -> None:
    import duckdb

    conn = duckdb.connect(path.as_posix())
    try:
        conn.execute(
            """
            CREATE TABLE decision_weeks (
                decision_date VARCHAR,
                decision_day_label VARCHAR,
                iso_year BIGINT,
                iso_week BIGINT,
                prediction_database VARCHAR,
                prediction_run_id VARCHAR,
                all_fields_database VARCHAR,
                all_fields_run_id VARCHAR,
                all_fields_day_label VARCHAR,
                all_fields_lag_days BIGINT,
                edge_parent_dir VARCHAR,
                edge_unified_database VARCHAR
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE panel_rows (
                decision_date VARCHAR,
                symbol VARCHAR,
                industry VARCHAR,
                bo DOUBLE
            )
            """
        )
    finally:
        conn.close()


def test_timeline_keeps_latest_run_per_week_and_skips_future_edge(tmp_path: Path) -> None:
    all_fields_root = tmp_path / "logs" / "tradingview_analysis" / "trading_view_all_fields_data"
    prediction_root = tmp_path / "logs" / "tradingview_analysis" / "prediction_analysis" / "duckdb_runs"
    edge_runs_root = tmp_path / "logs" / "tradingview_analysis" / "edge_research_tools" / "runs"
    output_root = tmp_path / "logs" / "tradingview_analysis" / "backtracking_analysis" / "runs"

    day_dir = all_fields_root / "03_07_2026"
    day_dir.mkdir(parents=True, exist_ok=True)
    all_fields_db = day_dir / "tradingview_all_fields_03_07_2026.duckdb"
    _init_all_fields_db(
        all_fields_db,
        run_id="af_20260703",
        run_date="2026-07-03",
        close_by_symbol={"NASDAQ:AAA": 10.0},
    )

    pred_dir = prediction_root / "iso_year=2026" / "week=27"
    pred_dir.mkdir(parents=True, exist_ok=True)
    prediction_db = pred_dir / "move_prediction_2026_W27.duckdb"
    _init_prediction_db(
        prediction_db,
        rows=[
            ("pred_old", "2026-07-01", "2026-07-01 10:00:00"),
            ("pred_new", "2026-07-03", "2026-07-03 10:00:00"),
        ],
    )

    edge_parent = edge_runs_root / "edge_parent_future"
    edge_parent.mkdir(parents=True, exist_ok=True)
    (edge_parent / "parent_run_manifest.json").write_text(
        json.dumps({"scan_day": "2026-07-04"}),
        encoding="utf-8",
    )

    config_path = _write_config(
        tmp_path,
        {
            "paths": {
                "all_fields_root": all_fields_root.as_posix(),
                "prediction_root": prediction_root.as_posix(),
                "edge_runs_root": edge_runs_root.as_posix(),
                "output_root": output_root.as_posix(),
                "focused_catalog_csv": (tmp_path / "focused.csv").as_posix(),
            }
        },
    )
    config = load_backtracking_config(config_path)
    timeline = build_decision_timeline(
        config,
        start_day_label="01_07_2026",
        end_day_label="05_07_2026",
    )
    assert len(timeline.decision_weeks) == 1
    week = timeline.decision_weeks[0]
    assert week.prediction_run_id == "pred_new"
    assert week.edge_parent_dir is None
    assert int(timeline.coverage_rows[0]["has_edge_parent"]) == 0


def test_select_field_bo_excludes_null_and_applies_group_cap(tmp_path: Path) -> None:
    run_dir = tmp_path / "logs" / "tradingview_analysis" / "backtracking_analysis" / "runs" / "backtracking_test"
    run_dir.mkdir(parents=True, exist_ok=True)
    db_path = run_dir / "backtracking.duckdb"
    _init_backtracking_run_db(db_path)

    import duckdb

    conn = duckdb.connect(db_path.as_posix())
    try:
        conn.execute(
            """
            INSERT INTO panel_rows VALUES
            ('2026-07-03', 'NASDAQ:AAA', 'Software', 0.9),
            ('2026-07-03', 'NASDAQ:BBB', 'Software', NULL),
            ('2026-07-03', 'NASDAQ:CCC', 'Hardware', 0.8),
            ('2026-07-03', 'NASDAQ:DDD', 'Software', 0.7)
            """
        )
    finally:
        conn.close()

    manifest = {
        "run_id": "backtracking_test",
        "window": {"start_day_label": "01_07_2026", "end_day_label": "31_07_2026"},
        "paths": {"all_fields_root": tmp_path.as_posix()},
    }
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = select_program_rows(
        run_dir=run_dir,
        selector="program_bo",
        field="bo",
        top=3,
        group_field="industry",
        cap=1,
    )
    picks_rows = rows_from_csv(Path(result["picks_path"]))
    symbols = [row["symbol"] for row in picks_rows]
    assert symbols == ["NASDAQ:AAA", "NASDAQ:CCC"]
    assert "NASDAQ:BBB" not in symbols


def test_score_uses_next_week_and_marks_missing_exit_unscored(tmp_path: Path) -> None:
    all_fields_root = tmp_path / "logs" / "tradingview_analysis" / "trading_view_all_fields_data"
    run_dir = tmp_path / "logs" / "tradingview_analysis" / "backtracking_analysis" / "runs" / "backtracking_test"
    run_dir.mkdir(parents=True, exist_ok=True)

    day_a = all_fields_root / "03_07_2026"
    day_b = all_fields_root / "10_07_2026"
    day_c = all_fields_root / "15_07_2026"
    day_a.mkdir(parents=True, exist_ok=True)
    day_b.mkdir(parents=True, exist_ok=True)
    day_c.mkdir(parents=True, exist_ok=True)
    db_a = day_a / "tradingview_all_fields_03_07_2026.duckdb"
    db_b = day_b / "tradingview_all_fields_10_07_2026.duckdb"
    db_c = day_c / "tradingview_all_fields_15_07_2026.duckdb"
    _init_all_fields_db(
        db_a,
        run_id="af_03",
        run_date="2026-07-03",
        close_by_symbol={"NASDAQ:AAA": 10.0, "NASDAQ:BBB": 20.0, "NASDAQ:CCC": 30.0},
    )
    _init_all_fields_db(
        db_b,
        run_id="af_10",
        run_date="2026-07-10",
        close_by_symbol={"NASDAQ:AAA": 11.0, "NASDAQ:CCC": 33.0},
    )
    _init_all_fields_db(
        db_c,
        run_id="af_15",
        run_date="2026-07-15",
        close_by_symbol={"NASDAQ:CCC": 36.0},
    )

    db_path = run_dir / "backtracking.duckdb"
    _init_backtracking_run_db(db_path)

    import duckdb

    conn = duckdb.connect(db_path.as_posix())
    try:
        conn.execute(
            """
            INSERT INTO decision_weeks VALUES
            (?, '03_07_2026', 2026, 27, ?, 'pred_a', ?, 'af_03', '03_07_2026', 0, NULL, NULL),
            (?, '10_07_2026', 2026, 28, ?, 'pred_b', ?, 'af_10', '10_07_2026', 0, NULL, NULL)
            """,
            [
                "2026-07-03",
                db_path.as_posix(),
                db_a.as_posix(),
                "2026-07-10",
                db_path.as_posix(),
                db_b.as_posix(),
            ],
        )
        conn.execute(
            """
            INSERT INTO panel_rows VALUES
            ('2026-07-03', 'NASDAQ:AAA', 'Software', 0.9),
            ('2026-07-10', 'NASDAQ:CCC', 'Software', 0.8)
            """
        )
    finally:
        conn.close()

    manifest = {
        "run_id": "backtracking_test",
        "window": {"start_day_label": "01_07_2026", "end_day_label": "15_07_2026"},
        "paths": {"all_fields_root": all_fields_root.as_posix()},
    }
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    picks_path = run_dir / "picks_program_bo.csv"
    with picks_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "decision_date",
                "symbol",
                "weight",
                "source_layer",
                "cited_fields",
                "note",
                "selector",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "decision_date": "2026-07-03",
                "symbol": "NASDAQ:AAA",
                "weight": "",
                "source_layer": "prediction",
                "cited_fields": "bo",
                "note": "",
                "selector": "program_bo",
            }
        )
        writer.writerow(
            {
                "decision_date": "2026-07-03",
                "symbol": "NASDAQ:BBB",
                "weight": "",
                "source_layer": "prediction",
                "cited_fields": "bo",
                "note": "",
                "selector": "program_bo",
            }
        )
        writer.writerow(
            {
                "decision_date": "2026-07-10",
                "symbol": "NASDAQ:CCC",
                "weight": "",
                "source_layer": "prediction",
                "cited_fields": "bo",
                "note": "",
                "selector": "program_bo",
            }
        )

    result = score_run(run_dir=run_dir, picks_path=picks_path)
    name_rows = rows_from_csv(Path(result["name_returns_path"]))
    by_symbol = {(row["decision_date"], row["symbol"]): row for row in name_rows}

    row_aaa = by_symbol[("2026-07-03", "NASDAQ:AAA")]
    assert int(row_aaa["scored"]) == 1
    assert pytest.approx(float(row_aaa["return_pct"]), rel=1e-6) == 10.0

    row_bbb = by_symbol[("2026-07-03", "NASDAQ:BBB")]
    assert int(row_bbb["scored"]) == 0
    assert row_bbb["unscored_reason"] == "missing_exit_close"

    row_ccc = by_symbol[("2026-07-10", "NASDAQ:CCC")]
    assert int(row_ccc["scored"]) == 1
    assert pytest.approx(float(row_ccc["return_pct"]), rel=1e-6) == pytest.approx(
        ((36.0 / 33.0) - 1.0) * 100.0, rel=1e-6
    )

    book_rows = rows_from_csv(Path(result["book_weeks_path"]))
    week1 = next(row for row in book_rows if row["decision_date"] == "2026-07-03")
    assert pytest.approx(float(week1["book_return_pct"]), rel=1e-6) == 10.0

    panel_columns = {
        str(row.get("column_name") or "")
        for row in query_move_prediction_duckdb(
            db_path,
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='main' AND table_name='panel_rows'
            """,
        )
    }
    assert "forward_return_pct" not in panel_columns


def test_daily_closes_and_best_swing_pick_the_larger_path(tmp_path: Path) -> None:
    all_fields_root = tmp_path / "logs" / "tradingview_analysis" / "trading_view_all_fields_data"
    run_dir = tmp_path / "logs" / "tradingview_analysis" / "backtracking_analysis" / "runs" / "backtracking_test"
    run_dir.mkdir(parents=True, exist_ok=True)
    for label, run_id, closes in (
        ("03_07_2026", "af_03", {"NASDAQ:AAA": 10.0, "NASDAQ:CCC": 30.0}),
        ("10_07_2026", "af_10", {"NASDAQ:AAA": 11.0, "NASDAQ:CCC": 33.0}),
        ("15_07_2026", "af_15", {"NASDAQ:AAA": 11.0, "NASDAQ:CCC": 36.0}),
    ):
        day_dir = all_fields_root / label
        day_dir.mkdir(parents=True, exist_ok=True)
        _init_all_fields_db(
            day_dir / f"tradingview_all_fields_{label}.duckdb",
            run_id=run_id,
            run_date=f"2026-{label[3:5]}-{label[0:2]}",
            close_by_symbol=closes,
        )

    db_path = run_dir / "backtracking.duckdb"
    _init_backtracking_run_db(db_path)
    import duckdb

    conn = duckdb.connect(db_path.as_posix())
    try:
        conn.execute(
            """
            INSERT INTO panel_rows VALUES
            ('2026-07-03', 'NASDAQ:AAA', 'Software', 0.2),
            ('2026-07-03', 'NASDAQ:CCC', 'Hardware', 0.4)
            """
        )
    finally:
        conn.close()
    (run_dir / "run_manifest.json").write_text(
        json.dumps(
            {
                "run_id": "backtracking_test",
                "window": {"start_day_label": "01_07_2026", "end_day_label": "15_07_2026"},
                "paths": {"all_fields_root": all_fields_root.as_posix()},
            }
        ),
        encoding="utf-8",
    )

    from backtracking.daily import attach_daily_closes
    from backtracking.paths import build_best_book

    attached = attach_daily_closes(run_dir)
    assert attached["rows"] == 6
    result = build_best_book(run_dir, max_names=1, min_sessions=1)
    assert result["packed_names"] == 1
    assert result["best_trade"]["symbol"] == "NASDAQ:CCC"
    assert result["best_trade"]["entry_date"] == "2026-07-03"
    assert result["best_trade"]["exit_date"] == "2026-07-15"
    assert pytest.approx(float(result["best_trade"]["return_pct"]), rel=1e-4) == 20.0
    panel_columns = {
        str(row.get("column_name") or "")
        for row in query_move_prediction_duckdb(
            db_path,
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='main' AND table_name='panel_rows'
            """,
        )
    }
    assert "forward_return_pct" not in panel_columns
