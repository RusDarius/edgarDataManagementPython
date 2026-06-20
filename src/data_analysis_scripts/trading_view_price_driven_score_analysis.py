from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from data_analysis_scripts._shared_analysis_utils import coerce_numeric as _coerce_numeric
from data_analysis_scripts.trading_view_move_prediction_analysis import (
    DIRECTIONAL_MOVE_SCORE_THRESHOLD,
    DuckDBWeeklyStorageLayout,
    LOG_DIR,
    STRONG_MOVE_SCORE_THRESHOLD,
    _build_consensus_scores,
    _build_derived_metrics,
    _build_duckdb_run_id,
    _build_metric_profiles,
    _build_prediction_rows,
    _build_profile_component_records,
    _build_profile_config_snapshots,
    _build_raw_csv_headers,
    _build_raw_csv_rows,
    _collect_code_version_metadata,
    _consensus_base_record,
    _duckdb_weekly_writer_lock,
    _enrich_with_peer_metrics,
    _extract_duckdb_scan_input,
    _filter_scan_data_by_market_cap,
    _format_percent,
    _format_score,
    _get_company_name,
    _get_symbol_name,
    _normalize_industries,
    _prediction_base_record,
    _slugify,
    resolve_move_prediction_scoring_profile,
)
from data_analysis_scripts.trading_view_move_prediction_profile_config import (
    resolve_profile_suite,
)
from data_analysis_scripts.trading_view_priceperf_analysis import PERIOD_LABELS
from db.trading_view_price_driven_score_duckdb import PriceDrivenScoreDuckDBStore
from generic_utils.log_to_files_util import log_to_file

PRICE_DRIVEN_LOG_DIR = LOG_DIR / "price_driven_score_analysis"
PRICE_DRIVEN_DUCKDB_ROOT = PRICE_DRIVEN_LOG_DIR / "duckdb_runs"

PERFORMANCE_LENSES: dict[str, dict[str, Any]] = {
    "change": {
        "field": "change",
        "label": PERIOD_LABELS.get("change", "Change Daily"),
        "auxiliary_fields": [
            "premarket_change",
            "postmarket_change",
            "gap",
            "premarket_gap",
        ],
    },
    "Perf.5D": {
        "field": "Perf.5D",
        "label": PERIOD_LABELS.get("Perf.5D", "5 Day"),
        "auxiliary_fields": [],
    },
    "Perf.1M": {
        "field": "Perf.1M",
        "label": PERIOD_LABELS.get("Perf.1M", "1 Month"),
        "auxiliary_fields": [],
    },
}

REVERSAL_PROFILES = frozenset(
    {
        "upside_reversal_v1",
        "swing_reversal_v1",
        "value_recovery_v1",
        "value_recovery_v2",
        "mean_reversion_exhaustion_v1",
        "value_recovery",
        "mean_reversion_exhaustion",
    }
)
QUALITY_DIP_PROFILES = frozenset(
    {
        "quality_value_compounder",
        "durable_value_compounder",
        "durable_value_compounder_v1",
        "quality_growth_at_reasonable_price_v1",
        "defensive_fortress_v1",
        "income_compounder_v1",
    }
)
EARLY_INFLECTION_PROFILES = frozenset(
    {
        "early_momentum_inflection",
        "early_momentum_inflection_v1",
        "forward_edge_active",
        "forward_edge_active_v1",
        "pre_earnings_drift_v1",
    }
)
UPWARD_DIRECTIONS = frozenset({"Up", "Strong Up"})


@dataclass(frozen=True)
class PerformanceCohortRow:
    symbol: str
    company: str
    sector: str
    industry: str
    market_cap_basic: float | None
    performance_lens: str
    performance_field: str
    performance_value: float
    decile: int
    decile_rank: int
    perf_percentile: float
    premarket_change: float | None = None
    postmarket_change: float | None = None
    gap: float | None = None
    premarket_gap: float | None = None


def _build_price_driven_run_id(
    run_label: str | None = None,
    reference_time: datetime | None = None,
) -> str:
    base_run_id = _build_duckdb_run_id(run_label, reference_time)
    if run_label and _slugify(run_label):
        return base_run_id
    return base_run_id.replace("move_prediction_", "price_driven_score_", 1)


def _build_price_driven_storage_layout(
    run_id: str,
    created_at_utc: datetime,
    output_dir: str | Path | None = None,
    database_path: str | Path | None = None,
    parquet_dir: str | Path | None = None,
) -> DuckDBWeeklyStorageLayout:
    if created_at_utc.tzinfo is None:
        created_at_utc = created_at_utc.replace(tzinfo=timezone.utc)
    created_at_utc = created_at_utc.astimezone(timezone.utc)
    base_output_dir = (
        Path(output_dir) if output_dir is not None else PRICE_DRIVEN_DUCKDB_ROOT
    )
    iso_calendar = created_at_utc.isocalendar()
    iso_year = iso_calendar.year
    iso_week = iso_calendar.week
    period_dir = base_output_dir / f"iso_year={iso_year}" / f"week={iso_week:02d}"
    run_output_dir = period_dir / "runs" / run_id
    resolved_database_path = (
        Path(database_path)
        if database_path is not None
        else period_dir / f"price_driven_score_{iso_year}_W{iso_week:02d}.duckdb"
    )
    resolved_parquet_dir = (
        Path(parquet_dir) if parquet_dir is not None else period_dir / "parquet"
    )
    return DuckDBWeeklyStorageLayout(
        period_dir=period_dir,
        run_output_dir=run_output_dir,
        database_path=resolved_database_path,
        parquet_dir=resolved_parquet_dir,
    )


def assign_performance_deciles(
    scan_rows: list[dict[str, Any]],
    performance_field: str,
) -> dict[str, PerformanceCohortRow]:
    """Assign NTILE(10) deciles (1=worst) for one performance field."""
    lens_key = performance_field
    lens_config = PERFORMANCE_LENSES.get(lens_key)
    if lens_config is None:
        lens_config = {"field": performance_field, "auxiliary_fields": []}
        lens_key = performance_field

    ranked_rows: list[tuple[dict[str, Any], float]] = []
    for row in scan_rows:
        performance_value = _coerce_numeric(row.get(performance_field))
        if performance_value is None:
            continue
        ranked_rows.append((row, float(performance_value)))

    cohort_by_symbol: dict[str, PerformanceCohortRow] = {}
    total = len(ranked_rows)
    if total == 0:
        return cohort_by_symbol

    ranked_rows.sort(key=lambda item: item[1])
    for rank, (row, performance_value) in enumerate(ranked_rows):
        decile = min(10, int(rank * 10 / total) + 1)
        perf_percentile = ((rank + 1) / total) * 100.0
        symbol = _get_symbol_name(row)
        auxiliary_values = {
            field_name: _coerce_numeric(row.get(field_name))
            for field_name in lens_config.get("auxiliary_fields", [])
        }
        cohort_by_symbol[symbol] = PerformanceCohortRow(
            symbol=symbol,
            company=_get_company_name(row),
            sector=str(row.get("sector") or ""),
            industry=str(row.get("industry") or ""),
            market_cap_basic=_coerce_numeric(row.get("market_cap_basic")),
            performance_lens=lens_key,
            performance_field=performance_field,
            performance_value=performance_value,
            decile=decile,
            decile_rank=rank + 1,
            perf_percentile=perf_percentile,
            premarket_change=auxiliary_values.get("premarket_change"),
            postmarket_change=auxiliary_values.get("postmarket_change"),
            gap=auxiliary_values.get("gap"),
            premarket_gap=auxiliary_values.get("premarket_gap"),
        )
    return cohort_by_symbol


def build_performance_cohort_maps(
    scan_rows: list[dict[str, Any]],
) -> dict[str, dict[str, PerformanceCohortRow]]:
    return {
        lens_key: assign_performance_deciles(scan_rows, lens_config["field"])
        for lens_key, lens_config in PERFORMANCE_LENSES.items()
    }


def _is_inverted_profile(
    profile_name: str,
    inverted_profiles: frozenset[str],
) -> bool:
    return profile_name in inverted_profiles


def is_upward_exemplar(
    profile_name: str,
    horizon: Mapping[str, Any],
    *,
    inverted_profiles: frozenset[str],
) -> bool:
    score = horizon.get("score")
    direction = str(horizon.get("direction") or "")
    if score is None:
        return False

    if _is_inverted_profile(profile_name, inverted_profiles):
        return False

    if direction in UPWARD_DIRECTIONS:
        return True
    if isinstance(score, (int, float)) and score >= DIRECTIONAL_MOVE_SCORE_THRESHOLD:
        return True
    return False


def _cohort_record_from_row(
    run_id: str,
    cohort: PerformanceCohortRow,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "symbol": cohort.symbol,
        "company": cohort.company,
        "sector": cohort.sector,
        "industry": cohort.industry,
        "market_cap_basic": cohort.market_cap_basic,
        "performance_lens": cohort.performance_lens,
        "performance_field": cohort.performance_field,
        "performance_value": cohort.performance_value,
        "decile": cohort.decile,
        "decile_rank": cohort.decile_rank,
        "perf_percentile": cohort.perf_percentile,
        "premarket_change": cohort.premarket_change,
        "postmarket_change": cohort.postmarket_change,
        "gap": cohort.gap,
        "premarket_gap": cohort.premarket_gap,
    }


def _build_price_driven_profile_horizon_records(
    run_id: str,
    profile_name: str,
    prediction_rows: list[dict[str, Any]],
    cohort_maps: dict[str, dict[str, PerformanceCohortRow]],
    *,
    inverted_profiles: frozenset[str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row_number, prediction in enumerate(prediction_rows, start=1):
        symbol = _get_symbol_name(prediction["row"])
        base_record = _prediction_base_record(
            run_id, profile_name, row_number, prediction
        )
        for lens_key, cohort_map in cohort_maps.items():
            cohort = cohort_map.get(symbol)
            if cohort is None:
                continue
            for horizon_name, horizon in prediction["horizons"].items():
                record = dict(base_record)
                record.update(
                    {
                        "performance_lens": lens_key,
                        "decile": cohort.decile,
                        "performance_value": cohort.performance_value,
                        "horizon_name": horizon_name,
                        "score": horizon.get("score"),
                        "direction": horizon.get("direction"),
                        "confidence": horizon.get("confidence"),
                        "coverage": horizon.get("coverage"),
                        "setup": horizon.get("setup"),
                        "risk_adjusted_score": horizon.get("risk_adjusted_score"),
                        "risk_tier": horizon.get("risk_tier"),
                        "is_upward_exemplar": is_upward_exemplar(
                            profile_name,
                            horizon,
                            inverted_profiles=inverted_profiles,
                        ),
                    }
                )
                records.append(record)
    return records


def _build_price_driven_consensus_horizon_records(
    run_id: str,
    consensus_rows: list[dict[str, Any]],
    cohort_maps: dict[str, dict[str, PerformanceCohortRow]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row_number, consensus_row in enumerate(consensus_rows, start=1):
        symbol = consensus_row["ticker"]
        base_record = _consensus_base_record(run_id, row_number, consensus_row)
        for lens_key, cohort_map in cohort_maps.items():
            cohort = cohort_map.get(symbol)
            if cohort is None:
                continue
            for horizon_name, horizon in consensus_row["horizons"].items():
                record = dict(base_record)
                record.update(
                    {
                        "performance_lens": lens_key,
                        "decile": cohort.decile,
                        "performance_value": cohort.performance_value,
                        "horizon_name": horizon_name,
                        "score": horizon.get("score"),
                        "direction": horizon.get("direction"),
                        "confidence": horizon.get("confidence"),
                        "agreement_ratio": horizon.get("agreement_ratio"),
                        "opinions": horizon.get("opinions"),
                        "risk_adjusted_score": horizon.get("risk_adjusted_score"),
                        "risk_tier": horizon.get("risk_tier"),
                    }
                )
                records.append(record)
    return records


def _normalized_perf_score(perf_percentile: float) -> float:
    return (perf_percentile / 100.0) * 6.0 - 3.0


def _classify_opportunity_archetype(
    exemplar_profiles: list[str],
    decile: int,
    consensus_weeks_direction: str,
) -> str:
    if not exemplar_profiles:
        return "no_upward_signal"

    exemplar_set = set(exemplar_profiles)
    if decile <= 2 and consensus_weeks_direction in UPWARD_DIRECTIONS:
        return "consensus_divergence"
    if decile <= 2 and exemplar_set & REVERSAL_PROFILES:
        return "beaten_down_reversal"
    if decile <= 2 and exemplar_set & QUALITY_DIP_PROFILES:
        return "quality_dip"
    if decile <= 3 and exemplar_set & EARLY_INFLECTION_PROFILES:
        return "early_inflection"
    if exemplar_profiles:
        return "consensus_divergence"
    return "no_upward_signal"


def _compute_opportunity_score(
    *,
    decile: int,
    perf_percentile: float,
    bullish_profile_count: int,
    long_profile_coverage: float,
    consensus_weeks_score: float | None,
    reversal_exemplar_count: int,
    fragility_conflict: bool,
) -> float:
    score = 0.0
    if decile <= 2:
        score += 3.0 - float(decile)
    elif decile <= 3:
        score += 0.5

    score += bullish_profile_count * 0.75
    score += long_profile_coverage * 1.25
    score += reversal_exemplar_count * 0.5

    if consensus_weeks_score is not None:
        if consensus_weeks_score >= DIRECTIONAL_MOVE_SCORE_THRESHOLD:
            score += 1.0
        if consensus_weeks_score >= STRONG_MOVE_SCORE_THRESHOLD:
            score += 0.5
        divergence_gap = consensus_weeks_score - _normalized_perf_score(perf_percentile)
        score += max(0.0, divergence_gap) * 0.35

    if fragility_conflict:
        score -= 1.5

    return round(score, 4)


def _resolve_max_profile_score(
    row_idx: int,
    resolved_profiles: list[str],
    profiles_data: dict[str, list[dict[str, Any]]],
    primary_horizon: str,
) -> tuple[float | None, str | None]:
    max_score: float | None = None
    max_profile: str | None = None
    for profile_name in resolved_profiles:
        horizon = (
            profiles_data[profile_name][row_idx]
            .get("horizons", {})
            .get(primary_horizon, {})
        )
        score = horizon.get("score")
        if score is None:
            continue
        score_value = float(score)
        if max_score is None or score_value > max_score:
            max_score = score_value
            max_profile = profile_name
    return max_score, max_profile


def build_opportunity_candidates(
    run_id: str,
    scan_rows: list[dict[str, Any]],
    profiles_data: dict[str, list[dict[str, Any]]],
    consensus_rows: list[dict[str, Any]],
    cohort_maps: dict[str, dict[str, PerformanceCohortRow]],
    resolved_profiles: list[str],
    *,
    inverted_profiles: frozenset[str],
    long_profiles: frozenset[str],
    primary_horizon: str = "weeks",
) -> list[dict[str, Any]]:
    symbol_to_consensus = {
        consensus_row["ticker"]: consensus_row for consensus_row in consensus_rows
    }
    symbol_to_row_idx = {_get_symbol_name(row): idx for idx, row in enumerate(scan_rows)}

    records: list[dict[str, Any]] = []
    for lens_key, cohort_map in cohort_maps.items():
        for symbol, cohort in cohort_map.items():
            row_idx = symbol_to_row_idx.get(symbol)
            if row_idx is None:
                continue

            exemplar_profiles: list[str] = []
            reversal_exemplar_count = 0
            fragility_conflict = False

            for profile_name in resolved_profiles:
                prediction = profiles_data[profile_name][row_idx]
                horizon = prediction["horizons"].get(primary_horizon, {})
                if is_upward_exemplar(
                    profile_name,
                    horizon,
                    inverted_profiles=inverted_profiles,
                ):
                    exemplar_profiles.append(profile_name)
                    if profile_name in REVERSAL_PROFILES:
                        reversal_exemplar_count += 1

                if profile_name == "fragility_short" or profile_name.endswith(
                    "fragility_short"
                ):
                    fragility_score = horizon.get("score")
                    if (
                        isinstance(fragility_score, (int, float))
                        and fragility_score >= DIRECTIONAL_MOVE_SCORE_THRESHOLD
                    ):
                        fragility_conflict = True

            long_profile_total = len(
                [name for name in resolved_profiles if name in long_profiles]
            )
            bullish_profile_count = len(exemplar_profiles)
            long_profile_coverage = (
                bullish_profile_count / long_profile_total
                if long_profile_total
                else 0.0
            )

            consensus_row = symbol_to_consensus.get(symbol, {})
            consensus_horizon = consensus_row.get("horizons", {}).get(primary_horizon, {})
            consensus_weeks_score = consensus_horizon.get("score")
            consensus_weeks_direction = str(consensus_horizon.get("direction") or "")

            primary_prediction = None
            for profile_name in resolved_profiles:
                if profile_name in exemplar_profiles:
                    primary_prediction = profiles_data[profile_name][row_idx]
                    break
            if primary_prediction is None and resolved_profiles:
                primary_prediction = profiles_data[resolved_profiles[0]][row_idx]

            primary_horizon_data = (
                primary_prediction["horizons"].get(primary_horizon, {})
                if primary_prediction
                else {}
            )
            max_profile_score, max_profile_name = _resolve_max_profile_score(
                row_idx,
                resolved_profiles,
                profiles_data,
                primary_horizon,
            )

            opportunity_score = _compute_opportunity_score(
                decile=cohort.decile,
                perf_percentile=cohort.perf_percentile,
                bullish_profile_count=bullish_profile_count,
                long_profile_coverage=long_profile_coverage,
                consensus_weeks_score=consensus_weeks_score,
                reversal_exemplar_count=reversal_exemplar_count,
                fragility_conflict=fragility_conflict,
            )
            divergence_gap = None
            if consensus_weeks_score is not None:
                divergence_gap = consensus_weeks_score - _normalized_perf_score(
                    cohort.perf_percentile
                )

            records.append(
                {
                    "run_id": run_id,
                    "symbol": symbol,
                    "company": cohort.company,
                    "sector": cohort.sector,
                    "industry": cohort.industry,
                    "market_cap_basic": cohort.market_cap_basic,
                    "performance_lens": lens_key,
                    "performance_value": cohort.performance_value,
                    "decile": cohort.decile,
                    "perf_percentile": cohort.perf_percentile,
                    "opportunity_score": opportunity_score,
                    "opportunity_archetype": _classify_opportunity_archetype(
                        exemplar_profiles,
                        cohort.decile,
                        consensus_weeks_direction,
                    ),
                    "primary_horizon": primary_horizon,
                    "primary_horizon_score": primary_horizon_data.get("score"),
                    "primary_horizon_direction": primary_horizon_data.get("direction"),
                    "primary_horizon_ras": primary_horizon_data.get(
                        "risk_adjusted_score"
                    ),
                    "consensus_weeks_score": consensus_weeks_score,
                    "consensus_weeks_direction": consensus_weeks_direction,
                    "bullish_profile_count": bullish_profile_count,
                    "long_profile_coverage": long_profile_coverage,
                    "divergence_gap": divergence_gap,
                    "exemplar_profiles_json": json.dumps(
                        sorted(exemplar_profiles), ensure_ascii=False
                    ),
                    "has_upward_exemplar": bool(exemplar_profiles),
                    "max_profile_score": max_profile_score,
                    "max_profile_name": max_profile_name,
                }
            )
    return records


def _score_profiles_for_universe(
    scan_rows: list[dict[str, Any]],
    resolved_profiles: list[str],
    profile_registry: Any,
) -> dict[str, list[dict[str, Any]]]:
    _enrich_with_peer_metrics(scan_rows)
    derived_metrics = [_build_derived_metrics(row) for row in scan_rows]
    metric_profiles = _build_metric_profiles(scan_rows, derived_metrics)

    profiles_data: dict[str, list[dict[str, Any]]] = {}
    for profile_name in resolved_profiles:
        resolved_profile = resolve_move_prediction_scoring_profile(
            profile_name,
            profile_registry=profile_registry,
        )
        profiles_data[profile_name] = _build_prediction_rows(
            scan_rows,
            metric_profiles,
            derived_metrics,
            resolved_profile,
        )
    return profiles_data


def _log_overview_report(
    log_file: Path,
    *,
    run_id: str,
    scan_count: int,
    filtered_count: int,
    resolved_profiles: list[str],
    cohort_maps: dict[str, dict[str, PerformanceCohortRow]],
    opportunity_records: list[dict[str, Any]],
    primary_horizon: str,
) -> None:
    log_to_file(log_file, "=" * 140)
    log_to_file(log_file, "PRICE-DRIVEN SCORE ANALYSIS OVERVIEW")
    log_to_file(log_file, "=" * 140)
    log_to_file(log_file, f"Run ID: {run_id}")
    log_to_file(
        log_file,
        f"Scan rows: {scan_count} | After market-cap filter: {filtered_count}",
    )
    log_to_file(log_file, f"Profiles: {', '.join(resolved_profiles)}")
    log_to_file(log_file, f"Primary horizon for opportunity ranking: {primary_horizon}")
    log_to_file(
        log_file,
        "Goal: find upward-move setups in worst recent price performers (decile bucketing), not score↔perf correlation.",
    )
    log_to_file(log_file, "")

    for lens_key, cohort_map in cohort_maps.items():
        lens_label = PERFORMANCE_LENSES[lens_key]["label"]
        decile_counts: dict[int, int] = {decile: 0 for decile in range(1, 11)}
        for cohort in cohort_map.values():
            decile_counts[cohort.decile] += 1
        log_to_file(log_file, f"Lens: {lens_label} ({lens_key})")
        for decile in range(1, 11):
            log_to_file(log_file, f"  D{decile}: {decile_counts[decile]} symbols")
        log_to_file(log_file, "")

    log_to_file(log_file, "Top opportunity candidates (all lenses)")
    log_to_file(log_file, "-" * 140)
    ranked = sorted(
        opportunity_records,
        key=lambda record: (
            record.get("opportunity_score") or 0.0,
            record.get("bullish_profile_count") or 0,
        ),
        reverse=True,
    )
    log_to_file(
        log_file,
        f"{'Lens':<12} {'D':>3} {'Ticker':<14} {'Company':<40} {'Opp':>8} "
        f"{'MaxScr':>8} {'MaxProfile':<28} {'Archetype':<24} {'Bull#':>5} "
        f"{'Perf':>10} {'Consensus':>10}",
    )
    for record in ranked[:40]:
        company = str(record.get("company") or "")
        max_profile = str(record.get("max_profile_name") or "")
        log_to_file(
            log_file,
            f"{str(record['performance_lens']):<12} "
            f"{int(record['decile']):>3} "
            f"{str(record['symbol']):<14} "
            f"{company:<40} "
            f"{_format_score(record.get('opportunity_score')):>8} "
            f"{_format_score(record.get('max_profile_score')):>8} "
            f"{max_profile:<28} "
            f"{str(record.get('opportunity_archetype', ''))[:24]:<24} "
            f"{int(record.get('bullish_profile_count') or 0):>5} "
            f"{_format_percent(record.get('performance_value')):>10} "
            f"{_format_score(record.get('consensus_weeks_score')):>10}",
        )
    log_to_file(log_file, "")


def _log_price_lens_report(
    log_file: Path,
    lens_key: str,
    cohort_map: dict[str, PerformanceCohortRow],
    profile_horizon_records: list[dict[str, Any]],
    primary_horizon: str,
) -> None:
    lens_label = PERFORMANCE_LENSES[lens_key]["label"]
    log_to_file(log_file, "=" * 140)
    log_to_file(log_file, f"PRICE LENS: {lens_label} ({lens_key})")
    log_to_file(log_file, "=" * 140)

    exemplars_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for record in profile_horizon_records:
        if record["performance_lens"] != lens_key:
            continue
        if record["horizon_name"] != primary_horizon:
            continue
        if not record.get("is_upward_exemplar"):
            continue
        exemplars_by_symbol.setdefault(record["symbol"], []).append(record)

    for decile in range(1, 11):
        decile_symbols = [
            cohort.symbol
            for cohort in cohort_map.values()
            if cohort.decile == decile
        ]
        decile_symbols.sort(
            key=lambda symbol: cohort_map[symbol].performance_value
        )
        log_to_file(log_file, f"Decile D{decile} ({len(decile_symbols)} symbols)")
        log_to_file(log_file, "-" * 140)
        if not decile_symbols:
            log_to_file(log_file, "  (empty)")
            log_to_file(log_file, "")
            continue

        log_to_file(
            log_file,
            f"  {'Ticker':<14} {'Company':<40} {'Perf':>10}  Profiles",
        )
        for symbol in decile_symbols:
            cohort = cohort_map[symbol]
            exemplar_records = exemplars_by_symbol.get(symbol, [])
            profile_names = sorted(
                {record["profile_name"] for record in exemplar_records}
            )
            if profile_names:
                profile_summary = ", ".join(profile_names)
            else:
                profile_summary = "no upward exemplar"
            log_to_file(
                log_file,
                f"  {symbol:<14} {cohort.company:<40} "
                f"perf={_format_percent(cohort.performance_value):>10} "
                f"profiles: {profile_summary}",
            )
        log_to_file(log_file, "")


def _log_profile_upward_exemplars_report(
    log_file: Path,
    profile_name: str,
    profile_horizon_records: list[dict[str, Any]],
    primary_horizon: str,
) -> None:
    log_to_file(log_file, "=" * 140)
    log_to_file(log_file, f"PROFILE UPWARD EXEMPLARS: {profile_name}")
    log_to_file(log_file, "=" * 140)

    worst_decile_records = [
        record
        for record in profile_horizon_records
        if record["profile_name"] == profile_name
        and record["horizon_name"] == primary_horizon
        and record.get("is_upward_exemplar")
        and int(record.get("decile") or 99) <= 2
    ]
    worst_decile_records.sort(
        key=lambda record: (
            int(record.get("decile") or 99),
            -(record.get("score") or 0.0),
        )
    )

    if not worst_decile_records:
        log_to_file(log_file, "No upward exemplars in worst deciles (D1-D2).")
        log_to_file(log_file, "")
        return

    log_to_file(
        log_file,
        f"{'Lens':<12} {'D':>3} {'Ticker':<14} {'Company':<40} {'Score':>8} "
        f"{'Dir':<12} {'Perf':>10} {'Setup':<24}",
    )
    log_to_file(log_file, "-" * 140)
    for record in worst_decile_records[:50]:
        company = str(record.get("company") or "")
        log_to_file(
            log_file,
            f"{str(record['performance_lens']):<12} "
            f"{int(record['decile']):>3} "
            f"{str(record['symbol']):<14} "
            f"{company:<40} "
            f"{_format_score(record.get('score')):>8} "
            f"{str(record.get('direction', ''))[:12]:<12} "
            f"{_format_percent(record.get('performance_value')):>10} "
            f"{str(record.get('setup', ''))[:24]:<24}",
        )
    log_to_file(log_file, "")


def run_price_driven_score_analysis_duckdb(
    scan_data: list[dict[str, Any]] | Mapping[str, Any],
    *,
    profile_names: list[str] | None = None,
    profile_suite_path: str | Path | None = None,
    industries: list[str] | str | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    output_dir: str | Path | None = None,
    database_path: str | Path | None = None,
    parquet_dir: str | Path | None = None,
    run_label: str | None = None,
    reference_time: datetime | None = None,
    api_request_metadata: Mapping[str, Any] | None = None,
    export_parquet: bool = True,
    create_indexes: bool = False,
    primary_horizon: str = "weeks",
) -> dict[str, Any]:
    """Price-performance decile analysis with move-prediction profile scoring.

  Buckets the filtered universe by ``change``, ``Perf.5D``, and ``Perf.1M`` deciles,
  scores the full universe with the requested profile suite, and persists joined
  results to a weekly DuckDB database under
  ``prediction_analysis/price_driven_score_analysis/duckdb_runs``.
    """
    profile_suite = resolve_profile_suite(profile_suite_path, profile_names)
    created_at_utc = reference_time or datetime.now(tz=timezone.utc)
    if created_at_utc.tzinfo is None:
        created_at_utc = created_at_utc.replace(tzinfo=timezone.utc)
    created_at_utc = created_at_utc.astimezone(timezone.utc)

    run_id = _build_price_driven_run_id(run_label, created_at_utc)
    run_id_generated = not bool(_slugify(run_label) if run_label else None)
    scan_rows, resolved_api_request_metadata = _extract_duckdb_scan_input(
        scan_data,
        api_request_metadata=api_request_metadata,
    )
    scan_count = len(scan_rows)
    filtered_rows = _filter_scan_data_by_market_cap(
        scan_rows,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
    )

    resolved_profiles = [
        resolve_move_prediction_scoring_profile(
            profile_name,
            profile_registry=profile_suite["registry"],
        ).name
        for profile_name in profile_suite["profile_names"]
    ]
    profile_config_snapshots = _build_profile_config_snapshots(
        resolved_profiles,
        profile_registry=profile_suite["registry"],
        consensus_profile_weights=profile_suite["consensus_profile_weights"],
        profile_suite_id=profile_suite.get("suite_id"),
        profile_suite_version=profile_suite.get("suite_version"),
    )
    profile_config_hashes = {
        snapshot["profile_name"]: snapshot["profile_config_hash"]
        for snapshot in profile_config_snapshots
    }
    inverted_profiles = frozenset(profile_suite.get("inverted_consensus_profiles") or [])
    long_profiles = frozenset(profile_suite.get("long_consensus_profiles") or [])
    code_version_metadata = _collect_code_version_metadata()
    industries_normalized = _normalize_industries(industries)

    storage_layout = _build_price_driven_storage_layout(
        run_id=run_id,
        created_at_utc=created_at_utc,
        output_dir=output_dir,
        database_path=database_path,
        parquet_dir=parquet_dir,
    )
    storage_layout.run_output_dir.mkdir(parents=True, exist_ok=True)
    storage_layout.database_path.parent.mkdir(parents=True, exist_ok=True)
    if export_parquet:
        storage_layout.parquet_dir.mkdir(parents=True, exist_ok=True)

    cohort_maps = build_performance_cohort_maps(filtered_rows)
    profiles_data = _score_profiles_for_universe(
        filtered_rows,
        resolved_profiles,
        profile_suite["registry"],
    )
    consensus_rows = _build_consensus_scores(
        filtered_rows,
        profile_names=resolved_profiles,
        profile_registry=profile_suite["registry"],
        consensus_profile_weights=profile_suite["consensus_profile_weights"],
        inverted_consensus_profiles=inverted_profiles,
        long_consensus_profiles=long_profiles,
    )

    profile_horizon_records: list[dict[str, Any]] = []
    profile_component_records: list[dict[str, Any]] = []
    for profile_name in resolved_profiles:
        profile_horizon_records.extend(
            _build_price_driven_profile_horizon_records(
                run_id,
                profile_name,
                profiles_data[profile_name],
                cohort_maps,
                inverted_profiles=inverted_profiles,
            )
        )
        profile_component_records.extend(
            _build_profile_component_records(
                run_id,
                profile_name,
                profiles_data[profile_name],
            )
        )

    consensus_horizon_records = _build_price_driven_consensus_horizon_records(
        run_id,
        consensus_rows,
        cohort_maps,
    )
    opportunity_records = build_opportunity_candidates(
        run_id,
        filtered_rows,
        profiles_data,
        consensus_rows,
        cohort_maps,
        resolved_profiles,
        inverted_profiles=inverted_profiles,
        long_profiles=long_profiles,
        primary_horizon=primary_horizon,
    )

    cohort_records = [
        _cohort_record_from_row(run_id, cohort)
        for cohort_map in cohort_maps.values()
        for cohort in cohort_map.values()
    ]

    overview_log = storage_layout.run_output_dir / "overview.log"
    lens_logs: dict[str, Path] = {}
    profile_logs: dict[str, Path] = {}
    starter_views: list[str] = []
    parquet_exports: dict[str, Path] = {}

    with _duckdb_weekly_writer_lock(storage_layout.database_path):
        with PriceDrivenScoreDuckDBStore(
            database_path=storage_layout.database_path,
            parquet_dir=storage_layout.parquet_dir if export_parquet else None,
        ) as duckdb_store:
            duckdb_store.begin_transaction()
            try:
                duckdb_store.drop_analysis_indexes()
                duckdb_store.delete_run_data(run_id)
                duckdb_store.register_run(
                    run_id=run_id,
                    created_at_utc=created_at_utc,
                    suite_name="tradingview_price_driven_score_analysis_duckdb",
                    scan_data_count=len(filtered_rows),
                    profile_names=resolved_profiles,
                    industries=industries_normalized,
                    min_market_cap_usd=min_market_cap_usd,
                    max_market_cap_usd=max_market_cap_usd,
                    include_blind_spot_sections=False,
                    profile_config_hashes=profile_config_hashes,
                    api_request_metadata=resolved_api_request_metadata,
                    code_version_metadata=code_version_metadata,
                    run_label=run_label,
                    run_id_generated=run_id_generated,
                    notes=(
                        "Price-driven decile analysis with move-prediction profile scoring. "
                        f"profile_suite_id={profile_suite.get('suite_id')}; "
                        f"profile_suite_version={profile_suite.get('suite_version')}; "
                        f"primary_horizon={primary_horizon}"
                    ),
                )
                duckdb_store.append_profile_config_snapshots(
                    [
                        {
                            "run_id": run_id,
                            **snapshot,
                            "captured_at_utc": created_at_utc,
                        }
                        for snapshot in profile_config_snapshots
                    ]
                )
                duckdb_store.append_tabular_output(
                    "raw_scan_rows",
                    _build_raw_csv_headers(filtered_rows),
                    _build_raw_csv_rows(filtered_rows),
                    context={"run_id": run_id},
                )
                duckdb_store.append_performance_cohort_rows(cohort_records)
                duckdb_store.append_price_driven_profile_horizon_scores(
                    profile_horizon_records
                )
                duckdb_store.append_profile_components(profile_component_records)
                duckdb_store.append_price_driven_consensus_horizon_scores(
                    consensus_horizon_records
                )
                duckdb_store.append_opportunity_candidates(opportunity_records)
                duckdb_store.commit()
            except Exception:
                duckdb_store.rollback()
                raise

            if create_indexes:
                duckdb_store.create_analysis_indexes()

            starter_views = duckdb_store.create_starter_views()

            if export_parquet:
                parquet_exports = duckdb_store.export_tables_to_parquet(
                    run_id=run_id,
                    parquet_dir=storage_layout.parquet_dir,
                )

    _log_overview_report(
        overview_log,
        run_id=run_id,
        scan_count=scan_count,
        filtered_count=len(filtered_rows),
        resolved_profiles=resolved_profiles,
        cohort_maps=cohort_maps,
        opportunity_records=opportunity_records,
        primary_horizon=primary_horizon,
    )
    duckdb_store_ref = PriceDrivenScoreDuckDBStore(storage_layout.database_path)
    with duckdb_store_ref:
        duckdb_store_ref.register_report(
            run_id=run_id,
            report_key="overview",
            report_type="overview_log",
            file_path=overview_log,
        )

    for lens_key in PERFORMANCE_LENSES:
        lens_log = storage_layout.run_output_dir / f"price_lens_{lens_key}.log"
        _log_price_lens_report(
            lens_log,
            lens_key,
            cohort_maps[lens_key],
            profile_horizon_records,
            primary_horizon,
        )
        lens_logs[lens_key] = lens_log
        with PriceDrivenScoreDuckDBStore(storage_layout.database_path) as store:
            store.register_report(
                run_id=run_id,
                report_key=f"price_lens_{lens_key}",
                report_type="price_lens_log",
                file_path=lens_log,
            )

    for profile_name in resolved_profiles:
        profile_log = (
            storage_layout.run_output_dir / f"profile_{profile_name}_upward_exemplars.log"
        )
        _log_profile_upward_exemplars_report(
            profile_log,
            profile_name,
            profile_horizon_records,
            primary_horizon,
        )
        profile_logs[profile_name] = profile_log
        with PriceDrivenScoreDuckDBStore(storage_layout.database_path) as store:
            store.register_report(
                run_id=run_id,
                report_key=f"profile_{profile_name}_upward_exemplars",
                report_type="profile_upward_exemplars_log",
                profile_name=profile_name,
                file_path=profile_log,
            )

    result: dict[str, Any] = {
        "_duckdb_database": storage_layout.database_path,
        "_duckdb_run_id": run_id,
        "_duckdb_run_output_dir": storage_layout.run_output_dir,
        "_duckdb_parquet_dir": storage_layout.parquet_dir,
        "_duckdb_period_dir": storage_layout.period_dir,
        "_duckdb_overview_log": overview_log,
        "_duckdb_starter_views": starter_views,
        "_price_lens_logs": lens_logs,
        "_profile_upward_exemplar_logs": profile_logs,
    }
    if export_parquet:
        result["_duckdb_parquet_exports"] = parquet_exports
    return result


__all__ = [
    "PERFORMANCE_LENSES",
    "PerformanceCohortRow",
    "assign_performance_deciles",
    "build_opportunity_candidates",
    "build_performance_cohort_maps",
    "is_upward_exemplar",
    "run_price_driven_score_analysis_duckdb",
]
