"""Semantic classification and field-name parsing for TradingView field catalogs."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Sequence

TIMEFRAME_LABELS = {
    "1": "1-minute",
    "5": "5-minute",
    "15": "15-minute",
    "30": "30-minute",
    "60": "1-hour",
    "120": "2-hour",
    "240": "4-hour",
    "1W": "weekly",
    "1M": "monthly",
}

SEMANTIC_BUCKETS: tuple[str, ...] = (
    "metadata_classification",
    "price_ohlc",
    "momentum_performance",
    "volatility_risk",
    "volume_liquidity",
    "technical_trend",
    "technical_oscillator",
    "technical_pattern",
    "fundamentals_profitability",
    "fundamentals_growth",
    "fundamentals_balance_sheet",
    "valuation",
    "cash_flow_dividend",
    "event_catalyst",
    "analyst_expectations",
    "fund_structure",
    "fixed_income",
    "structural_calendar",
    "other",
)

METADATA_BASE_NAMES: frozenset[str] = frozenset(
    {
        "type",
        "sector",
        "industry",
        "exchange",
        "country",
        "submarket",
        "currency",
        "index",
        "is_primary",
        "active_symbol",
        "market",
        "name",
        "description",
        "logoid",
        "typespecs",
        "symbol",
    }
)

UNIVERSE_FILTER_BASE_NAMES: frozenset[str] = frozenset(
    {
        "type",
        "market_cap_basic",
        "market_cap",
        "market_cap_calc",
        "active_symbol",
        "is_primary",
        "market",
    }
)

PRICE_OHLC_BASE_NAMES: frozenset[str] = frozenset({"close", "open", "high", "low"})

STRUCTURAL_CALENDAR_MARKERS: tuple[str, ...] = (
    "ex_dividend",
    "payment_date",
    "ex_date",
    "dividend_ex",
)

EXACT_RULES: dict[str, tuple[str, str]] = {
    "type": (
        "Instrument type for the symbol, such as stock, ETF, fund, index, crypto, or bond.",
        "Use it to segment the universe so the model only compares like-with-like instruments.",
    ),
    "is_primary": (
        "Flag showing whether this ticker is the primary listing for the instrument.",
        "Use it as a data-quality filter so the model avoids duplicate exposure across alternate listings.",
    ),
    "active_symbol": (
        "Flag showing whether the symbol is active for the current trading day/session.",
        "Use it to exclude halted, inactive, or stale symbols from live screens and signal generation.",
    ),
    "index": (
        "Index or benchmark association for the symbol.",
        "Use it for peer grouping, benchmark-relative analysis, and universe selection.",
    ),
    "sector": (
        "High-level sector classification for the company or instrument.",
        "Use it to build sector-aware models, neutralize sector bias, and compare companies within similar economics.",
    ),
    "industry": (
        "More specific industry classification within the sector.",
        "Use it for tighter peer comparison because valuation, margins, and growth expectations are usually industry-specific.",
    ),
    "exchange": (
        "Listing exchange for the instrument.",
        "Use it to separate different liquidity, trading-hours, and regulatory regimes.",
    ),
    "country": (
        "Country or region associated with the issuer or listing.",
        "Use it to account for macro, regulatory, currency, and geopolitical effects in the model.",
    ),
    "submarket": (
        "Submarket or listing tier for the instrument.",
        "Use it as a market-quality and liquidity filter because lower-tier venues often carry more execution risk.",
    ),
    "currency": (
        "Quote currency used for the instrument.",
        "Use it when normalizing values across markets so the model does not mix currencies incorrectly.",
    ),
    "market_cap": (
        "Market capitalization of the company or asset, usually price multiplied by shares outstanding or circulating supply.",
        "Use it as a size factor because company size influences volatility, liquidity, valuation ranges, and factor behavior.",
    ),
    "market_cap_basic": (
        "Basic market capitalization from fundamental share-count data.",
        "Use it as the main equity size anchor for market-cap buckets and size-based factor modeling.",
    ),
    "market_cap_calc": (
        "Calculated market capitalization, often derived from price and available share or supply data.",
        "Use it when a computed size figure is preferable or when comparing assets whose capitalization is not stored in the basic field.",
    ),
    "number_of_employees": (
        "Reported company headcount.",
        "Use it for operating-efficiency measures such as revenue per employee or EBITDA per employee.",
    ),
    "close": (
        "Closing or last traded price for the bar or timeframe.",
        "Use it as the core price input for return calculations, momentum features, technical indicators, and valuation ratios.",
    ),
    "open": (
        "Opening price for the bar or session.",
        "Use it to measure gaps, opening momentum, and intraday move quality relative to the session start.",
    ),
    "high": (
        "Highest traded price within the bar or session.",
        "Use it for range, breakout, resistance, and volatility calculations.",
    ),
    "low": (
        "Lowest traded price within the bar or session.",
        "Use it for range, support, downside-risk, and volatility calculations.",
    ),
    "volume": (
        "Trading volume for the selected bar or timeframe.",
        "Use it to confirm price moves because strong moves with high volume are usually more reliable than low-volume moves.",
    ),
    "change": (
        "Percentage price change over the selected bar or timeframe.",
        "Use it as a direct momentum feature for ranking strength, weakness, or short-term acceleration.",
    ),
    "change_abs": (
        "Absolute price change over the selected bar or timeframe.",
        "Use it alongside percentage change when dollar move size matters for risk, slippage, or position sizing.",
    ),
    "change_from_open": (
        "Percentage move from the session open to the current price.",
        "Use it to measure intraday trend persistence and buying or selling pressure after the open.",
    ),
    "change_from_open_abs": (
        "Absolute move from the session open to the current price.",
        "Use it in intraday models where the raw price distance from the open matters.",
    ),
    "average_volume": (
        "Average trading volume over a standard lookback window.",
        "Use it as a liquidity filter so the model avoids thinly traded names that can distort signals and execution.",
    ),
    "relative_volume_10d_calc": (
        "Current volume relative to the recent 10-day average volume.",
        "Use it to detect unusual participation because high relative volume often confirms catalysts, breakouts, or reversals.",
    ),
    "float_shares_outstanding": (
        "Public float, meaning the shares available for trading after excluding restricted or closely held shares.",
        "Use it to assess liquidity and squeeze potential because low float can amplify price moves.",
    ),
    "total_shares_outstanding_fundamental": (
        "Total shares outstanding from fundamental data.",
        "Use it to monitor dilution, support market-cap calculations, and normalize per-share metrics.",
    ),
    "total_shares_outstanding": (
        "Total shares or available coin supply, depending on the asset type.",
        "Use it for capitalization, dilution, and supply-side analysis.",
    ),
    "earnings_release_date": (
        "Date of the most recent earnings release.",
        "Use it to model post-earnings drift, freshness of fundamentals, and event-driven volatility windows.",
    ),
    "earnings_release_next_date": (
        "Date of the next scheduled earnings release.",
        "Use it to control event risk because price behavior often changes as earnings approach.",
    ),
    "return_on_assets": (
        "Return on assets, showing how efficiently the company turns assets into profit.",
        "Use it as a quality signal because stronger asset efficiency often indicates a better business model or capital discipline.",
    ),
    "return_on_equity": (
        "Return on equity, showing profit earned relative to shareholder equity.",
        "Use it as a quality and profitability factor, especially when comparing peers in the same industry.",
    ),
    "return_on_invested_capital": (
        "Return on invested capital, showing profit generated from the capital committed to the business.",
        "Use it to assess capital allocation quality because higher ROIC usually confers stronger long-run compounding ability.",
    ),
    "current_ratio": (
        "Current assets divided by current liabilities.",
        "Use it as a short-term liquidity health metric because it shows whether near-term obligations are covered.",
    ),
    "quick_ratio": (
        "Quick ratio, a stricter liquidity measure that excludes less liquid current assets such as inventory.",
        "Use it to evaluate balance-sheet resilience because stronger quick ratios confer more near-term financial flexibility.",
    ),
    "debt_to_equity": (
        "Debt-to-equity ratio, showing leverage relative to shareholder capital.",
        "Use it to measure balance-sheet risk because higher leverage can magnify returns but also increases fragility.",
    ),
    "net_debt": (
        "Total debt minus cash and equivalents.",
        "Use it to judge true leverage because cash-rich firms can carry headline debt with less real balance-sheet pressure.",
    ),
    "altman_z_score_ttm": (
        "Altman Z-score based on recent trailing data, designed as a financial-distress indicator.",
        "Use it as a bankruptcy-risk or fragility screen because weaker scores often confer higher balance-sheet risk.",
    ),
    "altman_z_score_fy": (
        "Altman Z-score based on annual financial data.",
        "Use it as a distress-risk feature in long-only filters, short candidates, or credit-style health scoring.",
    ),
    "piotroski_f_score_ttm": (
        "Piotroski F-score based on recent trailing data, summarizing profitability, leverage, liquidity, and operating improvement.",
        "Use it as a value-quality filter because stronger scores often confer better financial quality inside cheap stocks.",
    ),
    "piotroski_f_score_fy": (
        "Piotroski F-score based on annual data.",
        "Use it to separate high-quality value names from low-quality value traps.",
    ),
    "zmijewski_score_ttm": (
        "Zmijewski distress score based on recent trailing data.",
        "Use it as an additional bankruptcy-risk signal because worse values often confer elevated financial stress.",
    ),
    "zmijewski_score_fy": (
        "Zmijewski distress score based on annual data.",
        "Use it in distress screens and to penalize fragile balance sheets in a composite model.",
    ),
    "price_book_ratio": (
        "Price-to-book ratio using fiscal-year book value.",
        "Use it as an asset-based valuation factor, especially for financials or balance-sheet-heavy businesses.",
    ),
    "price_book_fq": (
        "Price-to-book ratio using the most recent quarter book value.",
        "Use it when you want a more current balance-sheet-based valuation measure.",
    ),
    "price_sales_ratio": (
        "Price-to-sales ratio using annual revenue.",
        "Use it for valuation where earnings are volatile or negative, because sales often give a cleaner baseline.",
    ),
    "price_revenue_ttm": (
        "Price-to-revenue ratio using trailing 12-month revenue.",
        "Use it as a more current version of sales-based valuation in growth or early-stage companies.",
    ),
    "price_free_cash_flow_ttm": (
        "Price-to-free-cash-flow ratio using trailing 12-month free cash flow.",
        "Use it as a cash-based valuation metric because lower values can confer stronger cash-generation value.",
    ),
    "enterprise_value_ebitda_ttm": (
        "Enterprise value divided by trailing 12-month EBITDA.",
        "Use it for capital-structure-neutral valuation because it compares operational cash earnings against full enterprise cost.",
    ),
    "enterprise_value_revenue_ttm": (
        "Enterprise value divided by trailing 12-month revenue.",
        "Use it in sectors where EBITDA or net income is weak or negative but revenue scale still matters.",
    ),
    "earnings_yield": (
        "Earnings yield, the inverse of the price-to-earnings ratio.",
        "Use it as a valuation factor because higher earnings yield generally confers cheaper profit generation versus price.",
    ),
    "ebitda": (
        "EBITDA, a pre-interest, pre-tax, pre-depreciation, and pre-amortization profitability measure.",
        "Use it to compare operating cash-generating ability across companies with different capital structures.",
    ),
    "gross_profit": (
        "Gross profit, equal to revenue minus direct cost of goods or services sold.",
        "Use it to assess pricing power and unit economics because stronger gross profit often confers better business quality.",
    ),
    "gross_margin": (
        "Gross margin, showing gross profit as a share of revenue.",
        "Use it as a quality factor because higher and improving gross margins often confer stronger competitive positioning.",
    ),
    "after_tax_margin": (
        "Net margin after tax, showing how much revenue remains as bottom-line profit.",
        "Use it to judge overall earnings efficiency because higher margins confer stronger profitability conversion.",
    ),
    "pre_tax_margin": (
        "Pretax profit margin, showing profitability before taxes.",
        "Use it to compare operating and financing effectiveness with less distortion from tax regimes.",
    ),
    "operating_margin": (
        "Operating margin, showing operating income relative to revenue.",
        "Use it as a core operating-efficiency feature because higher margins often confer stronger business quality.",
    ),
    "research_and_dev_ratio_ttm": (
        "Research and development expense as a share of revenue on a trailing basis.",
        "Use it to gauge innovation intensity because sustained productive R&D can confer future growth or competitive advantage.",
    ),
    "research_and_dev_ratio_fy": (
        "Research and development expense as a share of annual revenue.",
        "Use it to compare innovation spending across peers and sectors.",
    ),
    "sell_gen_admin_exp_other_ratio_ttm": (
        "Selling, general, and administrative expense ratio on a trailing basis.",
        "Use it to monitor overhead efficiency because a lower or improving ratio can confer operating leverage.",
    ),
    "sell_gen_admin_exp_other_ratio_fy": (
        "Selling, general, and administrative expense ratio on an annual basis.",
        "Use it to compare management cost discipline across similar businesses.",
    ),
    "dividends_paid": (
        "Cash dividends paid to shareholders.",
        "Use it to analyze shareholder return policy and dividend sustainability alongside cash flow coverage.",
    ),
    "dividend_yield_recent": (
        "Recent or forward dividend yield relative to price.",
        "Use it for income-oriented screening because higher yield can confer stronger direct shareholder cash return, with payout-risk checks.",
    ),
    "dividends_per_share_fq": (
        "Dividends per share for the most recent quarter.",
        "Use it to track payout trend, annualized yield potential, and shareholder return consistency.",
    ),
    "dps_common_stock_prim_issue_fy": (
        "Dividends per share for the fiscal year.",
        "Use it for annual income return modeling and dividend-growth analysis.",
    ),
    "dps_common_stock_prim_issue_yoy_growth_fy": (
        "Year-over-year growth in annual dividends per share.",
        "Use it as a dividend-growth factor because rising payouts can confer shareholder-friendliness and confidence in cash generation.",
    ),
    "premarket_change": (
        "Percentage move in the premarket session.",
        "Use it to capture overnight catalyst reaction because large premarket moves often confer higher open volatility and directional bias.",
    ),
    "premarket_volume": (
        "Trading volume in the premarket session.",
        "Use it to confirm whether premarket price action is meaningful or just thin-liquidity noise.",
    ),
    "postmarket_change": (
        "Percentage move in the postmarket session.",
        "Use it to capture after-hours reaction to earnings, guidance, or news catalysts.",
    ),
    "postmarket_volume": (
        "Trading volume in the postmarket session.",
        "Use it to separate material after-hours reactions from low-liquidity noise.",
    ),
    "Volatility.D": (
        "Daily volatility estimate.",
        "Use it for risk sizing because higher volatility usually confers larger price swings and larger drawdown potential.",
    ),
    "Volatility.W": (
        "Weekly volatility estimate.",
        "Use it to characterize medium-horizon risk regime and compare names with different day-to-day noise levels.",
    ),
    "Volatility.M": (
        "Monthly volatility estimate.",
        "Use it as a broader volatility-regime feature in portfolio construction or expected-move models.",
    ),
    "VWAP": (
        "Volume-weighted average price for the selected session or bar sequence.",
        "Use it as an execution and trend anchor because trading above VWAP often confers stronger session control by buyers.",
    ),
    "VWMA": (
        "Volume-weighted moving average, which weights price by volume instead of treating each bar equally.",
        "Use it as a trend filter that gives more influence to heavy-volume bars, which can confer better signal quality.",
    ),
    "Perf.W": (
        "Price performance over the last week.",
        "Use it as a short-horizon momentum feature for ranking near-term leaders and laggards.",
    ),
    "Perf.1M": (
        "Price performance over the last month.",
        "Use it as a short-to-medium momentum factor because recent winners often keep outperforming for a period.",
    ),
    "Perf.Y": (
        "Price performance over the last year.",
        "Use it as a broad trend and momentum feature for cross-sectional ranking.",
    ),
    "Perf.YTD": (
        "Year-to-date price performance.",
        "Use it to gauge current calendar-year leadership or lagging recovery candidates.",
    ),
}


@dataclass(frozen=True)
class FieldSemanticClassification:
    base_name: str
    timeframe: str | None
    lag: str | None
    semantic_bucket: str
    semantic_tags: tuple[str, ...]
    period_basis: str | None
    timeframe_class: str
    indicator_family: str | None
    is_timeframe_variant: bool


def parse_name(name: str) -> tuple[str, str | None, str | None]:
    timeframe = None
    lag = None

    tf_match = re.search(r"\|(.+)$", name)
    if tf_match:
        timeframe = tf_match.group(1)
        name = name[: tf_match.start()]

    lag_match = re.search(r"\[(\d+)\]$", name)
    if lag_match:
        lag = lag_match.group(1)
        name = name[: lag_match.start()]

    return name, timeframe, lag


def clean_display_name(display_name: str) -> str:
    return html.unescape(display_name or "").strip()


def add_context(text: str, timeframe: str | None, lag: str | None) -> str:
    if timeframe:
        tf_label = TIMEFRAME_LABELS.get(timeframe, timeframe)
        text = f"{text} This version is on the {tf_label} timeframe."
    if lag:
        text = (
            f"{text} The bracket notation means the value is taken {lag} bar(s) back."
        )
    return text


def infer_period_phrase(display_name: str, field_name: str) -> str:
    text = f"{field_name} {display_name}".upper()
    if "TTM" in text:
        return " on a trailing 12-month basis"
    if "MRQ" in text:
        return " for the most recent quarter"
    if "FQ" in text or "QUARTER" in text:
        return " on a quarterly basis"
    if "FY" in text or "ANNUAL" in text:
        return " on a fiscal-year basis"
    if "YTD" in text:
        return " on a year-to-date basis"
    if "1M" in text and "MONTHLY PERFORMANCE" not in text:
        return " over a 1-month window"
    if "3M" in text:
        return " over a 3-month window"
    if "6M" in text:
        return " over a 6-month window"
    if "1Y" in text:
        return " over a 1-year window"
    if "5Y" in text:
        return " over a 5-year window"
    return ""


def infer_period_basis(base_name: str, display_name: str) -> str | None:
    text = f"{base_name} {display_name}".upper()
    if "_TTM" in text or text.endswith("TTM") or " TTM" in text:
        return "TTM"
    if "_FQ" in text or " FQ" in text or "QUARTER" in text:
        return "FQ"
    if "_FY" in text or " FY" in text or "FISCAL-YEAR" in text:
        return "FY"
    if "YTD" in text:
        return "YTD"
    return None


def infer_timeframe_class(timeframe: str | None) -> str:
    if timeframe is None:
        return "daily"
    if timeframe in {"1", "5", "15", "30", "60", "120", "240"}:
        return "intraday"
    if timeframe == "1W":
        return "weekly"
    if timeframe == "1M":
        return "monthly"
    return "other"


def infer_indicator_family(base_name: str) -> str | None:
    upper = base_name.upper()
    patterns: list[tuple[str, str]] = [
        (r"^RSI\d*$", "rsi"),
        (r"^MACD", "macd"),
        (r"^STOCH", "stochastic"),
        (r"^ADX", "adx"),
        (r"^EMA\d+$", "ema"),
        (r"^SMA\d+$", "sma"),
        (r"^BB\.", "bollinger"),
        (r"^CANDLE\.", "candlestick"),
        (r"^ICHIMOKU", "ichimoku"),
        (r"^PIVOT\.", "pivot"),
        (r"^DONCHCH", "donchian"),
        (r"^KLTCHNL", "keltner"),
        (r"^RECOMMEND", "recommendation"),
        (r"^W\.R$", "williams_r"),
        (r"^CCI\d+", "cci"),
        (r"^ATR", "atr"),
        (r"^ADR", "adr"),
        (r"^VOLATILITY\.", "volatility"),
    ]
    for pattern, family in patterns:
        if re.search(pattern, upper):
            return family
    return None


def generic_type_use(type_name: str) -> str:
    lowered = (type_name or "").lower()
    if lowered == "percent":
        return "Use it as a normalized rate feature because percentage values compare more cleanly across companies than raw dollars."
    if lowered in {"price", "fundamental_price"}:
        return "Use it in valuation, scaling, and ratio calculations because currency-denominated fields often become more informative when normalized."
    if lowered == "number":
        return "Use it as a raw quantitative feature after scaling or ranking so it fits cleanly into a scoring model."
    if lowered == "bool":
        return "Use it as a filter or regime flag because binary fields often confer clean yes-or-no screening rules."
    if lowered == "time":
        return "Use it to build event-timing features such as days since, days until, or catalyst windows."
    return "Use it as contextual metadata, grouping input, or a join field rather than a direct numeric signal."


def explain_indicator(base_name: str, display_name: str) -> tuple[str, str] | None:
    upper_name = base_name.upper()
    lower_display = display_name.lower()

    ema_match = re.fullmatch(r"EMA(\d+)", upper_name)
    if ema_match:
        period = ema_match.group(1)
        return (
            f"Exponential moving average over {period} periods, giving more weight to recent price action.",
            f"Use it as a trend filter because price above EMA{period} usually confers a more favorable short- or medium-term trend state.",
        )

    sma_match = re.fullmatch(r"SMA(\d+)", upper_name)
    if sma_match:
        period = sma_match.group(1)
        return (
            f"Simple moving average over {period} periods, smoothing price without weighting recent bars more heavily.",
            f"Use it for trend direction and support-resistance analysis because sustained price above SMA{period} often confers positive trend structure.",
        )

    rsi_match = re.fullmatch(r"RSI(\d+)?", upper_name)
    if rsi_match:
        period = rsi_match.group(1) or "14"
        return (
            f"Relative Strength Index over {period} periods, a momentum oscillator scaled between 0 and 100.",
            "Use it to spot overbought, oversold, divergence, and momentum regime changes because it confers a normalized view of price thrust.",
        )

    if upper_name.startswith("STOCH.RSI"):
        return (
            "Stochastic RSI, which applies the stochastic formula to RSI values to create a faster momentum oscillator.",
            "Use it for faster entry and exit timing because it confers earlier momentum-turn signals than RSI alone.",
        )

    if upper_name.startswith("STOCH."):
        return (
            "Stochastic oscillator value showing where price sits within its recent range.",
            "Use it for overbought-oversold timing and crossover signals because it confers a range-relative momentum view.",
        )

    if upper_name.startswith("MACD"):
        return (
            "MACD component derived from the spread and smoothing of fast and slow moving averages.",
            "Use it to track momentum direction and crossover behavior because MACD-type features confer trend-plus-momentum information in one signal.",
        )

    if upper_name.startswith("ADX+DI"):
        return (
            "Positive directional indicator from the ADX system, measuring upward directional pressure.",
            "Use it with ADX and -DI because stronger +DI confers stronger bullish directional control.",
        )

    if upper_name.startswith("ADX-DI"):
        return (
            "Negative directional indicator from the ADX system, measuring downward directional pressure.",
            "Use it with ADX and +DI because stronger -DI confers stronger bearish directional control.",
        )

    adx_match = re.fullmatch(r"ADX(?:_(\d+))?", upper_name)
    if adx_match:
        period = adx_match.group(1) or "14"
        return (
            f"Average Directional Index over roughly {period} periods, measuring trend strength without direction.",
            "Use it as a trend-strength filter because high ADX confers a stronger trending environment, while low ADX suggests chop or range behavior.",
        )

    if upper_name in {
        "AO",
        "MOM",
        "MOM_14",
        "MONEYFLOW",
        "CCI20",
        "BBPOWER",
        "ROC",
        "UO",
        "W.R",
        "VWAP",
        "VWMA",
        "HULLMA9",
        "HULLMA20",
        "HULLMA200",
        "CHAIKINMONEYFLOW",
    }:
        indicator_meaning = {
            "AO": "Awesome Oscillator momentum reading",
            "MOM": "price momentum reading",
            "MOM_14": "14-period momentum reading",
            "MONEYFLOW": "money-flow style pressure reading",
            "CCI20": "20-period Commodity Channel Index reading",
            "BBPOWER": "Bull-Bear Power reading",
            "ROC": "rate-of-change momentum reading",
            "UO": "Ultimate Oscillator reading",
            "W.R": "Williams %R reading",
            "VWAP": "volume-weighted average price level",
            "VWMA": "volume-weighted moving average level",
            "HULLMA9": "9-period Hull moving average",
            "HULLMA20": "20-period Hull moving average",
            "HULLMA200": "200-period Hull moving average",
            "CHAIKINMONEYFLOW": "Chaikin Money Flow reading",
        }
        meaning = indicator_meaning[upper_name]
        return (
            f"{meaning.capitalize()} used in technical analysis.",
            "Use it as a technical feature for trend, momentum, mean-reversion, or participation confirmation depending on the broader setup.",
        )

    if upper_name.startswith("BB."):
        return (
            "Bollinger Band component based on a moving-average centerline and a volatility envelope.",
            "Use it for volatility-regime, breakout, and mean-reversion logic because band position confers context about how stretched price is.",
        )

    if upper_name.startswith("DONCHCH"):
        return (
            "Donchian Channel component showing recent high, low, or midpoint range boundaries.",
            "Use it for breakout systems because the channel boundaries confer clean recent support and resistance levels.",
        )

    if upper_name.startswith("KLTCHNL"):
        return (
            "Keltner Channel component built from a moving-average center and range-based envelope.",
            "Use it for trend and volatility structure because movement outside the channel can confer expansion or exhaustion information.",
        )

    if upper_name.startswith("ICHIMOKU"):
        return (
            "Ichimoku Cloud component used to judge trend, support-resistance, and momentum alignment.",
            "Use it for regime detection because Ichimoku structure can confer a clean bullish, bearish, or neutral state.",
        )

    if upper_name.startswith("PIVOT."):
        return (
            "Pivot-point level calculated from prior-period price action to estimate support and resistance.",
            "Use it as a trading map because pivot levels confer structured targets, breakout zones, and likely reaction areas.",
        )

    if upper_name.startswith("CANDLE."):
        return (
            "Candlestick pattern flag indicating that a named candle formation has been detected.",
            "Use it as a secondary timing feature because recognized candle patterns can confer reversal or continuation context when combined with trend and volume.",
        )

    if (
        "recommend" in lower_display
        or upper_name.startswith("RECOMMEND")
        or upper_name.startswith("REC.")
    ):
        return (
            "Composite technical recommendation score generated from grouped indicator logic.",
            "Use it as a summarized technical-state feature because it confers an already-aggregated view of bullish versus bearish technical evidence.",
        )

    return None


def explain_by_keywords(
    base_name: str, display_name: str, type_name: str
) -> tuple[str, str]:
    combined = f"{base_name} {display_name}".lower()
    period_phrase = infer_period_phrase(display_name, base_name)

    if any(
        token in combined
        for token in [
            "market cap",
            "enterprise value",
            "price to book",
            "price to sales",
            "price to revenue",
            "price to free cash flow",
            "graham",
            "tobin",
            "ncavps",
        ]
    ):
        return (
            f"Valuation-related field{period_phrase} describing how the market prices the company relative to size, assets, sales, cash flow, or enterprise value.",
            "Use it as a value-versus-expensiveness feature because cheaper valuation often confers more upside if business quality is intact.",
        )

    if any(
        token in combined
        for token in [
            "revenue",
            "sales",
            "gross profit",
            "ebitda",
            "ebit",
            "oper income",
            "operating income",
            "net income",
            "eps",
            "earnings per share",
            "margin",
            "return on",
            "roic",
            "roe",
            "roa",
            "asset turnover",
            "turnover",
        ]
    ):
        return (
            f"Fundamental operating-performance field{period_phrase} tied to growth, profitability, efficiency, or earnings quality.",
            "Use it to measure business strength because improving fundamentals often confer stronger medium- and long-horizon return potential.",
        )

    if any(
        token in combined
        for token in [
            "cash flow",
            "free cash flow",
            "capex",
            "capital expenditures",
            "working capital",
            "dividends paid",
            "cash ",
            "cash_",
            "cash and short term invest",
            "cash and short term investments",
        ]
    ):
        return (
            f"Cash-flow or liquidity field{period_phrase} describing cash generation, reinvestment, or near-term financial flexibility.",
            "Use it to judge funding strength and shareholder-return capacity because stronger cash generation confers more resilience and optionality.",
        )

    if any(
        token in combined
        for token in [
            "debt",
            "liabilities",
            "equity",
            "assets",
            "current ratio",
            "quick ratio",
            "interest cover",
            "altman",
            "zmijewski",
            "piotroski",
            "leverage",
            "goodwill",
        ]
    ):
        return (
            f"Balance-sheet or financial-health field{period_phrase} describing leverage, asset backing, liquidity, or distress risk.",
            "Use it to penalize fragile firms because stronger balance-sheet quality confers lower financial-risk and better downside protection.",
        )

    if any(token in combined for token in ["dividend", "yield", "payout"]):
        return (
            f"Dividend or shareholder-payout field{period_phrase} describing cash returned to investors or the sustainability of that return.",
            "Use it in income and quality screens because stable, covered payouts can confer better shareholder return discipline.",
        )

    if any(
        token in combined
        for token in [
            "premarket",
            "postmarket",
            "earnings release",
            "earnings date",
            "surprise",
            "forecast",
            "price target",
            "recommendation",
        ]
    ):
        return (
            f"Event or expectation field{period_phrase} tied to catalysts, analyst views, or market reaction windows.",
            "Use it for event-driven models because catalyst timing and expectation gaps can confer outsized short-term moves.",
        )

    if any(
        token in combined
        for token in [
            "volume",
            "relative volume",
            "value traded",
            "fund flows",
            "aum",
            "nav",
            "expense ratio",
            "holdings",
            "weight_top",
            "float",
            "shareholders",
            "shares outstanding",
        ]
    ):
        return (
            f"Liquidity, participation, or fund-structure field{period_phrase} describing trading depth, ownership base, or product composition.",
            "Use it to filter for tradability and structure because stronger liquidity or cleaner structure often confers more reliable signals.",
        )

    if any(
        token in combined
        for token in [
            "perf",
            "performance",
            "high.",
            "low.",
            "52 week",
            "gap",
            "volatility",
            "beta",
            "atr",
            "adrp",
        ]
    ):
        return (
            f"Market-behavior field{period_phrase} describing price trend, distance, breakout context, or volatility.",
            "Use it for momentum, reversal, and risk-regime modeling because market-behavior features confer direct information about how the instrument is trading.",
        )

    if any(
        token in combined
        for token in [
            "sector",
            "industry",
            "exchange",
            "country",
            "submarket",
            "issuer",
            "strategy",
            "focus",
            "kind",
            "category",
            "asset class",
            "market",
        ]
    ):
        return (
            "Classification or metadata field that identifies what the instrument is, where it trades, or how it is categorized.",
            "Use it to segment the universe and build context-aware models because classification features confer more meaningful peer comparisons.",
        )

    if any(
        token in combined
        for token in ["ipo", "offer", "offered shares", "announcement date"]
    ):
        return (
            "IPO-related field describing offer terms, timing, size, or early listing context.",
            "Use it in new-issue models because IPO structure can confer different volatility, float dynamics, and post-listing behavior.",
        )

    if any(
        token in combined
        for token in ["coupon", "maturity", "yield recent", "yield upcoming"]
    ):
        return (
            "Fixed-income style field describing bond income, maturity profile, or yield behavior.",
            "Use it when screening bonds or yield instruments because maturity and yield shape duration risk and expected carry.",
        )

    cleaned_display = display_name or base_name
    explanation = f"Field representing {cleaned_display.lower()}{period_phrase}."
    model_use = generic_type_use(type_name)
    return explanation, model_use


def explain_field(
    base_name: str,
    display_name: str,
    type_name: str,
    timeframe: str | None,
    lag: str | None,
) -> tuple[str, str]:
    if base_name in EXACT_RULES:
        explanation, model_use = EXACT_RULES[base_name]
    else:
        indicator_rule = explain_indicator(base_name, display_name)
        if indicator_rule is not None:
            explanation, model_use = indicator_rule
        else:
            explanation, model_use = explain_by_keywords(
                base_name, display_name, type_name
            )

    return add_context(explanation, timeframe, lag), add_context(
        model_use, timeframe, lag
    )


def _collect_semantic_tags(
    base_name: str, display_name: str, bucket: str
) -> list[str]:
    combined = f"{base_name} {display_name}".lower()
    tags: list[str] = []
    period_basis = infer_period_basis(base_name, display_name)
    if period_basis:
        tags.append(period_basis.lower())
    indicator_family = infer_indicator_family(base_name)
    if indicator_family:
        tags.append(indicator_family)
    if bucket == "volume_liquidity" and "market_cap" in combined:
        tags.append("size")
    if "growth" in combined or "yoy" in combined or "qoq" in combined:
        tags.append("growth")
    if "margin" in combined:
        tags.append("margin")
    return sorted(set(tags))


def classify_semantic_bucket(
    base_name: str, display_name: str, type_name: str = ""
) -> tuple[str, tuple[str, ...]]:
    combined = f"{base_name} {display_name}".lower()
    upper = base_name.upper()

    if any(marker in combined for marker in STRUCTURAL_CALENDAR_MARKERS):
        if any(
            token in combined
            for token in ["upcoming", "date", "ex_dividend", "payment_date"]
        ):
            bucket = "structural_calendar"
            return bucket, tuple(_collect_semantic_tags(base_name, display_name, bucket))

    if base_name in METADATA_BASE_NAMES:
        bucket = "metadata_classification"
        return bucket, tuple(_collect_semantic_tags(base_name, display_name, bucket))

    if base_name in PRICE_OHLC_BASE_NAMES:
        bucket = "price_ohlc"
        return bucket, tuple(_collect_semantic_tags(base_name, display_name, bucket))

    if upper.startswith("CANDLE.") or (
        "recommend" in combined
        or upper.startswith("RECOMMEND")
        or upper.startswith("REC.")
    ):
        bucket = "technical_pattern"
        return bucket, tuple(_collect_semantic_tags(base_name, display_name, bucket))

    if (
        explain_indicator(base_name, display_name) is not None
        and (
            upper.startswith(("RSI", "MACD", "STOCH", "CCI", "W.R", "AO", "MOM", "ROC", "UO", "BBPOWER", "MONEYFLOW"))
            or upper in {"CHAIKINMONEYFLOW"}
        )
    ):
        bucket = "technical_oscillator"
        return bucket, tuple(_collect_semantic_tags(base_name, display_name, bucket))

    if explain_indicator(base_name, display_name) is not None and (
        upper.startswith(("EMA", "SMA", "VWAP", "VWMA", "HULLMA", "ICHIMOKU", "PIVOT.", "DONCHCH", "KLTCHNL", "BB.", "ADX"))
    ):
        bucket = "technical_trend"
        return bucket, tuple(_collect_semantic_tags(base_name, display_name, bucket))

    if base_name.startswith("Perf.") or base_name in {
        "change",
        "change_abs",
        "change_from_open",
        "change_from_open_abs",
        "gap",
        "premarket_gap",
        "Mom",
        "ROC",
    }:
        bucket = "momentum_performance"
        return bucket, tuple(_collect_semantic_tags(base_name, display_name, bucket))

    if any(
        token in combined
        for token in ["atr", "adr", "adrp", "beta", "volatility."]
    ) or upper.startswith("VOLATILITY."):
        bucket = "volatility_risk"
        return bucket, tuple(_collect_semantic_tags(base_name, display_name, bucket))

    if any(
        token in combined
        for token in [
            "volume",
            "relative volume",
            "value traded",
            "float",
            "shareholders",
            "shares outstanding",
            "market cap",
        ]
    ):
        bucket = "volume_liquidity"
        return bucket, tuple(_collect_semantic_tags(base_name, display_name, bucket))

    if any(
        token in combined
        for token in ["yoy_growth", "qoq_growth", "_yoy_", "_qoq_"]
    ):
        bucket = "fundamentals_growth"
        return bucket, tuple(_collect_semantic_tags(base_name, display_name, bucket))

    if any(
        token in combined
        for token in [
            "revenue",
            "sales",
            "gross profit",
            "ebitda",
            "ebit",
            "oper income",
            "operating income",
            "net income",
            "eps",
            "earnings per share",
            "margin",
            "return on",
            "roic",
            "roe",
            "roa",
            "asset turnover",
        ]
    ):
        bucket = "fundamentals_profitability"
        return bucket, tuple(_collect_semantic_tags(base_name, display_name, bucket))

    if any(
        token in combined
        for token in [
            "debt",
            "liabilities",
            "current ratio",
            "quick ratio",
            "altman",
            "zmijewski",
            "piotroski",
            "leverage",
            "goodwill",
            "cash_ratio",
        ]
    ):
        bucket = "fundamentals_balance_sheet"
        return bucket, tuple(_collect_semantic_tags(base_name, display_name, bucket))

    if any(
        token in combined
        for token in [
            "enterprise value",
            "price to book",
            "price to sales",
            "price to revenue",
            "price to free cash flow",
            "price_earnings",
            "peg",
            "graham",
            "tobin",
            "ncavps",
            "earnings_yield",
        ]
    ):
        bucket = "valuation"
        return bucket, tuple(_collect_semantic_tags(base_name, display_name, bucket))

    if any(
        token in combined
        for token in [
            "cash flow",
            "free cash flow",
            "capex",
            "capital expenditures",
            "working capital",
            "dividends paid",
            "dividend",
            "payout",
        ]
    ) and bucket_not_structural_calendar(combined):
        bucket = "cash_flow_dividend"
        return bucket, tuple(_collect_semantic_tags(base_name, display_name, bucket))

    if any(
        token in combined
        for token in [
            "premarket",
            "postmarket",
            "earnings release",
            "earnings date",
            "surprise",
            "gap",
        ]
    ):
        bucket = "event_catalyst"
        return bucket, tuple(_collect_semantic_tags(base_name, display_name, bucket))

    if any(
        token in combined
        for token in ["price target", "analyst", "forecast", "recommendation"]
    ):
        bucket = "analyst_expectations"
        return bucket, tuple(_collect_semantic_tags(base_name, display_name, bucket))

    if any(
        token in combined
        for token in ["fund flows", "aum", "nav", "expense ratio", "holdings", "weight_top"]
    ):
        bucket = "fund_structure"
        return bucket, tuple(_collect_semantic_tags(base_name, display_name, bucket))

    if any(
        token in combined
        for token in ["coupon", "maturity", "yield recent", "yield upcoming"]
    ):
        bucket = "fixed_income"
        return bucket, tuple(_collect_semantic_tags(base_name, display_name, bucket))

    if any(
        token in combined
        for token in [
            "perf",
            "performance",
            "high.",
            "low.",
            "52 week",
            "gap",
            "beta",
        ]
    ):
        bucket = "momentum_performance"
        return bucket, tuple(_collect_semantic_tags(base_name, display_name, bucket))

    if any(
        token in combined
        for token in [
            "sector",
            "industry",
            "exchange",
            "country",
            "submarket",
            "issuer",
            "strategy",
            "focus",
            "kind",
            "category",
            "asset class",
            "market",
        ]
    ):
        bucket = "metadata_classification"
        return bucket, tuple(_collect_semantic_tags(base_name, display_name, bucket))

    bucket = "other"
    return bucket, tuple(_collect_semantic_tags(base_name, display_name, bucket))


def bucket_not_structural_calendar(combined: str) -> bool:
    return not any(marker in combined for marker in STRUCTURAL_CALENDAR_MARKERS)


def classify_field_semantics(
    field_name: str,
    display_name: str,
    type_name: str = "",
) -> FieldSemanticClassification:
    base_name, timeframe, lag = parse_name(field_name)
    bucket, tags = classify_semantic_bucket(base_name, display_name, type_name)
    return FieldSemanticClassification(
        base_name=base_name,
        timeframe=timeframe,
        lag=lag,
        semantic_bucket=bucket,
        semantic_tags=tags,
        period_basis=infer_period_basis(base_name, display_name),
        timeframe_class=infer_timeframe_class(timeframe),
        indicator_family=infer_indicator_family(base_name),
        is_timeframe_variant=timeframe is not None or lag is not None,
    )


def mode_bucket(bucket_values: Sequence[str]) -> tuple[str, bool]:
    if not bucket_values:
        return "other", False
    counts: dict[str, int] = {}
    for value in bucket_values:
        counts[value] = counts.get(value, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    top_bucket, top_count = ranked[0]
    conflict = len(ranked) > 1 and ranked[1][1] == top_count
    return top_bucket, conflict
