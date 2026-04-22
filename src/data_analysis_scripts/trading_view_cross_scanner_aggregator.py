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

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Callable

from data_analysis_scripts._shared_analysis_utils import (
    build_report_title as _build_report_title,
    coerce_numeric as _coerce_numeric,
    format_market_cap as _format_market_cap,
    reset_log_file as _reset_log_file,
)
from data_analysis_scripts.trading_view_move_prediction_analysis import (
    COMPONENT_ORDER,
    CONSENSUS_PROFILE_WEIGHTS,
    DIRECTIONAL_MOVE_SCORE_THRESHOLD,
    MINIMUM_COVERAGE_FOR_CONSENSUS,
    PRESET_SCORING_PROFILES,
    STRONG_MOVE_SCORE_THRESHOLD,
    ScoringProfile,
    resolve_move_prediction_scoring_profile,
    _build_derived_metrics,
    _build_metric_profiles,
    _build_prediction_rows,
    _enrich_with_peer_metrics,
    _get_symbol_name,
    _normalize_industries,
    _parse_event_days,
    _clamp,
    run_full_analysis_suite,
)
from data_analysis_scripts.trading_view_targets_analysis import (
    TARGET_HORIZONS,
    TARGET_HORIZON_LABELS,
    MODERATE_OPPORTUNITY_THRESHOLD,
    CompanyTargetResult,
    analyze_targets_scan,
    _build_peer_multiple_stats,
    _build_company_targets,
)
from generic_utils.log_to_files_util import log_to_file, log_rows_to_csv

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


# ---------------------------------------------------------------------------
# Contextual signal-quality constants
# ---------------------------------------------------------------------------

# 52-week extremity threshold: distance from the 52w high/low (as a fraction
# of close) under which we consider a stock "pinned" to the extreme.
EXTREMITY_PROXIMITY_FRACTION = 0.05  # 5%

# Volume confirmation thresholds (relative_volume_10d_calc).
VOLUME_CONFIRM_THRESHOLD = 1.20  # 20% above 10-day average → confirms move
VOLUME_DECAY_THRESHOLD = 0.60  # 40% below 10-day average → liquidity decay

# Lens dispersion threshold above which the target base price is treated
# as low-confidence in the signal_quality_score.
LENS_DISPERSION_HIGH = 0.25  # 25% spread between lens prices

# Divergence (in pp) between the blended model base upside and the analyst
# consensus upside above which we flag the name in the model-vs-street
# alert section (and on the horizon row itself).
ANALYST_DIVERGENCE_THRESHOLD_PCT = 30.0

# Signal quality score weights — combine the four contextual checks into
# a single per-horizon health read-out used by the conviction blend.
SIGNAL_QUALITY_WEIGHTS = {
    "rank_agreement": 0.40,
    "lens_dispersion": 0.20,  # inverted (low dispersion is good)
    "momentum_coherence": 0.25,
    "volume_confirmation": 0.15,
}

# ---------------------------------------------------------------------------
# Outlier / data-quality constants
# ---------------------------------------------------------------------------

# Raw-input sanity gates.  Each gate fires when the underlying field exits
# the plausibility envelope; the resulting tag is appended to the per-row
# ``data_quality_warnings`` list and feeds into ``confidence_modifier``.
DATA_QUALITY_GATES: dict[str, dict[str, float]] = {
    "extreme_beta": {"min": -0.5, "max": 5.0},  # beta_1_year
    "extreme_gap_pct": {"max_abs": 25.0},  # |gap|
    "extreme_growth_pct": {"max_abs": 500.0},  # any YoY growth_ttm
    "extreme_leverage": {"max": 30.0},  # debt_to_equity
    "extreme_eps_surprise_pct": {"max_abs": 200.0},  # eps_surprise_percent_fq
    "illiquid_float_shares": {"min": 10_000_000.0},  # float_shares_outstanding
    "illiquid_dollar_volume": {"min": 1_000_000.0},  # Value.Traded
    "negative_book_multiple": {"min": 0.0},  # price_book_fq
}

# Each warning multiplies the confidence modifier by this factor (compounding,
# floored at MIN_CONFIDENCE_MODIFIER).  Outlier z-flag uses its own factor.
DATA_QUALITY_WARNING_FACTOR = 0.88
STATISTICAL_OUTLIER_FACTOR = 0.80
MIN_CONFIDENCE_MODIFIER = 0.40

# Robust z-score threshold for statistical outlier detection (median + MAD).
ROBUST_Z_THRESHOLD = 3.5


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
    signal_agreement: float  # legacy magnitude-based, kept for backward compatibility
    agreement_label: str  # "Strong Agreement", "Moderate", "Divergent"
    conviction_score: float  # weighted composite (uses rank_agreement internally)
    conviction_label: str

    # New: lens-disagreement read-out from the targets module.
    # 0.0 = all four lenses agree on price; 0.4 = 40% spread (low confidence).
    lens_dispersion: float | None = None

    # New: rank-based agreement within the scan distribution.  Replaces the
    # raw magnitude product as the main agreement metric — robust to scans
    # where typical prediction/upside magnitudes are small.  Range [-1, +1].
    rank_agreement: float = 0.0

    # New: per-horizon signal quality score combining rank agreement, low
    # lens dispersion, multi-period momentum coherence, and volume
    # confirmation.  Range [0, 1] — higher = cleaner signal.
    signal_quality_score: float = 0.0

    # New: model-vs-street divergence on this horizon.  Computed only when
    # the targets module produced an analyst-base upside (i.e. analyst
    # consensus was available).  Positive = model more bullish than street.
    analyst_base_upside_pct: float | None = None
    model_vs_analyst_divergence_pct: float | None = None
    has_analyst_divergence: bool = False  # |divergence| >= 30 pp


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

    # New: contextual signal-quality reads (computed once per name from the
    # raw scan row, reused across all horizons).
    momentum_coherence: float = 0.0  # [-1, +1] sign coherence across periods
    volume_confirmation: str = (
        "neutral"  # "confirmed" / "unconfirmed" / "decay" / "neutral"
    )
    extremity_flag: str = "neutral"  # "extended_long" / "exhausted_short" / "neutral"

    # New: outlier mechanism — both raw-input gates and scan-distribution
    # statistics.  ``confidence_modifier`` ∈ [0.4, 1.0] is the multiplier
    # used to derive ``aggregate_conviction_adjusted`` for ranking purposes.
    # Raw ``aggregate_conviction`` is preserved so diagnostics stay readable.
    data_quality_warnings: list[str] = field(default_factory=list)
    is_statistical_outlier: bool = False
    confidence_modifier: float = 1.0
    aggregate_conviction_adjusted: float = 0.0


# ---------------------------------------------------------------------------
# Contextual signal-quality helpers
# ---------------------------------------------------------------------------


def _compute_momentum_coherence(row: dict[str, Any]) -> float:
    """Sign-coherence of multi-period performance fields.

    Returns a value in [-1, +1]:
      +1.0 → all available periods point the same direction (clean trend)
      -1.0 → opposite halves cancel out (whipsaw)
       0.0 → no data or balanced mix
    Uses ``Perf.W``, ``Perf.1M``, ``Perf.3M``, ``Perf.6M``, ``Perf.Y`` weighted
    so longer lookbacks count more (anchor on direction, not noise).
    """
    weights = {
        "Perf.W": 0.10,
        "Perf.1M": 0.20,
        "Perf.3M": 0.25,
        "Perf.6M": 0.20,
        "Perf.Y": 0.25,
    }
    total_weight = 0.0
    weighted_sign = 0.0
    for field_name, weight in weights.items():
        value = _coerce_numeric(row.get(field_name))
        if value is None:
            continue
        sign = 1.0 if value > 0 else (-1.0 if value < 0 else 0.0)
        weighted_sign += sign * weight
        total_weight += weight
    if total_weight == 0.0:
        return 0.0
    return _clamp(weighted_sign / total_weight, -1.0, 1.0)


def _compute_volume_confirmation(
    row: dict[str, Any],
    prediction_score: float | None,
) -> str:
    """Classify whether recent volume confirms or contradicts the prediction.

    Uses ``relative_volume_10d_calc`` (today's volume vs 10-day average).
      * "confirmed"   — bullish or bearish prediction backed by elevated volume
      * "unconfirmed" — directional prediction but volume below average
      * "decay"       — bearish prediction with collapsing turnover (often
                         precedes a low-volume bounce, not a real breakdown)
      * "neutral"     — prediction near zero or volume data missing
    """
    rel_vol = _coerce_numeric(row.get("relative_volume_10d_calc"))
    if rel_vol is None or prediction_score is None:
        return "neutral"
    if abs(prediction_score) < DIRECTIONAL_MOVE_SCORE_THRESHOLD:
        return "neutral"
    if rel_vol >= VOLUME_CONFIRM_THRESHOLD:
        return "confirmed"
    if prediction_score < 0 and rel_vol <= VOLUME_DECAY_THRESHOLD:
        return "decay"
    return "unconfirmed"


def _compute_extremity_flag(
    row: dict[str, Any],
    prediction_score: float | None,
) -> str:
    """Flag prediction-vs-52w-extreme conflicts.

    * "extended_long"   — bullish prediction but stock pinned to 52w high
                          (low remaining upside, mean-reversion risk)
    * "exhausted_short" — bearish prediction but stock pinned to 52w low
                          (downside likely already priced in)
    * "neutral"         — prediction not directional or no extremity
    """
    if prediction_score is None:
        return "neutral"
    close = _coerce_numeric(row.get("close"))
    high_52 = _coerce_numeric(row.get("price_52_week_high"))
    low_52 = _coerce_numeric(row.get("price_52_week_low"))
    if close is None or close <= 0:
        return "neutral"

    bullish = prediction_score >= DIRECTIONAL_MOVE_SCORE_THRESHOLD
    bearish = prediction_score <= -DIRECTIONAL_MOVE_SCORE_THRESHOLD

    if bullish and high_52 is not None and high_52 > 0:
        if (high_52 - close) / close <= EXTREMITY_PROXIMITY_FRACTION:
            return "extended_long"
    if bearish and low_52 is not None and low_52 > 0:
        if (close - low_52) / close <= EXTREMITY_PROXIMITY_FRACTION:
            return "exhausted_short"
    return "neutral"


# ---------------------------------------------------------------------------
# Rank-based agreement
# ---------------------------------------------------------------------------


def _rank_values(values: list[float | None]) -> list[float | None]:
    """Compute fractional ranks in ``[0, 1]`` for a list of values.

    ``None`` entries keep their position but receive ``None`` so the caller
    can detect missing data.  Ties get the average rank.
    """
    indexed = [(idx, v) for idx, v in enumerate(values) if v is not None]
    if not indexed:
        return [None] * len(values)
    indexed.sort(key=lambda x: x[1])
    n = len(indexed)
    ranks: list[float | None] = [None] * len(values)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        # Average rank for the tie group, normalised to [0, 1].
        avg_rank = (i + j) / 2.0
        normalised = avg_rank / max(n - 1, 1)
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = normalised
        i = j + 1
    return ranks


def _compute_rank_agreement(
    pred_rank: float | None,
    target_rank: float | None,
    pred_score: float | None,
    target_upside: float | None,
) -> float:
    """Rank-based agreement between prediction and target within the scan.

    Returns a value in [-1, +1]:
      +1 → prediction and target both at the top (or both at the bottom) of
           the scan-wide distribution (perfect concordance)
      -1 → one at the top, the other at the bottom (full inversion)
       0 → either side missing, or one is mid-distribution

    Sign concordance from the raw values is added as a tie-breaker so that
    two near-zero ranks (mid-distribution) but with opposite raw signs still
    register as a mild divergence.
    """
    if pred_rank is None or target_rank is None:
        return 0.0
    # Centre ranks on 0 so the product carries a sign.
    p = (pred_rank - 0.5) * 2.0  # [-1, +1]
    t = (target_rank - 0.5) * 2.0  # [-1, +1]
    rank_concord = p * t  # +1 same end of distribution, -1 opposite ends
    # Sign tie-breaker from raw values — adds a small bonus when raw signs
    # match even in the middle of the distribution.
    if pred_score is not None and target_upside is not None:
        sign_bonus = 0.15 if (pred_score * target_upside) >= 0 else -0.15
    else:
        sign_bonus = 0.0
    return _clamp(rank_concord + sign_bonus, -1.0, 1.0)


def _compute_signal_quality(
    rank_agreement: float,
    lens_dispersion: float | None,
    momentum_coherence: float,
    volume_confirmation: str,
) -> float:
    """Compose a per-horizon signal-quality score in ``[0, 1]``.

    High score ⇒ models agree (rank-wise), the target lenses agree among
    themselves, multi-period momentum is coherent, and volume confirms the
    move.  Used as a soft modifier — does not replace conviction but
    augments it as a confidence read.
    """
    # Rank agreement contribution: only positive values count toward quality.
    rank_part = max(0.0, rank_agreement)

    # Lens dispersion contribution (inverted): tight lens spread = high score.
    if lens_dispersion is None:
        dispersion_part = 0.5  # neutral when missing
    else:
        dispersion_part = _clamp(1.0 - lens_dispersion / LENS_DISPERSION_HIGH, 0.0, 1.0)

    # Momentum coherence contribution (use absolute coherence — both clean
    # bullish and clean bearish trends count as high quality).
    momentum_part = abs(momentum_coherence)

    # Volume confirmation contribution.
    vol_part = {
        "confirmed": 1.0,
        "neutral": 0.5,
        "unconfirmed": 0.25,
        "decay": 0.10,
    }.get(volume_confirmation, 0.5)

    score = (
        rank_part * SIGNAL_QUALITY_WEIGHTS["rank_agreement"]
        + dispersion_part * SIGNAL_QUALITY_WEIGHTS["lens_dispersion"]
        + momentum_part * SIGNAL_QUALITY_WEIGHTS["momentum_coherence"]
        + vol_part * SIGNAL_QUALITY_WEIGHTS["volume_confirmation"]
    )
    return round(_clamp(score, 0.0, 1.0), 4)


# ---------------------------------------------------------------------------
# Outlier / data-quality helpers
# ---------------------------------------------------------------------------


def _compute_data_quality_warnings(row: dict[str, Any]) -> list[str]:
    """Inspect the raw scan row for warped or implausible field values.

    Each gate is a defensive check against TradingView field anomalies that
    would otherwise warp downstream scoring without leaving a trace.  Returns
    a list of short tag strings (empty if the row passes all checks).
    """
    warnings: list[str] = []

    def _check(tag: str, field_name: str, predicate: Callable[[float], bool]) -> None:
        value = _coerce_numeric(row.get(field_name))
        if value is not None and predicate(value):
            warnings.append(tag)

    beta_gate = DATA_QUALITY_GATES["extreme_beta"]
    _check(
        "extreme_beta",
        "beta_1_year",
        lambda v: v > beta_gate["max"] or v < beta_gate["min"],
    )
    _check(
        "extreme_gap",
        "gap",
        lambda v: abs(v) > DATA_QUALITY_GATES["extreme_gap_pct"]["max_abs"],
    )

    growth_max = DATA_QUALITY_GATES["extreme_growth_pct"]["max_abs"]
    growth_fields = (
        "total_revenue_yoy_growth_ttm",
        "ebitda_yoy_growth_ttm",
        "net_income_yoy_growth_ttm",
        "free_cash_flow_yoy_growth_ttm",
        "earnings_per_share_diluted_yoy_growth_ttm",
    )
    for gf in growth_fields:
        value = _coerce_numeric(row.get(gf))
        if value is not None and abs(value) > growth_max:
            warnings.append("extreme_growth")
            break  # one is enough — no need to repeat the tag

    _check(
        "extreme_leverage",
        "debt_to_equity",
        lambda v: v > DATA_QUALITY_GATES["extreme_leverage"]["max"],
    )
    _check(
        "extreme_eps_surprise",
        "eps_surprise_percent_fq",
        lambda v: abs(v) > DATA_QUALITY_GATES["extreme_eps_surprise_pct"]["max_abs"],
    )
    _check(
        "illiquid_float",
        "float_shares_outstanding",
        lambda v: v < DATA_QUALITY_GATES["illiquid_float_shares"]["min"],
    )
    _check(
        "illiquid_dollar_volume",
        "Value.Traded",
        lambda v: v < DATA_QUALITY_GATES["illiquid_dollar_volume"]["min"],
    )
    _check(
        "negative_book_multiple",
        "price_book_fq",
        lambda v: v < DATA_QUALITY_GATES["negative_book_multiple"]["min"],
    )

    return warnings


def _robust_z_scores(values: list[float]) -> list[float]:
    """Robust z-scores using median and MAD (median absolute deviation).

    Falls back to the raw deviation when MAD is zero (degenerate distribution
    — usually a tightly bunched scan).  Returns 0.0 for empty inputs so the
    caller can iterate without branching.
    """
    if not values:
        return []
    med = median(values)
    deviations = [abs(v - med) for v in values]
    mad = median(deviations)
    # 1.4826 scales MAD to be a consistent estimator of the std dev under
    # a normal distribution — the standard convention for robust z-scores.
    if mad == 0:
        return [0.0] * len(values)
    scaled_mad = 1.4826 * mad
    return [(v - med) / scaled_mad for v in values]


def _compute_confidence_modifier(
    warnings: list[str],
    is_outlier: bool,
) -> float:
    """Combine data-quality warnings and the outlier flag into a multiplier.

    Each warning compounds a small penalty; the outlier flag adds an extra
    discount.  Result is floored at ``MIN_CONFIDENCE_MODIFIER`` so the worst
    cases still keep some signal (preventing total nullification).
    """
    modifier = 1.0
    for _ in warnings:
        modifier *= DATA_QUALITY_WARNING_FACTOR
    if is_outlier:
        modifier *= STATISTICAL_OUTLIER_FACTOR
    return round(max(MIN_CONFIDENCE_MODIFIER, modifier), 4)


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


# Per-horizon weights used to combine horizon convictions into the
# aggregate.  Anything not listed falls back to the default below.
HORIZON_IMPORTANCE_WEIGHTS = {
    "near_term": 0.20,
    "medium_term": 0.45,
    "long_term": 0.35,
}
DEFAULT_HORIZON_WEIGHT = 0.33


def _build_cross_scanner_results(
    scan_data: list[dict[str, Any]],
    resolved_profiles: list[ScoringProfile],
) -> list[CrossScannerResult]:
    """Run all profiles + targets on scan_data and merge into CrossScannerResult list.

    For each company, prediction scores are computed independently per profile,
    then aggregated using ``CONSENSUS_PROFILE_WEIGHTS`` (same weights as the
    consensus aggregator in the prediction module).  This gives a single
    weighted prediction score per horizon that reflects all profile lenses.

    Two-pass design:
      1. First pass builds per-row HorizonCrossResult/CrossScannerResult with
         contextual fields (momentum coherence, volume confirmation,
         extremity flag, data-quality warnings) and computes per-horizon
         scan-wide rank arrays for prediction score and target upside.
      2. Second pass applies robust-z outlier detection over the resulting
         aggregate convictions and stamps ``is_statistical_outlier``,
         ``confidence_modifier``, and ``aggregate_conviction_adjusted``
         using ``dataclasses.replace`` (results are frozen).
    """
    profile_names = [p.name for p in resolved_profiles]

    all_prediction_rows, target_results = _run_pipelines(scan_data, resolved_profiles)

    horizon_aggregates, pred_ranks, target_ranks = _collect_horizon_ranks(
        scan_data, profile_names, all_prediction_rows, target_results
    )

    cross_results: list[CrossScannerResult] = []
    for row_idx, row in enumerate(scan_data):
        target = target_results.get(_get_symbol_name(row))
        if target is None:
            continue
        result = _build_cross_result_for_row(
            row=row,
            target=target,
            row_idx=row_idx,
            profile_names=profile_names,
            all_prediction_rows=all_prediction_rows,
            horizon_aggregates=horizon_aggregates[row_idx],
            pred_ranks=pred_ranks,
            target_ranks=target_ranks,
        )
        if result is not None:
            cross_results.append(result)

    return _apply_outlier_pass(cross_results)


def _run_pipelines(
    scan_data: list[dict[str, Any]],
    resolved_profiles: list[ScoringProfile],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, CompanyTargetResult]]:
    """Run the prediction pipeline (per profile) and the targets pipeline once.

    Returns ``(all_prediction_rows, target_results)`` where prediction rows
    are keyed by profile name and aligned with ``scan_data`` row order, and
    target results are keyed by symbol.
    """
    # Shared first-stage pipeline (run once, reused by all profiles and targets).
    _enrich_with_peer_metrics(scan_data)
    derived_metrics = [_build_derived_metrics(row) for row in scan_data]
    profiles = _build_metric_profiles(scan_data, derived_metrics)

    all_prediction_rows: dict[str, list[dict[str, Any]]] = {
        sp.name: _build_prediction_rows(scan_data, profiles, derived_metrics, sp)
        for sp in resolved_profiles
    }

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

    return all_prediction_rows, target_results


def _collect_horizon_ranks(
    scan_data: list[dict[str, Any]],
    profile_names: list[str],
    all_prediction_rows: dict[str, list[dict[str, Any]]],
    target_results: dict[str, CompanyTargetResult],
) -> tuple[
    list[dict[str, dict[str, Any]]],
    dict[str, list[float | None]],
    dict[str, list[float | None]],
]:
    """Pre-pass: aggregate predictions and compute scan-wide rank arrays.

    Returns ``(horizon_aggregates, pred_ranks, target_ranks)``:
      * ``horizon_aggregates[row_idx][target_horizon]`` → aggregated prediction dict
      * ``pred_ranks[target_horizon][row_idx]`` → fractional rank in [0,1] or None
      * ``target_ranks[target_horizon][row_idx]`` → fractional rank in [0,1] or None
    """
    n = len(scan_data)
    pred_score_per_horizon: dict[str, list[float | None]] = {
        h: [None] * n for h in TARGET_HORIZONS
    }
    target_upside_per_horizon: dict[str, list[float | None]] = {
        h: [None] * n for h in TARGET_HORIZONS
    }
    horizon_aggregates: list[dict[str, dict[str, Any]]] = [{} for _ in range(n)]

    for row_idx, row in enumerate(scan_data):
        target = target_results.get(_get_symbol_name(row))
        if target is None:
            continue
        for target_horizon in TARGET_HORIZONS:
            target_h = target.horizon_targets.get(target_horizon)
            if target_h is None:
                continue
            agg = _aggregate_prediction_for_horizon(
                row_idx,
                TARGET_TO_PREDICTION[target_horizon],
                profile_names,
                all_prediction_rows,
            )
            if agg is None:
                continue
            horizon_aggregates[row_idx][target_horizon] = agg
            pred_score_per_horizon[target_horizon][row_idx] = agg["score"]
            target_upside_per_horizon[target_horizon][
                row_idx
            ] = target_h.base_upside_pct

    pred_ranks = {h: _rank_values(pred_score_per_horizon[h]) for h in TARGET_HORIZONS}
    target_ranks = {
        h: _rank_values(target_upside_per_horizon[h]) for h in TARGET_HORIZONS
    }
    return horizon_aggregates, pred_ranks, target_ranks


def _build_horizon_result(
    *,
    target_horizon: str,
    target_h: Any,
    target_result: CompanyTargetResult,
    agg: dict[str, Any],
    row: dict[str, Any],
    row_idx: int,
    momentum_coherence: float,
    avg_components: dict[str, float | None],
    pred_ranks: dict[str, list[float | None]],
    target_ranks: dict[str, list[float | None]],
) -> HorizonCrossResult:
    """Compose one HorizonCrossResult from a single row + horizon aggregate."""
    agg_score = agg["score"]
    agg_confidence = agg["confidence"]
    agg_coverage = agg["coverage"]

    # Legacy magnitude-based agreement (kept for CSV continuity).
    agreement = _compute_signal_agreement(agg_score, target_h.base_upside_pct)

    # Rank-based agreement using the scan-wide distribution.
    rank_agreement = _compute_rank_agreement(
        pred_rank=pred_ranks[target_horizon][row_idx],
        target_rank=target_ranks[target_horizon][row_idx],
        pred_score=agg_score,
        target_upside=target_h.base_upside_pct,
    )

    volume_status = _compute_volume_confirmation(row, agg_score)
    signal_quality = _compute_signal_quality(
        rank_agreement=rank_agreement,
        lens_dispersion=target_h.lens_dispersion,
        momentum_coherence=momentum_coherence,
        volume_confirmation=volume_status,
    )

    # Conviction uses the rank-based agreement (more informative than the
    # raw magnitude product) so weak-magnitude scans still produce
    # meaningful conviction labels.
    conviction = _compute_conviction_score(
        prediction_score=agg_score,
        base_upside_pct=target_h.base_upside_pct,
        signal_agreement=rank_agreement,
        prediction_coverage=agg_coverage,
        target_coverage=target_h.coverage,
        quality_score=avg_components.get("quality"),
        safety_score=avg_components.get("safety"),
    )

    # Analyst-vs-model divergence: only meaningful when the targets module
    # produced an analyst base upside (otherwise analyst lens was silent).
    analyst_base_upside = None
    analyst_summary = target_result.analyst_summary or {}
    raw_analyst_upside = analyst_summary.get("analyst_base_upside_pct")
    if isinstance(raw_analyst_upside, (int, float)):
        analyst_base_upside = float(raw_analyst_upside)

    divergence_pct: float | None = None
    has_divergence = False
    if analyst_base_upside is not None and target_h.base_upside_pct is not None:
        divergence_pct = target_h.base_upside_pct - analyst_base_upside
        has_divergence = abs(divergence_pct) >= ANALYST_DIVERGENCE_THRESHOLD_PCT

    return HorizonCrossResult(
        horizon=target_horizon,
        prediction_score=agg_score,
        prediction_direction=_direction_label_from_score(agg_score),
        prediction_confidence=agg_confidence,
        prediction_coverage=agg_coverage,
        profile_scores=agg["profile_scores"],
        profile_agreement_ratio=round(agg["profile_agreement_ratio"], 3),
        profile_count=agg["profile_count"],
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
        lens_dispersion=target_h.lens_dispersion,
        rank_agreement=round(rank_agreement, 4),
        signal_quality_score=signal_quality,
        analyst_base_upside_pct=analyst_base_upside,
        model_vs_analyst_divergence_pct=(
            round(divergence_pct, 2) if divergence_pct is not None else None
        ),
        has_analyst_divergence=has_divergence,
    )


def _average_components(
    profile_names: list[str],
    per_profile_components: dict[str, dict[str, float | None]],
) -> dict[str, float | None]:
    """Average each component score across all profiles that produced one."""
    avg: dict[str, float | None] = {}
    for comp in COMPONENT_ORDER:
        values = [
            per_profile_components[pn].get(comp)
            for pn in profile_names
            if per_profile_components[pn].get(comp) is not None
        ]
        avg[comp] = sum(values) / len(values) if values else None
    return avg


def _aggregate_horizon_conviction(
    horizon_results: dict[str, HorizonCrossResult],
) -> float:
    """Weighted average of conviction across horizons."""
    total_weight = 0.0
    weighted = 0.0
    for h_name, h_result in horizon_results.items():
        w = HORIZON_IMPORTANCE_WEIGHTS.get(h_name, DEFAULT_HORIZON_WEIGHT)
        weighted += h_result.conviction_score * w
        total_weight += w
    return round(weighted / total_weight, 4) if total_weight > 0 else 0.0


def _build_cross_result_for_row(
    *,
    row: dict[str, Any],
    target: CompanyTargetResult,
    row_idx: int,
    profile_names: list[str],
    all_prediction_rows: dict[str, list[dict[str, Any]]],
    horizon_aggregates: dict[str, dict[str, Any]],
    pred_ranks: dict[str, list[float | None]],
    target_ranks: dict[str, list[float | None]],
) -> CrossScannerResult | None:
    """Assemble the full CrossScannerResult for one row, or None if no horizons fit."""
    momentum_coherence = _compute_momentum_coherence(row)

    per_profile_pred: dict[str, dict[str, Any]] = {}
    per_profile_components: dict[str, dict[str, float | None]] = {}
    for pname in profile_names:
        pred_row = all_prediction_rows[pname][row_idx]
        per_profile_pred[pname] = pred_row
        per_profile_components[pname] = pred_row["components"]

    avg_components = _average_components(profile_names, per_profile_components)

    horizon_results: dict[str, HorizonCrossResult] = {}
    for target_horizon in TARGET_HORIZONS:
        target_h = target.horizon_targets.get(target_horizon)
        agg = horizon_aggregates.get(target_horizon)
        if target_h is None or agg is None:
            continue
        horizon_results[target_horizon] = _build_horizon_result(
            target_horizon=target_horizon,
            target_h=target_h,
            target_result=target,
            agg=agg,
            row=row,
            row_idx=row_idx,
            momentum_coherence=momentum_coherence,
            avg_components=avg_components,
            pred_ranks=pred_ranks,
            target_ranks=target_ranks,
        )

    if not horizon_results:
        return None

    # Anchor row-level contextual reads on medium-term when available.
    mt_anchor = horizon_results.get("medium_term") or next(
        iter(horizon_results.values())
    )
    anchor_score = mt_anchor.prediction_score
    volume_confirmation_overall = _compute_volume_confirmation(row, anchor_score)
    extremity_flag = _compute_extremity_flag(row, anchor_score)

    aggregate_conviction = _aggregate_horizon_conviction(horizon_results)

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

    # Divergence flags (using medium-term horizon when present).
    earnings_days = _parse_event_days(row.get("earnings_release_next_date"))
    mt_result = horizon_results.get("medium_term")
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
    else:
        has_div = has_cov_asym = has_earn_flag = False

    return CrossScannerResult(
        symbol=_get_symbol_name(row),
        company_name=target.company_name,
        industry=target.industry,
        sector=str(row.get("sector") or ""),
        close=_coerce_numeric(row.get("close")),
        market_cap=_coerce_numeric(row.get("market_cap_basic")),
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
        has_horizon_conflict=_detect_horizon_conflict(horizon_results),
        earnings_proximity_flag=has_earn_flag,
        raw_row=row,
        prediction_data=per_profile_pred,
        target_data=target,
        momentum_coherence=round(momentum_coherence, 4),
        volume_confirmation=volume_confirmation_overall,
        extremity_flag=extremity_flag,
        data_quality_warnings=_compute_data_quality_warnings(row),
        # is_statistical_outlier / confidence_modifier /
        # aggregate_conviction_adjusted are stamped by _apply_outlier_pass.
    )


def _apply_outlier_pass(
    cross_results: list[CrossScannerResult],
) -> list[CrossScannerResult]:
    """Stamp robust-z outlier flag and confidence modifier on each result."""
    convictions = [r.aggregate_conviction for r in cross_results]
    z_scores = _robust_z_scores(convictions)
    finalised: list[CrossScannerResult] = []
    for r, z in zip(cross_results, z_scores):
        is_outlier = abs(z) >= ROBUST_Z_THRESHOLD
        modifier = _compute_confidence_modifier(r.data_quality_warnings, is_outlier)
        finalised.append(
            replace(
                r,
                is_statistical_outlier=is_outlier,
                confidence_modifier=modifier,
                aggregate_conviction_adjusted=round(
                    r.aggregate_conviction * modifier, 4
                ),
            )
        )
    return finalised


def _aggregate_prediction_for_horizon(
    row_idx: int,
    pred_horizon: str,
    profile_names: list[str],
    all_prediction_rows: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    """Aggregate per-profile prediction scores for a single row + horizon.

    Returns a dict with aggregated score / confidence / coverage and the
    per-profile breakdown, or ``None`` when no profile contributed enough
    coverage.  Extracted from the main loop so the same aggregation is
    reused by both passes.
    """
    weighted_score = 0.0
    weight_used = 0.0
    weighted_confidence = 0.0
    weighted_coverage = 0.0
    profile_horizon_scores: dict[str, float | None] = {}
    n_positive = 0
    n_negative = 0
    n_contributing = 0

    for pname in profile_names:
        pred_h = all_prediction_rows[pname][row_idx]["horizons"].get(pred_horizon, {})
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
        weighted_confidence += (pred_h.get("confidence") or 0.0) * profile_weight
        weighted_coverage += h_coverage * profile_weight
        n_contributing += 1

        if effective_score >= DIRECTIONAL_MOVE_SCORE_THRESHOLD:
            n_positive += 1
        elif effective_score <= -DIRECTIONAL_MOVE_SCORE_THRESHOLD:
            n_negative += 1

    if weight_used == 0 or n_contributing == 0:
        return None

    agg_score = _clamp(weighted_score / weight_used, -3.0, 3.0)
    agg_confidence = weighted_confidence / weight_used
    agg_coverage = weighted_coverage / weight_used
    profile_agreement_ratio = max(n_positive, n_negative) / n_contributing

    return {
        "score": agg_score,
        "confidence": agg_confidence,
        "coverage": agg_coverage,
        "profile_scores": profile_horizon_scores,
        "profile_agreement_ratio": profile_agreement_ratio,
        "profile_count": n_contributing,
    }


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
    log_to_file(
        log_file,
        "Contextual signal-quality reads (per name / per horizon):",
    )
    log_to_file(
        log_file,
        "  • Rank agreement — prediction-score rank vs target-upside rank within the scan. "
        "Replaces the raw magnitude product as the agreement signal feeding conviction; "
        "stays meaningful even when typical scan magnitudes are small.",
    )
    log_to_file(
        log_file,
        "  • Lens dispersion — spread of the four target lenses (technical, multiple, "
        "trajectory, range) around the base price.  High dispersion ⇒ low-confidence base.",
    )
    log_to_file(
        log_file,
        "  • Momentum coherence — sign-coherence across Perf.W / 1M / 3M / 6M / Y. "
        "Catches whipsaw names where short and long lookbacks contradict each other.",
    )
    log_to_file(
        log_file,
        "  • Volume confirmation — relative_volume_10d_calc vs the directional prediction. "
        "Bullish predictions on quiet tape are flagged 'unconfirmed'.",
    )
    log_to_file(
        log_file,
        "  • Extremity flag — bullish prediction within 5% of the 52w high (extended_long) "
        "or bearish prediction within 5% of the 52w low (exhausted_short).",
    )
    log_to_file(log_file, "")
    log_to_file(
        log_file,
        "Outlier mechanism — every result is checked against:",
    )
    log_to_file(
        log_file,
        "  1. Raw-input data-quality gates (extreme beta, gap, growth, leverage, eps surprise; "
        "illiquid float / dollar volume; negative book multiple).",
    )
    log_to_file(
        log_file,
        f"  2. Robust z-score (median + MAD) on aggregate conviction across the scan; "
        f"|z| ≥ {ROBUST_Z_THRESHOLD} marks the row as a statistical outlier.",
    )
    log_to_file(
        log_file,
        f"  Each warning multiplies confidence_modifier by {DATA_QUALITY_WARNING_FACTOR:.2f}; "
        f"outlier flag adds a {STATISTICAL_OUTLIER_FACTOR:.2f}× factor; "
        f"floored at {MIN_CONFIDENCE_MODIFIER:.2f}.  "
        "aggregate_conviction_adjusted = aggregate_conviction × confidence_modifier "
        "(use this for ranking when sorting opportunities).",
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
        f"{'Ticker':<12} {'Company':<22} {'Industry':<20} {'MCap':>8} "
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
            f"{r.symbol:<12} {r.company_name[:22]:<22} {r.industry[:20]:<20} "
            f"{_format_market_cap(r.market_cap):>8} "
            f"{_fmt_score(h.prediction_score):>8} {h.prediction_direction:<10} "
            f"{_fmt_pct(h.base_upside_pct):>9} "
            f"{h.signal_agreement:+.2f}  {h.agreement_label:<18} "
            f"{h.conviction_score:+.3f}  {h.conviction_label:<16} "
            f"{h.profile_agreement_ratio:.0%}  {h.profile_count:>4} "
            f"{(h.prediction_coverage or 0):.0%} {h.target_coverage:.0%}",
        )

    log_to_file(log_file, "")


def _log_alert_section(
    log_file: Path,
    title: str,
    rows: list[CrossScannerResult],
    line_formatter: Callable[[CrossScannerResult], str | None],
) -> None:
    """Log a single alert subsection: title, separator, body or '(none)'.

    ``line_formatter`` returns the formatted line for each row, or ``None``
    if the row should be skipped (e.g. missing horizon data).
    """
    log_to_file(log_file, f"{title} ({len(rows)} names)")
    log_to_file(log_file, "-" * 160)
    if rows:
        for r in rows[:TOP_SECTION_ROWS]:
            line = line_formatter(r)
            if line is not None:
                log_to_file(log_file, line)
    else:
        log_to_file(log_file, "  (none)")
    log_to_file(log_file, "")


def _log_divergence_alerts(
    log_file: Path,
    results: list[CrossScannerResult],
) -> None:
    """Log names with divergence flags for manual review."""
    log_to_file(log_file, "DIVERGENCE ALERTS — Manual Review Required")
    log_to_file(log_file, "=" * 160)

    def _fmt_directional(r: CrossScannerResult) -> str | None:
        mt = r.horizon_results.get("medium_term")
        if mt is None:
            return None
        return (
            f"  {r.symbol:<12} {r.company_name[:24]:<24} {r.industry[:20]:<20} "
            f"pred={_fmt_score(mt.prediction_score)} ({mt.prediction_direction})  "
            f"target_upside={_fmt_pct(mt.base_upside_pct)}  "
            f"agreement={mt.signal_agreement:+.2f}"
        )

    def _fmt_coverage(r: CrossScannerResult) -> str:
        return (
            f"  {r.symbol:<12} {r.company_name[:24]:<24} {r.industry[:20]:<20} "
            f"pred_cov={r.prediction_avg_coverage:.0%}  "
            f"target_cov={r.target_avg_coverage:.0%}  "
            f"gap={r.coverage_gap:.0%}"
        )

    def _fmt_conflict(r: CrossScannerResult) -> str:
        nt = r.horizon_results.get("near_term")
        lt = r.horizon_results.get("long_term")
        nt_conv = f"{nt.conviction_score:+.3f}" if nt else "N/A"
        lt_conv = f"{lt.conviction_score:+.3f}" if lt else "N/A"
        return (
            f"  {r.symbol:<12} {r.company_name[:24]:<24} {r.industry[:20]:<20} "
            f"near_term={nt_conv}  long_term={lt_conv}"
        )

    def _fmt_earnings(r: CrossScannerResult) -> str:
        mt = r.horizon_results.get("medium_term")
        conf = (
            f"{mt.prediction_confidence:.0f}"
            if mt and mt.prediction_confidence
            else "N/A"
        )
        return (
            f"  {r.symbol:<12} {r.company_name[:24]:<24} {r.industry[:20]:<20} "
            f"pred_confidence={conf}  "
            f"aggregate_conv={r.aggregate_conviction:+.3f}"
        )

    _log_alert_section(
        log_file,
        "Directional-price divergence — prediction says one direction, target says the opposite",
        [r for r in results if r.has_directional_price_divergence],
        _fmt_directional,
    )
    _log_alert_section(
        log_file,
        "Coverage asymmetry — one module has strong coverage, the other is nearly blind",
        [r for r in results if r.has_coverage_asymmetry],
        _fmt_coverage,
    )
    _log_alert_section(
        log_file,
        "Horizon conflict — short-term and long-term conviction disagree on direction",
        [r for r in results if r.has_horizon_conflict],
        _fmt_conflict,
    )
    _log_alert_section(
        log_file,
        "Earnings proximity — within 14 days of earnings with low prediction confidence",
        [r for r in results if r.earnings_proximity_flag],
        _fmt_earnings,
    )

    def _fmt_analyst_divergence(r: CrossScannerResult) -> str | None:
        lt = r.horizon_results.get("long_term")
        if lt is None or not lt.has_analyst_divergence:
            return None
        direction = (
            "Model bullish vs street"
            if (lt.model_vs_analyst_divergence_pct or 0) > 0
            else "Model bearish vs street"
        )
        return (
            f"  {r.symbol:<12} {r.company_name[:24]:<24} {r.industry[:20]:<20} "
            f"model_lt={_fmt_pct(lt.base_upside_pct)}  "
            f"analyst={_fmt_pct(lt.analyst_base_upside_pct)}  "
            f"div={_fmt_pct(lt.model_vs_analyst_divergence_pct)}  {direction}"
        )

    analyst_divergent = [
        r
        for r in results
        if (
            (lt := r.horizon_results.get("long_term")) is not None
            and lt.has_analyst_divergence
        )
    ]
    _log_alert_section(
        log_file,
        f"Model-vs-street divergence — long-term blended base upside differs from "
        f"analyst consensus by >= {ANALYST_DIVERGENCE_THRESHOLD_PCT:.0f}pp",
        analyst_divergent,
        _fmt_analyst_divergence,
    )


def _log_sector_conviction_summary(
    log_file: Path,
    results: list[CrossScannerResult],
) -> None:
    """Per-sector summary of average conviction and agreement."""
    log_to_file(log_file, "SECTOR CONVICTION SUMMARY")
    log_to_file(log_file, "-" * 160)

    sector_groups: dict[str, list[CrossScannerResult]] = defaultdict(list)
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


def _log_table_section(
    log_file: Path,
    subtitle: str,
    header: str,
    rows: list[CrossScannerResult],
    line_formatter: Callable[[CrossScannerResult], str],
) -> None:
    """Log a subsection with a fixed-width header row and per-row body."""
    log_to_file(log_file, f"{subtitle} ({len(rows)} names)")
    log_to_file(log_file, "-" * 160)
    if rows:
        log_to_file(log_file, header)
        log_to_file(log_file, "-" * 160)
        for r in rows[:TOP_SECTION_ROWS]:
            log_to_file(log_file, line_formatter(r))
    else:
        log_to_file(log_file, "  (none)")
    log_to_file(log_file, "")


def _log_high_conviction_names(
    log_file: Path,
    results: list[CrossScannerResult],
) -> None:
    """Names where both modules strongly agree across all horizons."""
    log_to_file(log_file, "HIGH-CONVICTION NAMES — Strong agreement across horizons")
    log_to_file(log_file, "=" * 160)

    # Long candidates: aggregate conviction ≥ 0.40 and no divergence flags.
    longs = sorted(
        (
            r
            for r in results
            if r.aggregate_conviction >= 0.40
            and not r.has_directional_price_divergence
            and not r.has_horizon_conflict
        ),
        key=lambda r: r.aggregate_conviction,
        reverse=True,
    )

    long_header = (
        f"{'Ticker':<12} {'Company':<22} {'Industry':<20} {'MCap':>8} "
        f"{'AggConv':>8} {'Label':<16} "
        f"{'Qual':>6} {'Safe':>6} {'Mom':>6} {'Trend':>6} "
        f"{'MT Up%':>8} {'LT Up%':>8}"
    )

    def _fmt_long(r: CrossScannerResult) -> str:
        mt = r.horizon_results.get("medium_term")
        lt = r.horizon_results.get("long_term")
        mt_up = f"{mt.base_upside_pct:+.1f}%" if mt and mt.base_upside_pct else "N/A"
        lt_up = f"{lt.base_upside_pct:+.1f}%" if lt and lt.base_upside_pct else "N/A"
        return (
            f"{r.symbol:<12} {r.company_name[:22]:<22} {r.industry[:20]:<20} "
            f"{_format_market_cap(r.market_cap):>8} "
            f"{r.aggregate_conviction:+8.3f} {r.aggregate_label:<16} "
            f"{_fmt_score(r.component_scores.get('quality')):>6} "
            f"{_fmt_score(r.component_scores.get('safety')):>6} "
            f"{_fmt_score(r.component_scores.get('momentum')):>6} "
            f"{_fmt_score(r.component_scores.get('trend')):>6} "
            f"{mt_up:>8} {lt_up:>8}"
        )

    _log_table_section(log_file, "Long candidates", long_header, longs, _fmt_long)

    # Short / avoid candidates: aggregate conviction ≤ -0.40 or divergence + low quality.
    shorts = sorted(
        (
            r
            for r in results
            if r.aggregate_conviction <= -0.40
            or (
                r.has_directional_price_divergence
                and (r.component_scores.get("quality") or 0) < -0.30
            )
        ),
        key=lambda r: r.aggregate_conviction,
    )

    short_header = (
        f"{'Ticker':<12} {'Company':<22} {'Industry':<20} {'MCap':>8} "
        f"{'AggConv':>8} {'Label':<16} "
        f"{'Qual':>6} {'Safe':>6} {'DivFlg':>6} {'HrzFlg':>6} "
        f"{'MT Up%':>8} {'BearPr':>8}"
    )

    def _fmt_short(r: CrossScannerResult) -> str:
        mt = r.horizon_results.get("medium_term")
        mt_up = f"{mt.base_upside_pct:+.1f}%" if mt and mt.base_upside_pct else "N/A"
        bear = f"{mt.bear_price:.2f}" if mt and mt.bear_price else "N/A"
        return (
            f"{r.symbol:<12} {r.company_name[:22]:<22} {r.industry[:20]:<20} "
            f"{_format_market_cap(r.market_cap):>8} "
            f"{r.aggregate_conviction:+8.3f} {r.aggregate_label:<16} "
            f"{_fmt_score(r.component_scores.get('quality')):>6} "
            f"{_fmt_score(r.component_scores.get('safety')):>6} "
            f"{'  YES' if r.has_directional_price_divergence else '   no':>6} "
            f"{'  YES' if r.has_horizon_conflict else '   no':>6} "
            f"{mt_up:>8} {bear:>8}"
        )

    _log_table_section(
        log_file, "Short / avoid candidates", short_header, shorts, _fmt_short
    )


def _log_outlier_alerts(
    log_file: Path,
    results: list[CrossScannerResult],
) -> None:
    """Surface statistical outliers and data-quality warnings.

    Two subsections:
      1. Statistical outliers — rows whose aggregate_conviction lies far from
         the scan median (robust z-score based).  Shows the raw vs adjusted
         conviction and which warnings/flags fired.
      2. Most-attenuated names — rows where the data-quality + outlier
         pipeline reduced confidence the most (lowest confidence_modifier).
         Useful for spotting names that look strong on raw conviction but
         are based on warped inputs.
    """
    log_to_file(log_file, "OUTLIER & DATA-QUALITY ALERTS")
    log_to_file(log_file, "=" * 160)

    outliers = sorted(
        (r for r in results if r.is_statistical_outlier),
        key=lambda r: abs(r.aggregate_conviction),
        reverse=True,
    )

    outlier_header = (
        f"{'Ticker':<12} {'Company':<22} {'Industry':<20} "
        f"{'AggConv':>8} {'Adj':>8} {'Mod':>6} "
        f"{'Mom':>6} {'Vol':<14} {'Extr':<16} {'Warnings':<60}"
    )

    def _fmt_outlier(r: CrossScannerResult) -> str:
        warns = ";".join(r.data_quality_warnings) or "(none)"
        return (
            f"{r.symbol:<12} {r.company_name[:22]:<22} {r.industry[:20]:<20} "
            f"{r.aggregate_conviction:+8.3f} "
            f"{r.aggregate_conviction_adjusted:+8.3f} "
            f"{r.confidence_modifier:>6.2f} "
            f"{r.momentum_coherence:>+6.2f} "
            f"{r.volume_confirmation:<14} {r.extremity_flag:<16} {warns[:60]:<60}"
        )

    _log_table_section(
        log_file, "Statistical outliers", outlier_header, outliers, _fmt_outlier
    )

    # Most-attenuated names: ordered ascending so the most-discounted appear first.
    attenuated = sorted(
        (r for r in results if r.confidence_modifier < 1.0),
        key=lambda r: r.confidence_modifier,
    )

    attenuated_header = (
        f"{'Ticker':<12} {'Company':<22} {'Industry':<20} "
        f"{'AggConv':>8} {'Adj':>8} {'Mod':>6} {'Outlier':>8} "
        f"{'Warnings':<80}"
    )

    def _fmt_attenuated(r: CrossScannerResult) -> str:
        warns = ";".join(r.data_quality_warnings) or "(none)"
        return (
            f"{r.symbol:<12} {r.company_name[:22]:<22} {r.industry[:20]:<20} "
            f"{r.aggregate_conviction:+8.3f} "
            f"{r.aggregate_conviction_adjusted:+8.3f} "
            f"{r.confidence_modifier:>6.2f} "
            f"{'  YES' if r.is_statistical_outlier else '   no':>8} "
            f"{warns[:80]:<80}"
        )

    _log_table_section(
        log_file,
        "Most-attenuated by data-quality / outlier checks",
        attenuated_header,
        attenuated,
        _fmt_attenuated,
    )


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
        "aggregate_conviction_adjusted",
        "aggregate_label",
        "confidence_modifier",
        "is_statistical_outlier",
        "data_quality_warnings",
        "momentum_coherence",
        "volume_confirmation",
        "extremity_flag",
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
                f"{prefix}_lens_dispersion",
                f"{prefix}_signal_agreement",
                f"{prefix}_agreement_label",
                f"{prefix}_rank_agreement",
                f"{prefix}_signal_quality_score",
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
        str(result.aggregate_conviction_adjusted),
        result.aggregate_label,
        str(result.confidence_modifier),
        str(result.is_statistical_outlier),
        ";".join(result.data_quality_warnings),
        str(result.momentum_coherence),
        result.volume_confirmation,
        result.extremity_flag,
        str(result.prediction_avg_coverage),
        str(result.target_avg_coverage),
        str(result.coverage_gap),
        str(result.has_directional_price_divergence),
        str(result.has_coverage_asymmetry),
        str(result.has_horizon_conflict),
        str(result.earnings_proximity_flag),
    ]
    # Per-horizon block has 20 fixed fields (was 17; added lens_dispersion,
    # rank_agreement, signal_quality_score) plus per-profile scores.
    fixed_horizon_fields = 20
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
                    str(h.lens_dispersion if h.lens_dispersion is not None else ""),
                    str(h.signal_agreement),
                    h.agreement_label,
                    str(h.rank_agreement),
                    str(h.signal_quality_score),
                    str(h.conviction_score),
                    h.conviction_label,
                ]
            )
            for pname in result.profile_names:
                row.append(str(h.profile_scores.get(pname) or ""))
        else:
            row.extend([""] * (fixed_horizon_fields + len(result.profile_names)))
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
    _log_outlier_alerts(log_file, cross_results)

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
