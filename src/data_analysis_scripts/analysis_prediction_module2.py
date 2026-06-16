"""Orthogonal outlook scoring for TradingView scans (prediction module2)."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from data_analysis_scripts.prediction_module2_profile_config import (
    Module2Profile,
    resolve_module2_profile_suite,
)
from data_analysis_scripts.prediction_module2_scoring import (
    build_family_orthogonal_consensus,
    build_overlap_report,
    prepare_scoring_context,
    rank_profile_results,
    score_universe_for_profile,
)
from data_analysis_scripts.trading_view_move_prediction_analysis import (
    _enrich_with_peer_metrics,
    _get_company_name,
)
from generic_utils.log_to_files_util import log_to_file, log_rows_to_csv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODULE2_LOG_DIR = (
    PROJECT_ROOT / "logs" / "tradingview_analysis" / "prediction_module2"
)
DEFAULT_MODULE2_DUCKDB_ROOT = DEFAULT_MODULE2_LOG_DIR / "duckdb_runs"
TOP_SECTION_ROWS = 30


def _slugify(value: str | None) -> str:
    if not value:
        return ""
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in value.strip().lower())
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_")


def _build_run_id(run_label: str | None, created_at_utc: datetime) -> str:
    timestamp = created_at_utc.strftime("%Y%m%d_%H%M")
    suffix = uuid.uuid4().hex[:8]
    label = _slugify(run_label)
    if label:
        return f"module2_{label}_{timestamp}_utc_{suffix}"
    return f"module2_{timestamp}_utc_{suffix}"


def _extract_scan_rows(scan_data: list[dict[str, Any]] | Mapping[str, Any]) -> list[dict[str, Any]]:
    if isinstance(scan_data, Mapping):
        rows = scan_data.get("rows") or scan_data.get("data") or []
        if isinstance(rows, list):
            return [dict(row) for row in rows]
        return []
    return [dict(row) for row in scan_data]


def _profile_config_hash(profile: Module2Profile) -> str:
    payload = {
        "profile_id": profile.profile_id,
        "outlook_family": profile.outlook_family,
        "version": profile.version,
        "regime_gates_hard": [
            {
                "field": rule.field,
                "source": rule.source,
                "min_z": rule.min_z,
                "max_z": rule.max_z,
            }
            for rule in profile.regime_gates_hard
        ],
        "pillars": {
            name: {
                "weight": pillar.weight,
                "fields": pillar.fields,
                "derived": pillar.derived,
            }
            for name, pillar in profile.pillars.items()
        },
        "horizon_weights": profile.horizon_weights,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def merge_all_fields_enrichment(
    scan_rows: list[dict[str, Any]],
    all_fields_duckdb_path: str | Path | None,
) -> list[dict[str, Any]]:
    if all_fields_duckdb_path is None:
        return scan_rows

    duckdb_path = Path(all_fields_duckdb_path)
    if not duckdb_path.exists():
        return scan_rows

    try:
        from db.trading_view_move_prediction_duckdb import query_move_prediction_duckdb
    except ImportError:
        return scan_rows

    tables = query_move_prediction_duckdb(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'",
        database_path=duckdb_path,
    )
    table_names = {row.get("table_name") for row in tables}
    source_table = None
    for candidate in ("all_fields_rows", "raw_scan_rows"):
        if candidate in table_names:
            source_table = candidate
            break
    if source_table is None:
        return scan_rows

    enriched_rows = query_move_prediction_duckdb(
        f"SELECT * FROM {source_table}",
        database_path=duckdb_path,
    )
    by_symbol = {
        str(row.get("symbol")): row for row in enriched_rows if row.get("symbol")
    }
    merged: list[dict[str, Any]] = []
    for row in scan_rows:
        symbol = str(row.get("symbol") or "")
        merged_row = dict(row)
        extra = by_symbol.get(symbol)
        if extra:
            for key, value in extra.items():
                if key in {"run_id", "row_number"}:
                    continue
                if merged_row.get(key) in (None, "") and value not in (None, ""):
                    merged_row[key] = value
        merged.append(merged_row)
    return merged


def _build_profile_csv_rows(
    ranked_rows: Sequence[Mapping[str, Any]],
    profile: Module2Profile,
) -> tuple[list[str], list[list[str]]]:
    headers = [
        "symbol",
        "company_name",
        "score",
        "hard_gate_passed",
        "soft_gate_multiplier",
        "base_score",
    ]
    headers.extend(f"pillar_{name}" for name in profile.pillars.keys())
    rows: list[list[str]] = []
    for row in ranked_rows:
        csv_row = [
            str(row.get("symbol") or ""),
            _get_company_name(dict(row) if isinstance(row, Mapping) else row),
            "" if row.get("score") is None else f"{float(row['score']):.4f}",
            str(bool(row.get("hard_gate_passed"))),
            "" if row.get("soft_gate_multiplier") is None else f"{float(row['soft_gate_multiplier']):.4f}",
            "" if row.get("base_score") is None else f"{float(row['base_score']):.4f}",
        ]
        pillar_scores = row.get("pillar_scores") or {}
        for pillar_name in profile.pillars.keys():
            value = pillar_scores.get(pillar_name)
            csv_row.append("" if value is None else f"{float(value):.4f}")
        rows.append(csv_row)
    return headers, rows


def _write_profile_log(
    log_path: Path,
    profile: Module2Profile,
    ranked_rows: Sequence[Mapping[str, Any]],
    scan_data_count: int,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()
    log_to_file(log_path, f"Prediction Module2 — {profile.profile_id}")
    log_to_file(log_path, "=" * 120)
    log_to_file(
        log_path,
        f"Outlook family: {profile.outlook_family} | Universe: {scan_data_count} symbols",
    )
    for note in profile.intro_metric_notes:
        log_to_file(log_path, f"- {note}")
    log_to_file(log_path, "")
    log_to_file(
        log_path,
        f"{'Rank':<5} {'Symbol':<18} {'Company':<32} {'Score':>8} {'Gate':>6} {'Soft':>6} {'Pillars'}",
    )
    for index, row in enumerate(ranked_rows[:TOP_SECTION_ROWS], start=1):
        pillar_scores = row.get("pillar_scores") or {}
        pillar_summary = ", ".join(
            f"{name}={pillar_scores.get(name):.2f}"
            for name in profile.pillars.keys()
            if pillar_scores.get(name) is not None
        )
        company_name = str(row.get("company_name") or row.get("name") or "")
        log_to_file(
            log_path,
            (
                f"{index:<5} {str(row.get('symbol') or ''):<18} "
                f"{company_name[:32]:<32} "
                f"{float(row.get('score') or 0.0):>8.3f} "
                f"{str(bool(row.get('hard_gate_passed'))):>6} "
                f"{float(row.get('soft_gate_multiplier') or 0.0):>6.2f} "
                f"{pillar_summary}"
            ),
        )


def run_module2_profile_suite(
    scan_data: list[dict[str, Any]] | Mapping[str, Any],
    *,
    profile_suite_path: str | Path | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    all_fields_duckdb_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    run_label: str | None = None,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    suite = resolve_module2_profile_suite(profile_suite_path)
    created_at_utc = reference_time or datetime.now(tz=timezone.utc)
    if created_at_utc.tzinfo is None:
        created_at_utc = created_at_utc.replace(tzinfo=timezone.utc)
    created_at_utc = created_at_utc.astimezone(timezone.utc)
    run_id = _build_run_id(run_label, created_at_utc)

    scan_rows = _extract_scan_rows(scan_data)
    if min_market_cap_usd is not None:
        scan_rows = [
            row
            for row in scan_rows
            if (row.get("market_cap_basic") or 0) >= min_market_cap_usd
        ]
    if max_market_cap_usd is not None:
        scan_rows = [
            row
            for row in scan_rows
            if (row.get("market_cap_basic") or 0) <= max_market_cap_usd
        ]

    _enrich_with_peer_metrics(scan_rows)
    scan_rows = merge_all_fields_enrichment(scan_rows, all_fields_duckdb_path)
    derived_metrics, metric_profiles, catalog = prepare_scoring_context(scan_rows, suite)

    iso_calendar = created_at_utc.isocalendar()
    run_output_dir = Path(output_dir) if output_dir is not None else (
        DEFAULT_MODULE2_LOG_DIR
        / "duckdb_runs"
        / f"iso_year={iso_calendar.year}"
        / f"week={iso_calendar.week:02d}"
        / "runs"
        / run_id
    )
    run_output_dir.mkdir(parents=True, exist_ok=True)

    profile_results: dict[str, Any] = {}
    profile_rankings: dict[str, list[dict[str, Any]]] = {}
    profile_scores: dict[str, list[dict[str, Any]]] = {}

    for profile_name in suite.profile_names:
        profile = suite.registry.get_profile(profile_name)
        if profile is None:
            raise ValueError(f"Module2 profile not registered: {profile_name}")
        scored_rows = score_universe_for_profile(
            scan_rows,
            derived_metrics,
            profile,
            metric_profiles,
            catalog=catalog,
        )
        ranked_rows = rank_profile_results(scored_rows, top_n=TOP_SECTION_ROWS)
        profile_scores[profile_name] = scored_rows
        profile_rankings[profile_name] = ranked_rows

        csv_path = run_output_dir / f"module2__profile_{profile_name}.csv"
        headers, csv_rows = _build_profile_csv_rows(ranked_rows, profile)
        log_rows_to_csv(csv_path, headers, csv_rows)

        log_path = run_output_dir / f"module2__profile_{profile_name}.log"
        _write_profile_log(log_path, profile, ranked_rows, len(scan_rows))

        profile_results[profile_name] = {
            "profile_id": profile_name,
            "outlook_family": profile.outlook_family,
            "profile_config_hash": _profile_config_hash(profile),
            "csv_path": str(csv_path),
            "log_path": str(log_path),
            "top_leaders": ranked_rows[:TOP_SECTION_ROWS],
            "gate_pass_rate": (
                sum(1 for row in scored_rows if row.get("hard_gate_passed"))
                / len(scored_rows)
                if scored_rows
                else 0.0
            ),
        }

    consensus_rows = build_family_orthogonal_consensus(profile_scores, suite)
    overlap_report = build_overlap_report(profile_rankings, top_n=TOP_SECTION_ROWS)

    consensus_csv = run_output_dir / "module2__consensus.csv"
    log_rows_to_csv(
        consensus_csv,
        ["symbol", "company_name", "horizon_name", "consensus_score", "coverage", "family_scores_json"],
        [
            [
                str(row.get("symbol") or ""),
                str(row.get("company_name") or row.get("name") or ""),
                str(row.get("horizon_name") or ""),
                f"{float(row['consensus_score']):.4f}",
                f"{float(row['coverage']):.4f}",
                json.dumps(row.get("family_scores") or {}, sort_keys=True),
            ]
            for row in consensus_rows[:TOP_SECTION_ROWS]
        ],
    )

    overlap_path = run_output_dir / "module2__overlap_report.json"
    overlap_path.write_text(
        json.dumps(overlap_report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    overview_log = run_output_dir / "_module2_run_overview.log"
    if overview_log.exists():
        overview_log.unlink()
    log_to_file(overview_log, f"Module2 run id: {run_id}")
    log_to_file(overview_log, f"Suite: {suite.suite_id} ({suite.suite_version})")
    log_to_file(overview_log, f"Scan count: {len(scan_rows)}")
    log_to_file(overview_log, f"Consensus mode: {suite.consensus_mode}")
    for pair in overlap_report.get("pair_overlaps", []):
        log_to_file(
            overview_log,
            (
                f"Overlap {pair['left_profile']} vs {pair['right_profile']}: "
                f"Jaccard={pair['jaccard_top_n']:.3f}"
            ),
        )

    return {
        "run_id": run_id,
        "created_at_utc": created_at_utc.isoformat(),
        "scan_data_count": len(scan_rows),
        "suite_id": suite.suite_id,
        "suite_version": suite.suite_version,
        "suite_path": suite.source_path,
        "run_output_dir": str(run_output_dir),
        "profiles": profile_results,
        "consensus_top": consensus_rows[:TOP_SECTION_ROWS],
        "consensus_csv": str(consensus_csv),
        "overlap_report_path": str(overlap_path),
        "overlap_report": overlap_report,
        "overview_log": str(overview_log),
    }


def _ensure_module2_duckdb_schema(connection: Any) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS module2_run_metadata (
            run_id VARCHAR PRIMARY KEY,
            created_at_utc TIMESTAMP,
            suite_id VARCHAR,
            suite_version VARCHAR,
            suite_path VARCHAR,
            scan_data_count BIGINT,
            consensus_mode VARCHAR,
            run_output_dir VARCHAR,
            overlap_report_path VARCHAR
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS module2_profile_scores (
            run_id VARCHAR,
            profile_id VARCHAR,
            outlook_family VARCHAR,
            symbol VARCHAR,
            company_name VARCHAR,
            score DOUBLE,
            hard_gate_passed BOOLEAN,
            soft_gate_multiplier DOUBLE,
            pillar_scores_json VARCHAR
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS module2_consensus_scores (
            run_id VARCHAR,
            symbol VARCHAR,
            company_name VARCHAR,
            horizon_name VARCHAR,
            consensus_score DOUBLE,
            coverage DOUBLE,
            family_scores_json VARCHAR
        )
        """
    )
    connection.execute(
        "ALTER TABLE module2_profile_scores ADD COLUMN IF NOT EXISTS company_name VARCHAR"
    )
    connection.execute(
        "ALTER TABLE module2_consensus_scores ADD COLUMN IF NOT EXISTS company_name VARCHAR"
    )


def run_module2_suite_duckdb(
    scan_data: list[dict[str, Any]] | Mapping[str, Any],
    *,
    profile_suite_path: str | Path | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    all_fields_duckdb_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    database_path: str | Path | None = None,
    run_label: str | None = None,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    result = run_module2_profile_suite(
        scan_data,
        profile_suite_path=profile_suite_path,
        min_market_cap_usd=min_market_cap_usd,
        all_fields_duckdb_path=all_fields_duckdb_path,
        output_dir=output_dir,
        run_label=run_label,
        reference_time=reference_time,
    )

    created_at_utc = datetime.fromisoformat(result["created_at_utc"])
    iso_calendar = created_at_utc.isocalendar()
    resolved_database_path = Path(database_path) if database_path is not None else (
        DEFAULT_MODULE2_DUCKDB_ROOT
        / f"iso_year={iso_calendar.year}"
        / f"week={iso_calendar.week:02d}"
        / f"prediction_module2_{iso_calendar.year}_{iso_calendar.week:02d}.duckdb"
    )
    resolved_database_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        from db.trading_view_move_prediction_duckdb import (
            open_move_prediction_duckdb_connection,
        )
    except ImportError:
        result["database_path"] = None
        result["database_error"] = "duckdb package not installed"
        return result

    run_id = result["run_id"]
    suite = resolve_module2_profile_suite(profile_suite_path)
    with open_move_prediction_duckdb_connection(resolved_database_path) as connection:
        _ensure_module2_duckdb_schema(connection)
        connection.execute("DELETE FROM module2_run_metadata WHERE run_id = ?", [run_id])
        connection.execute("DELETE FROM module2_profile_scores WHERE run_id = ?", [run_id])
        connection.execute("DELETE FROM module2_consensus_scores WHERE run_id = ?", [run_id])

        connection.execute(
            """
            INSERT INTO module2_run_metadata (
                run_id,
                created_at_utc,
                suite_id,
                suite_version,
                suite_path,
                scan_data_count,
                consensus_mode,
                run_output_dir,
                overlap_report_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_id,
                created_at_utc,
                result["suite_id"],
                result["suite_version"],
                result.get("suite_path"),
                result["scan_data_count"],
                suite.consensus_mode,
                result["run_output_dir"],
                result["overlap_report_path"],
            ],
        )

        scan_rows = _extract_scan_rows(scan_data)
        derived_metrics, metric_profiles, catalog = prepare_scoring_context(scan_rows, suite)
        for profile_name in suite.profile_names:
            profile = suite.registry.get_profile(profile_name)
            if profile is None:
                continue
            scored_rows = score_universe_for_profile(
                scan_rows,
                derived_metrics,
                profile,
                metric_profiles,
                catalog=catalog,
            )
            for row in scored_rows:
                connection.execute(
                    """
                    INSERT INTO module2_profile_scores (
                        run_id,
                        profile_id,
                        outlook_family,
                        symbol,
                        company_name,
                        score,
                        hard_gate_passed,
                        soft_gate_multiplier,
                        pillar_scores_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        run_id,
                        profile_name,
                        profile.outlook_family,
                        row.get("symbol"),
                        row.get("company_name") or row.get("name"),
                        row.get("score"),
                        bool(row.get("hard_gate_passed")),
                        row.get("soft_gate_multiplier"),
                        json.dumps(row.get("pillar_scores") or {}, sort_keys=True),
                    ],
                )

        for row in result.get("consensus_top") or []:
            connection.execute(
                """
                INSERT INTO module2_consensus_scores (
                    run_id,
                    symbol,
                    company_name,
                    horizon_name,
                    consensus_score,
                    coverage,
                    family_scores_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    run_id,
                    row.get("symbol"),
                    row.get("company_name") or row.get("name"),
                    row.get("horizon_name"),
                    row.get("consensus_score"),
                    row.get("coverage"),
                    json.dumps(row.get("family_scores") or {}, sort_keys=True),
                ],
            )

    result["database_path"] = str(resolved_database_path)
    return result
