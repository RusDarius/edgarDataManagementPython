from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean, median
from typing import Any

from data_loaders.api_tradingview_client import ApiTradingViewClient
from generic_utils.log_to_files_util import log_to_file

LOG_DIR = Path(
    r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis"
)

SAFETY_CORE_COLUMNS = [
    "ticker-view",
    "close",
    "type",
    "typespecs",
    "exchange",
    "country",
    "sector",
    "industry",
    "market",
    "market_cap_basic",
    "fundamental_currency_code",
    "current_ratio",
    "current_ratio_current",
    "current_ratio_fq",
    "current_ratio_fy",
    "quick_ratio",
    "cash_ratio",
    "cash_n_equivalents_fq",
    "cash_n_short_term_invest_fq",
    "total_current_assets",
    "total_liabilities_fq",
    "total_liabilities_fy",
    "total_debt",
    "net_debt",
    "debt_to_equity",
    "debt_to_asset_fq",
    "debt_to_asset_fy",
    "debt_to_assets",
    "debt_to_revenue_ttm",
    "altman_z_score_ttm",
    "altman_z_score_fy",
    "gross_margin",
    "gross_profit_margin_fy",
    "operating_margin",
    "oper_income_margin_fy",
    "after_tax_margin",
    "return_on_assets",
    "return_on_equity",
    "return_on_invested_capital",
    "cash_f_operating_activities_ttm",
    "cash_f_investing_activities_ttm",
    "cash_f_financing_activities_ttm",
    "capital_expenditures_ttm",
    "free_cash_flow_margin_ttm",
    "free_cash_flow_yoy_growth_ttm",
    "cash_dividend_coverage_ratio_ttm",
    "total_revenue_yoy_growth_ttm",
    "net_income_yoy_growth_ttm",
    "asset_turnover_current",
    "asset_turnover_fy",
    "short_term_debt_fy",
    "cash_n_equivalents_fy",
]


@dataclass(frozen=True)
class MetricRule:
    field: str
    label: str
    high_is_good: bool
    category: str


@dataclass(frozen=True)
class IndividualMetricRule:
    field: str
    label: str
    category: str
    high_is_good: bool
    good_threshold: float
    neutral_threshold: float


CORE_METRIC_RULES = [
    MetricRule("current_ratio", "Current Ratio", True, "Liquidity"),
    MetricRule("current_ratio_current", "Current Ratio Current", True, "Liquidity"),
    MetricRule("current_ratio_fq", "Current Ratio FQ", True, "Liquidity"),
    MetricRule("current_ratio_fy", "Current Ratio FY", True, "Liquidity"),
    MetricRule("quick_ratio", "Quick Ratio", True, "Liquidity"),
    MetricRule("cash_ratio", "Cash Ratio", True, "Liquidity"),
    MetricRule(
        "cash_n_equivalents_fq",
        "Cash and Equivalents FQ",
        True,
        "Liquidity",
    ),
    MetricRule(
        "cash_n_short_term_invest_fq",
        "Cash and ST Investments FQ",
        True,
        "Liquidity",
    ),
    MetricRule(
        "cash_n_equivalents_fy",
        "Cash and Equivalents FY",
        True,
        "Liquidity",
    ),
    MetricRule("total_current_assets", "Total Current Assets", True, "Liquidity"),
    MetricRule("total_liabilities_fq", "Total Liabilities FQ", False, "Solvency"),
    MetricRule("total_liabilities_fy", "Total Liabilities FY", False, "Solvency"),
    MetricRule("total_debt", "Total Debt", False, "Solvency"),
    MetricRule("net_debt", "Net Debt", False, "Solvency"),
    MetricRule("debt_to_equity", "Debt/Equity", False, "Solvency"),
    MetricRule("debt_to_asset_fq", "Debt/Asset FQ", False, "Solvency"),
    MetricRule("debt_to_asset_fy", "Debt/Asset FY", False, "Solvency"),
    MetricRule("debt_to_assets", "Debt/Assets", False, "Solvency"),
    MetricRule("short_term_debt_fy", "Short-Term Debt FY", False, "Solvency"),
    MetricRule("debt_to_revenue_ttm", "Debt/Revenue TTM", False, "Solvency"),
    MetricRule("altman_z_score_ttm", "Altman Z TTM", True, "Solvency"),
    MetricRule("altman_z_score_fy", "Altman Z FY", True, "Solvency"),
    MetricRule("gross_margin", "Gross Margin", True, "Profitability"),
    MetricRule(
        "gross_profit_margin_fy", "Gross Profit Margin FY", True, "Profitability"
    ),
    MetricRule("operating_margin", "Operating Margin", True, "Profitability"),
    MetricRule(
        "oper_income_margin_fy",
        "Operating Income Margin FY",
        True,
        "Profitability",
    ),
    MetricRule("after_tax_margin", "After-tax Margin", True, "Profitability"),
    MetricRule("return_on_assets", "ROA", True, "Profitability"),
    MetricRule("return_on_equity", "ROE", True, "Profitability"),
    MetricRule("return_on_invested_capital", "ROIC", True, "Profitability"),
    MetricRule(
        "cash_f_operating_activities_ttm",
        "Operating Cash Flow TTM",
        True,
        "Cash Flow",
    ),
    MetricRule(
        "cash_f_investing_activities_ttm",
        "Investing Cash Flow TTM",
        True,
        "Cash Flow",
    ),
    MetricRule(
        "cash_f_financing_activities_ttm",
        "Financing Cash Flow TTM",
        True,
        "Cash Flow",
    ),
    MetricRule(
        "capital_expenditures_ttm", "Capital Expenditures TTM", True, "Cash Flow"
    ),
    MetricRule(
        "free_cash_flow_margin_ttm",
        "Free Cash Flow Margin TTM",
        True,
        "Cash Flow",
    ),
    MetricRule(
        "free_cash_flow_yoy_growth_ttm",
        "Free Cash Flow YoY Growth",
        True,
        "Cash Flow",
    ),
    MetricRule(
        "cash_dividend_coverage_ratio_ttm",
        "Cash Dividend Coverage",
        True,
        "Cash Flow",
    ),
    MetricRule(
        "total_revenue_yoy_growth_ttm",
        "Revenue YoY Growth",
        True,
        "Trend Support",
    ),
    MetricRule(
        "net_income_yoy_growth_ttm",
        "Net Income YoY Growth",
        True,
        "Trend Support",
    ),
    MetricRule(
        "asset_turnover_current",
        "Asset Turnover Current",
        True,
        "Trend Support",
    ),
    MetricRule("asset_turnover_fy", "Asset Turnover FY", True, "Trend Support"),
    MetricRule("net_debt_to_market_cap", "Net Debt / Market Cap", False, "Derived"),
    MetricRule(
        "cash_coverage_liabilities",
        "Cash & ST Invest / Liabilities",
        True,
        "Derived",
    ),
    MetricRule(
        "liability_load_vs_scale",
        "Liabilities / Market Cap",
        False,
        "Derived",
    ),
    MetricRule("capex_burden", "Capex Burden", False, "Derived"),
    MetricRule(
        "operating_cash_backing_debt",
        "Operating Cash / Total Debt",
        True,
        "Derived",
    ),
    MetricRule(
        "cash_n_equivalents_fy_to_short_term_debt_fy",
        "Cash Eq FY / Short-Term Debt FY",
        True,
        "Derived",
    ),
]

INDIVIDUAL_METRIC_RULES = [
    IndividualMetricRule(
        "current_ratio_fq", "Current Ratio FQ", "Liquidity", True, 1.5, 1.0
    ),
    IndividualMetricRule("quick_ratio", "Quick Ratio", "Liquidity", True, 1.0, 0.7),
    IndividualMetricRule("cash_ratio", "Cash Ratio", "Liquidity", True, 0.2, 0.1),
    IndividualMetricRule("debt_to_equity", "Debt/Equity", "Solvency", False, 1.0, 2.0),
    IndividualMetricRule("debt_to_assets", "Debt/Assets", "Solvency", False, 0.5, 0.7),
    IndividualMetricRule(
        "debt_to_revenue_ttm",
        "Debt/Revenue TTM",
        "Solvency",
        False,
        1.0,
        2.0,
    ),
    IndividualMetricRule(
        "altman_z_score_ttm", "Altman Z TTM", "Solvency", True, 3.0, 1.8
    ),
    IndividualMetricRule(
        "gross_margin", "Gross Margin", "Profitability", True, 30.0, 15.0
    ),
    IndividualMetricRule(
        "operating_margin",
        "Operating Margin",
        "Profitability",
        True,
        15.0,
        5.0,
    ),
    IndividualMetricRule(
        "after_tax_margin", "After-tax Margin", "Profitability", True, 10.0, 3.0
    ),
    IndividualMetricRule("return_on_assets", "ROA", "Profitability", True, 5.0, 2.0),
    IndividualMetricRule("return_on_equity", "ROE", "Profitability", True, 15.0, 8.0),
    IndividualMetricRule(
        "return_on_invested_capital", "ROIC", "Profitability", True, 10.0, 6.0
    ),
    IndividualMetricRule(
        "cash_f_operating_activities_ttm",
        "Operating Cash Flow TTM",
        "Cash Flow",
        True,
        0.0,
        -1.0,
    ),
    IndividualMetricRule(
        "free_cash_flow_margin_ttm",
        "Free Cash Flow Margin TTM",
        "Cash Flow",
        True,
        10.0,
        0.0,
    ),
    IndividualMetricRule(
        "free_cash_flow_yoy_growth_ttm",
        "Free Cash Flow YoY Growth",
        "Cash Flow",
        True,
        5.0,
        0.0,
    ),
    IndividualMetricRule(
        "cash_dividend_coverage_ratio_ttm",
        "Cash Dividend Coverage",
        "Cash Flow",
        True,
        2.0,
        1.0,
    ),
    IndividualMetricRule(
        "total_revenue_yoy_growth_ttm",
        "Revenue YoY Growth",
        "Trend Support",
        True,
        5.0,
        0.0,
    ),
    IndividualMetricRule(
        "net_income_yoy_growth_ttm",
        "Net Income YoY Growth",
        "Trend Support",
        True,
        5.0,
        0.0,
    ),
    IndividualMetricRule(
        "asset_turnover_fy",
        "Asset Turnover FY",
        "Trend Support",
        True,
        0.7,
        0.4,
    ),
    IndividualMetricRule(
        "net_debt_to_market_cap",
        "Net Debt / Market Cap",
        "Derived",
        False,
        0.3,
        0.6,
    ),
    IndividualMetricRule(
        "cash_coverage_liabilities",
        "Cash & ST Invest / Liabilities",
        "Derived",
        True,
        0.5,
        0.2,
    ),
    IndividualMetricRule(
        "cash_n_equivalents_fy_to_short_term_debt_fy",
        "Cash Eq FY / Short-Term Debt FY",
        "Derived",
        True,
        1.5,
        1.0,
    ),
    IndividualMetricRule(
        "liability_load_vs_scale",
        "Liabilities / Market Cap",
        "Derived",
        False,
        1.0,
        2.0,
    ),
    IndividualMetricRule("capex_burden", "Capex Burden", "Derived", False, 0.6, 1.0),
    IndividualMetricRule(
        "operating_cash_backing_debt",
        "Operating Cash / Total Debt",
        "Derived",
        True,
        0.25,
        0.1,
    ),
]

HORIZON_METRIC_FIELDS = {
    "1Y": [
        "current_ratio_fq",
        "quick_ratio",
        "cash_ratio",
        "cash_n_equivalents_fy_to_short_term_debt_fy",
        "debt_to_equity",
        "debt_to_revenue_ttm",
        "altman_z_score_ttm",
        "operating_margin",
        "after_tax_margin",
        "cash_f_operating_activities_ttm",
        "free_cash_flow_margin_ttm",
        "total_revenue_yoy_growth_ttm",
        "net_income_yoy_growth_ttm",
        "net_debt_to_market_cap",
    ],
    "3Y": [
        "current_ratio_fq",
        "quick_ratio",
        "debt_to_equity",
        "debt_to_assets",
        "altman_z_score_ttm",
        "gross_margin",
        "operating_margin",
        "after_tax_margin",
        "return_on_assets",
        "return_on_equity",
        "return_on_invested_capital",
        "free_cash_flow_margin_ttm",
        "free_cash_flow_yoy_growth_ttm",
        "cash_dividend_coverage_ratio_ttm",
        "asset_turnover_fy",
        "cash_coverage_liabilities",
        "cash_n_equivalents_fy_to_short_term_debt_fy",
        "liability_load_vs_scale",
        "operating_cash_backing_debt",
    ],
    "5Y": [
        "debt_to_equity",
        "debt_to_assets",
        "debt_to_revenue_ttm",
        "altman_z_score_ttm",
        "gross_margin",
        "operating_margin",
        "after_tax_margin",
        "return_on_assets",
        "return_on_equity",
        "return_on_invested_capital",
        "cash_f_operating_activities_ttm",
        "free_cash_flow_margin_ttm",
        "cash_dividend_coverage_ratio_ttm",
        "asset_turnover_fy",
        "net_debt_to_market_cap",
        "cash_coverage_liabilities",
        "cash_n_equivalents_fy_to_short_term_debt_fy",
        "liability_load_vs_scale",
        "capex_burden",
        "operating_cash_backing_debt",
    ],
}

HORIZON_SCORE_BANDS = {
    "1Y": {"good": 0.30, "bad": -0.30},
    "3Y": {"good": 0.25, "bad": -0.25},
    "5Y": {"good": 0.20, "bad": -0.20},
}

GOOD_PCTL = 0.67
BAD_PCTL = 0.33

ALWAYS_PROFILED_FIELDS = [
    "cash_n_equivalents_fy_to_short_term_debt_fy",
]


def _discover_numeric_fields(rows: list[dict[str, Any]]) -> list[str]:
    fields: set[str] = set()
    for row in rows:
        for field, value in row.items():
            if _coerce_numeric(value) is not None:
                fields.add(field)
    return sorted(fields)


def _median_absolute_deviation(values: list[float], center: float) -> float:
    absolute_deviations = [abs(value - center) for value in values]
    return median(absolute_deviations)


def _build_numeric_field_profiles(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, float | int | None]]:
    profiles: dict[str, dict[str, float | int | None]] = {}
    fields_to_profile = sorted(
        set(_discover_numeric_fields(rows)).union(ALWAYS_PROFILED_FIELDS)
    )

    for field in fields_to_profile:
        values = [_coerce_numeric(row.get(field)) for row in rows]
        numeric_values = [value for value in values if value is not None]
        if not numeric_values:
            profiles[field] = {
                "count": 0,
                "mean": None,
                "median": None,
                "mad": None,
            }
            continue

        median_value = median(numeric_values)
        profiles[field] = {
            "count": len(numeric_values),
            "mean": fmean(numeric_values),
            "median": median_value,
            "mad": _median_absolute_deviation(numeric_values, median_value),
        }
    return profiles


def _coerce_numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _slugify(value: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "_" for char in str(value))
    return "_".join(part for part in slug.split("_") if part)


def _build_log_file_name(
    industries: list[str] | None,
    min_market_cap_usd: float | None,
    max_market_cap_usd: float | None,
) -> Path:
    industry_segment = "all_industries"
    if industries:
        industry_segment = "__".join(_slugify(industry) for industry in industries)

    min_segment = (
        f"min_{int(min_market_cap_usd)}"
        if min_market_cap_usd is not None
        else "min_none"
    )
    max_segment = (
        f"max_{int(max_market_cap_usd)}"
        if max_market_cap_usd is not None
        else "max_none"
    )

    return (
        LOG_DIR
        / f"tradingview_safety_core__{industry_segment}__{min_segment}__{max_segment}.log"
    )


def _reset_log_file(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("", encoding="utf-8")


def _format_number(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.{decimals}f}"


def _format_percent(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.2f}%"


def _format_market_cap(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value / 1e9:.2f}B"


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _map_raw_scan_row(raw_row: dict[str, Any]) -> dict[str, Any]:
    values = raw_row.get("d", [])
    mapped_values = {
        column: values[index] if index < len(values) else None
        for index, column in enumerate(SAFETY_CORE_COLUMNS)
    }
    return {
        "symbol": raw_row.get("s"),
        **mapped_values,
    }


def _normalize_scan_data(scan_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_rows: list[dict[str, Any]] = []
    for row in scan_data:
        if "symbol" in row:
            normalized_rows.append(dict(row))
            continue

        if "s" in row and "d" in row:
            normalized_rows.append(_map_raw_scan_row(row))

    return normalized_rows


def _get_symbol_name(row: dict[str, Any]) -> str:
    ticker_view = row.get("ticker-view")
    if isinstance(ticker_view, dict):
        name = ticker_view.get("name")
        if name:
            return str(name)
    return str(row.get("symbol") or "N/A")


def _get_company_description(row: dict[str, Any]) -> str:
    ticker_view = row.get("ticker-view")
    if isinstance(ticker_view, dict):
        description = ticker_view.get("description")
        if description:
            return str(description)
    return ""


def _get_industry(row: dict[str, Any]) -> str:
    industry = row.get("industry")
    if not industry:
        return "Unknown"
    return str(industry)


def _attach_derived_metrics(row: dict[str, Any]) -> None:
    market_cap = _coerce_numeric(row.get("market_cap_basic"))
    net_debt = _coerce_numeric(row.get("net_debt"))
    total_liabilities = _coerce_numeric(row.get("total_liabilities_fq"))
    cash_and_st = _coerce_numeric(row.get("cash_n_short_term_invest_fq"))
    capex = _coerce_numeric(row.get("capital_expenditures_ttm"))
    cfo = _coerce_numeric(row.get("cash_f_operating_activities_ttm"))
    total_debt = _coerce_numeric(row.get("total_debt"))
    short_term_debt_fy = _coerce_numeric(row.get("short_term_debt_fy"))
    cash_n_equivalents_fy = _coerce_numeric(row.get("cash_n_equivalents_fy"))

    row["net_debt_to_market_cap"] = _safe_div(net_debt, market_cap)
    row["cash_coverage_liabilities"] = _safe_div(cash_and_st, total_liabilities)
    row["liability_load_vs_scale"] = _safe_div(total_liabilities, market_cap)
    row["capex_burden"] = _safe_div(abs(capex) if capex is not None else None, cfo)
    row["operating_cash_backing_debt"] = _safe_div(cfo, total_debt)
    row["cash_n_equivalents_fy_to_short_term_debt_fy"] = _safe_div(
        cash_n_equivalents_fy,
        short_term_debt_fy,
    )


def _evaluate_threshold_metric(
    value: float | None,
    high_is_good: bool,
    good_threshold: float,
    neutral_threshold: float,
) -> tuple[str, int]:
    if value is None:
        return "N/A", 0

    if high_is_good:
        if value >= good_threshold:
            return "GOOD", 1
        if value >= neutral_threshold:
            return "NEUTRAL", 0
        return "BAD", -1

    if value <= good_threshold:
        return "GOOD", 1
    if value <= neutral_threshold:
        return "NEUTRAL", 0
    return "BAD", -1


def _find_individual_rule(field_name: str) -> IndividualMetricRule | None:
    for rule in INDIVIDUAL_METRIC_RULES:
        if rule.field == field_name:
            return rule
    return None


def _evaluate_horizon(
    row: dict[str, Any],
    horizon: str,
) -> dict[str, Any]:
    fields = HORIZON_METRIC_FIELDS[horizon]
    metrics = []
    good = 0
    bad = 0
    neutral = 0
    considered = 0

    for field_name in fields:
        rule = _find_individual_rule(field_name)
        if rule is None:
            continue

        value = _coerce_numeric(row.get(rule.field))
        flag, score = _evaluate_threshold_metric(
            value=value,
            high_is_good=rule.high_is_good,
            good_threshold=rule.good_threshold,
            neutral_threshold=rule.neutral_threshold,
        )

        if flag == "N/A":
            metrics.append(
                {
                    "field": rule.field,
                    "label": rule.label,
                    "category": rule.category,
                    "value": None,
                    "flag": "N/A",
                    "direction": (
                        "higher-is-better" if rule.high_is_good else "lower-is-better"
                    ),
                }
            )
            continue

        considered += 1
        if score > 0:
            good += 1
        elif score < 0:
            bad += 1
        else:
            neutral += 1

        metrics.append(
            {
                "field": rule.field,
                "label": rule.label,
                "category": rule.category,
                "value": value,
                "flag": flag,
                "direction": (
                    "higher-is-better" if rule.high_is_good else "lower-is-better"
                ),
                "good_threshold": rule.good_threshold,
                "neutral_threshold": rule.neutral_threshold,
            }
        )

    score_ratio = 0.0
    if considered > 0:
        score_ratio = (good - bad) / considered

    bands = HORIZON_SCORE_BANDS[horizon]
    if score_ratio >= bands["good"]:
        horizon_flag = "GOOD"
    elif score_ratio <= bands["bad"]:
        horizon_flag = "BAD"
    else:
        horizon_flag = "MIXED"

    return {
        "horizon": horizon,
        "flag": horizon_flag,
        "score": score_ratio,
        "good": good,
        "bad": bad,
        "neutral": neutral,
        "considered": considered,
        "metrics": metrics,
    }


def assess_individual_company_safety(entry: dict[str, Any]) -> dict[str, Any]:
    """
    Evaluate one company row for financial strength across 1Y, 3Y, and 5Y horizons.

    The input should be one mapped API entry ("symbol" + named fields) or one
    raw TradingView row ({"s": ..., "d": [...]}).
    """
    normalized_rows = _normalize_scan_data([entry])
    if not normalized_rows:
        return {
            "error": "Invalid entry format. Expected mapped row or raw TradingView row.",
            "input": entry,
        }

    row = normalized_rows[0]
    _attach_derived_metrics(row)

    horizon_results = {
        horizon: _evaluate_horizon(row, horizon) for horizon in ("1Y", "3Y", "5Y")
    }

    hard_flags = []
    altman = _coerce_numeric(row.get("altman_z_score_ttm"))
    current_ratio = _coerce_numeric(row.get("current_ratio_fq"))
    debt_to_equity = _coerce_numeric(row.get("debt_to_equity"))
    cfo = _coerce_numeric(row.get("cash_f_operating_activities_ttm"))

    if altman is not None and altman < 1.8:
        hard_flags.append("Distress risk: Altman Z < 1.8")
    if current_ratio is not None and current_ratio < 1.0:
        hard_flags.append("Tight short-term liquidity: current ratio < 1")
    if debt_to_equity is not None and debt_to_equity > 2.0:
        hard_flags.append("High leverage: debt/equity > 2")
    if cfo is not None and cfo < 0:
        hard_flags.append("Weak cash generation: operating cash flow < 0")

    overall_score = (
        0.35 * horizon_results["1Y"]["score"]
        + 0.35 * horizon_results["3Y"]["score"]
        + 0.30 * horizon_results["5Y"]["score"]
    )
    overall_flag = _overall_flag(overall_score)

    return {
        "symbol": row.get("symbol", "N/A"),
        "ticker": _get_symbol_name(row),
        "company": _get_company_description(row),
        "industry": _get_industry(row),
        "market_cap": _coerce_numeric(row.get("market_cap_basic")),
        "overall": {
            "flag": overall_flag,
            "score": overall_score,
        },
        "horizons": horizon_results,
        "hard_risk_flags": hard_flags,
    }


def log_individual_company_safety_assessment(
    entry: dict[str, Any],
    log_file: Path | None = None,
) -> tuple[dict[str, Any], Path]:
    """Run individual safety assessment and write a readable log."""
    assessment = assess_individual_company_safety(entry)

    if log_file is None:
        ticker = _slugify(
            str(assessment.get("ticker") or assessment.get("symbol") or "unknown")
        )
        log_file = LOG_DIR / f"tradingview_individual_safety__{ticker}.log"

    _reset_log_file(log_file)

    if "error" in assessment:
        log_to_file(log_file, "Individual Safety Assessment")
        log_to_file(log_file, "=" * 120)
        log_to_file(log_file, str(assessment["error"]))
        return assessment, log_file

    log_to_file(log_file, "Individual Safety Assessment")
    log_to_file(log_file, "=" * 120)
    log_to_file(
        log_file,
        (
            f"Symbol={assessment['symbol']} | ticker={assessment['ticker']} | "
            f"company={assessment['company'] or 'N/A'} | industry={assessment['industry']} | "
            f"market_cap={_format_market_cap(assessment['market_cap'])}"
        ),
    )
    log_to_file(
        log_file,
        (
            f"Overall financial strength: {assessment['overall']['flag']} "
            f"(score={assessment['overall']['score']:+.2f})"
        ),
    )
    log_to_file(log_file, "")

    for horizon in ("1Y", "3Y", "5Y"):
        horizon_result = assessment["horizons"][horizon]
        log_to_file(log_file, f"Horizon {horizon}")
        log_to_file(log_file, "-" * 120)
        log_to_file(
            log_file,
            (
                f"FLAG={horizon_result['flag']} | score={horizon_result['score']:+.2f} | "
                f"GOOD={horizon_result['good']} BAD={horizon_result['bad']} "
                f"NEUTRAL={horizon_result['neutral']} CONSIDERED={horizon_result['considered']}"
            ),
        )
        for metric_result in horizon_result["metrics"]:
            value_text = _format_metric_value(
                metric_result["field"], metric_result["value"]
            )
            log_to_file(
                log_file,
                (
                    f"  - [{metric_result['category']}] {metric_result['label']:<34} "
                    f"value={value_text:<14} flag={metric_result['flag']:<7} "
                    f"rule={metric_result['direction']}"
                ),
            )
        log_to_file(log_file, "")

    if assessment["hard_risk_flags"]:
        log_to_file(log_file, "Hard Risk Flags")
        log_to_file(log_file, "-" * 120)
        for hard_flag in assessment["hard_risk_flags"]:
            log_to_file(log_file, f"- {hard_flag}")
    else:
        log_to_file(log_file, "Hard Risk Flags: none triggered")

    return assessment, log_file


def _sorted_numeric_values(rows: list[dict[str, Any]], field: str) -> list[float]:
    values = [_coerce_numeric(row.get(field)) for row in rows]
    numeric_values = [value for value in values if value is not None]
    numeric_values.sort()
    return numeric_values


def _percentile_rank(sorted_values: list[float], value: float | None) -> float | None:
    if value is None or not sorted_values:
        return None
    index = bisect_right(sorted_values, value)
    return index / len(sorted_values)


def _evaluate_metric_status(
    value: float | None,
    sorted_industry: list[float],
    high_is_good: bool,
) -> tuple[str, int, float | None]:
    industry_pct = _percentile_rank(sorted_industry, value)

    # Require enough industry depth for meaningful ranks.
    if len(sorted_industry) < 3 or industry_pct is None:
        return "N/A", 0, industry_pct

    if high_is_good:
        if industry_pct >= GOOD_PCTL:
            return "GOOD", 1, industry_pct
        if industry_pct <= BAD_PCTL:
            return "BAD", -1, industry_pct
    else:
        if industry_pct <= BAD_PCTL:
            return "GOOD", 1, industry_pct
        if industry_pct >= GOOD_PCTL:
            return "BAD", -1, industry_pct

    return "NEUTRAL", 0, industry_pct


def _format_metric_value(field: str, value: float | None) -> str:
    if value is None:
        return "N/A"

    if "margin" in field or "growth" in field:
        return _format_percent(value)
    return _format_number(value, 3)


def _format_raw_numeric(value: float | None) -> str:
    if value is None:
        return "N/A"
    return _format_number(value, 6)


def _log_all_field_universe_stats(
    log_file: Path,
    numeric_field_profiles: dict[str, dict[str, float | int | None]],
) -> None:
    log_to_file(log_file, "All-Field Universe Medians and Means")
    log_to_file(log_file, "-" * 180)
    log_to_file(
        log_file,
        f"{'Field':<42} {'Count':>8} {'Median':>18} {'Mean':>18} {'MAD':>18}",
    )
    log_to_file(log_file, "-" * 180)

    for field, profile in sorted(
        numeric_field_profiles.items(),
        key=lambda item: (-(int(item[1]["count"])), item[0]),
    ):
        count = int(profile["count"])
        median_value = (
            float(profile["median"]) if profile["median"] is not None else None
        )
        mean_value = float(profile["mean"]) if profile["mean"] is not None else None
        mad_value = float(profile["mad"]) if profile["mad"] is not None else None
        log_to_file(
            log_file,
            (
                f"{field[:42]:<42} {count:>8} "
                f"{_format_raw_numeric(median_value):>18} "
                f"{_format_raw_numeric(mean_value):>18} "
                f"{_format_raw_numeric(mad_value):>18}"
            ),
        )
    log_to_file(log_file, "")


def _log_per_row_all_field_deviations(
    log_file: Path,
    row: dict[str, Any],
    numeric_field_profiles: dict[str, dict[str, float | int]],
) -> None:
    log_to_file(log_file, "      All-field values and deviations")
    log_to_file(log_file, "      " + "-" * 168)
    log_to_file(
        log_file,
        (
            f"      {'Field':<34} {'Value':>16} {'Median':>16} {'Mean':>16} "
            f"{'DevMedian':>16} {'DevMean':>16}"
        ),
    )
    log_to_file(log_file, "      " + "-" * 168)

    for field in sorted(numeric_field_profiles.keys()):
        value = _coerce_numeric(row.get(field))
        if value is None:
            continue

        profile = numeric_field_profiles[field]
        median_value = float(profile["median"])
        mean_value = float(profile["mean"])
        dev_median = value - median_value
        dev_mean = value - mean_value

        log_to_file(
            log_file,
            (
                f"      {field[:34]:<34} {_format_raw_numeric(value):>16} "
                f"{_format_raw_numeric(median_value):>16} {_format_raw_numeric(mean_value):>16} "
                f"{_format_raw_numeric(dev_median):>16} {_format_raw_numeric(dev_mean):>16}"
            ),
        )
    log_to_file(log_file, "")


def _build_row_standouts(
    row: dict[str, Any],
    numeric_field_profiles: dict[str, dict[str, float | int]],
    zscore_threshold: float,
) -> list[tuple[str, float, float, float, float]]:
    standouts: list[tuple[str, float, float, float, float]] = []

    for field, profile in numeric_field_profiles.items():
        value = _coerce_numeric(row.get(field))
        if value is None:
            continue

        count = int(profile["count"])
        if count < 3:
            continue

        median_value = float(profile["median"])
        mad_value = float(profile["mad"])
        if mad_value <= 0:
            continue

        robust_sigma = mad_value * 1.4826
        robust_z = abs(value - median_value) / robust_sigma
        if robust_z >= zscore_threshold:
            standouts.append((field, robust_z, value, median_value, mad_value))

    standouts.sort(key=lambda item: item[1], reverse=True)
    return standouts


def _log_row_standouts(
    log_file: Path,
    row: dict[str, Any],
    numeric_field_profiles: dict[str, dict[str, float | int]],
    zscore_threshold: float,
    max_items: int,
) -> None:
    standouts = _build_row_standouts(
        row=row,
        numeric_field_profiles=numeric_field_profiles,
        zscore_threshold=zscore_threshold,
    )

    if not standouts:
        log_to_file(log_file, "      Standout abnormalities: none")
        return

    log_to_file(
        log_file,
        (
            f"      Standout abnormalities (robust z >= {zscore_threshold:.1f}; "
            f"showing up to {max_items}):"
        ),
    )
    for field, robust_z, value, median_value, mad_value in standouts[:max_items]:
        log_to_file(
            log_file,
            (
                f"        - {field}: value={_format_raw_numeric(value)} | "
                f"median={_format_raw_numeric(median_value)} | "
                f"MAD={_format_raw_numeric(mad_value)} | "
                f"robust_z={robust_z:.2f}"
            ),
        )


def _build_pointer(
    metric: MetricRule,
    value: float | None,
    status: str,
    industry_pct: float | None,
) -> str:
    industry_text = "N/A" if industry_pct is None else f"{industry_pct * 100:.0f}"

    direction = "higher-is-better" if metric.high_is_good else "lower-is-better"
    return (
        f"{status} | {metric.label}={_format_metric_value(metric.field, value)} | "
        f"industry pctl={industry_text} | {direction}"
    )


def _build_industry_groups(
    rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        industry = _get_industry(row)
        grouped.setdefault(industry, []).append(row)
    return grouped


def _log_header(
    log_file: Path,
    rows_count: int,
    industries: list[str] | None,
    min_market_cap_usd: float | None,
    max_market_cap_usd: float | None,
) -> None:
    log_to_file(log_file, "TradingView Safety Core Scan Analysis")
    log_to_file(log_file, "=" * 180)
    log_to_file(
        log_file,
        (
            f"Rows analyzed: {rows_count} | industries={industries or 'all'} | "
            f"min_market_cap_usd={min_market_cap_usd} | max_market_cap_usd={max_market_cap_usd}"
        ),
    )
    log_to_file(
        log_file,
        (
            "Method: peer-relative safety scoring using Screen 1 core metrics. "
            "Each metric is tagged GOOD/BAD/NEUTRAL relative to industry peers only."
        ),
    )
    log_to_file(
        log_file,
        (
            "industry pctl definition: percentile rank of the company inside its own industry "
            "for that specific metric (0-100 scale)."
        ),
    )
    log_to_file(
        log_file,
        (
            "Reading it: higher industry pctl means a higher raw metric value vs industry peers. "
            "Whether that is GOOD or BAD depends on the metric direction label "
            "(higher-is-better or lower-is-better)."
        ),
    )
    log_to_file(
        log_file,
        (
            "Coverage rule: a metric is scored only when at least 3 industry observations exist; "
            "otherwise it is skipped as N/A."
        ),
    )
    log_to_file(log_file, "")


def _log_universe_summary(log_file: Path, rows: list[dict[str, Any]]) -> None:
    log_to_file(log_file, "Universe Summary")
    log_to_file(log_file, "-" * 180)

    market_caps = sorted(
        value
        for value in (_coerce_numeric(row.get("market_cap_basic")) for row in rows)
        if value is not None
    )
    median_mcap = median(market_caps) if market_caps else None

    altman_values = _sorted_numeric_values(rows, "altman_z_score_ttm")
    debt_equity_values = _sorted_numeric_values(rows, "debt_to_equity")
    current_ratio_values = _sorted_numeric_values(rows, "current_ratio_fq")
    op_margin_values = _sorted_numeric_values(rows, "operating_margin")

    log_to_file(log_file, f"Median market cap: {_format_market_cap(median_mcap)}")
    log_to_file(
        log_file,
        (
            "Median anchors | "
            f"Current Ratio FQ: {_format_number(median(current_ratio_values) if current_ratio_values else None)} | "
            f"Debt/Equity: {_format_number(median(debt_equity_values) if debt_equity_values else None)} | "
            f"Altman Z: {_format_number(median(altman_values) if altman_values else None)} | "
            f"Operating Margin: {_format_percent(median(op_margin_values) if op_margin_values else None)}"
        ),
    )
    log_to_file(log_file, "")


def _overall_flag(score: float) -> str:
    if score >= 0.20:
        return "GOOD"
    if score <= -0.20:
        return "BAD"
    return "MIXED"


def analyze_safety_core_scan(
    scan_data: list[dict[str, Any]],
    industries: list[str] | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    include_all_field_deviation_table: bool = True,
    include_standout_abnormalities: bool = False,
    standout_zscore_threshold: float = 3.0,
    max_standouts_per_ticker: int = 8,
) -> Path:
    """
    Analyze Screen 1 safety-core data and log per-ticker GOOD/BAD risk pointers.

    The function accepts either mapped scan rows ("symbol" + named fields) or raw
    TradingView scan rows with shape {"s": ..., "d": [...]}. Raw rows are mapped
    internally using SAFETY_CORE_COLUMNS.
    """
    normalized_rows = _normalize_scan_data(scan_data)
    for row in normalized_rows:
        _attach_derived_metrics(row)

    log_file = _build_log_file_name(industries, min_market_cap_usd, max_market_cap_usd)
    _reset_log_file(log_file)

    _log_header(
        log_file,
        rows_count=len(normalized_rows),
        industries=industries,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
    )

    if not normalized_rows:
        log_to_file(log_file, "No rows returned. Nothing to analyze.")
        return log_file

    _log_universe_summary(log_file, normalized_rows)

    numeric_field_profiles = _build_numeric_field_profiles(normalized_rows)
    _log_all_field_universe_stats(log_file, numeric_field_profiles)

    industries_map = _build_industry_groups(normalized_rows)
    industry_values_map = {
        industry: {
            metric.field: _sorted_numeric_values(industry_rows, metric.field)
            for metric in CORE_METRIC_RULES
        }
        for industry, industry_rows in industries_map.items()
    }

    ticker_results = []
    for row in normalized_rows:
        industry = _get_industry(row)

        good_flags = 0
        bad_flags = 0
        neutral_flags = 0
        considered = 0
        pointers_good = []
        pointers_bad = []

        for metric in CORE_METRIC_RULES:
            value = _coerce_numeric(row.get(metric.field))
            status, score, industry_pct = _evaluate_metric_status(
                value=value,
                sorted_industry=industry_values_map[industry][metric.field],
                high_is_good=metric.high_is_good,
            )

            if status == "N/A":
                continue

            considered += 1
            if score > 0:
                good_flags += 1
                pointers_good.append(
                    (
                        metric.category,
                        _build_pointer(metric, value, status, industry_pct),
                    )
                )
            elif score < 0:
                bad_flags += 1
                pointers_bad.append(
                    (
                        metric.category,
                        _build_pointer(metric, value, status, industry_pct),
                    )
                )
            else:
                neutral_flags += 1

        net_score = 0.0
        if considered > 0:
            net_score = (good_flags - bad_flags) / considered

        ticker_results.append(
            {
                "row": row,
                "industry": industry,
                "good": good_flags,
                "bad": bad_flags,
                "neutral": neutral_flags,
                "considered": considered,
                "score": net_score,
                "flag": _overall_flag(net_score),
                "good_pointers": pointers_good,
                "bad_pointers": pointers_bad,
            }
        )

    industry_result_map: dict[str, list[dict[str, Any]]] = {}
    for result in ticker_results:
        industry_result_map.setdefault(result["industry"], []).append(result)

    log_to_file(log_file, "Industry Risk Snapshot")
    log_to_file(log_file, "-" * 180)
    log_to_file(
        log_file,
        f"{'Industry':<32} {'Rows':>6} {'GOOD':>6} {'MIXED':>7} {'BAD':>6} {'AvgScore':>10} {'MedianMCap':>12}",
    )
    log_to_file(log_file, "-" * 180)
    for industry, industry_results in sorted(
        industry_result_map.items(),
        key=lambda item: (sum(result["score"] for result in item[1]) / len(item[1])),
        reverse=True,
    ):
        row_count = len(industry_results)
        avg_score = sum(result["score"] for result in industry_results) / row_count
        flag_counts = {
            "GOOD": sum(1 for result in industry_results if result["flag"] == "GOOD"),
            "MIXED": sum(1 for result in industry_results if result["flag"] == "MIXED"),
            "BAD": sum(1 for result in industry_results if result["flag"] == "BAD"),
        }
        industry_mcaps = sorted(
            value
            for value in (
                _coerce_numeric(result["row"].get("market_cap_basic"))
                for result in industry_results
            )
            if value is not None
        )
        median_mcap = median(industry_mcaps) if industry_mcaps else None

        log_to_file(
            log_file,
            (
                f"{industry[:32]:<32} {row_count:>6} {flag_counts['GOOD']:>6} {flag_counts['MIXED']:>7} {flag_counts['BAD']:>6} "
                f"{avg_score:>10.2f} {_format_market_cap(median_mcap):>12}"
            ),
        )
    log_to_file(log_file, "")

    log_to_file(log_file, "Per-Ticker Safety Diagnosis")
    log_to_file(log_file, "-" * 180)
    sorted_results = sorted(
        ticker_results,
        key=lambda result: (
            result["score"],
            _coerce_numeric(result["row"].get("market_cap_basic")) or float("-inf"),
        ),
        reverse=True,
    )

    for index, result in enumerate(sorted_results, start=1):
        row = result["row"]
        symbol = row.get("symbol", "N/A")
        ticker = _get_symbol_name(row)
        company = _get_company_description(row)
        market_cap = _coerce_numeric(row.get("market_cap_basic"))

        log_to_file(
            log_file,
            (
                f"[{index:03d}] {symbol} | ticker={ticker} | company={company or 'N/A'} | "
                f"industry={result['industry']} | mcap={_format_market_cap(market_cap)}"
            ),
        )
        log_to_file(
            log_file,
            (
                f"      FLAG={result['flag']} | score={result['score']:+.2f} | "
                f"GOOD={result['good']} BAD={result['bad']} NEUTRAL={result['neutral']} CONSIDERED={result['considered']}"
            ),
        )

        if result["good_pointers"]:
            log_to_file(log_file, "      GOOD pointers:")
            for category, pointer in result["good_pointers"][:4]:
                log_to_file(log_file, f"        - [{category}] {pointer}")
        else:
            log_to_file(log_file, "      GOOD pointers: none")

        if result["bad_pointers"]:
            log_to_file(log_file, "      BAD pointers:")
            for category, pointer in result["bad_pointers"][:4]:
                log_to_file(log_file, f"        - [{category}] {pointer}")
        else:
            log_to_file(log_file, "      BAD pointers: none")

        hard_flags = []
        altman = _coerce_numeric(row.get("altman_z_score_ttm"))
        current_ratio = _coerce_numeric(row.get("current_ratio_fq"))
        debt_to_equity = _coerce_numeric(row.get("debt_to_equity"))
        cfo = _coerce_numeric(row.get("cash_f_operating_activities_ttm"))

        if altman is not None and altman < 1.8:
            hard_flags.append("Distress risk: Altman Z < 1.8")
        if current_ratio is not None and current_ratio < 1.0:
            hard_flags.append("Tight short-term liquidity: current ratio < 1")
        if debt_to_equity is not None and debt_to_equity > 2.0:
            hard_flags.append("High leverage: debt/equity > 2")
        if cfo is not None and cfo < 0:
            hard_flags.append("Weak cash generation: operating cash flow < 0")

        if hard_flags:
            log_to_file(log_file, "      Global risk flags:")
            for risk_flag in hard_flags:
                log_to_file(log_file, f"        - {risk_flag}")
        else:
            log_to_file(log_file, "      Global risk flags: none triggered")

        if include_all_field_deviation_table:
            _log_per_row_all_field_deviations(
                log_file=log_file,
                row=row,
                numeric_field_profiles=numeric_field_profiles,
            )

        if include_standout_abnormalities:
            _log_row_standouts(
                log_file=log_file,
                row=row,
                numeric_field_profiles=numeric_field_profiles,
                zscore_threshold=standout_zscore_threshold,
                max_items=max_standouts_per_ticker,
            )

        log_to_file(log_file, "")

    return log_file


def run_safety_core_scan(
    scan_data: list[dict[str, Any]],
    industries: list[str] | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    include_all_field_deviation_table: bool = True,
    include_standout_abnormalities: bool = False,
    standout_zscore_threshold: float = 3.0,
    max_standouts_per_ticker: int = 8,
) -> Path:
    """Fetch safety-core data from TradingView and run the risk analysis logger."""
    return analyze_safety_core_scan(
        scan_data=scan_data,
        industries=industries,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
        include_all_field_deviation_table=include_all_field_deviation_table,
        include_standout_abnormalities=include_standout_abnormalities,
        standout_zscore_threshold=standout_zscore_threshold,
        max_standouts_per_ticker=max_standouts_per_ticker,
    )
