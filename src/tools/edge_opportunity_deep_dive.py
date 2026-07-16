"""
Generic helpers for point-in-time opportunity deep-dives across edge research
DuckDB artifacts and TradingView all-fields snapshots.

Reusable for any parent edge run + matching all-fields day. Not part of the
integrated edge suite pipeline; intended for adhoc / research allocation work.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import duckdb
import pandas as pd

# Columns pulled from all_fields for allocation theses.
ALL_FIELDS_SELECT = """
    symbol,
    name,
    description,
    exchange,
    country,
    sector,
    industry,
    TRY_CAST(close AS DOUBLE) AS close,
    TRY_CAST(market_cap_basic AS DOUBLE) AS market_cap,
    TRY_CAST("change" AS DOUBLE) AS chg_1d,
    TRY_CAST("change|1W" AS DOUBLE) AS chg_1w,
    TRY_CAST("change|1M" AS DOUBLE) AS chg_1m,
    TRY_CAST("Perf.3M" AS DOUBLE) AS perf_3m,
    TRY_CAST("Perf.6M" AS DOUBLE) AS perf_6m,
    TRY_CAST("Perf.Y" AS DOUBLE) AS perf_1y,
    TRY_CAST("Perf.YTD" AS DOUBLE) AS perf_ytd,
    TRY_CAST(price_52_week_high AS DOUBLE) AS high_52w,
    TRY_CAST(price_52_week_low AS DOUBLE) AS low_52w,
    TRY_CAST(price_earnings_ttm AS DOUBLE) AS pe_ttm,
    TRY_CAST(price_book_ratio AS DOUBLE) AS pb,
    TRY_CAST(price_sales_ratio AS DOUBLE) AS ps,
    TRY_CAST(price_free_cash_flow_ttm AS DOUBLE) AS p_fcf,
    TRY_CAST(price_revenue_ttm AS DOUBLE) AS p_rev,
    TRY_CAST(enterprise_value_to_revenue_ttm AS DOUBLE) AS ev_sales,
    TRY_CAST(enterprise_value_to_free_cash_flow_ttm AS DOUBLE) AS ev_fcf,
    TRY_CAST(earnings_yield AS DOUBLE) AS earnings_yield,
    TRY_CAST(dividend_yield_recent AS DOUBLE) AS div_yield,
    TRY_CAST(gross_margin_ttm AS DOUBLE) AS gross_margin,
    TRY_CAST(operating_margin AS DOUBLE) AS oper_margin,
    TRY_CAST(net_margin AS DOUBLE) AS net_margin,
    TRY_CAST(return_on_equity AS DOUBLE) AS roe,
    TRY_CAST(return_on_assets AS DOUBLE) AS roa,
    TRY_CAST(return_on_invested_capital AS DOUBLE) AS roic,
    TRY_CAST(total_revenue_yoy_growth_ttm AS DOUBLE) AS rev_yoy,
    TRY_CAST(earnings_per_share_diluted_yoy_growth_ttm AS DOUBLE) AS eps_yoy,
    TRY_CAST(free_cash_flow_margin_ttm AS DOUBLE) AS fcf_margin,
    TRY_CAST(free_cash_flow_yoy_growth_ttm AS DOUBLE) AS fcf_yoy,
    TRY_CAST(debt_to_equity AS DOUBLE) AS debt_equity,
    TRY_CAST(debt_to_assets AS DOUBLE) AS debt_assets,
    TRY_CAST(current_ratio AS DOUBLE) AS current_ratio,
    TRY_CAST(quick_ratio AS DOUBLE) AS quick_ratio,
    TRY_CAST(total_debt AS DOUBLE) AS total_debt,
    TRY_CAST(net_debt AS DOUBLE) AS net_debt,
    TRY_CAST(cash_n_equivalents_fq AS DOUBLE) AS cash,
    TRY_CAST(altman_z_score_ttm AS DOUBLE) AS altman_z,
    TRY_CAST(beta_1_year AS DOUBLE) AS beta_1y,
    TRY_CAST(Volatility.D AS DOUBLE) AS vol_d,
    TRY_CAST(Volatility.W AS DOUBLE) AS vol_w,
    TRY_CAST(ATRP AS DOUBLE) AS atrp,
    TRY_CAST(RSI AS DOUBLE) AS rsi,
    TRY_CAST("Recommend.All" AS DOUBLE) AS recommend_all,
    TRY_CAST("relative_volume_10d_calc" AS DOUBLE) AS relvol_10d,
    TRY_CAST(price_target_average AS DOUBLE) AS pt_avg,
    TRY_CAST(price_target_median AS DOUBLE) AS pt_median,
    TRY_CAST(price_target_high AS DOUBLE) AS pt_high,
    TRY_CAST(price_target_low AS DOUBLE) AS pt_low,
    TRY_CAST(price_target_1y_delta AS DOUBLE) AS pt_upside_pct,
    AnalystRating AS analyst_rating,
    earnings_release_next_date,
    TRY_CAST(earnings_per_share_forecast_next_fq AS DOUBLE) AS eps_next_fq,
    TRY_CAST(eps_surprise_percent_fq AS DOUBLE) AS eps_surprise_pct
"""


@dataclass
class DeepDivePaths:
    edge_parent: Path
    all_fields_db: Path
    conviction_csv: Path | None = None

    @property
    def aggregate(self) -> Path:
        return self.edge_parent / "aggregate"

    def child(self, *parts: str) -> Path:
        return self.aggregate.joinpath(*parts)


@dataclass
class NameRiskProfile:
    risk_tier: str  # LOW / MODERATE / HIGH / VERY_HIGH
    risk_score: float  # 0..1 higher = riskier
    flags: list[str] = field(default_factory=list)
    mitigants: list[str] = field(default_factory=list)


@dataclass
class NameAllocationScore:
    allocation_score: float  # 0..100
    allocation_rank: int | None = None
    components: dict[str, float] = field(default_factory=dict)
    stance: str = "WATCH"


def _safe_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _pct_from_high(close: float | None, high: float | None) -> float | None:
    if close is None or high is None or high == 0:
        return None
    return 100.0 * (close / high - 1.0)


def _sql_in(symbols: Sequence[str]) -> str:
    return ",".join("'" + s.replace("'", "''") + "'" for s in symbols)


def load_all_fields_snapshot(db_path: Path, symbols: Sequence[str]) -> pd.DataFrame:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        # Column availability varies slightly across snapshots; select defensively.
        available = set(con.execute("DESCRIBE all_fields_rows").fetchdf()["column_name"].tolist())
        # Map desired aliases to possible source columns.
        wanted = {
            "symbol": "symbol",
            "name": "name",
            "description": "description",
            "exchange": "exchange",
            "country": "country",
            "sector": "sector",
            "industry": "industry",
            "close": "close",
            "market_cap": "market_cap_basic",
            "chg_1d": "change",
            "chg_1w": "change|1W",
            "chg_1m": "change|1M",
            "perf_3m": "Perf.3M",
            "perf_6m": "Perf.6M",
            "perf_1y": "Perf.Y",
            "perf_ytd": "Perf.YTD",
            "high_52w": "price_52_week_high",
            "low_52w": "price_52_week_low",
            "pe_ttm": "price_earnings_ttm",
            "pb": "price_book_ratio",
            "ps": "price_sales_ratio",
            "p_fcf": "price_free_cash_flow_ttm",
            "p_rev": "price_revenue_ttm",
            "ev_sales": "enterprise_value_to_revenue_ttm",
            "ev_fcf": "enterprise_value_to_free_cash_flow_ttm",
            "earnings_yield": "earnings_yield",
            "div_yield": "dividend_yield_recent",
            "gross_margin": "gross_margin_ttm" if "gross_margin_ttm" in available else "gross_margin",
            "oper_margin": "operating_margin",
            "net_margin": "net_margin" if "net_margin" in available else None,
            "roe": "return_on_equity",
            "roa": "return_on_assets",
            "roic": "return_on_invested_capital",
            "rev_yoy": "total_revenue_yoy_growth_ttm",
            "eps_yoy": "earnings_per_share_diluted_yoy_growth_ttm",
            "fcf_margin": "free_cash_flow_margin_ttm",
            "fcf_yoy": "free_cash_flow_yoy_growth_ttm",
            "debt_equity": "debt_to_equity",
            "debt_assets": "debt_to_assets",
            "current_ratio": "current_ratio",
            "quick_ratio": "quick_ratio",
            "total_debt": "total_debt",
            "net_debt": "net_debt",
            "cash": "cash_n_equivalents_fq" if "cash_n_equivalents_fq" in available else "cash_n_short_term_invest_fq",
            "altman_z": "altman_z_score_ttm" if "altman_z_score_ttm" in available else "altman_z_score_fy",
            "beta_1y": "beta_1_year",
            "vol_d": "Volatility.D",
            "vol_w": "Volatility.W",
            "atrp": "ATRP",
            "rsi": "RSI",
            "recommend_all": "Recommend.All",
            "relvol_10d": "relative_volume_10d_calc",
            "pt_avg": "price_target_average",
            "pt_median": "price_target_median",
            "pt_high": "price_target_high",
            "pt_low": "price_target_low",
            "pt_upside_pct": "price_target_1y_delta",
            "analyst_rating": "AnalystRating",
            "earnings_release_next_date": "earnings_release_next_date",
            "eps_next_fq": "earnings_per_share_forecast_next_fq",
            "eps_surprise_pct": "eps_surprise_percent_fq",
        }
        select_parts = []
        for alias, src in wanted.items():
            if src is None or src not in available:
                select_parts.append(f"NULL AS {alias}")
            elif src in ("change", "change|1W", "change|1M", "Perf.3M", "Perf.6M", "Perf.Y", "Perf.YTD",
                         "Volatility.D", "Volatility.W", "Recommend.All", "relative_volume_10d_calc"):
                select_parts.append(f'TRY_CAST("{src}" AS DOUBLE) AS {alias}')
            elif src == "AnalystRating" or src == "earnings_release_next_date" or src in (
                "symbol", "name", "description", "exchange", "country", "sector", "industry"
            ):
                select_parts.append(f'"{src}" AS {alias}' if "|" in src or "." in src or " " in src else f"{src} AS {alias}")
            else:
                col = f'"{src}"' if any(ch in src for ch in ".| ") else src
                select_parts.append(f"TRY_CAST({col} AS DOUBLE) AS {alias}")

        sql = f"""
            SELECT {", ".join(select_parts)}
            FROM all_fields_rows
            WHERE symbol IN ({_sql_in(symbols)})
        """
        return con.execute(sql).fetchdf()
    finally:
        con.close()


def load_unified_rows(paths: DeepDivePaths, symbols: Sequence[str]) -> pd.DataFrame:
    db = paths.child("historical_edge_progression", "edge_unified_with_history.duckdb")
    if not db.exists():
        db = paths.child("edge_unified_highlights", "edge_unified_highlights.duckdb")
    con = duckdb.connect(str(db), read_only=True)
    try:
        cols = set(con.execute("DESCRIBE symbol_unified_highlights").fetchdf()["column_name"].tolist())
        keep = [
            "symbol", "company_name", "sector", "industry", "country", "exchange",
            "unified_edge_highlight_rank", "big_mover_rank", "upside_prediction_rank",
            "forward_upside_rank", "tradeable_safety_rank", "safety_bucket",
            "safety_companion_score", "balance_sheet_safety_score",
            "forward_valuation_upside_pct", "forward_valuation_bear_upside_pct",
            "forward_valuation_bull_upside_pct", "forward_company_style",
            "forward_selected_lenses", "forward_near_term_base_upside_pct",
            "forward_medium_term_base_upside_pct", "forward_long_term_base_upside_pct",
            "median_fwd_5d_in_setup", "win_rate_5d_in_setup", "target_rate_5d_in_setup",
            "hist_occurrence_count_in_setup", "lane_median_fwd_5d", "lane_win_rate_5d",
            "historical_validation_bucket", "historical_validation_win_rate",
            "historical_validation_median_fwd_pct", "historical_validation_pass",
            "composite_score", "strict_filter_pass",
            "historical_edge_daily_state", "historical_edge_current_rank",
            "historical_edge_rank_improvement_5d", "historical_edge_current_score",
            "historical_edge_hist_median_5d_pct", "historical_edge_hist_win_rate_5d",
            "safety_detail_altman_z_val", "safety_detail_debt_to_equity_val",
            "safety_detail_current_ratio_val", "safety_detail_free_cash_flow_margin_val",
            "safety_detail_earnings_yield_val", "safety_detail_net_debt_val",
            "safety_rank_global",
        ]
        present = [c for c in keep if c in cols]
        return con.execute(
            f"SELECT {', '.join(present)} FROM symbol_unified_highlights WHERE symbol IN ({_sql_in(symbols)})"
        ).fetchdf()
    finally:
        con.close()


def load_opportunity_rows(paths: DeepDivePaths, symbols: Sequence[str]) -> pd.DataFrame:
    db = paths.child("upside_opportunity_scan", "upside_opportunity_scan.duckdb")
    if not db.exists():
        return pd.DataFrame()
    con = duckdb.connect(str(db), read_only=True)
    try:
        cols = set(con.execute("DESCRIBE upside_opportunity_candidates").fetchdf()["column_name"].tolist())
        keep = [
            "symbol", "upside_opportunity_rank", "upside_opportunity_score", "opportunity_tier",
            "directional_lean", "opp_binary_risk_score", "opp_downside_risk_score",
            "opp_momentum_component", "opp_historical_validation_component",
            "tradeable_safety_blend_score",
        ]
        present = [c for c in keep if c in cols]
        return con.execute(
            f"SELECT {', '.join(present)} FROM upside_opportunity_candidates WHERE symbol IN ({_sql_in(symbols)})"
        ).fetchdf()
    finally:
        con.close()


def load_trade_plan_rows(paths: DeepDivePaths, symbols: Sequence[str]) -> pd.DataFrame:
    db = paths.child("edge_trade_plan", "edge_trade_plan.duckdb")
    if not db.exists():
        return pd.DataFrame()
    con = duckdb.connect(str(db), read_only=True)
    try:
        cols = set(con.execute("DESCRIBE edge_trade_plan").fetchdf()["column_name"].tolist())
        keep = [
            "symbol", "entry_state", "trade_plan_rank", "trade_plan_score",
            "trade_plan_quality_score", "trade_plan_timing_score",
            "matched_entry_variants", "perf_5d", "perf_1m", "extension_atr_units",
            "relative_volume_10d_calc", "recommend_all", "next_earnings_at",
        ]
        present = [c for c in keep if c in cols]
        return con.execute(
            f"SELECT {', '.join(present)} FROM edge_trade_plan WHERE symbol IN ({_sql_in(symbols)})"
        ).fetchdf()
    finally:
        con.close()


def load_blindspot_rows(paths: DeepDivePaths, symbols: Sequence[str]) -> pd.DataFrame:
    db = paths.child("blindspot_lane", "edge_blindspot_lane.duckdb")
    if not db.exists():
        return pd.DataFrame()
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(
            f"""
            SELECT
                symbol, quiet_mover_rank, quiet_mover_score, blindspot_flag,
                currently_quant_hot_flag, in_shortlist_flag,
                perf_5d, perf_1m, perf_3m,
                blindspot_hist_median_fwd_pct, blindspot_hist_win_rate,
                volume_base_trend_pct, trend_persistence_score
            FROM blindspot_symbol_candidates
            WHERE symbol IN ({_sql_in(symbols)})
            """
        ).fetchdf()
    finally:
        con.close()


def load_safety_rows(paths: DeepDivePaths, symbols: Sequence[str]) -> pd.DataFrame:
    db = paths.child("edge_unified_highlights", "edge_unified_highlights.duckdb")
    if not db.exists():
        return pd.DataFrame()
    con = duckdb.connect(str(db), read_only=True)
    try:
        tables = {t[0] for t in con.execute("SHOW TABLES").fetchall()}
        if "safety_scored_universe" not in tables:
            return pd.DataFrame()
        return con.execute(
            f"""
            SELECT
                symbol, safety_rank, safety_companion_score,
                balance_sheet_safety_score, cash_generation_value_score,
                current_ratio_val, debt_to_equity_val, altman_z_val,
                free_cash_flow_margin_val, earnings_yield_val,
                net_debt_val, net_cash_to_mcap,
                indicator_altman_safe, indicator_altman_distress,
                indicator_fcf_margin_positive, indicator_debt_to_equity_low
            FROM safety_scored_universe
            WHERE symbol IN ({_sql_in(symbols)})
            """
        ).fetchdf()
    finally:
        con.close()


def load_conviction_rows(csv_path: Path | None, symbols: Sequence[str]) -> pd.DataFrame:
    if csv_path is None or not csv_path.exists():
        return pd.DataFrame()
    df = pd.read_csv(csv_path)
    return df[df["symbol"].isin(symbols)].copy()


def compute_risk_profile(row: Mapping[str, Any]) -> NameRiskProfile:
    flags: list[str] = []
    mitigants: list[str] = []
    score = 0.0

    safety = str(row.get("safety_bucket") or "")
    if safety in ("speculative", "aggressive"):
        flags.append(f"safety_bucket={safety}")
        score += 0.22 if safety == "speculative" else 0.14
    elif safety in ("safer", "balanced"):
        mitigants.append(f"safety_bucket={safety}")
        score -= 0.08 if safety == "safer" else 0.04

    binary = _safe_float(row.get("opp_binary_risk_score"))
    if binary is not None:
        if binary >= 0.45:
            flags.append(f"binary_risk={binary:.2f}")
            score += 0.18
        elif binary <= 0.30:
            mitigants.append(f"binary_risk={binary:.2f}")
            score -= 0.05

    downside = _safe_float(row.get("opp_downside_risk_score"))
    if downside is not None and downside >= 0.45:
        flags.append(f"downside_risk={downside:.2f}")
        score += 0.12

    earn_d = _safe_float(row.get("earnings_days_until"))
    if earn_d is not None:
        if earn_d <= 3:
            flags.append(f"earnings_in_{earn_d:.1f}d")
            score += 0.20
        elif earn_d <= 8:
            flags.append(f"earnings_in_{earn_d:.1f}d")
            score += 0.10
        elif earn_d >= 30:
            mitigants.append(f"earnings_far_{earn_d:.0f}d")
            score -= 0.03

    pe = _safe_float(row.get("pe_ttm"))
    if pe is not None and (pe < 0 or pe > 80):
        flags.append(f"stretched_or_neg_PE={pe:.1f}")
        score += 0.08

    debt_eq = _safe_float(row.get("debt_equity"))
    if debt_eq is not None and debt_eq > 2.0:
        flags.append(f"high_debt_equity={debt_eq:.2f}")
        score += 0.10
    elif debt_eq is not None and debt_eq < 0.5:
        mitigants.append(f"low_debt_equity={debt_eq:.2f}")
        score -= 0.03

    altman = _safe_float(row.get("altman_z")) or _safe_float(row.get("safety_detail_altman_z_val"))
    if altman is not None:
        if altman < 1.8:
            flags.append(f"altman_distress={altman:.2f}")
            score += 0.15
        elif altman >= 3.0:
            mitigants.append(f"altman_safe={altman:.2f}")
            score -= 0.05

    from_high = _safe_float(row.get("pct_from_52w_high"))
    if from_high is not None and from_high > -3:
        flags.append(f"near_52w_high={from_high:.1f}%")
        score += 0.08
    elif from_high is not None and from_high < -35:
        flags.append(f"deep_drawdown_from_high={from_high:.1f}%")
        score += 0.06  # opportunity but also impaired tape

    beta = _safe_float(row.get("beta_1y"))
    if beta is not None and beta >= 1.8:
        flags.append(f"high_beta={beta:.2f}")
        score += 0.08

    atrp = _safe_float(row.get("atrp"))
    if atrp is not None and atrp >= 6:
        flags.append(f"high_ATRP={atrp:.1f}")
        score += 0.08

    rsi = _safe_float(row.get("rsi"))
    if rsi is not None and rsi >= 75:
        flags.append(f"RSI_overbought={rsi:.0f}")
        score += 0.06
    elif rsi is not None and rsi <= 30:
        mitigants.append(f"RSI_oversold={rsi:.0f}")

    hv = str(row.get("historical_validation_bucket") or "")
    if hv in ("thin", "weak"):
        flags.append(f"hist_validation={hv}")
        score += 0.08
    elif hv in ("supported", "strong"):
        mitigants.append(f"hist_validation={hv}")
        score -= 0.05

    hist_state = str(row.get("historical_edge_daily_state") or "")
    if hist_state == "EXIT":
        flags.append("hist_edge=EXIT")
        score += 0.06
    elif hist_state in ("ENTER_FRESH", "HOLD"):
        mitigants.append(f"hist_edge={hist_state}")
        score -= 0.04

    sector = str(row.get("sector") or "")
    if sector in ("Electronic Technology", "Producer Manufacturing") and (_safe_float(row.get("chg_1w")) or 0) < -3:
        flags.append("sector_in_unwind")
        score += 0.10

    score = max(0.0, min(1.0, score))
    if score >= 0.65:
        tier = "VERY_HIGH"
    elif score >= 0.45:
        tier = "HIGH"
    elif score >= 0.28:
        tier = "MODERATE"
    else:
        tier = "LOW"
    return NameRiskProfile(risk_tier=tier, risk_score=round(score, 3), flags=flags, mitigants=mitigants)


def _derive_safety_bucket(safety_score: float | None) -> str | None:
    if safety_score is None:
        return None
    if safety_score >= 0.78:
        return "safer"
    if safety_score >= 0.62:
        return "balanced"
    if safety_score >= 0.48:
        return "aggressive"
    return "speculative"


def compute_allocation_score(row: Mapping[str, Any], risk: NameRiskProfile) -> NameAllocationScore:
    """Score motivation to allocate. Works for shortlist and out-of-shortlist names."""
    comps: dict[str, float] = {}
    in_shortlist = row.get("upside_prediction_rank") is not None or row.get("unified_edge_highlight_rank") is not None

    up = _safe_float(row.get("upside_prediction_rank"))
    if up is not None:
        comps["upside_rank"] = max(0.0, 16.0 * (1.0 - min(up, 400) / 400.0))
    else:
        comps["upside_rank"] = 0.0

    fwd = _safe_float(row.get("forward_valuation_upside_pct"))
    if fwd is not None:
        comps["fwd_valuation"] = max(0.0, min(14.0, fwd / 8.0))
    else:
        comps["fwd_valuation"] = 0.0

    setup5 = _safe_float(row.get("median_fwd_5d_in_setup"))
    win5 = _safe_float(row.get("win_rate_5d_in_setup"))
    if setup5 is not None and win5 is not None:
        comps["setup_hist"] = max(0.0, min(10.0, 0.35 * setup5 + 7.0 * win5))
    else:
        comps["setup_hist"] = 0.0

    safety_s = _safe_float(row.get("safety_companion_score")) or 0.0
    comps["safety"] = 14.0 * safety_s

    conv = _safe_float(row.get("conviction_rank"))
    cscore = _safe_float(row.get("conviction_score")) or 0.0
    if conv is not None and conv == conv:  # not NaN
        # Top conviction gets heavy weight — these are often outside edge shortlist.
        comps["conviction"] = max(0.0, 22.0 * (1.0 - min(conv, 60) / 60.0))
        if cscore >= 1.2:
            comps["conviction"] = min(24.0, comps["conviction"] + 3.0)
    else:
        comps["conviction"] = 0.0

    quiet = _safe_float(row.get("quiet_mover_rank"))
    if quiet is not None and quiet <= 40:
        comps["blindspot"] = max(0.0, 12.0 * (1.0 - quiet / 40.0))
    elif row.get("blindspot_flag"):
        comps["blindspot"] = 4.0
    else:
        comps["blindspot"] = 0.0

    lean = str(row.get("directional_lean") or "")
    if lean == "STRONG_UP":
        comps["opp_lean"] = 7.0
    elif lean == "LEAN_UP":
        comps["opp_lean"] = 5.0
    elif lean == "NEUTRAL":
        comps["opp_lean"] = 2.0
    else:
        comps["opp_lean"] = 0.0

    pt = _safe_float(row.get("pt_upside_pct"))
    if pt is not None:
        comps["analyst_pt"] = max(0.0, min(6.0, pt / 15.0))
    else:
        comps["analyst_pt"] = 0.0

    # Fundamental quality from all-fields (helps non-shortlist names).
    fund = 0.0
    pe = _safe_float(row.get("pe_ttm"))
    if pe is not None and 0 < pe <= 25:
        fund += 3.0
    elif pe is not None and 25 < pe <= 40:
        fund += 1.5
    roe = _safe_float(row.get("roe"))
    if roe is not None and roe >= 15:
        fund += 2.0
    elif roe is not None and roe >= 8:
        fund += 1.0
    fcf_m = _safe_float(row.get("fcf_margin"))
    if fcf_m is not None and fcf_m >= 10:
        fund += 2.0
    elif fcf_m is not None and fcf_m >= 3:
        fund += 1.0
    altman = _safe_float(row.get("altman_z"))
    if altman is not None and altman >= 3:
        fund += 2.0
    elif altman is not None and altman >= 1.8:
        fund += 1.0
    rev = _safe_float(row.get("rev_yoy"))
    if rev is not None and rev >= 10:
        fund += 1.5
    comps["fundamentals"] = min(10.0, fund)

    # Non-shortlist names that still clear safety + conviction/blindspot get a bridge bonus.
    if not in_shortlist and (comps["conviction"] >= 10 or comps["blindspot"] >= 6):
        comps["off_shortlist_bridge"] = 6.0
    else:
        comps["off_shortlist_bridge"] = 0.0

    comps["risk_haircut"] = -28.0 * risk.risk_score

    total = sum(comps.values())
    total = max(0.0, min(100.0, total))

    if total >= 68 and risk.risk_tier in ("LOW", "MODERATE"):
        stance = "CORE_ALLOCATE"
    elif total >= 52 and risk.risk_tier != "VERY_HIGH":
        stance = "TACTICAL_ALLOCATE"
    elif total >= 38:
        stance = "WATCH_SIZE_SMALL"
    else:
        stance = "PASS_OR_EVENT_ONLY"

    earn_d = _safe_float(row.get("earnings_days_until"))
    if earn_d is not None and earn_d <= 5 and stance in ("CORE_ALLOCATE", "TACTICAL_ALLOCATE"):
        stance = "EVENT_HALF_SIZE"
    elif earn_d is not None and earn_d <= 8 and stance == "CORE_ALLOCATE":
        stance = "TACTICAL_ALLOCATE"

    return NameAllocationScore(
        allocation_score=round(total, 1),
        components={k: round(v, 2) for k, v in comps.items()},
        stance=stance,
    )


def build_short_thesis(row: Mapping[str, Any], risk: NameRiskProfile) -> str:
    bits = []
    industry = row.get("industry") or row.get("af_industry") or ""
    sector = row.get("sector") or ""
    bits.append(f"{industry or sector} sleeve")

    if row.get("quiet_mover_rank") and _safe_float(row.get("quiet_mover_rank")) <= 20:
        bits.append(f"blindspot quiet #{int(row['quiet_mover_rank'])}")
    if row.get("conviction_rank") and _safe_float(row.get("conviction_rank")) <= 30:
        bits.append(f"move conviction #{int(row['conviction_rank'])}")
    fwd = _safe_float(row.get("forward_valuation_upside_pct"))
    if fwd is not None and fwd >= 20:
        bits.append(f"fwd val +{fwd:.0f}%")
    elif fwd is not None and fwd < 0:
        bits.append(f"fwd val {fwd:.0f}% (rich/extended)")
    up = _safe_float(row.get("upside_prediction_rank"))
    if up is not None and up <= 50:
        bits.append(f"upside pred #{int(up)}")
    lean = row.get("directional_lean")
    if lean in ("LEAN_UP", "STRONG_UP"):
        bits.append(str(lean))
    hist = row.get("historical_edge_daily_state")
    if hist:
        bits.append(f"hist {hist}")
    if risk.flags:
        bits.append("risk: " + "; ".join(risk.flags[:3]))
    return " · ".join(bits)


def merge_name_deep_dive(
    symbols: Sequence[str],
    paths: DeepDivePaths,
    thesis_overrides: Mapping[str, str] | None = None,
    stance_overrides: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Build a detailed per-symbol allocation record list."""
    af = load_all_fields_snapshot(paths.all_fields_db, symbols)
    uni = load_unified_rows(paths, symbols)
    opp = load_opportunity_rows(paths, symbols)
    tp = load_trade_plan_rows(paths, symbols)
    bs = load_blindspot_rows(paths, symbols)
    safety = load_safety_rows(paths, symbols)
    conv = load_conviction_rows(paths.conviction_csv, symbols)

    af_i = af.set_index("symbol") if not af.empty else pd.DataFrame()
    uni_i = uni.set_index("symbol") if not uni.empty else pd.DataFrame()
    opp_i = opp.set_index("symbol") if not opp.empty else pd.DataFrame()
    tp_i = tp.set_index("symbol") if not tp.empty else pd.DataFrame()
    bs_i = bs.set_index("symbol") if not bs.empty else pd.DataFrame()
    safety_i = safety.set_index("symbol") if not safety.empty else pd.DataFrame()
    conv_i = conv.set_index("symbol") if not conv.empty else pd.DataFrame()

    thesis_overrides = dict(thesis_overrides or {})
    stance_overrides = dict(stance_overrides or {})
    rows: list[dict[str, Any]] = []

    for sym in symbols:
        merged: dict[str, Any] = {"symbol": sym}
        for frame, prefix in (
            (af_i, ""),
            (uni_i, ""),
            (opp_i, ""),
            (tp_i, "tp_"),
            (bs_i, "bs_"),
            (safety_i, "saf_"),
            (conv_i, "conv_"),
        ):
            if sym not in frame.index:
                continue
            rec = frame.loc[sym]
            if isinstance(rec, pd.DataFrame):
                rec = rec.iloc[0]
            for k, v in rec.items():
                key = k if not prefix else f"{prefix}{k}"
                # Prefer non-prefixed for core identity fields.
                if k in merged and prefix:
                    continue
                if prefix and k in ("symbol",):
                    continue
                # Flatten conviction key names
                if prefix == "conv_":
                    mapping = {
                        "rank_overall": "conviction_rank",
                        "conviction_score": "conviction_score",
                        "manager_action_signal": "conviction_action",
                        "entry_readiness": "entry_readiness",
                        "earnings_days_until": "earnings_days_until",
                        "breakout_conviction_tier": "breakout_tier",
                        "company": "company_name_conv",
                        "sleeve": "conviction_sleeve",
                    }
                    key = mapping.get(k, key)
                if prefix == "bs_":
                    mapping = {
                        "quiet_mover_rank": "quiet_mover_rank",
                        "quiet_mover_score": "quiet_mover_score",
                        "blindspot_flag": "blindspot_flag",
                        "currently_quant_hot_flag": "currently_quant_hot_flag",
                        "in_shortlist_flag": "in_shortlist_flag",
                        "blindspot_hist_median_fwd_pct": "blindspot_hist_median_fwd_pct",
                        "blindspot_hist_win_rate": "blindspot_hist_win_rate",
                    }
                    key = mapping.get(k, key)
                if prefix == "tp_":
                    mapping = {
                        "entry_state": "trade_plan_state",
                        "trade_plan_rank": "trade_plan_rank",
                        "matched_entry_variants": "matched_entry_variants",
                    }
                    key = mapping.get(k, key)
                if prefix == "saf_" and k == "safety_companion_score" and "safety_companion_score" in merged:
                    continue
                if hasattr(v, "item"):
                    try:
                        v = v.item()
                    except Exception:
                        v = str(v)
                if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    v = None
                merged[key] = v

        # Identity fallbacks
        if not merged.get("company_name"):
            merged["company_name"] = merged.get("name") or merged.get("company_name_conv") or sym

        # Fill safety from full scored universe when not on unified shortlist.
        if merged.get("safety_companion_score") is None and merged.get("saf_safety_companion_score") is not None:
            merged["safety_companion_score"] = merged.get("saf_safety_companion_score")
        if merged.get("balance_sheet_safety_score") is None and merged.get("saf_balance_sheet_safety_score") is not None:
            merged["balance_sheet_safety_score"] = merged.get("saf_balance_sheet_safety_score")
        if merged.get("safety_rank_global") is None and merged.get("saf_safety_rank") is not None:
            merged["safety_rank_global"] = merged.get("saf_safety_rank")
        if merged.get("altman_z") is None and merged.get("saf_altman_z_val") is not None:
            merged["altman_z"] = merged.get("saf_altman_z_val")
        if merged.get("debt_equity") is None and merged.get("saf_debt_to_equity_val") is not None:
            merged["debt_equity"] = merged.get("saf_debt_to_equity_val")
        if merged.get("current_ratio") is None and merged.get("saf_current_ratio_val") is not None:
            merged["current_ratio"] = merged.get("saf_current_ratio_val")
        if merged.get("fcf_margin") is None and merged.get("saf_free_cash_flow_margin_val") is not None:
            merged["fcf_margin"] = merged.get("saf_free_cash_flow_margin_val")
        if not merged.get("safety_bucket"):
            merged["safety_bucket"] = _derive_safety_bucket(_safe_float(merged.get("safety_companion_score")))

        close = _safe_float(merged.get("close"))
        high = _safe_float(merged.get("high_52w"))
        merged["pct_from_52w_high"] = _pct_from_high(close, high)
        if close and _safe_float(merged.get("low_52w")):
            lo = _safe_float(merged.get("low_52w"))
            merged["pct_from_52w_low"] = 100.0 * (close / lo - 1.0) if lo else None

        # Normalize NaN conviction ranks
        cr = _safe_float(merged.get("conviction_rank"))
        if cr is None:
            merged["conviction_rank"] = None

        risk = compute_risk_profile(merged)
        alloc = compute_allocation_score(merged, risk)
        thesis = thesis_overrides.get(sym) or build_short_thesis(merged, risk)
        stance = stance_overrides.get(sym) or alloc.stance

        mcap = _safe_float(merged.get("market_cap"))
        merged_out = {
            **{k: v for k, v in merged.items() if not str(k).startswith("Unnamed")},
            "thesis": thesis,
            "stance": stance,
            "allocation_score": alloc.allocation_score,
            "allocation_components": alloc.components,
            "risk_tier": risk.risk_tier,
            "risk_score": risk.risk_score,
            "risk_flags": risk.flags,
            "risk_mitigants": risk.mitigants,
            "market_cap_bn": round(mcap / 1e9, 2) if mcap else None,
            "pct_from_52w_high": round(merged["pct_from_52w_high"], 1) if merged.get("pct_from_52w_high") is not None else None,
        }
        rows.append(merged_out)

    rows.sort(key=lambda r: (-r["allocation_score"], r.get("risk_score", 1)))
    for i, r in enumerate(rows, start=1):
        r["allocation_rank"] = i
    return rows


def rows_to_jsonable(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        clean = {}
        for k, v in r.items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                clean[k] = None
            elif hasattr(v, "item"):
                try:
                    clean[k] = v.item()
                except Exception:
                    clean[k] = str(v)
            elif isinstance(v, (pd.Timestamp,)):
                clean[k] = str(v)
            else:
                clean[k] = v
        out.append(clean)
    return out


def write_deep_dive_json(rows: Sequence[dict[str, Any]], path: Path, meta: Mapping[str, Any] | None = None) -> Path:
    payload = {"meta": dict(meta or {}), "names": rows_to_jsonable(rows)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path
