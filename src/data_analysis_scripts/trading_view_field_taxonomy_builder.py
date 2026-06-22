"""Build TradingView field taxonomy splits for meaning, usage, and scan relevance."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from data_analysis_scripts.trading_view_all_fields_metric_pattern_analyzer import (
    AllFieldsAnalysisConfig,
    FORWARD_PERFORMANCE_FIELDS,
    PERFORMANCE_PREFIXES,
    QUANTIFIABLE_PREDICTOR_CATALOG_TYPES,
    _classify_scan_period_predictor_profile,
    _is_predictor_excluded,
    _normalize_float,
    _quote_path_literal,
    _quote_sql_literal,
    _write_csv_rows,
)
from data_analysis_scripts.trading_view_field_semantic_classifier import (
    SEMANTIC_BUCKETS,
    UNIVERSE_FILTER_BASE_NAMES,
    classify_field_semantics,
    mode_bucket,
    parse_name,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIELD_CATALOG = PROJECT_ROOT / "savedData" / "trading_view_stock_fields.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "savedData" / "trading_view_field_taxonomy"
DEFAULT_SCAN_RUN_ROOT = (
    PROJECT_ROOT
    / "logs"
    / "tradingview_analysis"
    / "trading_view_all_fields_data"
    / "pattern_analysis"
    / "runs"
    / "scan_period_close_forward_tracking_29mar_15jun2026_b108820e"
)
DEFAULT_PROFILE_DIR = PROJECT_ROOT / "config" / "move_prediction_profiles" / "profiles"
TAXONOMY_SCHEMA_VERSION = "trading_view_field_taxonomy_v1"

USAGE_SPLIT_FILES: dict[str, str] = {
    "predictor_eligible": "predictor_eligible",
    "universe_filters": "universe_filter",
    "performance_targets": "target",
    "metadata_only": "metadata_only",
    "structural_non_tradable": "structural_non_tradable",
    "sparse_or_noise": "sparse_or_noise",
    "intraday_variants": "intraday_variants",
    "daily_and_higher": "daily_and_higher",
    "profile_referenced": "profile_referenced",
    "unused_by_profiles": "unused_by_profiles",
}

RELEVANCE_SPLIT_FILES: dict[str, str] = {
    "tier_promote": "promote",
    "tier_watch": "watch",
    "tier_neutral": "neutral",
    "tier_demote": "demote",
    "tier_ignore": "ignore",
    "stability_passed": "stability_passed",
    "stability_failed": "stability_failed",
    "no_scan_evidence": "no_scan_evidence",
}


@dataclass(frozen=True)
class ProfileFieldReference:
    profile_id: str
    component: str
    signal_key: str


def _import_duckdb():
    import duckdb

    return duckdb


def _file_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "path": path.as_posix(),
        "size_bytes": stat.st_size,
        "modified_utc": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "sha256": digest,
    }


def _is_performance_field(field_name: str) -> bool:
    if field_name in FORWARD_PERFORMANCE_FIELDS:
        return True
    return any(field_name.startswith(prefix) for prefix in PERFORMANCE_PREFIXES)


def _usage_role(
    *,
    field_name: str,
    base_name: str,
    catalog_type: str,
    is_predictor_candidate: bool,
    predictor_profile_class: str,
) -> str:
    if _is_performance_field(field_name):
        return "target"
    if base_name in UNIVERSE_FILTER_BASE_NAMES:
        return "universe_filter"
    if predictor_profile_class == "structural_non_tradable":
        return "metadata_only"
    if predictor_profile_class == "sparse_or_noise":
        return "exclude_redundant"
    if catalog_type in {"text", "bool", "time", "time-yyyymmdd", "interface", "map", "set"}:
        return "metadata_only"
    if not is_predictor_candidate:
        return "exclude_redundant"
    return "predictor_feature"


def _load_catalog_rows(field_catalog: Path) -> list[dict[str, str]]:
    with field_catalog.open("r", encoding="utf-8-sig", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def _load_profile_field_references(profile_dir: Path) -> dict[str, list[ProfileFieldReference]]:
    references: dict[str, list[ProfileFieldReference]] = defaultdict(list)
    if not profile_dir.exists():
        return references

    for profile_path in sorted(profile_dir.glob("*.json")):
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
        profile_id = str(payload.get("profile_id") or profile_path.stem)
        component_weights = (
            payload.get("profile", {}).get("component_signal_weights", {}) or {}
        )
        for component, signals in component_weights.items():
            if not isinstance(signals, dict):
                continue
            for signal_key in signals:
                references[signal_key].append(
                    ProfileFieldReference(
                        profile_id=profile_id,
                        component=component,
                        signal_key=signal_key,
                    )
                )
    return references


def _load_scan_evidence(
    scan_run_root: Path,
    *,
    stability_min_runs: int,
    stability_sign_gate: float,
    performance_target: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    period_total_parquet = (
        scan_run_root / "period_total" / "period_field_performance_patterns.parquet"
    )
    stability_parquet = scan_run_root / "aggregates" / "cross_run_field_stability.parquet"
    rolling_parquets = sorted(
        scan_run_root.glob("rolling_windows/*/period_field_performance_patterns.parquet")
    )
    if not period_total_parquet.exists():
        raise FileNotFoundError(
            f"Missing period-total patterns: {period_total_parquet.as_posix()}"
        )
    if not rolling_parquets:
        raise FileNotFoundError("No rolling-window pattern summaries found.")

    period_total_sql = _quote_path_literal(period_total_parquet)
    rolling_paths_sql = ", ".join(_quote_path_literal(path) for path in rolling_parquets)
    stability_sql = (
        _quote_path_literal(stability_parquet) if stability_parquet.exists() else "NULL"
    )

    duckdb = _import_duckdb()
    conn = duckdb.connect()
    try:
        period_result = conn.execute(
            f"""
            SELECT predictor_field,
                predictor_display_name,
                predictor_type,
                pattern_score,
                pearson_corr_adjusted,
                quintile_spread_adjusted,
                top_quintile_avg_perf,
                bottom_quintile_avg_perf,
                pair_n,
                predictor_fill_rate
            FROM read_parquet({period_total_sql})
            WHERE performance_field = {_quote_sql_literal(performance_target)}
            """
        )
        period_columns = [col[0] for col in period_result.description]
        period_row_dicts = [
            dict(zip(period_columns, row)) for row in period_result.fetchall()
        ]

        progression_result = conn.execute(
            f"""
            WITH window_patterns AS (
                SELECT predictor_field,
                    pattern_score,
                    quintile_spread_adjusted
                FROM read_parquet([{rolling_paths_sql}])
                WHERE performance_field = {_quote_sql_literal(performance_target)}
            )
            SELECT predictor_field,
                COUNT(*) AS windows_seen,
                SUM(CASE WHEN pattern_score > 0 THEN 1 ELSE 0 END) AS positive_score_windows,
                SUM(CASE WHEN quintile_spread_adjusted > 0 THEN 1 ELSE 0 END) AS positive_spread_windows,
                AVG(pattern_score) AS mean_pattern_score,
                MEDIAN(pattern_score) AS median_pattern_score
            FROM window_patterns
            GROUP BY predictor_field
            """
        )
        progression_columns = [col[0] for col in progression_result.description]
        progression_by_field = {
            str(row[0]): dict(zip(progression_columns, row))
            for row in progression_result.fetchall()
        }

        stability_by_field: dict[str, dict[str, Any]] = {}
        if stability_parquet.exists():
            stability_result = conn.execute(
                f"""
                SELECT predictor_field,
                    runs_seen,
                    sign_consistency_ratio,
                    rank_stability_score,
                    median_quintile_spread,
                    mean_quintile_spread,
                    median_pearson,
                    mean_pearson
                FROM read_parquet({stability_sql})
                WHERE performance_field = {_quote_sql_literal(performance_target)}
                """
            )
            stability_columns = [col[0] for col in stability_result.description]
            stability_by_field = {
                str(row[0]): dict(zip(stability_columns, row))
                for row in stability_result.fetchall()
            }
    finally:
        conn.close()

    evidence_by_field: dict[str, dict[str, Any]] = {}
    for row in period_row_dicts:
        field_name = str(row["predictor_field"])
        progression = progression_by_field.get(field_name, {})
        stability = stability_by_field.get(field_name, {})
        period_score = _normalize_float(row.get("pattern_score"))
        rank_stability = _normalize_float(stability.get("rank_stability_score"))
        sign_consistency = _normalize_float(stability.get("sign_consistency_ratio"))
        runs_seen = int(stability.get("runs_seen") or progression.get("windows_seen") or 0)
        passes_stability = (
            runs_seen >= stability_min_runs
            and sign_consistency is not None
            and sign_consistency >= stability_sign_gate
        )
        mean_window_score = _normalize_float(progression.get("mean_pattern_score"))
        composite_relevance_score = (
            (period_score or 0.0) * 0.45
            + (rank_stability or 0.0) * 0.35
            + (mean_window_score or 0.0) * 0.20
        )
        spread = _normalize_float(row.get("quintile_spread_adjusted"))
        direction_anchor = spread if spread is not None else period_score
        if direction_anchor is None or abs(direction_anchor) < 1e-9:
            relevance_direction = "neutral"
        elif direction_anchor > 0:
            relevance_direction = "bullish"
        else:
            relevance_direction = "bearish"

        evidence_by_field[field_name] = {
            "period_pattern_score": period_score,
            "period_quintile_spread_pp": spread,
            "period_pearson": _normalize_float(row.get("pearson_corr_adjusted")),
            "period_top_quintile_avg_pct": _normalize_float(row.get("top_quintile_avg_perf")),
            "period_bottom_quintile_avg_pct": _normalize_float(
                row.get("bottom_quintile_avg_perf")
            ),
            "pair_n": int(row.get("pair_n") or 0),
            "predictor_fill_rate": _normalize_float(row.get("predictor_fill_rate")),
            "windows_seen": int(progression.get("windows_seen") or 0),
            "positive_score_windows": int(progression.get("positive_score_windows") or 0),
            "positive_spread_windows": int(progression.get("positive_spread_windows") or 0),
            "mean_window_pattern_score": mean_window_score,
            "median_window_pattern_score": _normalize_float(
                progression.get("median_pattern_score")
            ),
            "runs_seen": runs_seen,
            "sign_consistency_ratio": sign_consistency,
            "rank_stability_score": rank_stability,
            "median_stability_quintile_spread_pp": _normalize_float(
                stability.get("median_quintile_spread")
            ),
            "passes_stability_gate": passes_stability,
            "composite_relevance_score": composite_relevance_score,
            "relevance_direction": relevance_direction,
            "scan_evidence_status": "present",
        }

    meta = {
        "period_total_parquet": period_total_parquet.as_posix(),
        "rolling_window_count": len(rolling_parquets),
        "stability_parquet": stability_parquet.as_posix()
        if stability_parquet.exists()
        else None,
        "performance_target": performance_target,
        "stability_min_runs": stability_min_runs,
        "sign_consistency_gate": stability_sign_gate,
        "field_count_with_evidence": len(evidence_by_field),
    }
    return evidence_by_field, meta


def _assign_relevance_tiers(
    variant_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    tradable_positive: list[dict[str, Any]] = []
    for row in variant_rows:
        profile_class = row.get("predictor_profile_class")
        composite = row.get("composite_relevance_score")
        if (
            profile_class == "tradable_price_indicator"
            and composite is not None
            and composite > 0
        ):
            tradable_positive.append(row)

    promote_threshold = None
    if tradable_positive:
        scores = sorted(
            float(row["composite_relevance_score"])
            for row in tradable_positive
            if row.get("composite_relevance_score") is not None
        )
        if scores:
            index = max(0, math.ceil(0.9 * len(scores)) - 1)
            promote_threshold = scores[index]

    for row in variant_rows:
        profile_class = row.get("predictor_profile_class")
        status = row.get("scan_evidence_status", "no_data")
        composite = row.get("composite_relevance_score")
        period_score = row.get("period_pattern_score")
        passes_stability = bool(row.get("passes_stability_gate"))
        direction = row.get("relevance_direction")

        if profile_class in {"structural_non_tradable", "sparse_or_noise"}:
            row["relevance_tier"] = "ignore"
        elif status != "present":
            row["relevance_tier"] = "neutral"
            row["scan_evidence_status"] = "no_data"
        elif (
            passes_stability
            and profile_class == "tradable_price_indicator"
            and composite is not None
            and promote_threshold is not None
            and composite >= promote_threshold
            and composite > 0
        ):
            row["relevance_tier"] = "promote"
        elif (
            passes_stability
            and profile_class == "tradable_price_indicator"
            and direction == "bearish"
        ):
            row["relevance_tier"] = "demote"
        elif (
            profile_class == "tradable_price_indicator"
            and period_score is not None
            and period_score > 0
        ):
            row["relevance_tier"] = "watch"
        else:
            row["relevance_tier"] = "neutral"

        row["stability_split"] = (
            "stability_passed"
            if passes_stability
            else "stability_failed" if status == "present" else "no_scan_evidence"
        )
    return variant_rows


def _build_variant_rows(
    catalog_rows: Sequence[Mapping[str, str]],
    profile_refs: Mapping[str, Sequence[ProfileFieldReference]],
    evidence_by_field: Mapping[str, Mapping[str, Any]],
    analysis_config: AllFieldsAnalysisConfig,
) -> list[dict[str, Any]]:
    variant_rows: list[dict[str, Any]] = []
    for row in catalog_rows:
        field_name = str(row.get("Name", "")).strip()
        if not field_name:
            continue
        display_name = str(row.get("Display name", "")).strip()
        catalog_type = str(row.get("Type", "")).strip()
        semantics = classify_field_semantics(field_name, display_name, catalog_type)
        base_name = semantics.base_name
        is_predictor_candidate = not _is_predictor_excluded(field_name, analysis_config)
        is_quantifiable = catalog_type in QUANTIFIABLE_PREDICTOR_CATALOG_TYPES
        predictor_profile_class = _classify_scan_period_predictor_profile(field_name)
        usage = _usage_role(
            field_name=field_name,
            base_name=base_name,
            catalog_type=catalog_type,
            is_predictor_candidate=is_predictor_candidate,
            predictor_profile_class=predictor_profile_class,
        )

        refs = list(profile_refs.get(field_name, []))
        if not refs:
            refs = list(profile_refs.get(base_name, []))

        profile_ids = sorted({ref.profile_id for ref in refs})
        components = sorted({ref.component for ref in refs})
        evidence = dict(evidence_by_field.get(field_name, {}))
        if not evidence:
            evidence = {
                "scan_evidence_status": "no_data",
                "period_pattern_score": None,
                "period_quintile_spread_pp": None,
                "period_pearson": None,
                "period_top_quintile_avg_pct": None,
                "period_bottom_quintile_avg_pct": None,
                "pair_n": None,
                "predictor_fill_rate": None,
                "windows_seen": None,
                "positive_score_windows": None,
                "positive_spread_windows": None,
                "mean_window_pattern_score": None,
                "median_window_pattern_score": None,
                "runs_seen": None,
                "sign_consistency_ratio": None,
                "rank_stability_score": None,
                "median_stability_quintile_spread_pp": None,
                "passes_stability_gate": False,
                "composite_relevance_score": None,
                "relevance_direction": "neutral",
            }

        variant_rows.append(
            {
                "field_name": field_name,
                "display_name": display_name,
                "catalog_type": catalog_type,
                "explanation": str(row.get("explanation", "")).strip(),
                "model_use": str(row.get("model_use", "")).strip(),
                "base_name": base_name,
                "timeframe": semantics.timeframe or "",
                "lag": semantics.lag or "",
                "semantic_bucket": semantics.semantic_bucket,
                "semantic_tags": "|".join(semantics.semantic_tags),
                "period_basis": semantics.period_basis or "",
                "timeframe_class": semantics.timeframe_class,
                "indicator_family": semantics.indicator_family or "",
                "is_timeframe_variant": semantics.is_timeframe_variant,
                "is_quantifiable": is_quantifiable,
                "is_performance_field": _is_performance_field(field_name),
                "is_predictor_candidate": is_predictor_candidate,
                "predictor_profile_class": predictor_profile_class,
                "usage_role": usage,
                "used_in_move_prediction_profiles": "|".join(profile_ids),
                "move_prediction_component": "|".join(components),
                "profile_referenced": bool(profile_ids),
                **evidence,
            }
        )
    return _assign_relevance_tiers(variant_rows)


def _recommended_default_variant(
    family_variants: Sequence[Mapping[str, Any]],
) -> str:
    def sort_key(row: Mapping[str, Any]) -> tuple[int, float, str]:
        timeframe = str(row.get("timeframe") or "")
        timeframe_rank = {
            "": 0,
            "1W": 1,
            "1M": 2,
        }.get(timeframe, 9)
        fill = float(row.get("predictor_fill_rate") or 0.0)
        return (timeframe_rank, -fill, str(row.get("field_name")))

    if not family_variants:
        return ""
    return str(sorted(family_variants, key=sort_key)[0]["field_name"])


def _build_family_rows(variant_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in variant_rows:
        grouped[str(row["base_name"])].append(dict(row))

    family_rows: list[dict[str, Any]] = []
    for base_name, variants in sorted(grouped.items()):
        buckets = [str(row["semantic_bucket"]) for row in variants]
        family_bucket, bucket_conflict = mode_bucket(buckets)
        tradable_variants = [
            row
            for row in variants
            if row.get("predictor_profile_class") == "tradable_price_indicator"
        ]
        tier_source = tradable_variants or variants
        tier_rank = {"promote": 5, "watch": 4, "demote": 3, "neutral": 2, "ignore": 1}
        family_tier = max(
            (str(row.get("relevance_tier") or "neutral") for row in tier_source),
            key=lambda tier: tier_rank.get(tier, 0),
        )
        if tradable_variants:
            tradable_tiers = [
                str(row.get("relevance_tier") or "neutral") for row in tradable_variants
            ]
            family_tier = max(tradable_tiers, key=lambda tier: tier_rank.get(tier, 0))

        composites = [
            float(row["composite_relevance_score"])
            for row in variants
            if row.get("composite_relevance_score") is not None
        ]
        best_variant_row = max(
            variants,
            key=lambda row: abs(float(row.get("composite_relevance_score") or 0.0)),
            default={},
        )
        period_scores = [
            float(row["period_pattern_score"])
            for row in variants
            if row.get("period_pattern_score") is not None
        ]
        spreads = [
            float(row["period_quintile_spread_pp"])
            for row in variants
            if row.get("period_quintile_spread_pp") is not None
        ]
        stability_flags = [
            bool(row.get("passes_stability_gate")) for row in variants
        ]

        profile_ids = sorted(
            {
                profile_id
                for row in variants
                for profile_id in str(row.get("used_in_move_prediction_profiles") or "").split(
                    "|"
                )
                if profile_id
            }
        )
        components = sorted(
            {
                component
                for row in variants
                for component in str(row.get("move_prediction_component") or "").split("|")
                if component
            }
        )

        default_variant_name = _recommended_default_variant(variants)
        default_variant = next(
            (row for row in variants if row.get("field_name") == default_variant_name),
            variants[0] if variants else {},
        )
        family_rows.append(
            {
                "base_name": base_name,
                "variant_count": len(variants),
                "tradable_variant_count": len(tradable_variants),
                "semantic_bucket": family_bucket,
                "semantic_bucket_conflict": bucket_conflict,
                "relevance_tier": family_tier,
                "family_relevance_tier": family_tier,
                "recommended_default_variant": default_variant_name,
                "best_variant": str(best_variant_row.get("field_name") or ""),
                "max_composite_relevance_score": max(composites) if composites else None,
                "max_period_pattern_score": max(period_scores) if period_scores else None,
                "median_quintile_spread_pp": median(spreads) if spreads else None,
                "stability_pass_rate": (
                    sum(1 for flag in stability_flags if flag) / len(stability_flags)
                    if stability_flags
                    else 0.0
                ),
                "passes_stability_gate": any(stability_flags),
                "scan_evidence_status": (
                    "present"
                    if any(row.get("scan_evidence_status") == "present" for row in variants)
                    else "no_data"
                ),
                "composite_relevance_score": best_variant_row.get("composite_relevance_score"),
                "period_pattern_score": best_variant_row.get("period_pattern_score"),
                "is_predictor_candidate": any(
                    row.get("is_predictor_candidate") for row in variants
                ),
                "all_predictor_candidate": all(
                    row.get("is_predictor_candidate") for row in variants
                ),
                "profile_referenced": any(row.get("profile_referenced") for row in variants),
                "usage_role": str(default_variant.get("usage_role") or ""),
                "predictor_profile_class": str(
                    default_variant.get("predictor_profile_class") or ""
                ),
                "timeframe_class": str(default_variant.get("timeframe_class") or ""),
                "has_intraday_variant": any(
                    row.get("timeframe_class") == "intraday" for row in variants
                ),
                "has_daily_or_higher_variant": any(
                    row.get("timeframe_class") in {"daily", "weekly", "monthly"}
                    for row in variants
                ),
                "used_in_move_prediction_profiles": "|".join(profile_ids),
                "move_prediction_component": "|".join(components),
                "usage_roles": "|".join(
                    sorted({str(row.get("usage_role")) for row in variants})
                ),
                "predictor_profile_classes": "|".join(
                    sorted({str(row.get("predictor_profile_class")) for row in variants})
                ),
            }
        )
    return family_rows


def _write_bucket_splits(
    rows: Sequence[Mapping[str, Any]],
    output_dir: Path,
    bucket_field: str,
    buckets: Sequence[str],
) -> dict[str, str]:
    output_paths: dict[str, str] = {}
    by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_bucket[str(row.get(bucket_field) or "other")].append(dict(row))

    for bucket in buckets:
        bucket_rows = by_bucket.get(bucket, [])
        path = output_dir / f"{bucket}.csv"
        _write_csv_rows(path, bucket_rows, fieldnames=list(rows[0].keys()) if rows else None)
        output_paths[bucket] = path.as_posix()
    return output_paths


def _write_usage_splits(
    rows: Sequence[Mapping[str, Any]], output_dir: Path, *, family_level: bool = False
) -> dict[str, str]:
    output_paths: dict[str, str] = {}
    for filename, predicate in USAGE_SPLIT_FILES.items():
        if predicate == "predictor_eligible":
            split_rows = [
                row
                for row in rows
                if row.get("is_predictor_candidate")
                and row.get("usage_role") == "predictor_feature"
            ]
        elif predicate == "universe_filter":
            split_rows = [row for row in rows if row.get("usage_role") == "universe_filter"]
        elif predicate == "target":
            split_rows = [row for row in rows if row.get("usage_role") == "target"]
        elif predicate == "metadata_only":
            split_rows = [row for row in rows if row.get("usage_role") == "metadata_only"]
        elif predicate == "structural_non_tradable":
            if family_level:
                split_rows = [
                    row
                    for row in rows
                    if "structural_non_tradable"
                    in str(row.get("predictor_profile_classes") or "")
                ]
            else:
                split_rows = [
                    row
                    for row in rows
                    if row.get("predictor_profile_class") == "structural_non_tradable"
                ]
        elif predicate == "sparse_or_noise":
            if family_level:
                split_rows = [
                    row
                    for row in rows
                    if "sparse_or_noise" in str(row.get("predictor_profile_classes") or "")
                ]
            else:
                split_rows = [
                    row
                    for row in rows
                    if row.get("predictor_profile_class") == "sparse_or_noise"
                ]
        elif predicate == "intraday_variants":
            if family_level:
                split_rows = [row for row in rows if row.get("has_intraday_variant")]
            else:
                split_rows = [row for row in rows if row.get("timeframe_class") == "intraday"]
        elif predicate == "daily_and_higher":
            if family_level:
                split_rows = [row for row in rows if row.get("has_daily_or_higher_variant")]
            else:
                split_rows = [
                    row
                    for row in rows
                    if row.get("timeframe_class") in {"daily", "weekly", "monthly"}
                ]
        elif predicate == "profile_referenced":
            split_rows = [row for row in rows if row.get("profile_referenced")]
        elif predicate == "unused_by_profiles":
            split_rows = [
                row
                for row in rows
                if row.get("is_predictor_candidate")
                and not row.get("profile_referenced")
            ]
        else:
            split_rows = []
        path = output_dir / f"{filename}.csv"
        _write_csv_rows(path, split_rows, fieldnames=list(rows[0].keys()) if rows else None)
        output_paths[filename] = path.as_posix()
    return output_paths


def _write_relevance_splits(
    rows: Sequence[Mapping[str, Any]], output_dir: Path
) -> dict[str, str]:
    output_paths: dict[str, str] = {}
    tier_values = {"promote", "watch", "neutral", "demote", "ignore"}
    for filename, predicate in RELEVANCE_SPLIT_FILES.items():
        if predicate in tier_values:
            split_rows = [row for row in rows if row.get("relevance_tier") == predicate]
        elif predicate == "stability_passed":
            split_rows = [row for row in rows if row.get("passes_stability_gate")]
        elif predicate == "stability_failed":
            split_rows = [
                row
                for row in rows
                if row.get("scan_evidence_status") == "present"
                and not row.get("passes_stability_gate")
            ]
        else:
            split_rows = [row for row in rows if row.get("scan_evidence_status") == "no_data"]
        path = output_dir / f"{filename}.csv"
        _write_csv_rows(path, split_rows, fieldnames=list(rows[0].keys()) if rows else None)
        output_paths[filename] = path.as_posix()
    return output_paths


def _resolve_scan_tracking_defaults(scan_run_root: Path) -> dict[str, Any]:
    tracking_json = scan_run_root / "_scan_period_close_forward_tracking.json"
    if not tracking_json.exists():
        return {
            "tracking_id": scan_run_root.name,
            "performance_target": "period_return_pct",
            "stability_min_runs": 3,
            "sign_consistency_gate": 0.6,
            "universe_filter": None,
            "start_day_label": "",
            "end_day_label": "",
        }
    payload = json.loads(tracking_json.read_text(encoding="utf-8"))
    return {
        "tracking_id": str(payload.get("tracking_id") or scan_run_root.name),
        "performance_target": str(payload.get("performance_target") or "period_return_pct"),
        "stability_min_runs": int(payload.get("min_runs_for_stability") or 3),
        "sign_consistency_gate": float(payload.get("require_sign_consistency") or 0.6),
        "universe_filter": payload.get("universe_filter"),
        "start_day_label": str(payload.get("start_day_label") or ""),
        "end_day_label": str(payload.get("end_day_label") or ""),
    }


def build_trading_view_field_taxonomy(
    *,
    field_catalog: str | Path = DEFAULT_FIELD_CATALOG,
    scan_run_root: str | Path = DEFAULT_SCAN_RUN_ROOT,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    profile_dir: str | Path = DEFAULT_PROFILE_DIR,
    stability_min_runs: int | None = None,
    sign_consistency_gate: float | None = None,
) -> dict[str, Any]:
    catalog_path = Path(field_catalog)
    scan_root = Path(scan_run_root)
    output_root = Path(output_dir)
    profile_path = Path(profile_dir)

    tracking_defaults = _resolve_scan_tracking_defaults(scan_root)
    resolved_stability_min_runs = (
        stability_min_runs
        if stability_min_runs is not None
        else tracking_defaults["stability_min_runs"]
    )
    resolved_sign_gate = (
        sign_consistency_gate
        if sign_consistency_gate is not None
        else tracking_defaults["sign_consistency_gate"]
    )
    performance_target = tracking_defaults["performance_target"]

    catalog_rows = _load_catalog_rows(catalog_path)
    profile_refs = _load_profile_field_references(profile_path)
    evidence_by_field, scan_meta = _load_scan_evidence(
        scan_root,
        stability_min_runs=resolved_stability_min_runs,
        stability_sign_gate=resolved_sign_gate,
        performance_target=performance_target,
    )
    variant_rows = _build_variant_rows(
        catalog_rows,
        profile_refs,
        evidence_by_field,
        AllFieldsAnalysisConfig(),
    )
    family_rows = _build_family_rows(variant_rows)

    variants_root = output_root / "variants"
    families_root = output_root / "families"
    scan_evidence_root = output_root / "scan_evidence"
    for path in (
        variants_root,
        variants_root / "by_meaning",
        variants_root / "by_usage",
        variants_root / "by_relevance",
        families_root,
        families_root / "by_meaning",
        families_root / "by_usage",
        families_root / "by_relevance",
        scan_evidence_root,
    ):
        path.mkdir(parents=True, exist_ok=True)

    variant_master = variants_root / "master_registry.csv"
    family_master = families_root / "master_registry.csv"
    variant_fieldnames = list(
        dict.fromkeys(key for row in variant_rows for key in row.keys())
    )
    family_fieldnames = list(dict.fromkeys(key for row in family_rows for key in row.keys()))
    _write_csv_rows(variant_master, variant_rows, fieldnames=variant_fieldnames)
    _write_csv_rows(family_master, family_rows, fieldnames=family_fieldnames)

    variant_meaning_paths = _write_bucket_splits(
        variant_rows,
        variants_root / "by_meaning",
        "semantic_bucket",
        SEMANTIC_BUCKETS,
    )
    family_meaning_paths = _write_bucket_splits(
        family_rows,
        families_root / "by_meaning",
        "semantic_bucket",
        SEMANTIC_BUCKETS,
    )
    variant_usage_paths = _write_usage_splits(variant_rows, variants_root / "by_usage")
    family_usage_paths = _write_usage_splits(
        family_rows, families_root / "by_usage", family_level=True
    )
    variant_relevance_paths = _write_relevance_splits(
        variant_rows, variants_root / "by_relevance"
    )
    family_relevance_paths = _write_relevance_splits(
        family_rows, families_root / "by_relevance"
    )

    tracking_id = tracking_defaults["tracking_id"]
    lifecycle_suffix = tracking_id.rsplit("_", 1)[-1]
    variant_evidence_path = scan_evidence_root / f"{lifecycle_suffix}_variant_performance.csv"
    family_evidence_path = scan_evidence_root / f"{lifecycle_suffix}_family_rollup.csv"
    snapshot_meta_path = scan_evidence_root / f"{lifecycle_suffix}_snapshot_meta.json"
    _write_csv_rows(
        variant_evidence_path,
        variant_rows,
        fieldnames=list(variant_rows[0].keys()),
    )
    _write_csv_rows(
        family_evidence_path,
        family_rows,
        fieldnames=list(family_rows[0].keys()),
    )

    snapshot_meta = {
        "tracking_id": tracking_id,
        "scan_run_root": scan_root.as_posix(),
        "start_day_label": tracking_defaults["start_day_label"],
        "end_day_label": tracking_defaults["end_day_label"],
        "performance_target": performance_target,
        "universe_filter": tracking_defaults["universe_filter"],
        "stability_min_runs": resolved_stability_min_runs,
        "sign_consistency_gate": resolved_sign_gate,
        "regime_note": (
            "Mar-Jun 2026 risk-on window: volatility/beta fields often promoted; "
            "size/consensus fields may show bearish stability. Refresh relevance on new scan runs."
        ),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        **scan_meta,
    }
    snapshot_meta_path.write_text(
        json.dumps(snapshot_meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    manifest = {
        "schema_version": TAXONOMY_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_field_catalog": _file_fingerprint(catalog_path),
        "scan_tracking_id": tracking_id,
        "scan_run_root": scan_root.as_posix(),
        "variant_count": len(variant_rows),
        "family_count": len(family_rows),
        "outputs": {
            "variant_master_registry": variant_master.as_posix(),
            "family_master_registry": family_master.as_posix(),
            "variant_by_meaning": variant_meaning_paths,
            "family_by_meaning": family_meaning_paths,
            "variant_by_usage": variant_usage_paths,
            "family_by_usage": family_usage_paths,
            "variant_by_relevance": variant_relevance_paths,
            "family_by_relevance": family_relevance_paths,
            "scan_evidence_variant_performance": variant_evidence_path.as_posix(),
            "scan_evidence_family_rollup": family_evidence_path.as_posix(),
            "scan_evidence_snapshot_meta": snapshot_meta_path.as_posix(),
        },
    }
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "manifest_path": manifest_path.as_posix(),
        "output_dir": output_root.as_posix(),
        "variant_count": len(variant_rows),
        "family_count": len(family_rows),
        "tracking_id": tracking_id,
    }
