"""Top-N Jaccard overlap reports for move-prediction profile suites."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from data_analysis_scripts.prediction_module2_scoring import jaccard_overlap
from data_analysis_scripts.trading_view_move_prediction_analysis import (
    DEFAULT_HORIZON_WEIGHTS,
    _build_derived_metrics,
    _build_metric_profiles,
    _build_prediction_rows,
    _enrich_with_peer_metrics,
    resolve_move_prediction_scoring_profile,
)
from data_analysis_scripts.trading_view_move_prediction_profile_config import (
    ProfileConfigRegistry,
    load_profile_suite,
    resolve_profile_suite,
)

CRITICAL_PAIR_GATES: dict[tuple[str, str], float] = {
    ("breakout_long_v1", "quality_continuation_v1"): 0.35,
    ("breakout_long_v1", "sustained_momentum_safety_v1"): 0.35,
    ("sustained_momentum_safety_v1", "quality_continuation_v1"): 0.35,
    ("sustained_momentum_safety_v1", "early_momentum_inflection_v1"): 0.35,
    ("asymmetric_value", "value_recovery_v2"): 0.35,
    ("asymmetric_value", "value_recovery_v3"): 0.35,
    ("forward_edge_active_v2", "quality_value_compounder"): 0.35,
    ("upside_reversal_v1", "breakout_long_v1"): 0.35,
    ("upside_reversal_v1", "early_momentum_inflection_v1"): 0.35,
    ("upside_reversal_v1", "value_recovery_v2"): 0.35,
    ("upside_reversal_v1", "value_recovery_v3"): 0.35,
    ("breakout_long_v1", "value_recovery_v3"): 0.35,
}

OVERLAY_PROFILES = frozenset({"fragility_short", "mean_reversion_exhaustion_v1"})
OVERLAY_MAX_JACCARD = 0.15


def _filter_scan_rows(
    scan_data: list[dict[str, Any]],
    *,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for row in scan_data:
        market_cap = row.get("market_cap_basic")
        if min_market_cap_usd is not None and (
            market_cap is None or float(market_cap) < min_market_cap_usd
        ):
            continue
        if max_market_cap_usd is not None and (
            market_cap is None or float(market_cap) > max_market_cap_usd
        ):
            continue
        filtered.append(row)
    return filtered


def build_profile_rankings_for_horizon(
    scan_data: list[dict[str, Any]],
    profile_names: Sequence[str],
    *,
    horizon_name: str,
    top_n: int = 10,
    profile_registry: ProfileConfigRegistry | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Score each profile and return top-N rows per profile for one horizon."""
    if horizon_name not in DEFAULT_HORIZON_WEIGHTS:
        raise ValueError(f"Unknown horizon: {horizon_name}")

    rows = _filter_scan_rows(
        scan_data,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
    )
    if not rows:
        return {profile_name: [] for profile_name in profile_names}

    working_rows = [dict(row) for row in rows]
    _enrich_with_peer_metrics(working_rows)
    derived_metrics = [_build_derived_metrics(row) for row in working_rows]
    metric_profiles = _build_metric_profiles(working_rows, derived_metrics)

    rankings: dict[str, list[dict[str, Any]]] = {}
    for profile_name in profile_names:
        scoring_profile = resolve_move_prediction_scoring_profile(
            profile_name,
            profile_registry=profile_registry,
        )
        prediction_rows = _build_prediction_rows(
            working_rows,
            metric_profiles,
            derived_metrics,
            scoring_profile,
        )
        scored_rows: list[dict[str, Any]] = []
        for prediction in prediction_rows:
            horizon = prediction["horizons"].get(horizon_name, {})
            score = horizon.get("score")
            if score is None:
                continue
            row = prediction["row"]
            scored_rows.append(
                {
                    "symbol": row.get("symbol"),
                    "ticker": row.get("symbol"),
                    "company": row.get("name"),
                    "score": float(score),
                    "direction": horizon.get("direction"),
                }
            )
        scored_rows.sort(key=lambda item: item["score"], reverse=True)
        rankings[profile_name] = scored_rows[:top_n]
    return rankings


def build_move_prediction_overlap_report(
    scan_data: list[dict[str, Any]],
    profile_names: Sequence[str],
    *,
    horizon_names: Sequence[str] | None = None,
    top_n: int = 10,
    profile_registry: ProfileConfigRegistry | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
) -> dict[str, Any]:
    """Build per-horizon Jaccard overlap report for a profile list."""
    horizons = list(horizon_names or DEFAULT_HORIZON_WEIGHTS.keys())
    horizon_reports: dict[str, Any] = {}

    for horizon_name in horizons:
        rankings = build_profile_rankings_for_horizon(
            scan_data,
            profile_names,
            horizon_name=horizon_name,
            top_n=top_n,
            profile_registry=profile_registry,
            min_market_cap_usd=min_market_cap_usd,
            max_market_cap_usd=max_market_cap_usd,
        )
        profile_name_list = sorted(rankings.keys())
        pair_overlaps: list[dict[str, Any]] = []
        for left_index, left_name in enumerate(profile_name_list):
            left_symbols = [row.get("symbol") for row in rankings[left_name]]
            for right_name in profile_name_list[left_index + 1 :]:
                right_symbols = [row.get("symbol") for row in rankings[right_name]]
                pair_overlaps.append(
                    {
                        "left_profile": left_name,
                        "right_profile": right_name,
                        "jaccard_top_n": jaccard_overlap(left_symbols, right_symbols),
                        "shared_symbols": sorted(
                            {
                                symbol
                                for symbol in left_symbols
                                if symbol in set(right_symbols)
                            }
                        ),
                    }
                )
        horizon_reports[horizon_name] = {
            "top_n": top_n,
            "pair_overlaps": pair_overlaps,
        }

    return {
        "top_n": top_n,
        "horizons": horizon_reports,
        "gate_failures": evaluate_overlap_gates(horizon_reports),
    }


def evaluate_overlap_gates(
    horizon_reports: Mapping[str, Mapping[str, Any]],
    *,
    critical_pairs: Mapping[tuple[str, str], float] | None = None,
) -> list[dict[str, Any]]:
    """Return gate violations for critical pairs and overlay profiles."""
    failures: list[dict[str, Any]] = []
    gates = critical_pairs or CRITICAL_PAIR_GATES

    for horizon_name, report in horizon_reports.items():
        if horizon_name not in {"weeks", "months"}:
            continue
        pair_lookup = {
            (pair["left_profile"], pair["right_profile"]): pair
            for pair in report.get("pair_overlaps", [])
        }
        pair_lookup.update(
            {
                (pair["right_profile"], pair["left_profile"]): pair
                for pair in report.get("pair_overlaps", [])
            }
        )

        for (left_name, right_name), max_jaccard in gates.items():
            pair = pair_lookup.get((left_name, right_name))
            if pair is None:
                continue
            jaccard = float(pair["jaccard_top_n"])
            if jaccard > max_jaccard:
                failures.append(
                    {
                        "horizon": horizon_name,
                        "left_profile": left_name,
                        "right_profile": right_name,
                        "jaccard_top_n": jaccard,
                        "max_allowed": max_jaccard,
                        "gate": "critical_pair",
                    }
                )

        for pair in report.get("pair_overlaps", []):
            left_name = pair["left_profile"]
            right_name = pair["right_profile"]
            if left_name not in OVERLAY_PROFILES and right_name not in OVERLAY_PROFILES:
                continue
            long_name = (
                right_name if left_name in OVERLAY_PROFILES else left_name
            )
            if long_name in OVERLAY_PROFILES:
                continue
            jaccard = float(pair["jaccard_top_n"])
            if jaccard > OVERLAY_MAX_JACCARD:
                failures.append(
                    {
                        "horizon": horizon_name,
                        "left_profile": left_name,
                        "right_profile": right_name,
                        "jaccard_top_n": jaccard,
                        "max_allowed": OVERLAY_MAX_JACCARD,
                        "gate": "overlay_separation",
                    }
                )

    return failures


def run_suite_overlap_report(
    scan_data: list[dict[str, Any]],
    *,
    profile_suite_path: str | Path | None = None,
    top_n: int = 10,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run overlap report for a suite manifest and optionally write JSON."""
    suite = resolve_profile_suite(profile_suite_path)
    report = build_move_prediction_overlap_report(
        scan_data,
        suite["profile_names"],
        top_n=top_n,
        profile_registry=suite["registry"],
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
    )
    report["suite_id"] = suite.get("suite_id")
    report["suite_path"] = suite.get("source_path")
    report["profile_names"] = list(suite["profile_names"])

    if output_path is not None:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        report["output_path"] = str(destination)

    return report


def compare_suite_overlap_reports(
    scan_data: list[dict[str, Any]],
    *,
    left_suite_path: str | Path,
    right_suite_path: str | Path,
    top_n: int = 10,
    min_market_cap_usd: float | None = None,
) -> dict[str, Any]:
    """Compare overlap gate failures between two suite manifests."""
    left_suite = load_profile_suite(left_suite_path)
    right_suite = load_profile_suite(right_suite_path)
    left_report = build_move_prediction_overlap_report(
        scan_data,
        left_suite["profile_names"],
        top_n=top_n,
        profile_registry=left_suite["registry"],
        min_market_cap_usd=min_market_cap_usd,
    )
    right_report = build_move_prediction_overlap_report(
        scan_data,
        right_suite["profile_names"],
        top_n=top_n,
        profile_registry=right_suite["registry"],
        min_market_cap_usd=min_market_cap_usd,
    )
    return {
        "left_suite_id": left_suite["suite_id"],
        "right_suite_id": right_suite["suite_id"],
        "left_gate_failures": left_report["gate_failures"],
        "right_gate_failures": right_report["gate_failures"],
        "left_horizons": left_report["horizons"],
        "right_horizons": right_report["horizons"],
    }
