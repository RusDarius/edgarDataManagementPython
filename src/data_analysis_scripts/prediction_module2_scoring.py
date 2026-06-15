"""Catalog-driven scoring, gates, pillars, and consensus for prediction module2."""

from __future__ import annotations

from statistics import median
from typing import Any, Mapping, Sequence

from data_analysis_scripts._shared_analysis_utils import (
    coerce_numeric,
    median_absolute_deviation,
)
from data_analysis_scripts.prediction_module2_derived import (
    MODULE2_DERIVED_FIELD_NAMES,
    build_module2_derived_metrics,
)
from data_analysis_scripts.trading_view_move_prediction_analysis import (
    DERIVED_PROFILE_FIELDS,
)
from data_analysis_scripts.prediction_module2_profile_config import (
    AntiSignalRule,
    Module2Profile,
    Module2ProfileSuite,
    RegimeGateRule,
)
from data_analysis_scripts.trading_view_all_fields_metric_pattern_analyzer import (
    LOWER_IS_BETTER_MARKERS,
    load_field_catalog,
)
from data_analysis_scripts.trading_view_move_prediction_analysis import (
    _robust_signal,
)

QUANTIFIABLE_CATALOG_TYPES = frozenset({"number", "percent", "fundamental_price"})
DEFAULT_HORIZON_NAMES = ("days", "weeks", "months", "years")
DEFAULT_HORIZON_BLEND = {
    "days": 0.45,
    "weeks": 0.35,
    "months": 0.15,
    "years": 0.05,
}


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _infer_field_direction(field_name: str, catalog_entry: Any | None) -> str:
    normalized = field_name.lower()
    if any(marker in normalized for marker in LOWER_IS_BETTER_MARKERS):
        return "lower_is_better"
    if catalog_entry is not None:
        joined = (
            f"{catalog_entry.display_name} {catalog_entry.explanation} "
            f"{catalog_entry.model_use}"
        ).lower()
        if any(
            marker.replace("_", " ") in joined for marker in LOWER_IS_BETTER_MARKERS
        ):
            return "lower_is_better"
    return "higher_is_better"


def collect_profile_field_names(profile: Module2Profile) -> set[str]:
    field_names: set[str] = set()
    for pillar in profile.pillars.values():
        field_names.update(pillar.fields.keys())
        field_names.update(pillar.derived.keys())
    for rule in profile.regime_gates_hard + profile.regime_gates_soft:
        field_names.add(rule.field)
    for rule in profile.anti_signals.values():
        field_names.add(rule.field)
    return field_names


def collect_suite_field_names(suite: Module2ProfileSuite) -> set[str]:
    field_names: set[str] = set()
    for profile_name in suite.profile_names:
        profile = suite.registry.get_profile(profile_name)
        if profile is not None:
            field_names.update(collect_profile_field_names(profile))
    return field_names


def build_metric_profiles_for_fields(
    scan_data: Sequence[Mapping[str, Any]],
    derived_metrics: Sequence[Mapping[str, float | None]],
    field_names: Sequence[str],
) -> dict[str, dict[str, float | int | None]]:
    profiles: dict[str, dict[str, float | int | None]] = {}
    derived_set = set(DERIVED_PROFILE_FIELDS) | set(MODULE2_DERIVED_FIELD_NAMES)

    for field_name in field_names:
        if field_name in derived_set:
            numeric_values = [
                value
                for value in (metrics.get(field_name) for metrics in derived_metrics)
                if value is not None
            ]
        else:
            numeric_values = [
                value
                for value in (coerce_numeric(row.get(field_name)) for row in scan_data)
                if value is not None
            ]

        if not numeric_values:
            profiles[field_name] = {
                "count": 0,
                "mean": None,
                "median": None,
                "mad": None,
                "min": None,
                "max": None,
            }
            continue

        sorted_values = sorted(numeric_values)
        median_value = median(numeric_values)
        profiles[field_name] = {
            "count": len(numeric_values),
            "mean": sum(numeric_values) / len(numeric_values),
            "median": median_value,
            "mad": median_absolute_deviation(numeric_values, median_value),
            "min": sorted_values[0],
            "max": sorted_values[-1],
        }
    return profiles


def resolve_field_value(
    row: Mapping[str, Any],
    derived_row: Mapping[str, float | None],
    field_name: str,
    *,
    source: str = "derived",
) -> float | None:
    derived_set = set(DERIVED_PROFILE_FIELDS) | set(MODULE2_DERIVED_FIELD_NAMES)
    if source == "derived" or field_name in derived_set:
        value = derived_row.get(field_name)
        if value is not None:
            return value
    return coerce_numeric(row.get(field_name))


def field_z_score(
    row: Mapping[str, Any],
    derived_row: Mapping[str, float | None],
    field_name: str,
    metric_profiles: Mapping[str, Mapping[str, float | int | None]],
    *,
    source: str = "derived",
    catalog_entry: Any | None = None,
) -> float | None:
    raw_value = resolve_field_value(row, derived_row, field_name, source=source)
    if raw_value is None:
        return None
    profile = metric_profiles.get(field_name)
    signal = _robust_signal(raw_value, profile)
    if signal is None:
        return None
    if _infer_field_direction(field_name, catalog_entry) == "lower_is_better":
        return -signal
    return signal


def evaluate_gate_rule(
    row: Mapping[str, Any],
    derived_row: Mapping[str, float | None],
    rule: RegimeGateRule,
    metric_profiles: Mapping[str, Mapping[str, float | int | None]],
    *,
    catalog: Any | None = None,
) -> tuple[bool, float]:
    catalog_entry = catalog.get(rule.field) if catalog is not None else None
    z_score = field_z_score(
        row,
        derived_row,
        rule.field,
        metric_profiles,
        source=rule.source,
        catalog_entry=catalog_entry,
    )
    if z_score is None:
        return False, 0.0

    passed = True
    if rule.min_z is not None and z_score < rule.min_z:
        passed = False
    if rule.max_z is not None and z_score > rule.max_z:
        passed = False
    return passed, z_score


def evaluate_hard_gates(
    row: Mapping[str, Any],
    derived_row: Mapping[str, float | None],
    profile: Module2Profile,
    metric_profiles: Mapping[str, Mapping[str, float | int | None]],
    *,
    catalog: Any | None = None,
) -> tuple[bool, dict[str, float]]:
    gate_values: dict[str, float] = {}
    for rule in profile.regime_gates_hard:
        passed, z_score = evaluate_gate_rule(
            row, derived_row, rule, metric_profiles, catalog=catalog
        )
        if z_score is not None:
            gate_values[rule.field] = z_score
        if not passed:
            return False, gate_values
    return True, gate_values


def evaluate_soft_gates(
    row: Mapping[str, Any],
    derived_row: Mapping[str, float | None],
    profile: Module2Profile,
    metric_profiles: Mapping[str, Mapping[str, float | int | None]],
    *,
    catalog: Any | None = None,
) -> float:
    if not profile.regime_gates_soft:
        return 1.0

    total_weight = 0.0
    passed_weight = 0.0
    for rule in profile.regime_gates_soft:
        passed, _ = evaluate_gate_rule(
            row, derived_row, rule, metric_profiles, catalog=catalog
        )
        total_weight += rule.weight
        if passed:
            passed_weight += rule.weight
    if total_weight <= 0:
        return 1.0
    return passed_weight / total_weight


def apply_anti_signals(
    score: float,
    row: Mapping[str, Any],
    derived_row: Mapping[str, float | None],
    profile: Module2Profile,
    metric_profiles: Mapping[str, Mapping[str, float | int | None]],
    *,
    catalog: Any | None = None,
) -> float:
    adjusted = score
    for rule in profile.anti_signals.values():
        catalog_entry = catalog.get(rule.field) if catalog is not None else None
        z_score = field_z_score(
            row,
            derived_row,
            rule.field,
            metric_profiles,
            source=rule.source,
            catalog_entry=catalog_entry,
        )
        if z_score is None:
            continue
        violated = False
        if rule.min_z is not None and z_score < rule.min_z:
            violated = True
        if rule.max_z is not None and z_score > rule.max_z:
            violated = True
        if violated:
            adjusted -= rule.penalty * max(0.0, abs(z_score))
    return adjusted


def apply_directional_bias(score: float, profile: Module2Profile) -> float:
    if score >= 0:
        return score * profile.directional_bias.positive_multiplier
    return score * profile.directional_bias.negative_multiplier


def _weighted_average(values: Mapping[str, float | None], weights: Mapping[str, float]) -> float | None:
    total_weight = 0.0
    weighted_total = 0.0
    for name, weight in weights.items():
        if weight <= 0:
            continue
        value = values.get(name)
        if value is None:
            continue
        total_weight += weight
        weighted_total += value * weight
    if total_weight <= 0:
        return None
    return weighted_total / total_weight


def score_pillars(
    row: Mapping[str, Any],
    derived_row: Mapping[str, float | None],
    profile: Module2Profile,
    metric_profiles: Mapping[str, Mapping[str, float | int | None]],
    *,
    catalog: Any | None = None,
) -> dict[str, float | None]:
    pillar_scores: dict[str, float | None] = {}
    for pillar_name, pillar in profile.pillars.items():
        signal_values: dict[str, float | None] = {}
        weights: dict[str, float] = {}

        for field_name, weight in pillar.fields.items():
            catalog_entry = catalog.get(field_name) if catalog is not None else None
            signal_values[field_name] = field_z_score(
                row,
                derived_row,
                field_name,
                metric_profiles,
                source="raw",
                catalog_entry=catalog_entry,
            )
            weights[field_name] = weight

        for field_name, weight in pillar.derived.items():
            signal_values[field_name] = field_z_score(
                row,
                derived_row,
                field_name,
                metric_profiles,
                source="derived",
            )
            weights[field_name] = weight

        pillar_scores[pillar_name] = _weighted_average(signal_values, weights)
    return pillar_scores


def blend_pillar_scores(
    pillar_scores: Mapping[str, float | None],
    profile: Module2Profile,
) -> float | None:
    weights = {
        pillar_name: pillar.weight
        for pillar_name, pillar in profile.pillars.items()
        if pillar.weight > 0
    }
    return _weighted_average(dict(pillar_scores), weights)


def score_profile_row(
    row: Mapping[str, Any],
    derived_row: Mapping[str, float | None],
    profile: Module2Profile,
    metric_profiles: Mapping[str, Mapping[str, float | int | None]],
    *,
    catalog: Any | None = None,
) -> dict[str, Any]:
    hard_passed, gate_values = evaluate_hard_gates(
        row, derived_row, profile, metric_profiles, catalog=catalog
    )
    soft_multiplier = evaluate_soft_gates(
        row, derived_row, profile, metric_profiles, catalog=catalog
    )
    pillar_scores = score_pillars(
        row, derived_row, profile, metric_profiles, catalog=catalog
    )
    base_score = blend_pillar_scores(pillar_scores, profile)
    if base_score is None:
        final_score = None
    else:
        adjusted = apply_directional_bias(base_score, profile)
        adjusted = apply_anti_signals(
            adjusted, row, derived_row, profile, metric_profiles, catalog=catalog
        )
        adjusted *= soft_multiplier
        if not hard_passed:
            adjusted = None
        final_score = _clamp(adjusted, -3.0, 3.0) if adjusted is not None else None

    return {
        "score": final_score,
        "hard_gate_passed": hard_passed,
        "soft_gate_multiplier": soft_multiplier,
        "gate_values": gate_values,
        "pillar_scores": pillar_scores,
        "base_score": base_score,
    }


def score_universe_for_profile(
    scan_data: Sequence[Mapping[str, Any]],
    derived_metrics: Sequence[Mapping[str, float | None]],
    profile: Module2Profile,
    metric_profiles: Mapping[str, Mapping[str, float | int | None]],
    *,
    catalog: Any | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for row, derived_row in zip(scan_data, derived_metrics, strict=True):
        scored = score_profile_row(
            row, derived_row, profile, metric_profiles, catalog=catalog
        )
        results.append(
            {
                "symbol": row.get("symbol"),
                "name": row.get("name"),
                **scored,
            }
        )
    return results


def rank_profile_results(
    scored_rows: Sequence[Mapping[str, Any]],
    *,
    top_n: int = 30,
) -> list[dict[str, Any]]:
    ranked = sorted(
        [
            dict(row)
            for row in scored_rows
            if row.get("score") is not None and row.get("hard_gate_passed")
        ],
        key=lambda row: float(row["score"]),
        reverse=True,
    )
    return ranked[:top_n]


def jaccard_overlap(
    left_symbols: Sequence[str | None],
    right_symbols: Sequence[str | None],
) -> float:
    left = {symbol for symbol in left_symbols if symbol}
    right = {symbol for symbol in right_symbols if symbol}
    if not left and not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def build_overlap_report(
    profile_rankings: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    top_n: int = 30,
) -> dict[str, Any]:
    profile_names = sorted(profile_rankings.keys())
    pair_overlaps: list[dict[str, Any]] = []
    for index, left_name in enumerate(profile_names):
        left_symbols = [
            row.get("symbol") for row in profile_rankings[left_name][:top_n]
        ]
        for right_name in profile_names[index + 1 :]:
            right_symbols = [
                row.get("symbol") for row in profile_rankings[right_name][:top_n]
            ]
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

    family_purity: dict[str, float] = {}
    for profile_name, rows in profile_rankings.items():
        top_rows = list(rows[:top_n])
        if not top_rows:
            family_purity[profile_name] = 0.0
            continue
        passed = sum(1 for row in top_rows if row.get("hard_gate_passed"))
        family_purity[profile_name] = passed / len(top_rows)

    return {
        "top_n": top_n,
        "pair_overlaps": pair_overlaps,
        "family_purity": family_purity,
    }


def build_family_orthogonal_consensus(
    profile_scores: Mapping[str, Sequence[Mapping[str, Any]]],
    suite: Module2ProfileSuite,
    *,
    horizon_name: str = "weeks",
) -> list[dict[str, Any]]:
    symbol_records: dict[str, dict[str, Any]] = {}
    family_best: dict[tuple[str, str], float] = {}

    for profile_name in suite.profile_names:
        profile = suite.registry.get_profile(profile_name)
        if profile is None:
            continue
        family = profile.outlook_family
        weight = suite.consensus_profile_weights.get(profile_name, 0.0)
        if weight <= 0:
            continue
        sign = -1.0 if profile_name in suite.inverted_consensus_profiles else 1.0

        for row in profile_scores.get(profile_name, []):
            symbol = row.get("symbol")
            score = row.get("score")
            if not symbol or score is None or not row.get("hard_gate_passed"):
                continue
            signed_score = float(score) * sign
            family_key = (symbol, family)
            family_best[family_key] = max(family_best.get(family_key, signed_score), signed_score)

            record = symbol_records.setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "name": row.get("name"),
                    "family_scores": {},
                    "profile_scores": {},
                    "coverage": 0.0,
                },
            )
            record["profile_scores"][profile_name] = signed_score

    for (symbol, family), family_score in family_best.items():
        record = symbol_records[symbol]
        record["family_scores"][family] = family_score

    family_weights: dict[str, float] = {}
    for family, profile_names in suite.outlook_families.items():
        family_weight = sum(
            suite.consensus_profile_weights.get(profile_name, 0.0)
            for profile_name in profile_names
        )
        if family_weight > 0:
            family_weights[family] = family_weight

    total_family_weight = sum(family_weights.values()) or 1.0
    normalized_family_weights = {
        family: weight / total_family_weight for family, weight in family_weights.items()
    }

    consensus_rows: list[dict[str, Any]] = []
    for symbol, record in symbol_records.items():
        family_scores = record["family_scores"]
        if not family_scores:
            continue
        weighted_score = 0.0
        used_weight = 0.0
        for family, family_score in family_scores.items():
            weight = normalized_family_weights.get(family, 0.0)
            if weight <= 0:
                continue
            weighted_score += family_score * weight
            used_weight += weight
        if used_weight <= 0:
            continue
        consensus_score = weighted_score / used_weight
        consensus_rows.append(
            {
                "symbol": symbol,
                "name": record.get("name"),
                "horizon_name": horizon_name,
                "consensus_score": _clamp(consensus_score, -3.0, 3.0),
                "coverage": used_weight,
                "family_scores": family_scores,
                "profile_scores": record["profile_scores"],
            }
        )

    return sorted(
        consensus_rows,
        key=lambda row: float(row["consensus_score"]),
        reverse=True,
    )


def prepare_scoring_context(
    scan_data: Sequence[Mapping[str, Any]],
    suite: Module2ProfileSuite,
) -> tuple[
    list[dict[str, float | None]],
    dict[str, dict[str, float | int | None]],
    Any,
]:
    catalog = load_field_catalog()
    derived_metrics = [build_module2_derived_metrics(row) for row in scan_data]
    field_names = collect_suite_field_names(suite)
    metric_profiles = build_metric_profiles_for_fields(
        scan_data, derived_metrics, sorted(field_names)
    )
    return derived_metrics, metric_profiles, catalog
