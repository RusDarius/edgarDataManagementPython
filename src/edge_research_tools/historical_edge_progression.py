from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .config import DEFAULT_EDGE_RESEARCH_ROOT
from .historical_edge_backtest import run_historical_edge_backtest

DEFAULT_DAILY_HISTORICAL_EDGE_DATABASE = (
    DEFAULT_EDGE_RESEARCH_ROOT
    / "historical_backtests"
    / "daily_historical_edge_progression.duckdb"
)


def _import_duckdb():
    import duckdb

    return duckdb


def _q(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _read_csv(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _rows(conn: Any, sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, params)
    columns = [str(item[0]) for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def load_historical_progression_fields(
    *,
    historical_database_path: str | Path,
    config_hash: str,
) -> dict[str, dict[str, Any]]:
    duckdb = _import_duckdb()
    conn = duckdb.connect(Path(historical_database_path).as_posix(), read_only=True)
    try:
        rows = _rows(
            conn,
            """
            WITH scoped AS (
                SELECT *,
                    LAG(historical_edge_rank, 1) OVER symbol_history AS previous_rank,
                    LAG(anchor_date, 1) OVER symbol_history AS previous_seen_date,
                    LAG(historical_edge_rank, 5) OVER symbol_history AS rank_5_anchors_ago,
                    LAG(historical_edge_score, 1) OVER symbol_history AS previous_score,
                    ROW_NUMBER() OVER (
                        PARTITION BY symbol ORDER BY anchor_date DESC
                    ) AS latest_row_number,
                    COUNT(*) OVER (PARTITION BY symbol) AS anchors_seen,
                    MIN(anchor_date) OVER (PARTITION BY symbol) AS first_seen_date,
                    MIN(historical_edge_rank) OVER (PARTITION BY symbol) AS best_rank,
                    MEDIAN(historical_edge_rank) OVER (PARTITION BY symbol) AS median_rank
                FROM edge_candidate_snapshots
                WHERE config_hash = ?
                WINDOW symbol_history AS (
                    PARTITION BY symbol ORDER BY anchor_date
                )
            ),
            anchor_total AS (
                SELECT COUNT(*) AS anchor_count,
                    MAX(anchor_date) AS latest_anchor_date,
                    MAX(anchor_date) FILTER (WHERE reverse_rank = 2)
                        AS previous_anchor_date
                FROM (
                    SELECT anchor_date,
                        ROW_NUMBER() OVER (ORDER BY anchor_date DESC) AS reverse_rank
                    FROM edge_anchor_plan
                    WHERE config_hash = ? AND status = 'complete'
                )
            ),
            recent_ranked AS (
                SELECT symbol, anchor_date, historical_edge_rank,
                    ROW_NUMBER() OVER (
                        PARTITION BY symbol ORDER BY anchor_date DESC
                    ) AS recent_row_number
                FROM scoped
            ),
            trajectories AS (
                SELECT symbol,
                    STRING_AGG(
                        STRFTIME(anchor_date, '%Y-%m-%d') || ':' ||
                        CAST(historical_edge_rank AS VARCHAR),
                        ' | ' ORDER BY anchor_date
                    ) AS rank_trajectory_last_10
                FROM recent_ranked
                WHERE recent_row_number <= 10
                GROUP BY symbol
            )
            SELECT latest.symbol,
                anchor_total.latest_anchor_date AS historical_edge_latest_anchor_date,
                CASE WHEN latest.anchor_date = anchor_total.latest_anchor_date
                     THEN latest.historical_edge_rank ELSE NULL END
                    AS historical_edge_current_rank,
                CASE WHEN latest.anchor_date = anchor_total.latest_anchor_date
                     THEN latest.previous_rank ELSE latest.historical_edge_rank END
                    AS historical_edge_previous_rank,
                CASE WHEN latest.anchor_date < anchor_total.latest_anchor_date
                          OR latest.previous_rank IS NULL THEN NULL
                     ELSE latest.previous_rank - latest.historical_edge_rank END
                    AS historical_edge_rank_improvement_1d,
                latest.rank_5_anchors_ago AS historical_edge_rank_5_anchors_ago,
                CASE WHEN latest.rank_5_anchors_ago IS NULL THEN NULL
                     ELSE latest.rank_5_anchors_ago - latest.historical_edge_rank END
                    AS historical_edge_rank_improvement_5d,
                CASE WHEN latest.anchor_date = anchor_total.latest_anchor_date
                     THEN latest.historical_edge_score ELSE NULL END
                    AS historical_edge_current_score,
                CASE WHEN latest.anchor_date < anchor_total.latest_anchor_date
                          OR latest.previous_score IS NULL THEN NULL
                     ELSE latest.historical_edge_score - latest.previous_score END
                    AS historical_edge_score_change_1d,
                latest.best_rank AS historical_edge_best_rank,
                ROUND(latest.median_rank, 2) AS historical_edge_median_rank,
                latest.anchors_seen AS historical_edge_anchors_seen,
                anchor_total.anchor_count AS historical_edge_total_anchors,
                ROUND(latest.anchors_seen / NULLIF(anchor_total.anchor_count, 0), 4)
                    AS historical_edge_inclusion_rate,
                latest.first_seen_date AS historical_edge_first_seen_date,
                latest.anchor_date AS historical_edge_last_seen_date,
                latest.hist_sample_count_5d AS historical_edge_mature_samples_5d,
                latest.hist_median_return_5d_pct AS historical_edge_hist_median_5d_pct,
                latest.hist_win_rate_5d AS historical_edge_hist_win_rate_5d,
                latest.any_setup_rate AS historical_edge_setup_rate,
                 CASE WHEN latest.anchor_date = anchor_total.latest_anchor_date
                          AND latest.historical_edge_rank <= 20
                          AND (
                             latest.previous_rank IS NULL
                             OR latest.previous_rank > 100
                             OR latest.previous_seen_date < anchor_total.previous_anchor_date
                          )
                     THEN 1 ELSE 0 END AS historical_edge_fresh_top20_entry_flag,
                 CASE WHEN latest.anchor_date < anchor_total.latest_anchor_date
                     THEN 1 ELSE 0 END AS historical_edge_rank_exit_flag,
                CASE
                    WHEN latest.anchor_date < anchor_total.latest_anchor_date THEN 'EXIT'
                    WHEN latest.historical_edge_rank <= 20
                         AND (
                             latest.previous_rank IS NULL
                             OR latest.previous_rank > 100
                             OR latest.previous_seen_date < anchor_total.previous_anchor_date
                         )
                        THEN 'ENTER_FRESH'
                    WHEN latest.historical_edge_rank <= 100 THEN 'HOLD'
                    WHEN latest.historical_edge_rank <= 200 THEN 'WATCH'
                    ELSE 'OUTSIDE'
                END AS historical_edge_daily_state,
                trajectories.rank_trajectory_last_10 AS historical_edge_rank_trajectory_last_10
            FROM scoped AS latest
            CROSS JOIN anchor_total
            LEFT JOIN trajectories USING (symbol)
            WHERE latest.latest_row_number = 1
            """,
            [config_hash, config_hash],
        )
    finally:
        conn.close()
    return {str(row["symbol"]): row for row in rows}


def merge_unified_historical_progression(
    unified_rows: Sequence[Mapping[str, Any]],
    progression_by_symbol: Mapping[str, Mapping[str, Any]],
    *,
    config_hash: str,
    historical_database_path: str | Path,
) -> list[dict[str, Any]]:
    progression_columns = (
        list(next(iter(progression_by_symbol.values())).keys())
        if progression_by_symbol
        else []
    )
    output: list[dict[str, Any]] = []
    for source_row in unified_rows:
        row = dict(source_row)
        symbol = str(row.get("symbol") or "")
        source_progression = progression_by_symbol.get(symbol)
        progression = dict(source_progression or {})
        progression.pop("symbol", None)
        for column in progression_columns:
            if column != "symbol" and column not in progression:
                progression[column] = ""
        if source_progression is None:
            progression["historical_edge_rank_exit_flag"] = 0
            progression["historical_edge_fresh_top20_entry_flag"] = 0
            progression["historical_edge_daily_state"] = "OUTSIDE"
        row.update(progression)
        row["historical_edge_config_hash"] = config_hash
        row["historical_edge_database_path"] = Path(historical_database_path).as_posix()
        output.append(row)
    return output


def _write_augmented_unified_database(
    *,
    database_path: Path,
    augmented_csv_path: Path,
    progression_csv_path: Path,
    historical_result: Mapping[str, Any],
) -> None:
    duckdb = _import_duckdb()
    database_path.unlink(missing_ok=True)
    conn = duckdb.connect(database_path.as_posix())
    try:
        conn.execute(f"""
            CREATE TABLE symbol_unified_highlights AS
            SELECT * FROM read_csv_auto({_q(augmented_csv_path)}, HEADER=TRUE)
            """)
        conn.execute(f"""
            CREATE TABLE historical_edge_progression AS
            SELECT * FROM read_csv_auto({_q(progression_csv_path)}, HEADER=TRUE)
            """)
        conn.execute("CREATE TABLE unified_run_manifest(key VARCHAR, value VARCHAR)")
        manifest_rows = [
            ("historical_edge_database_path", str(historical_result["database_path"])),
            ("historical_edge_config_hash", str(historical_result["config_hash"])),
            ("historical_edge_anchor_count", str(historical_result["anchor_count"])),
            (
                "historical_edge_new_anchor_count",
                str(historical_result["new_anchor_count"]),
            ),
            ("historical_edge_anchor_mode", "every_n_trading_days"),
            ("historical_edge_spacing_trading_days", "1"),
        ]
        conn.executemany(
            "INSERT INTO unified_run_manifest VALUES (?, ?)", manifest_rows
        )
    finally:
        conn.close()


def build_daily_historical_edge_progression(
    *,
    snapshot_database_path: str | Path,
    unified_csv_path: str | Path,
    unified_database_path: str | Path,
    output_dir: str | Path,
    historical_database_path: str | Path = DEFAULT_DAILY_HISTORICAL_EDGE_DATABASE,
    historical_top_n: int = 2000,
    min_composite: float = 0.45,
    markets: Sequence[str] | None = None,
    duckdb_threads: int = 16,
    memory_limit_gb: float = 24.0,
) -> dict[str, Any]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    historical_result = run_historical_edge_backtest(
        snapshot_database_path=snapshot_database_path,
        output_database_path=historical_database_path,
        anchor_mode="every_n_trading_days",
        spacing_trading_days=1,
        top_n=max(1, int(historical_top_n)),
        min_composite=float(min_composite),
        markets=markets,
        incremental=True,
        duckdb_threads=int(duckdb_threads),
        memory_limit_gb=float(memory_limit_gb),
        artifact_output_dir=output_path / "historical_store",
        export_candidate_csv=False,
    )
    progression_by_symbol = load_historical_progression_fields(
        historical_database_path=historical_result["database_path"],
        config_hash=str(historical_result["config_hash"]),
    )
    unified_rows = _read_csv(unified_csv_path)
    augmented_rows = merge_unified_historical_progression(
        unified_rows,
        progression_by_symbol,
        config_hash=str(historical_result["config_hash"]),
        historical_database_path=historical_result["database_path"],
    )
    progression_rows = [
        {
            "symbol": row.get("symbol", ""),
            **{
                key: value
                for key, value in row.items()
                if key.startswith("historical_edge_")
            },
        }
        for row in augmented_rows
    ]
    progression_csv = output_path / "edge_historical_progression.csv"
    augmented_unified_csv = output_path / "edge_unified_with_history.csv"
    augmented_unified_database = output_path / "edge_unified_with_history.duckdb"
    report_md = output_path / "edge_historical_progression_report.md"
    manifest_path = output_path / "edge_historical_progression_manifest.json"
    _write_csv(augmented_unified_csv, augmented_rows)
    _write_csv(progression_csv, progression_rows)
    _write_augmented_unified_database(
        database_path=augmented_unified_database,
        augmented_csv_path=augmented_unified_csv,
        progression_csv_path=progression_csv,
        historical_result=historical_result,
    )

    state_counts: dict[str, int] = {}
    for row in progression_rows:
        state = str(row.get("historical_edge_daily_state") or "OUTSIDE")
        state_counts[state] = state_counts.get(state, 0) + 1
    report_lines = [
        "# Daily Historical Edge Progression",
        "",
        "The shared historical store is refreshed at every available trading date. Only new anchors are computed; mature outcomes and daily summaries are refreshed on every run.",
        "",
        f"- dense anchors: {historical_result['anchor_count']}",
        f"- new anchors this run: {historical_result['new_anchor_count']}",
        f"- historical candidate rows: {historical_result['candidate_count']}",
        f"- current unified rows augmented: {len(augmented_rows)}",
        f"- historical top N: {int(historical_top_n)}",
        f"- config hash: `{historical_result['config_hash']}`",
        f"- shared database: `{historical_result['database_path']}`",
        "",
        "## Daily States",
        "",
        "- `ENTER_FRESH`: current rank <= 20 after being absent or below rank 100.",
        "- `HOLD`: current rank <= 100 without a fresh-entry transition.",
        "- `WATCH`: current rank 101-200.",
        "- `EXIT`: the name was previously present but is absent from the latest dense anchor.",
        "- `OUTSIDE`: current rank is below the top-200 action bands, or the name has no stored rank.",
        "",
        "## State Counts",
        "",
    ]
    report_lines.extend(
        f"- {key}: {value}" for key, value in sorted(state_counts.items())
    )
    report_md.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    manifest = {
        "command": "daily-historical-edge-progression",
        "historical_database_path": str(historical_result["database_path"]),
        "historical_config_hash": str(historical_result["config_hash"]),
        "historical_anchor_count": int(historical_result["anchor_count"]),
        "historical_new_anchor_count": int(historical_result["new_anchor_count"]),
        "historical_candidate_count": int(historical_result["candidate_count"]),
        "historical_top_n": int(historical_top_n),
        "anchor_mode": "every_n_trading_days",
        "spacing_trading_days": 1,
        "markets": list(markets or ()),
        "unified_csv_path": Path(unified_csv_path).as_posix(),
        "unified_database_path": Path(unified_database_path).as_posix(),
        "augmented_unified_csv": augmented_unified_csv.as_posix(),
        "augmented_unified_database": augmented_unified_database.as_posix(),
        "progression_csv": progression_csv.as_posix(),
        "report_md": report_md.as_posix(),
        "row_count": len(progression_rows),
        "state_counts": state_counts,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        "output_dir": output_path,
        "progression_csv": progression_csv,
        "augmented_unified_csv": augmented_unified_csv,
        "augmented_unified_database": augmented_unified_database,
        "report_md": report_md,
        "manifest_path": manifest_path,
        "row_count": len(progression_rows),
        "state_counts": state_counts,
        "historical_result": historical_result,
        "historical_database_path": Path(historical_result["database_path"]),
        "historical_config_hash": str(historical_result["config_hash"]),
        "historical_anchor_count": int(historical_result["anchor_count"]),
        "historical_new_anchor_count": int(historical_result["new_anchor_count"]),
    }
