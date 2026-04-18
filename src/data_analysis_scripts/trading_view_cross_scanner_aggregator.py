"""Cross-scanner aggregator — merges move-prediction and forward-target outputs.

The move-prediction module answers **"which direction and how confident?"**
The forward-target module answers **"what price and how much upside?"**

Neither module alone captures the full picture:

    ┌─────────────────────────┐   ┌──────────────────────────┐
    │  Move-Prediction Suite  │   │  Forward-Target Scanner  │
    │  ── directional scores  │   │  ── bear/base/bull price │
    │  ── 8 profile lenses    │   │  ── 3 valuation lenses   │
    │  ── consensus ranking   │   │  ── opportunity scores   │
    │  ── blind-spot alerts   │   │  ── multiple comparisons │
    └────────────┬────────────┘   └────────────┬─────────────┘
                 │                              │
                 └──────────┬───────────────────┘
                            ▼
              ┌──────────────────────────┐
              │  Cross-Scanner Aggregate │
              │  ── conviction score     │
              │  ── signal agreement     │
              │  ── divergence alerts    │
              │  ── composite ranking    │
              └──────────────────────────┘

Blindspots this aggregator addresses
-------------------------------------
1. **Directional-price divergence** — prediction says "Strong Up" but target
   shows the stock already trades above fair value.  Neither module flags this
   on its own.
2. **Coverage gap asymmetry** — prediction may have high confidence from
   momentum/trend data while targets have zero trajectory coverage because
   fundamentals are missing.  The aggregator penalises names where one side
   is blind.
3. **Horizon misalignment** — prediction's "weeks" horizon may be bullish
   while targets' "near_term" is bearish (technical resistance cluster).
   Cross-checking exposes conflicting signals.
4. **Profile-target agreement** — a name ranked #1 by quality_value_compounder
   but showing "Strong Overvaluation" in the target scan is a red flag for
   momentum-disguised-as-quality.
5. **Missing short-side target context** — prediction fragility_short flags
   names but has no price target for the downside.  The target module's bear
   price fills that gap.
6. **Earnings-proximity blind spot** — prediction penalises confidence near
   earnings but target module ignores event timing entirely.
7. **Single peer-group limitation** — both modules treat the entire scan as
   one peer group; the aggregator can surface per-sector conviction deltas.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from data_analysis_scripts._shared_analysis_utils import (
    build_report_title as _build_report_title,
    coerce_numeric as _coerce_numeric,
    format_market_cap as _format_market_cap,
    reset_log_file as _reset_log_file,
    sort_key_desc as _sort_key_desc,
)
from data_analysis_scripts.trading_view_move_prediction_analysis import (
    COMPONENT_ORDER,
    CONSENSUS_PROFILE_WEIGHTS,
    DEFAULT_HORIZON_WEIGHTS,
    DIRECTIONAL_MOVE_SCORE_THRESHOLD,
    MINIMUM_COVERAGE_FOR_CONSENSUS,
    PRESET_SCORING_PROFILES,
    STRONG_MOVE_SCORE_THRESHOLD,
    ScoringProfile,
    resolve_move_prediction_scoring_profile,
    _build_derived_metrics,
    _build_metric_profiles,
    _build_component_scores,
    _build_horizon_prediction,
    _build_prediction_rows,
    _enrich_with_peer_metrics,
    _get_symbol_name,
    _get_company_name,
    _normalize_industries,
    _resolve_horizon_weights,
    _parse_event_days,
    _clamp,
    run_full_analysis_suite,
)
from data_analysis_scripts.trading_view_targets_analysis import (
    TARGET_HORIZONS,
    TARGET_HORIZON_LABELS,
    MODERATE_OPPORTUNITY_THRESHOLD,
    STRONG_OPPORTUNITY_THRESHOLD,
    CompanyTargetResult,
    HorizonTarget,
    analyze_targets_scan,
    _build_peer_multiple_stats,
    _build_company_targets,
)
from generic_utils.log_to_files_util import log_to_file, log_rows_to_csv
from data_loaders.api_tradingview_client import ApiTradingViewClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOG_DIR = Path(
    r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis\cross_scanner"
)

TOP_SECTION_ROWS = 30

# Map prediction horizons → target horizons for cross-referencing.
# Prediction uses: days, weeks, months, years
# Targets use:     near_term, medium_term, long_term
HORIZON_CROSSMAP: dict[str, str] = {
    "days": "near_term",
    "weeks": "near_term",
    "months": "medium_term",
    "years": "long_term",
}

# Reverse map for target → prediction (pick the more relevant prediction horizon).
TARGET_TO_PREDICTION: dict[str, str] = {
    "near_term": "weeks",
    "medium_term": "months",
    "long_term": "years",
}

# Conviction scoring — how much each signal source contributes.
CONVICTION_WEIGHTS = {
    "prediction_score": 0.30,  # directional score from prediction module
    "target_upside": 0.25,  # base-case upside from target module
    "signal_agreement": 0.20,  # do both modules agree on direction?
    "coverage_quality": 0.15,  # penalise when one side is blind
    "component_support": 0.10,  # quality + safety floor
}

# Agreement classification thresholds.
AGREEMENT_STRONG = 0.80
AGREEMENT_MODERATE = 0.50
DIVERGENCE_THRESHOLD = -0.20  # below this = clear divergence

# Conviction score labels.
CONVICTION_LABELS = {
    "strong_buy": 1.20,
    "buy": 0.60,
    "lean_long": 0.25,
    "neutral": -0.25,
    "lean_short": -0.60,
    "sell": -1.20,
    "strong_sell": float("-inf"),
}

DATE_FOLDER_FORMAT = "%d_%m_%Y"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HorizonCrossResult:
    """Cross-referenced result for one target horizon."""

    horizon: str

    # From prediction module (weighted aggregate across all profiles).
    prediction_score: float | None
    prediction_direction: str
    prediction_confidence: float | None
    prediction_coverage: float | None

    # Per-profile prediction scores for this horizon.
    profile_scores: dict[str, float | None]  # profile_name → score

    # Profile agreement metrics.
    profile_agreement_ratio: float  # fraction of profiles agreeing on direction
    profile_count: int  # how many profiles contributed

    # From target module.
    bear_price: float | None
    base_price: float | None
    bull_price: float | None
    base_upside_pct: float | None
    opportunity_score: float | None
    opportunity_label: str
    target_coverage: float

    # Cross-scanner derived.
    signal_agreement: float  # -1.0 to +1.0
    agreement_label: str  # "Strong Agreement", "Moderate", "Divergent"
    conviction_score: float  # weighted composite
    conviction_label: str


@dataclass(frozen=True)
class CrossScannerResult:
    """Aggregated result for one company across both scanners and all profiles."""

    symbol: str
    company_name: str
    industry: str
    sector: str
    close: float | None
    market_cap: float | None

    # Per-horizon cross results.
    horizon_results: dict[str, HorizonCrossResult]

    # Aggregate conviction (weighted average across horizons).
    aggregate_conviction: float
    aggregate_label: str

    # Component scores (averaged across profiles).
    component_scores: dict[str, float | None]

    # Per-profile component scores for drill-down.
    profile_component_scores: dict[str, dict[str, float | None]]

    # Profiles used in this aggregation.
    profile_names: list[str]

    # Coverage diagnostics.
    prediction_avg_coverage: float
    target_avg_coverage: float
    coverage_gap: float  # abs(pred_coverage - target_coverage)

    # Divergence flags.
    has_directional_price_divergence: bool  # prediction bullish + target overvalued
    has_coverage_asymmetry: bool  # one side blind, other confident
    has_horizon_conflict: bool  # short-term vs long-term disagree
    earnings_proximity_flag: bool  # near earnings with low confidence

    # Original data references.
    raw_row: dict[str, Any]
    prediction_data: dict[str, dict[str, Any]]  # profile_name → prediction_row
    target_data: CompanyTargetResult


# ---------------------------------------------------------------------------
# Signal agreement calculation
# ---------------------------------------------------------------------------


def _compute_signal_agreement(
    prediction_score: float | None,
    base_upside_pct: float | None,
) -> float:
    """Compute how well prediction direction and target upside agree.

    Returns a value in [-1.0, +1.0]:
      +1.0 = perfect agreement (both bullish or both bearish)
      0.0  = one side neutral or missing
      -1.0 = full divergence (bullish prediction + overvalued target, or vice versa)
    """
    if prediction_score is None or base_upside_pct is None:
        return 0.0

    # Normalise prediction score to [-1, 1] range (it's in [-3, 3]).
    pred_direction = _clamp(prediction_score / 2.0, -1.0, 1.0)

    # Normalise target upside to [-1, 1] range.
    # ±50% upside maps to ±1.0.
    target_direction = _clamp(base_upside_pct / 50.0, -1.0, 1.0)

    # Agreement = correlation of directions.
    # Both positive or both negative → positive agreement.
    # Opposite signs → negative (divergence).
    agreement = pred_direction * target_direction

    # Scale: if both are strong, agreement is amplified.
    magnitude = min(abs(pred_direction), abs(target_direction))
    return _clamp(agreement * (0.5 + 0.5 * magnitude), -1.0, 1.0)


def _agreement_label(agreement: float) -> str:
    if agreement >= AGREEMENT_STRONG:
        return "Strong Agreement"
    if agreement >= AGREEMENT_MODERATE:
        return "Moderate Agreement"
    if agreement >= 0.0:
        return "Weak Agreement"
    if agreement >= DIVERGENCE_THRESHOLD:
        return "Mild Divergence"
    return "Divergent"


# ---------------------------------------------------------------------------
# Conviction score
# ---------------------------------------------------------------------------


def _compute_conviction_score(
    prediction_score: float | None,
    base_upside_pct: float | None,
    signal_agreement: float,
    prediction_coverage: float | None,
    target_coverage: float,
    quality_score: float | None,
    safety_score: float | None,
) -> float:
    """Build a single conviction score from all signal sources.

    Each source is normalised to roughly [-1, +1] then weighted.
    """
    # 1. Prediction direction signal [-1, 1].
    pred_signal = _clamp((prediction_score or 0.0) / 2.0, -1.0, 1.0)

    # 2. Target upside signal [-1, 1].
    target_signal = _clamp((base_upside_pct or 0.0) / 50.0, -1.0, 1.0)

    # 3. Signal agreement already in [-1, 1].
    agree_signal = signal_agreement

    # 4. Coverage quality: penalise when one side is blind.
    pred_cov = prediction_coverage if prediction_coverage is not None else 0.0
    avg_coverage = (pred_cov + target_coverage) / 2.0
    coverage_penalty = _clamp(avg_coverage * 2.0 - 1.0, -1.0, 1.0)

    # 5. Component floor: quality + safety provide a fundamental floor.
    q = quality_score if quality_score is not None else 0.0
    s = safety_score if safety_score is not None else 0.0
    component_floor = _clamp((q + s) / 3.0, -1.0, 1.0)

    conviction = (
        pred_signal * CONVICTION_WEIGHTS["prediction_score"]
        + target_signal * CONVICTION_WEIGHTS["target_upside"]
        + agree_signal * CONVICTION_WEIGHTS["signal_agreement"]
        + coverage_penalty * CONVICTION_WEIGHTS["coverage_quality"]
        + component_floor * CONVICTION_WEIGHTS["component_support"]
    )

    return round(_clamp(conviction, -2.0, 2.0), 4)


def _conviction_label(score: float) -> str:
    for label, threshold in CONVICTION_LABELS.items():
        if score >= threshold:
            return label.replace("_", " ").title()
    return "Strong Sell"


# ---------------------------------------------------------------------------
# Divergence detection
# ---------------------------------------------------------------------------


def _detect_directional_price_divergence(
    prediction_score: float | None,
    base_upside_pct: float | None,
) -> bool:
    """Prediction says bullish but target says overvalued, or vice versa."""
    if prediction_score is None or base_upside_pct is None:
        return False
    bullish_pred = prediction_score >= DIRECTIONAL_MOVE_SCORE_THRESHOLD
    bearish_target = base_upside_pct < -MODERATE_OPPORTUNITY_THRESHOLD
    bearish_pred = prediction_score <= -DIRECTIONAL_MOVE_SCORE_THRESHOLD
    bullish_target = base_upside_pct > MODERATE_OPPORTUNITY_THRESHOLD
    return (bullish_pred and bearish_target) or (bearish_pred and bullish_target)


def _detect_coverage_asymmetry(
    prediction_coverage: float | None,
    target_coverage: float,
    threshold: float = 0.40,
) -> bool:
    """One module has strong coverage, the other is nearly blind."""
    pred_cov = prediction_coverage if prediction_coverage is not None else 0.0
    return abs(pred_cov - target_coverage) >= threshold


def _detect_horizon_conflict(
    horizon_results: dict[str, HorizonCrossResult],
) -> bool:
    """Short-term and long-term conviction scores disagree on direction."""
    short_horizons = ["near_term"]
    long_horizons = ["long_term"]

    short_scores = [
        horizon_results[h].conviction_score
        for h in short_horizons
        if h in horizon_results
    ]
    long_scores = [
        horizon_results[h].conviction_score
        for h in long_horizons
        if h in horizon_results
    ]

    if not short_scores or not long_scores:
        return False

    avg_short = sum(short_scores) / len(short_scores)
    avg_long = sum(long_scores) / len(long_scores)

    # Conflict = opposite signs with meaningful magnitude.
    return (avg_short * avg_long < 0) and (
        abs(avg_short) > 0.15 and abs(avg_long) > 0.15
    )


def _detect_earnings_proximity(
    earnings_days: int | None,
    prediction_confidence: float | None,
) -> bool:
    """Flag names near earnings where prediction confidence is already low."""
    if earnings_days is None:
        return False
    near_earnings = 0 <= earnings_days <= 14
    low_confidence = (prediction_confidence or 0.0) < 45.0
    return near_earnings and low_confidence


# ---------------------------------------------------------------------------
# Core aggregation pipeline
# ---------------------------------------------------------------------------


def _resolve_profile_list(
    scoring_profiles: list[str] | None,
) -> list[ScoringProfile]:
    """Resolve a list of profile names to ScoringProfile objects.

    If *None*, returns all preset profiles.
    """
    if scoring_profiles is None:
        return list(PRESET_SCORING_PROFILES.values())
    return [resolve_move_prediction_scoring_profile(name) for name in scoring_profiles]


def _direction_label_from_score(score: float | None) -> str:
    if score is None:
        return "N/A"
    if score >= STRONG_MOVE_SCORE_THRESHOLD:
        return "Strong Up"
    if score >= DIRECTIONAL_MOVE_SCORE_THRESHOLD:
        return "Up"
    if score <= -STRONG_MOVE_SCORE_THRESHOLD:
        return "Strong Down"
    if score <= -DIRECTIONAL_MOVE_SCORE_THRESHOLD:
        return "Down"
    return "Neutral"


def _build_cross_scanner_results(
    scan_data: list[dict[str, Any]],
    resolved_profiles: list[ScoringProfile],
) -> list[CrossScannerResult]:
    """Run all profiles + targets on scan_data and merge into CrossScannerResult list.

    For each company, prediction scores are computed independently per profile,
    then aggregated using ``CONSENSUS_PROFILE_WEIGHTS`` (same weights as the
    consensus aggregator in the prediction module).  This gives a single
    weighted prediction score per horizon that reflects all profile lenses.
    """
    profile_names = [p.name for p in resolved_profiles]

    # Shared first-stage pipeline (run once, reused by all profiles and targets).
    _enrich_with_peer_metrics(scan_data)
    derived_metrics = [_build_derived_metrics(row) for row in scan_data]
    profiles = _build_metric_profiles(scan_data, derived_metrics)

    # Run prediction pipeline for each scoring profile.
    # Key: profile_name → list[prediction_row] (same order as scan_data).
    all_prediction_rows: dict[str, list[dict[str, Any]]] = {}
    for scoring_profile in resolved_profiles:
        all_prediction_rows[scoring_profile.name] = _build_prediction_rows(
            scan_data, profiles, derived_metrics, scoring_profile
        )

    # Target module pipeline (profile-independent: uses first profile for
    # component-score tilting in scenario bands, which is a minor effect).
    peer_multiple_stats = _build_peer_multiple_stats(scan_data)
    target_results: dict[str, CompanyTargetResult] = {}
    for row, derived_row in zip(scan_data, derived_metrics):
        result = _build_company_targets(
            row, derived_row, profiles, peer_multiple_stats, resolved_profiles[0]
        )
        if result is not None:
            target_results[result.symbol] = result

    # Merge: iterate by row index (same order across all profile runs).
    cross_results: list[CrossScannerResult] = []
    row_count = len(scan_data)

    for row_idx in range(row_count):
        row = scan_data[row_idx]
        symbol = _get_symbol_name(row)
        target = target_results.get(symbol)
        if target is None:
            continue

        close = _coerce_numeric(row.get("close"))
        market_cap = _coerce_numeric(row.get("market_cap_basic"))
        earnings_days = _parse_event_days(row.get("earnings_release_next_date"))

        # Collect per-profile prediction data for this row.
        per_profile_pred: dict[str, dict[str, Any]] = {}
        per_profile_components: dict[str, dict[str, float | None]] = {}
        for pname in profile_names:
            pred_row = all_prediction_rows[pname][row_idx]
            per_profile_pred[pname] = pred_row
            per_profile_components[pname] = pred_row["components"]

        # Average component scores across profiles.
        avg_components: dict[str, float | None] = {}
        for comp in COMPONENT_ORDER:
            values = [
                per_profile_components[pn].get(comp)
                for pn in profile_names
                if per_profile_components[pn].get(comp) is not None
            ]
            avg_components[comp] = sum(values) / len(values) if values else None

        # Build per-horizon cross results with multi-profile aggregation.
        horizon_results: dict[str, HorizonCrossResult] = {}
        for target_horizon in TARGET_HORIZONS:
            pred_horizon = TARGET_TO_PREDICTION[target_horizon]
            target_h = target.horizon_targets.get(target_horizon)
            if target_h is None:
                continue

            # Aggregate prediction scores across profiles (weighted).
            weighted_score = 0.0
            weight_used = 0.0
            weighted_confidence = 0.0
            weighted_coverage = 0.0
            profile_horizon_scores: dict[str, float | None] = {}
            n_positive = 0
            n_negative = 0
            n_contributing = 0

            for pname in profile_names:
                pred_h = per_profile_pred[pname]["horizons"].get(pred_horizon, {})
                h_score = pred_h.get("score")
                h_coverage = pred_h.get("coverage", 0.0) or 0.0
                profile_horizon_scores[pname] = h_score

                if h_score is None or h_coverage < MINIMUM_COVERAGE_FOR_CONSENSUS:
                    continue

                # Sign-invert fragility_short (bearish profile).
                effective_score = -h_score if pname == "fragility_short" else h_score

                profile_weight = CONSENSUS_PROFILE_WEIGHTS.get(pname, 0.10)
                weighted_score += effective_score * profile_weight
                weight_used += profile_weight
                weighted_confidence += (
                    pred_h.get("confidence") or 0.0
                ) * profile_weight
                weighted_coverage += h_coverage * profile_weight
                n_contributing += 1

                if effective_score >= DIRECTIONAL_MOVE_SCORE_THRESHOLD:
                    n_positive += 1
                elif effective_score <= -DIRECTIONAL_MOVE_SCORE_THRESHOLD:
                    n_negative += 1

            if weight_used == 0 or n_contributing == 0:
                continue

            agg_score = _clamp(weighted_score / weight_used, -3.0, 3.0)
            agg_confidence = weighted_confidence / weight_used
            agg_coverage = weighted_coverage / weight_used

            max_directional = max(n_positive, n_negative)
            profile_agree_ratio = (
                max_directional / n_contributing if n_contributing > 0 else 0.0
            )

            agreement = _compute_signal_agreement(agg_score, target_h.base_upside_pct)

            conviction = _compute_conviction_score(
                prediction_score=agg_score,
                base_upside_pct=target_h.base_upside_pct,
                signal_agreement=agreement,
                prediction_coverage=agg_coverage,
                target_coverage=target_h.coverage,
                quality_score=avg_components.get("quality"),
                safety_score=avg_components.get("safety"),
            )

            horizon_results[target_horizon] = HorizonCrossResult(
                horizon=target_horizon,
                prediction_score=agg_score,
                prediction_direction=_direction_label_from_score(agg_score),
                prediction_confidence=agg_confidence,
                prediction_coverage=agg_coverage,
                profile_scores=profile_horizon_scores,
                profile_agreement_ratio=round(profile_agree_ratio, 3),
                profile_count=n_contributing,
                bear_price=target_h.bear_price,
                base_price=target_h.base_price,
                bull_price=target_h.bull_price,
                base_upside_pct=target_h.base_upside_pct,
                opportunity_score=target_h.opportunity_score,
                opportunity_label=target_h.opportunity_label,
                target_coverage=target_h.coverage,
                signal_agreement=agreement,
                agreement_label=_agreement_label(agreement),
                conviction_score=conviction,
                conviction_label=_conviction_label(conviction),
            )

        if not horizon_results:
            continue

        # Aggregate conviction across horizons (weighted by horizon importance).
        horizon_importance = {"near_term": 0.20, "medium_term": 0.45, "long_term": 0.35}
        total_weight = 0.0
        weighted_conviction = 0.0
        for h_name, h_result in horizon_results.items():
            w = horizon_importance.get(h_name, 0.33)
            weighted_conviction += h_result.conviction_score * w
            total_weight += w

        aggregate_conviction = (
            round(weighted_conviction / total_weight, 4) if total_weight > 0 else 0.0
        )

        # Coverage diagnostics.
        pred_coverages = [
            h.prediction_coverage
            for h in horizon_results.values()
            if h.prediction_coverage is not None
        ]
        target_coverages = [h.target_coverage for h in horizon_results.values()]
        pred_avg = sum(pred_coverages) / len(pred_coverages) if pred_coverages else 0.0
        target_avg = (
            sum(target_coverages) / len(target_coverages) if target_coverages else 0.0
        )

        # Divergence flags (using medium-term horizon).
        mt_result = horizon_results.get("medium_term")
        has_div = False
        has_cov_asym = False
        has_earn_flag = False
        if mt_result is not None:
            has_div = _detect_directional_price_divergence(
                mt_result.prediction_score, mt_result.base_upside_pct
            )
            has_cov_asym = _detect_coverage_asymmetry(
                mt_result.prediction_coverage, mt_result.target_coverage
            )
            has_earn_flag = _detect_earnings_proximity(
                earnings_days, mt_result.prediction_confidence
            )

        has_horizon_conf = _detect_horizon_conflict(horizon_results)

        cross_results.append(
            CrossScannerResult(
                symbol=symbol,
                company_name=target.company_name,
                industry=target.industry,
                sector=str(row.get("sector") or ""),
                close=close,
                market_cap=market_cap,
                horizon_results=horizon_results,
                aggregate_conviction=aggregate_conviction,
                aggregate_label=_conviction_label(aggregate_conviction),
                component_scores=avg_components,
                profile_component_scores=per_profile_components,
                profile_names=profile_names,
                prediction_avg_coverage=round(pred_avg, 3),
                target_avg_coverage=round(target_avg, 3),
                coverage_gap=round(abs(pred_avg - target_avg), 3),
                has_directional_price_divergence=has_div,
                has_coverage_asymmetry=has_cov_asym,
                has_horizon_conflict=has_horizon_conf,
                earnings_proximity_flag=has_earn_flag,
                raw_row=row,
                prediction_data=per_profile_pred,
                target_data=target,
            )
        )

    return cross_results


# ---------------------------------------------------------------------------
# Report logging
# ---------------------------------------------------------------------------


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "     N/A"
    return f"{value:+8.2f}%"


def _fmt_score(value: float | None) -> str:
    if value is None:
        return "    N/A"
    return f"{value:+7.3f}"


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "    N/A"
    return f"{value:8.2f}"


def _log_methodology(log_file: Path, profile_names: list[str]) -> None:
    log_to_file(log_file, "METHODOLOGY — Cross-Scanner Aggregator (Multi-Profile)")
    log_to_file(log_file, "-" * 160)
    log_to_file(
        log_file,
        "This report merges two independent analysis pipelines run on the same "
        "TradingView scan data:",
    )
    log_to_file(log_file, "")
    log_to_file(
        log_file,
        "  1. Move-Prediction Module — scores each name directionally across "
        "8 components (attention, event, momentum, trend, quality, valuation, "
        "safety, scale) and 4 time horizons (days/weeks/months/years).",
    )
    log_to_file(
        log_file,
        "  2. Forward-Target Module — estimates bear/base/bull price targets "
        "through 3 lenses (multiple-anchored, technical-anchored, fundamental-"
        "trajectory) across 3 horizons (near/medium/long term).",
    )
    log_to_file(log_file, "")
    log_to_file(
        log_file,
        f"  Profiles evaluated ({len(profile_names)}): " + ", ".join(profile_names),
    )
    log_to_file(
        log_file,
        "  Profile weights: "
        + ", ".join(
            f"{p}={CONSENSUS_PROFILE_WEIGHTS.get(p, 0.10):.2f}" for p in profile_names
        ),
    )
    log_to_file(log_file, "")
    log_to_file(
        log_file,
        "The aggregator runs every profile independently, then computes a "
        "weighted prediction score per horizon using CONSENSUS_PROFILE_WEIGHTS "
        "(same weights as the consensus aggregator).  fragility_short scores "
        "are sign-inverted before aggregation.  The weighted prediction score "
        "is then cross-referenced with target-module upside to produce:",
    )
    log_to_file(
        log_file,
        "  • Signal agreement — do prediction consensus and targets agree on direction?",
    )
    log_to_file(
        log_file,
        "  • Conviction score — weighted composite of prediction strength, "
        "target upside, agreement, coverage quality, and fundamental floor.",
    )
    log_to_file(
        log_file,
        "  • Profile agreement ratio — what fraction of profiles agree on direction.",
    )
    log_to_file(
        log_file,
        "  • Divergence alerts — flags when modules contradict each other, "
        "when coverage is asymmetric, or when horizons conflict.",
    )
    log_to_file(log_file, "")
    log_to_file(
        log_file,
        "Conviction weights: "
        + " | ".join(f"{k}={v:.0%}" for k, v in CONVICTION_WEIGHTS.items()),
    )
    log_to_file(
        log_file,
        "Horizon importance: near_term=20% | medium_term=45% | long_term=35%",
    )
    log_to_file(log_file, "")


def _log_conviction_ranking(
    log_file: Path,
    results: list[CrossScannerResult],
    horizon: str,
    section_title: str,
    sort_descending: bool = True,
) -> None:
    """Log a conviction-ranked table for one horizon."""
    log_to_file(log_file, section_title)
    log_to_file(log_file, "-" * 160)

    scored = [r for r in results if horizon in r.horizon_results]
    scored.sort(
        key=lambda r: r.horizon_results[horizon].conviction_score,
        reverse=sort_descending,
    )

    header = (
        f"{'Ticker':<12} {'Company':<22} {'MCap':>8} "
        f"{'PredScr':>8} {'Dir':<10} {'BaseUp%':>9} "
        f"{'Agree':>7} {'AgrLbl':<18} "
        f"{'Conv':>7} {'Label':<16} "
        f"{'PrfAgr':>6} {'#Prf':>4} {'PCov':>5} {'TCov':>5}"
    )
    log_to_file(log_file, header)
    log_to_file(log_file, "-" * 160)

    for r in scored[:TOP_SECTION_ROWS]:
        h = r.horizon_results[horizon]
        log_to_file(
            log_file,
            f"{r.symbol:<12} {r.company_name[:22]:<22} "
            f"{_format_market_cap(r.market_cap):>8} "
            f"{_fmt_score(h.prediction_score):>8} {h.prediction_direction:<10} "
            f"{_fmt_pct(h.base_upside_pct):>9} "
            f"{h.signal_agreement:+.2f}  {h.agreement_label:<18} "
            f"{h.conviction_score:+.3f}  {h.conviction_label:<16} "
            f"{h.profile_agreement_ratio:.0%}  {h.profile_count:>4} "
            f"{(h.prediction_coverage or 0):.0%} {h.target_coverage:.0%}",
        )

    log_to_file(log_file, "")


def _log_divergence_alerts(
    log_file: Path,
    results: list[CrossScannerResult],
) -> None:
    """Log names with divergence flags for manual review."""
    log_to_file(log_file, "DIVERGENCE ALERTS — Manual Review Required")
    log_to_file(log_file, "=" * 160)

    # 1. Directional-price divergence.
    div_results = [r for r in results if r.has_directional_price_divergence]
    log_to_file(
        log_file,
        f"Directional-price divergence ({len(div_results)} names) — "
        "prediction says one direction, target says the opposite",
    )
    log_to_file(log_file, "-" * 160)
    if div_results:
        for r in div_results[:TOP_SECTION_ROWS]:
            mt = r.horizon_results.get("medium_term")
            if mt is None:
                continue
            log_to_file(
                log_file,
                f"  {r.symbol:<12} {r.company_name[:24]:<24} "
                f"pred={_fmt_score(mt.prediction_score)} ({mt.prediction_direction})  "
                f"target_upside={_fmt_pct(mt.base_upside_pct)}  "
                f"agreement={mt.signal_agreement:+.2f}",
            )
    else:
        log_to_file(log_file, "  (none)")
    log_to_file(log_file, "")

    # 2. Coverage asymmetry.
    cov_results = [r for r in results if r.has_coverage_asymmetry]
    log_to_file(
        log_file,
        f"Coverage asymmetry ({len(cov_results)} names) — "
        "one module has strong coverage, the other is nearly blind",
    )
    log_to_file(log_file, "-" * 160)
    if cov_results:
        for r in cov_results[:TOP_SECTION_ROWS]:
            log_to_file(
                log_file,
                f"  {r.symbol:<12} {r.company_name[:24]:<24} "
                f"pred_cov={r.prediction_avg_coverage:.0%}  "
                f"target_cov={r.target_avg_coverage:.0%}  "
                f"gap={r.coverage_gap:.0%}",
            )
    else:
        log_to_file(log_file, "  (none)")
    log_to_file(log_file, "")

    # 3. Horizon conflict.
    conflict_results = [r for r in results if r.has_horizon_conflict]
    log_to_file(
        log_file,
        f"Horizon conflict ({len(conflict_results)} names) — "
        "short-term and long-term conviction disagree on direction",
    )
    log_to_file(log_file, "-" * 160)
    if conflict_results:
        for r in conflict_results[:TOP_SECTION_ROWS]:
            nt = r.horizon_results.get("near_term")
            lt = r.horizon_results.get("long_term")
            nt_conv = f"{nt.conviction_score:+.3f}" if nt else "N/A"
            lt_conv = f"{lt.conviction_score:+.3f}" if lt else "N/A"
            log_to_file(
                log_file,
                f"  {r.symbol:<12} {r.company_name[:24]:<24} "
                f"near_term={nt_conv}  long_term={lt_conv}",
            )
    else:
        log_to_file(log_file, "  (none)")
    log_to_file(log_file, "")

    # 4. Earnings proximity.
    earn_results = [r for r in results if r.earnings_proximity_flag]
    log_to_file(
        log_file,
        f"Earnings proximity ({len(earn_results)} names) — "
        "within 14 days of earnings with low prediction confidence",
    )
    log_to_file(log_file, "-" * 160)
    if earn_results:
        for r in earn_results[:TOP_SECTION_ROWS]:
            mt = r.horizon_results.get("medium_term")
            conf = (
                f"{mt.prediction_confidence:.0f}"
                if mt and mt.prediction_confidence
                else "N/A"
            )
            log_to_file(
                log_file,
                f"  {r.symbol:<12} {r.company_name[:24]:<24} "
                f"pred_confidence={conf}  "
                f"aggregate_conv={r.aggregate_conviction:+.3f}",
            )
    else:
        log_to_file(log_file, "  (none)")
    log_to_file(log_file, "")


def _log_sector_conviction_summary(
    log_file: Path,
    results: list[CrossScannerResult],
) -> None:
    """Per-sector summary of average conviction and agreement."""
    log_to_file(log_file, "SECTOR CONVICTION SUMMARY")
    log_to_file(log_file, "-" * 160)

    sector_groups: dict[str, list[CrossScannerResult]] = collections.defaultdict(list)
    for r in results:
        sector_groups[r.sector or "Unknown"].append(r)

    sector_stats: list[tuple[str, int, float, float, int, int]] = []
    for sector, group in sorted(sector_groups.items(), key=lambda x: -len(x[1])):
        convictions = [r.aggregate_conviction for r in group]
        avg_conv = sum(convictions) / len(convictions) if convictions else 0.0

        mt_agreements = [
            r.horizon_results["medium_term"].signal_agreement
            for r in group
            if "medium_term" in r.horizon_results
        ]
        avg_agree = sum(mt_agreements) / len(mt_agreements) if mt_agreements else 0.0

        n_divergent = sum(1 for r in group if r.has_directional_price_divergence)
        n_conflict = sum(1 for r in group if r.has_horizon_conflict)

        sector_stats.append(
            (sector, len(group), avg_conv, avg_agree, n_divergent, n_conflict)
        )

    sector_stats.sort(key=lambda x: x[2], reverse=True)

    log_to_file(
        log_file,
        f"{'Sector':<35} {'Count':>6} {'AvgConv':>8} {'AvgAgree':>9} "
        f"{'Diverg':>7} {'HrzConf':>8}",
    )
    log_to_file(log_file, "-" * 160)
    for sector, count, avg_conv, avg_agree, n_div, n_conf in sector_stats:
        log_to_file(
            log_file,
            f"{sector[:35]:<35} {count:>6} {avg_conv:+8.3f} {avg_agree:+9.3f} "
            f"{n_div:>7} {n_conf:>8}",
        )
    log_to_file(log_file, "")


def _log_high_conviction_names(
    log_file: Path,
    results: list[CrossScannerResult],
) -> None:
    """Names where both modules strongly agree across all horizons."""
    log_to_file(log_file, "HIGH-CONVICTION NAMES — Strong agreement across horizons")
    log_to_file(log_file, "=" * 160)

    # Long candidates: aggregate conviction ≥ 0.40 and no divergence flags.
    longs = [
        r
        for r in results
        if r.aggregate_conviction >= 0.40
        and not r.has_directional_price_divergence
        and not r.has_horizon_conflict
    ]
    longs.sort(key=lambda r: r.aggregate_conviction, reverse=True)

    log_to_file(log_file, f"Long candidates ({len(longs)} names)")
    log_to_file(log_file, "-" * 160)
    if longs:
        header = (
            f"{'Ticker':<12} {'Company':<22} {'MCap':>8} "
            f"{'AggConv':>8} {'Label':<16} "
            f"{'Qual':>6} {'Safe':>6} {'Mom':>6} {'Trend':>6} "
            f"{'MT Up%':>8} {'LT Up%':>8}"
        )
        log_to_file(log_file, header)
        log_to_file(log_file, "-" * 160)
        for r in longs[:TOP_SECTION_ROWS]:
            mt = r.horizon_results.get("medium_term")
            lt = r.horizon_results.get("long_term")
            mt_up = (
                f"{mt.base_upside_pct:+.1f}%" if mt and mt.base_upside_pct else "N/A"
            )
            lt_up = (
                f"{lt.base_upside_pct:+.1f}%" if lt and lt.base_upside_pct else "N/A"
            )
            log_to_file(
                log_file,
                f"{r.symbol:<12} {r.company_name[:22]:<22} "
                f"{_format_market_cap(r.market_cap):>8} "
                f"{r.aggregate_conviction:+8.3f} {r.aggregate_label:<16} "
                f"{_fmt_score(r.component_scores.get('quality')):>6} "
                f"{_fmt_score(r.component_scores.get('safety')):>6} "
                f"{_fmt_score(r.component_scores.get('momentum')):>6} "
                f"{_fmt_score(r.component_scores.get('trend')):>6} "
                f"{mt_up:>8} {lt_up:>8}",
            )
    else:
        log_to_file(log_file, "  (none)")
    log_to_file(log_file, "")

    # Short / avoid candidates: aggregate conviction ≤ -0.40 or divergence + low quality.
    shorts = [
        r
        for r in results
        if r.aggregate_conviction <= -0.40
        or (
            r.has_directional_price_divergence
            and (r.component_scores.get("quality") or 0) < -0.30
        )
    ]
    shorts.sort(key=lambda r: r.aggregate_conviction)

    log_to_file(log_file, f"Short / avoid candidates ({len(shorts)} names)")
    log_to_file(log_file, "-" * 160)
    if shorts:
        header = (
            f"{'Ticker':<12} {'Company':<22} {'MCap':>8} "
            f"{'AggConv':>8} {'Label':<16} "
            f"{'Qual':>6} {'Safe':>6} {'DivFlg':>6} {'HrzFlg':>6} "
            f"{'MT Up%':>8} {'BearPr':>8}"
        )
        log_to_file(log_file, header)
        log_to_file(log_file, "-" * 160)
        for r in shorts[:TOP_SECTION_ROWS]:
            mt = r.horizon_results.get("medium_term")
            mt_up = (
                f"{mt.base_upside_pct:+.1f}%" if mt and mt.base_upside_pct else "N/A"
            )
            bear = f"{mt.bear_price:.2f}" if mt and mt.bear_price else "N/A"
            log_to_file(
                log_file,
                f"{r.symbol:<12} {r.company_name[:22]:<22} "
                f"{_format_market_cap(r.market_cap):>8} "
                f"{r.aggregate_conviction:+8.3f} {r.aggregate_label:<16} "
                f"{_fmt_score(r.component_scores.get('quality')):>6} "
                f"{_fmt_score(r.component_scores.get('safety')):>6} "
                f"{'  YES' if r.has_directional_price_divergence else '   no':>6} "
                f"{'  YES' if r.has_horizon_conflict else '   no':>6} "
                f"{mt_up:>8} {bear:>8}",
            )
    else:
        log_to_file(log_file, "  (none)")
    log_to_file(log_file, "")


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


def _build_csv_headers(profile_names: list[str]) -> list[str]:
    headers = [
        "symbol",
        "company",
        "industry",
        "sector",
        "market_cap",
        "close",
        "aggregate_conviction",
        "aggregate_label",
        "prediction_avg_coverage",
        "target_avg_coverage",
        "coverage_gap",
        "directional_price_divergence",
        "coverage_asymmetry",
        "horizon_conflict",
        "earnings_proximity_flag",
    ]
    for horizon in TARGET_HORIZONS:
        prefix = horizon
        headers.extend(
            [
                f"{prefix}_prediction_score",
                f"{prefix}_prediction_direction",
                f"{prefix}_prediction_confidence",
                f"{prefix}_prediction_coverage",
                f"{prefix}_profile_agreement_ratio",
                f"{prefix}_profile_count",
                f"{prefix}_base_price",
                f"{prefix}_base_upside_pct",
                f"{prefix}_bear_price",
                f"{prefix}_bull_price",
                f"{prefix}_opportunity_score",
                f"{prefix}_opportunity_label",
                f"{prefix}_target_coverage",
                f"{prefix}_signal_agreement",
                f"{prefix}_agreement_label",
                f"{prefix}_conviction_score",
                f"{prefix}_conviction_label",
            ]
        )
        # Per-profile scores for this horizon.
        for pname in profile_names:
            headers.append(f"{prefix}_{pname}_score")
    for comp in COMPONENT_ORDER:
        headers.append(f"component_{comp}")
    return headers


def _build_csv_row(result: CrossScannerResult) -> list[str]:
    row: list[str] = [
        result.symbol,
        result.company_name,
        result.industry,
        result.sector,
        str(result.market_cap or ""),
        str(result.close or ""),
        str(result.aggregate_conviction),
        result.aggregate_label,
        str(result.prediction_avg_coverage),
        str(result.target_avg_coverage),
        str(result.coverage_gap),
        str(result.has_directional_price_divergence),
        str(result.has_coverage_asymmetry),
        str(result.has_horizon_conflict),
        str(result.earnings_proximity_flag),
    ]
    for horizon in TARGET_HORIZONS:
        h = result.horizon_results.get(horizon)
        if h is not None:
            row.extend(
                [
                    str(h.prediction_score or ""),
                    h.prediction_direction,
                    str(h.prediction_confidence or ""),
                    str(h.prediction_coverage or ""),
                    str(h.profile_agreement_ratio),
                    str(h.profile_count),
                    str(h.base_price or ""),
                    str(h.base_upside_pct or ""),
                    str(h.bear_price or ""),
                    str(h.bull_price or ""),
                    str(h.opportunity_score or ""),
                    h.opportunity_label,
                    str(h.target_coverage),
                    str(h.signal_agreement),
                    h.agreement_label,
                    str(h.conviction_score),
                    h.conviction_label,
                ]
            )
            for pname in result.profile_names:
                row.append(str(h.profile_scores.get(pname) or ""))
        else:
            row.extend([""] * (17 + len(result.profile_names)))
    for comp in COMPONENT_ORDER:
        row.append(str(result.component_scores.get(comp) or ""))
    return row


# ---------------------------------------------------------------------------
# File naming
# ---------------------------------------------------------------------------


def _build_report_file_name(
    industries: list[str] | None,
    min_market_cap_usd: float | None,
    max_market_cap_usd: float | None,
    output_dir: Path | None = None,
) -> Path:
    base_dir = output_dir or LOG_DIR
    base_dir.mkdir(parents=True, exist_ok=True)

    parts: list[str] = ["cross_scanner"]
    if industries:
        parts.append("_".join(i[:20].replace(" ", "") for i in industries[:3]))
    else:
        parts.append("all")

    if min_market_cap_usd:
        parts.append(f"min{min_market_cap_usd / 1e9:.0f}B")
    if max_market_cap_usd:
        parts.append(f"max{max_market_cap_usd / 1e9:.0f}B")

    timestamp = datetime.now().strftime("%d_%m_%Y")
    filename = f"{'_'.join(parts)}_{timestamp}.log"
    return base_dir / filename


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_cross_scanner_aggregate(
    scan_data: list[dict[str, Any]],
    industries: list[str] | str | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    scoring_profiles: list[str] | str | None = None,
    output_dir: str | Path | None = None,
) -> Path:
    """Run all prediction profiles + target pipeline and produce a merged report.

    Parameters
    ----------
    scan_data:
        Mapped rows from ``ApiTradingViewClient.scan_global_market_move_prediction``.
    industries, min_market_cap_usd, max_market_cap_usd:
        Metadata for report labelling (filtering is done at scan time).
    scoring_profiles:
        List of prediction-module profile names to evaluate.  If *None*, all
        preset profiles are used.  A single string is treated as a one-element
        list.
    output_dir:
        Override directory for report files.

    Returns
    -------
    Path to the generated log file.
    """
    industries_list = _normalize_industries(industries)

    # Normalise profile input.
    if isinstance(scoring_profiles, str):
        profile_name_list: list[str] | None = [scoring_profiles]
    else:
        profile_name_list = scoring_profiles

    resolved_profiles = _resolve_profile_list(profile_name_list)
    profile_names = [p.name for p in resolved_profiles]

    resolved_output_dir = Path(output_dir) if output_dir is not None else None

    log_file = _build_report_file_name(
        industries_list,
        min_market_cap_usd,
        max_market_cap_usd,
        output_dir=resolved_output_dir,
    )
    csv_file = log_file.with_suffix(".csv")

    _reset_log_file(log_file)

    log_to_file(
        log_file,
        _build_report_title("Cross-Scanner Aggregator — Prediction × Targets"),
    )
    log_to_file(log_file, "=" * 160)
    log_to_file(
        log_file,
        f"Rows: {len(scan_data)} | industries={industries_list or 'all'} | "
        f"min_mcap={min_market_cap_usd} | max_mcap={max_market_cap_usd} | "
        f"profiles={', '.join(profile_names)}",
    )
    log_to_file(log_file, "")

    if not scan_data:
        log_to_file(log_file, "No scan data provided.")
        return log_file

    # Build merged results across all profiles.
    cross_results = _build_cross_scanner_results(scan_data, resolved_profiles)

    log_to_file(
        log_file,
        f"Cross-referenced {len(cross_results)} companies "
        f"(out of {len(scan_data)} scan rows) across {len(profile_names)} profiles.",
    )
    log_to_file(log_file, "")

    # Report sections.
    _log_methodology(log_file, profile_names)

    for horizon in TARGET_HORIZONS:
        label = TARGET_HORIZON_LABELS[horizon]
        _log_conviction_ranking(
            log_file,
            cross_results,
            horizon,
            f"CONVICTION RANKING — {label} (top opportunities)",
            sort_descending=True,
        )
        _log_conviction_ranking(
            log_file,
            cross_results,
            horizon,
            f"CONVICTION RANKING — {label} (most overvalued / short candidates)",
            sort_descending=False,
        )

    _log_divergence_alerts(log_file, cross_results)
    _log_sector_conviction_summary(log_file, cross_results)
    _log_high_conviction_names(log_file, cross_results)

    # CSV export.
    csv_headers = _build_csv_headers(profile_names)
    csv_rows = [_build_csv_row(r) for r in cross_results]
    log_rows_to_csv(csv_file, csv_headers, csv_rows)

    log_to_file(log_file, f"CSV exported to {csv_file}")
    log_to_file(log_file, "")

    return log_file


def run_full_combined_suite(
    scan_data: list[dict[str, Any]],
    industries: list[str] | str | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    scoring_profiles: list[str] | str | None = None,
    include_blind_spot_sections: bool = False,
    output_dir: str | Path | None = None,
) -> dict[str, Path]:
    """Run all three analysis layers and return paths to every generated file.

    Layers executed:
    1. ``run_full_analysis_suite`` — all prediction profiles + consensus
    2. ``analyze_targets_scan`` — forward target estimation
    3. ``run_cross_scanner_aggregate`` — merged conviction report across all profiles

    Parameters
    ----------
    scoring_profiles:
        Profile names for the cross-scanner aggregation.  If *None*, all
        preset profiles are used.  The prediction suite always runs all
        profiles regardless of this parameter.

    Returns a dict mapping report name → log file path.
    """
    resolved_output_dir = Path(output_dir) if output_dir is not None else None

    # 1. Full prediction suite (all profiles + consensus).
    prediction_logs = run_full_analysis_suite(
        scan_data=scan_data,
        industries=industries,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
        include_blind_spot_sections=include_blind_spot_sections,
        output_dir=str(resolved_output_dir) if resolved_output_dir else None,
    )

    # 2. Forward target scan (profile-independent for valuation lenses;
    #    uses first resolved profile for minor scenario-band tilting).
    targets_log = analyze_targets_scan(
        scan_data=scan_data,
        industries=industries,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
        output_dir=str(resolved_output_dir) if resolved_output_dir else None,
    )

    # 3. Cross-scanner aggregate (runs all specified profiles).
    cross_log = run_cross_scanner_aggregate(
        scan_data=scan_data,
        industries=industries,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
        scoring_profiles=scoring_profiles,
        output_dir=str(resolved_output_dir) if resolved_output_dir else None,
    )

    result = dict(prediction_logs)
    result["_targets"] = targets_log
    result["_cross_scanner"] = cross_log
    return result
