import json
from copy import deepcopy
from typing import Any

import requests

from constants.trading_view_constants import TRADING_VIEW_ALL_MARKETS_ARRAY

TRADINGVIEW_AMERICA_SCAN_URL = (
    "https://scanner.tradingview.com/america/scan?label-product=screener-stock"
)

TRADINGVIEW_AMERICA_ETF_SCAN_URL = (
    "https://scanner.tradingview.com/america/scan?label-product=screener-etf"
)

TRADINGVIEW_GLOBAL_SCAN_URL = (
    "https://scanner.tradingview.com/global/scan?label-product=screener-stock"
)

TRADINGVIEW_GLOBAL_ETF_SCAN_URL = (
    "https://scanner.tradingview.com/global/scan?label-product=screener-etf"
)

USA_MAIN_LISTING_FOR_INDUSTRIES_PARSE_PAYLOAD = {
    "columns": [
        "ticker-view",
        "close",
        "type",
        "typespecs",
        "pricescale",
        "minmov",
        "fractional",
        "minmove2",
        "currency",
        "change",
        "market_cap_basic",
        "fundamental_currency_code",
        "price_earnings_ttm",
        "enterprise_value_ebitda_ttm",
        "enterprise_value_to_ebit_ttm",
        "enterprise_value_to_revenue_ttm",
        "Perf.Y",
        "industry.tr",
        "market",
        "industry",
        "neg_capital_expenditures_ttm",
    ],
    "filter": [
        {
            "left": "is_primary",
            "operation": "equal",
            "right": True,
        }
    ],
    "ignore_unknown_fields": False,
    "options": {"lang": "en"},
    "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
    "symbols": {},
    "markets": ["america"],
    "filter2": {
        "operator": "and",
        "operands": [
            {
                "operation": {
                    "operator": "or",
                    "operands": [
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "stock",
                                        }
                                    },
                                    {
                                        "expression": {
                                            "left": "typespecs",
                                            "operation": "has",
                                            "right": ["common"],
                                        }
                                    },
                                ],
                            }
                        },
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "stock",
                                        }
                                    },
                                    {
                                        "expression": {
                                            "left": "typespecs",
                                            "operation": "has",
                                            "right": ["preferred"],
                                        }
                                    },
                                ],
                            }
                        },
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "dr",
                                        }
                                    }
                                ],
                            }
                        },
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "fund",
                                        }
                                    },
                                    {
                                        "expression": {
                                            "left": "typespecs",
                                            "operation": "has_none_of",
                                            "right": ["etf"],
                                        }
                                    },
                                ],
                            }
                        },
                    ],
                }
            },
            {
                "expression": {
                    "left": "typespecs",
                    "operation": "has_none_of",
                    "right": ["pre-ipo"],
                }
            },
        ],
    },
}

USA_ALL_LISTING_FOR_INDUSTRIES_PARSE_PAYLOAD = {
    "columns": [
        "ticker-view",
        "close",
        "type",
        "typespecs",
        "pricescale",
        "minmov",
        "fractional",
        "minmove2",
        "currency",
        "change",
        "market_cap_basic",
        "fundamental_currency_code",
        "price_earnings_ttm",
        "enterprise_value_ebitda_ttm",
        "enterprise_value_to_ebit_ttm",
        "enterprise_value_to_revenue_ttm",
        "Perf.Y",
        "industry.tr",
        "market",
        "industry",
        "neg_capital_expenditures_ttm",
    ],
    "ignore_unknown_fields": False,
    "options": {"lang": "en"},
    "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
    "symbols": {},
    "markets": ["america"],
    "filter2": {
        "operator": "and",
        "operands": [
            {
                "operation": {
                    "operator": "or",
                    "operands": [
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "stock",
                                        }
                                    },
                                    {
                                        "expression": {
                                            "left": "typespecs",
                                            "operation": "has",
                                            "right": ["common"],
                                        }
                                    },
                                ],
                            }
                        },
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "stock",
                                        }
                                    },
                                    {
                                        "expression": {
                                            "left": "typespecs",
                                            "operation": "has",
                                            "right": ["preferred"],
                                        }
                                    },
                                ],
                            }
                        },
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "dr",
                                        }
                                    }
                                ],
                            }
                        },
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "fund",
                                        }
                                    },
                                    {
                                        "expression": {
                                            "left": "typespecs",
                                            "operation": "has_none_of",
                                            "right": ["etf"],
                                        }
                                    },
                                ],
                            }
                        },
                    ],
                }
            },
            {
                "expression": {
                    "left": "typespecs",
                    "operation": "has_none_of",
                    "right": ["pre-ipo"],
                }
            },
        ],
    },
}

GLOBAL_MARKET_INDUSTRIES_VALUATION_PAYLOAD = {
    "columns": [
        "ticker-view",
        "industry",
        "industry.tr",
        "market_cap_basic",
        "type",
        "typespecs",
        "fundamental_currency_code",
        "Perf.1Y.MarketCap",
        "Perf.Y",
        "price_earnings_ttm",
        "price_earnings_growth_ttm",
        "price_sales_current",
        "price_book_fq",
        "price_to_cash_f_operating_activities_ttm",
        "price_free_cash_flow_ttm",
        "price_to_cash_ratio",
        "enterprise_value_current",
        "enterprise_value_to_revenue_ttm",
        "enterprise_value_to_ebit_ttm",
        "enterprise_value_ebitda_ttm",
    ],
    "ignore_unknown_fields": False,
    "options": {"lang": "en"},
    "price_conversion": {"to_currency": "usd"},
    "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
    "symbols": {},
    "markets": [
        "america",
        "argentina",
        "australia",
        "austria",
        "bahrain",
        "bangladesh",
        "belgium",
        "brazil",
        "canada",
        "chile",
        "china",
        "colombia",
        "cyprus",
        "czech",
        "denmark",
        "egypt",
        "estonia",
        "finland",
        "france",
        "germany",
        "greece",
        "hongkong",
        "hungary",
        "iceland",
        "india",
        "indonesia",
        "ireland",
        "israel",
        "italy",
        "japan",
        "kenya",
        "kuwait",
        "latvia",
        "lithuania",
        "luxembourg",
        "malaysia",
        "mexico",
        "morocco",
        "netherlands",
        "newzealand",
        "nigeria",
        "norway",
        "pakistan",
        "peru",
        "philippines",
        "poland",
        "portugal",
        "qatar",
        "romania",
        "russia",
        "ksa",
        "serbia",
        "singapore",
        "slovakia",
        "rsa",
        "korea",
        "spain",
        "srilanka",
        "sweden",
        "switzerland",
        "taiwan",
        "thailand",
        "tunisia",
        "turkey",
        "uae",
        "uk",
        "venezuela",
        "vietnam",
    ],
    "filter": [{"left": "is_primary", "operation": "equal", "right": True}],
    "filter2": {
        "operator": "and",
        "operands": [
            {
                "operation": {
                    "operator": "or",
                    "operands": [
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "stock",
                                        }
                                    },
                                    {
                                        "expression": {
                                            "left": "typespecs",
                                            "operation": "has",
                                            "right": ["common"],
                                        }
                                    },
                                ],
                            }
                        },
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "stock",
                                        }
                                    },
                                    {
                                        "expression": {
                                            "left": "typespecs",
                                            "operation": "has",
                                            "right": ["preferred"],
                                        }
                                    },
                                ],
                            }
                        },
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "dr",
                                        }
                                    }
                                ],
                            }
                        },
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "fund",
                                        }
                                    },
                                    {
                                        "expression": {
                                            "left": "typespecs",
                                            "operation": "has_none_of",
                                            "right": ["etf"],
                                        }
                                    },
                                ],
                            }
                        },
                    ],
                }
            },
            {
                "expression": {
                    "left": "typespecs",
                    "operation": "has_none_of",
                    "right": ["pre-ipo"],
                }
            },
        ],
    },
}

USA_GLOBAL_MARKET_PRICEPERF_METRICS_PAYLOAD = {
    "columns": [
        "name",
        "ticker-view",
        "change",
        "market_cap_basic",
        "type",
        "typespecs",
        "fundamental_currency_code",
        "Perf.5Y",
        "Perf.6M",
        "Perf.All",
        "Perf.1M",
        "Perf.W",
        "Perf.Y",
        "Perf.YTD",
        "Perf.10Y",
        "Perf.3Y",
        "Perf.5D",
        "market",
    ],
    "filter": [{"left": "is_primary", "operation": "equal", "right": True}],
    "ignore_unknown_fields": False,
    "options": {"lang": "en"},
    "price_conversion": {"to_currency": "usd"},
    "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
    "symbols": {},
    "markets": TRADING_VIEW_ALL_MARKETS_ARRAY,
    "filter2": {
        "operator": "and",
        "operands": [
            {
                "operation": {
                    "operator": "or",
                    "operands": [
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "stock",
                                        }
                                    },
                                    {
                                        "expression": {
                                            "left": "typespecs",
                                            "operation": "has",
                                            "right": ["common"],
                                        }
                                    },
                                ],
                            }
                        },
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "stock",
                                        }
                                    },
                                    {
                                        "expression": {
                                            "left": "typespecs",
                                            "operation": "has",
                                            "right": ["preferred"],
                                        }
                                    },
                                ],
                            }
                        },
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "dr",
                                        }
                                    }
                                ],
                            }
                        },
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "fund",
                                        }
                                    },
                                    {
                                        "expression": {
                                            "left": "typespecs",
                                            "operation": "has_none_of",
                                            "right": ["etf"],
                                        }
                                    },
                                ],
                            }
                        },
                    ],
                }
            },
            {
                "expression": {
                    "left": "typespecs",
                    "operation": "has_none_of",
                    "right": ["pre-ipo"],
                }
            },
        ],
    },
}

GLOBAL_MARKET_ACTIVITY_FLOAT_ATTENTION_BASE_PAYLOAD = {
    "columns": [
        "name",
        "ticker-view",
        "close",
        "exchange",
        "country",
        "sector",
        "industry",
        "market_cap_basic",
        "float_shares_outstanding",
        "volume",
        "average_volume_10d_calc",
        "average_volume_30d_calc",
        "average_volume_60d_calc",
        "average_volume_90d_calc",
        "relative_volume_10d_calc",
        "Value.Traded",
        "AvgValue.Traded_10d",
        "AvgValue.Traded_30d",
        "AvgValue.Traded_60d",
        "AvgValue.Traded_90d",
        "ADR",
        "ATR",
        "ATRP",
        "Volatility.D",
        "Volatility.W",
        "Volatility.M",
        "beta_1_year",
        "beta_3_year",
        "beta_5_year",
        "change",
        "gap",
        "premarket_gap",
        "premarket_change",
        "premarket_volume",
        "postmarket_change",
        "postmarket_volume",
        "Perf.5D",
        "Perf.W",
        "Perf.1M",
        "Perf.3M",
        "Perf.YTD",
        "Perf.Y",
        "RSI",
        "RSI7",
        "MACD.macd",
        "MACD.signal",
        "Mom",
        "ROC",
        "Recommend.All",
        "Recommend.MA",
        "Recommend.Other",
        "VWAP",
        "VWMA",
        "SMA50",
        "SMA200",
        "EMA50",
        "EMA200",
        "earnings_release_date",
        "earnings_release_next_date",
        "market",
    ],
    "sort": {"sortBy": "relative_volume_10d_calc", "sortOrder": "desc"},
    "filter": [{"left": "is_primary", "operation": "equal", "right": True}],
    "ignore_unknown_fields": False,
    "options": {"lang": "en"},
    "symbols": {},
    "markets": TRADING_VIEW_ALL_MARKETS_ARRAY,
    "filter2": {
        "operator": "and",
        "operands": [
            {
                "operation": {
                    "operator": "or",
                    "operands": [
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "stock",
                                        }
                                    },
                                    {
                                        "expression": {
                                            "left": "typespecs",
                                            "operation": "has",
                                            "right": ["common"],
                                        }
                                    },
                                ],
                            }
                        },
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "stock",
                                        }
                                    },
                                    {
                                        "expression": {
                                            "left": "typespecs",
                                            "operation": "has",
                                            "right": ["preferred"],
                                        }
                                    },
                                ],
                            }
                        },
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "dr",
                                        }
                                    }
                                ],
                            }
                        },
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "fund",
                                        }
                                    },
                                    {
                                        "expression": {
                                            "left": "typespecs",
                                            "operation": "has_none_of",
                                            "right": ["etf"],
                                        }
                                    },
                                ],
                            }
                        },
                    ],
                }
            },
            {
                "expression": {
                    "left": "typespecs",
                    "operation": "has_none_of",
                    "right": ["pre-ipo"],
                }
            },
        ],
    },
}

GLOBAL_MARKET_SAFETY_CORE_BASE_PAYLOAD = {
    "columns": [
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
        "price_earnings_ttm",
        "price_free_cash_flow_ttm",
        "price_book_fq",
        "price_revenue_ttm",
        "short_term_debt_fy",
        "cash_n_equivalents_fy",
    ],
    "filter": [{"left": "is_primary", "operation": "equal", "right": True}],
    "ignore_unknown_fields": False,
    "options": {"lang": "en"},
    "price_conversion": {"to_currency": "usd"},
    "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
    "symbols": {},
    "markets": TRADING_VIEW_ALL_MARKETS_ARRAY,
    "filter2": {
        "operator": "and",
        "operands": [
            {
                "operation": {
                    "operator": "or",
                    "operands": [
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "stock",
                                        }
                                    },
                                    {
                                        "expression": {
                                            "left": "typespecs",
                                            "operation": "has",
                                            "right": ["common"],
                                        }
                                    },
                                ],
                            }
                        },
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "stock",
                                        }
                                    },
                                    {
                                        "expression": {
                                            "left": "typespecs",
                                            "operation": "has",
                                            "right": ["preferred"],
                                        }
                                    },
                                ],
                            }
                        },
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "dr",
                                        }
                                    }
                                ],
                            }
                        },
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "fund",
                                        }
                                    },
                                    {
                                        "expression": {
                                            "left": "typespecs",
                                            "operation": "has_none_of",
                                            "right": ["etf"],
                                        }
                                    },
                                ],
                            }
                        },
                    ],
                }
            },
            {
                "expression": {
                    "left": "typespecs",
                    "operation": "has_none_of",
                    "right": ["pre-ipo"],
                }
            },
        ],
    },
}

GLOBAL_MARKET_MOVE_PREDICTION_BASE_PAYLOAD = {
    "columns": [
        "name",
        "ticker-view",
        "close",
        "exchange",
        "country",
        "sector",
        "industry",
        "market",
        "type",
        "typespecs",
        "market_cap_basic",
        "float_shares_outstanding",
        "float_shares_percent_current",
        "volume",
        "average_volume_10d_calc",
        "relative_volume_10d_calc",
        "Value.Traded",
        "AvgValue.Traded_10d",
        "ADR",
        "ATR",
        "ATRP",
        "Volatility.D",
        "Volatility.W",
        "Volatility.M",
        "beta_1_year",
        "change",
        "gap",
        "premarket_gap",
        "premarket_change",
        "premarket_volume",
        "postmarket_change",
        "postmarket_volume",
        "Perf.5D",
        "Perf.W",
        "Perf.1M",
        "Perf.3M",
        "Perf.6M",
        "Perf.YTD",
        "Perf.Y",
        "Perf.5Y",
        "RSI",
        "RSI7",
        "MACD.macd",
        "MACD.signal",
        "Mom",
        "ROC",
        "Recommend.All",
        "Recommend.MA",
        "Recommend.Other",
        "VWAP",
        "VWMA",
        "SMA50",
        "SMA200",
        "EMA50",
        "EMA200",
        "earnings_release_date",
        "earnings_release_next_date",
        "current_ratio",
        "quick_ratio",
        "cash_ratio",
        "cash_n_short_term_invest_fy",
        "cash_n_short_term_invest_fq",
        "short_term_debt_fy",
        "short_term_debt_fq",
        "total_debt",
        "net_debt",
        "debt_to_equity",
        "debt_to_revenue_ttm",
        "altman_z_score_ttm",
        "gross_margin",
        "operating_margin",
        "after_tax_margin",
        "return_on_assets",
        "return_on_equity",
        "return_on_invested_capital",
        "total_revenue_yoy_growth_ttm",
        "total_revenue_qoq_growth_fq",
        "ebitda_yoy_growth_ttm",
        "ebitda_qoq_growth_fq",
        "net_income_yoy_growth_ttm",
        "net_income_qoq_growth_fq",
        "free_cash_flow_yoy_growth_ttm",
        "free_cash_flow_qoq_growth_fq",
        "price_earnings_ttm",
        "price_earnings_growth_ttm",
        "price_sales_current",
        "price_book_fq",
        "price_free_cash_flow_ttm",
        "price_to_cash_f_operating_activities_ttm",
        "enterprise_value_to_revenue_ttm",
        "enterprise_value_to_ebit_ttm",
        "enterprise_value_ebitda_ttm",
        "price_52_week_high",
        "price_52_week_low",
        "High.6M",
        "Low.6M",
        "earnings_yield",
        "free_cash_flow_margin_ttm",
        "buyback_yield",
        "dividends_yield_current",
        "Aroon.Up",
        "Aroon.Down",
        "ADX",
        "ADX+DI",
        "ADX-DI",
        "BB.upper",
        "BB.lower",
        "total_revenue",
        "enterprise_value_fq",
        "number_of_employees",
        "SMA10",
        "SMA20",
        "SMA30",
        "EMA10",
        "EMA20",
        "EMA30",
        "High.1M",
        "Low.1M",
        "High.3M",
        "Low.3M",
        "Stoch.RSI.K",
        "CCI20",
        "earnings_per_share_forecast_next_fq",
        "earnings_per_share_fq",
        "eps_surprise_percent_fq",
        "earnings_per_share_forecast_next_fy",
        "average_volume_30d_calc",
        "Stoch.RSI.D",
        "change_from_open",
        "earnings_per_share_diluted_yoy_growth_ttm",
        "ebitda",
        "net_income",
        "Pivot.M.Classic.Middle",
        "gross_profit_margin_fy",
        # Analyst-consensus targets (street view): used by the targets module
        # as an institutional-grade lens that mirrors sell-side fair value.
        "price_target_average",
        "price_target_median",
        "price_target_high",
        "price_target_low",
        "price_target_1y",
        "AnalystRating",
        # Book-value / Graham-style anchor inputs.
        "book_value_per_share_fq",
        # Fundamental-quality anchors (sustainable growth + value-trap filters).
        "sustainable_growth_rate_ttm",
        "piotroski_f_score_ttm",
        # Yield / dividend-discount inputs.
        "dividend_yield_recent",
        "dividends_per_share_fq",
        "dps_common_stock_prim_issue_yoy_growth_fy",
        # Additional valuation multiples (broaden the multiple-anchored lens).
        "enterprise_value_to_free_cash_flow_ttm",
        "enterprise_value_to_gross_profit_ttm",
        # Leverage quality (modulates trajectory + bear bands).
        "total_debt_to_ebitda_fq",
        # Durable value / quality-value investing inputs.
        "price_earnings_forward_fy",
        "graham_numbers_ttm",
        "graham_numbers_fy",
        "book_tangible_per_share_current",
        "book_tangible_per_share_fq",
        "cash_per_share_current",
        "cash_per_share_fq",
        "free_cash_flow_ttm",
        "cash_f_operating_activities_ttm",
        "operating_cash_flow_per_share_ttm",
        "free_cash_flow_per_share_ttm",
        "ebit_ttm",
        "gross_profit_ttm",
        "enterprise_value_current",
        "total_revenue_cagr_5y",
        "free_cash_flow_cagr_5y",
        "net_income_cagr_5y",
        "earnings_per_share_basic_cagr_5y",
        "earnings_per_share_diluted_5y_growth_fy",
        "gross_margin_ttm",
        "operating_margin_ttm",
        "net_margin_ttm",
        "ebitda_margin_ttm",
        "asset_turnover_current",
        "return_on_capital_employed_fy",
        "return_on_common_equity_ttm",
        "return_on_tang_equity_fy",
        "sloan_ratio_ttm",
        "interst_cover_ttm",
        "ebitda_interst_cover_ttm",
        "cash_dividend_coverage_ratio_ttm",
        "cash_n_short_term_invest_to_total_debt_fq",
        "cash_n_short_term_invest_to_total_debt_fy",
        "cash_n_short_term_invest_to_total_current_liabilities_fq",
        "cash_n_short_term_invest_to_total_current_liabilities_fy",
        "total_debt_to_ebitda_fy",
        "net_debt_to_ebitda_fq",
        "net_debt_to_ebitda_fy",
        "dividend_payout_ratio_ttm",
        "ncavps_ratio_current",
        "ncavps_ratio_fq",
        "zmijewski_score_ttm",
        # Camarilla pivots — additional technical anchors (support/resistance).
        "Pivot.M.Camarilla.S1",
        "Pivot.M.Camarilla.S2",
        "Pivot.M.Camarilla.R1",
        "Pivot.M.Camarilla.R2",
        # Tier-1 confirmation signals (June 2026 active-manager update).
        "DonchCh20.Upper",
        "DonchCh20.Lower",
        "P.SAR",
        "HullMA9",
        "ChaikinMoneyFlow",
        "BBPower",
        "Recommend.All|1W",
        "Recommend.MA|1M",
        "W.R",
    ],
    "sort": {"sortBy": "relative_volume_10d_calc", "sortOrder": "desc"},
    "filter": [{"left": "is_primary", "operation": "equal", "right": True}],
    "ignore_unknown_fields": True,
    "options": {"lang": "en"},
    "price_conversion": {"to_currency": "usd"},
    "symbols": {},
    "markets": TRADING_VIEW_ALL_MARKETS_ARRAY,
    "filter2": {
        "operator": "and",
        "operands": [
            {
                "operation": {
                    "operator": "or",
                    "operands": [
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "stock",
                                        }
                                    },
                                    {
                                        "expression": {
                                            "left": "typespecs",
                                            "operation": "has",
                                            "right": ["common"],
                                        }
                                    },
                                ],
                            }
                        },
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "stock",
                                        }
                                    },
                                    {
                                        "expression": {
                                            "left": "typespecs",
                                            "operation": "has",
                                            "right": ["preferred"],
                                        }
                                    },
                                ],
                            }
                        },
                        {
                            "operation": {
                                "operator": "and",
                                "operands": [
                                    {
                                        "expression": {
                                            "left": "type",
                                            "operation": "equal",
                                            "right": "dr",
                                        }
                                    }
                                ],
                            }
                        },
                    ],
                }
            },
            {
                "expression": {
                    "left": "typespecs",
                    "operation": "has_none_of",
                    "right": ["pre-ipo"],
                }
            },
        ],
    },
}


# Keep these premarket context fields explicitly enforced for every
# move-prediction scan variant. Even if payload variants drift, the runtime
# request builder will append them so downstream suites can rely on them.
MOVE_PREDICTION_REQUIRED_CONTEXT_COLUMNS = [
    "premarket_change",
    "premarket_gap",
]


# Additional earnings calendar columns layered on top of the move-prediction
# payload. The base payload already includes ``earnings_release_date`` and
# ``earnings_release_next_date``; the fields below provide the rest of the
# upcoming-earnings catalyst metadata (calendar dates, intraday timing, and
# trading-day mapped variants for the next FQ / FY release).
MOVE_PREDICTION_EARNINGS_EXTRA_COLUMNS = [
    "earnings_release_calendar_date",
    "earnings_release_next_calendar_date",
    "earnings_release_next_time",
    "earnings_release_next_trading_date_fq",
    "earnings_release_next_trading_date_fy",
    "earnings_release_time",
]

GLOBAL_MARKET_MOVE_PREDICTION_WITH_EARNINGS_BASE_PAYLOAD = deepcopy(
    GLOBAL_MARKET_MOVE_PREDICTION_BASE_PAYLOAD
)
GLOBAL_MARKET_MOVE_PREDICTION_WITH_EARNINGS_BASE_PAYLOAD["columns"] = list(
    GLOBAL_MARKET_MOVE_PREDICTION_BASE_PAYLOAD["columns"]
) + [
    column
    for column in MOVE_PREDICTION_EARNINGS_EXTRA_COLUMNS
    if column not in GLOBAL_MARKET_MOVE_PREDICTION_BASE_PAYLOAD["columns"]
]


# Global ETF screener: same payload shell as the stock move-prediction scan,
# but the universe is world primary listings (``is_primary``) of
# ``type=fund`` + ``typespecs has etf``. Columns were probed against the
# ETF screener (2026-08-16). Stock fundamental / earnings / valuation fields
# that returned no values on that probe are omitted entirely rather than
# requested as nulls.
AMERICA_ETF_FILTER2 = {
    "operator": "and",
    "operands": [
        {
            "operation": {
                "operator": "or",
                "operands": [
                    {
                        "operation": {
                            "operator": "and",
                            "operands": [
                                {
                                    "expression": {
                                        "left": "type",
                                        "operation": "equal",
                                        "right": "fund",
                                    }
                                },
                                {
                                    "expression": {
                                        "left": "typespecs",
                                        "operation": "has",
                                        "right": ["etf"],
                                    }
                                },
                            ],
                        }
                    }
                ],
            }
        }
    ],
}

AMERICA_ETF_MOVE_PREDICTION_COLUMNS = [
    "name",
    "ticker-view",
    "description",
    "close",
    "exchange",
    "country",
    "sector",
    "sector.tr",
    "industry",
    "industry.tr",
    "market",
    "type",
    "typespecs",
    "aum",
    "nav",
    "nav_discount_premium",
    "expense_ratio",
    "etf_holdings_count",
    "asset_class",
    "asset_class.tr",
    "category",
    "category.tr",
    "focus",
    "focus.tr",
    "niche",
    "niche.tr",
    "brand",
    "holdings_region",
    "country_code_fund",
    "etf_fund_currency",
    "volume",
    "average_volume_10d_calc",
    "relative_volume_10d_calc",
    "Value.Traded",
    "AvgValue.Traded_10d",
    "ADR",
    "ATR",
    "ATRP",
    "Volatility.D",
    "Volatility.W",
    "Volatility.M",
    "beta_1_year",
    "change",
    "gap",
    "premarket_gap",
    "premarket_change",
    "premarket_volume",
    "postmarket_change",
    "postmarket_volume",
    "Perf.5D",
    "Perf.W",
    "Perf.1M",
    "Perf.3M",
    "Perf.6M",
    "Perf.YTD",
    "Perf.Y",
    "Perf.3Y",
    "Perf.5Y",
    "Perf.10Y",
    "Perf.All",
    "RSI",
    "RSI7",
    "MACD.macd",
    "MACD.signal",
    "Mom",
    "ROC",
    "Recommend.All",
    "Recommend.MA",
    "Recommend.Other",
    "VWAP",
    "VWMA",
    "SMA50",
    "SMA200",
    "EMA50",
    "EMA200",
    "price_52_week_high",
    "price_52_week_low",
    "High.6M",
    "Low.6M",
    "Aroon.Up",
    "Aroon.Down",
    "ADX",
    "ADX+DI",
    "ADX-DI",
    "BB.upper",
    "BB.lower",
    "SMA10",
    "SMA20",
    "SMA30",
    "EMA10",
    "EMA20",
    "EMA30",
    "High.1M",
    "Low.1M",
    "High.3M",
    "Low.3M",
    "Stoch.RSI.K",
    "CCI20",
    "average_volume_30d_calc",
    "Stoch.RSI.D",
    "change_from_open",
    "Pivot.M.Classic.Middle",
    "AnalystRating",
    "dividend_yield_recent",
    "Pivot.M.Camarilla.S1",
    "Pivot.M.Camarilla.S2",
    "Pivot.M.Camarilla.R1",
    "Pivot.M.Camarilla.R2",
    "DonchCh20.Upper",
    "DonchCh20.Lower",
    "P.SAR",
    "HullMA9",
    "ChaikinMoneyFlow",
    "BBPower",
    "Recommend.All|1W",
    "Recommend.MA|1M",
    "W.R",
    "aum_perf.1M",
    "aum_perf.3M",
    "aum_perf.YTD",
    "aum_perf.1Y",
    "aum_perf.3Y",
    "aum_perf.5Y",
    "fund_flows.1M",
    "fund_flows.3M",
    "fund_flows.YTD",
    "fund_flows.1Y",
    "fund_flows.3Y",
    "fund_flows.5Y",
    "nav_perf.1M",
    "nav_perf.3M",
    "nav_perf.YTD",
    "nav_perf.1Y",
    "nav_perf.3Y",
    "nav_perf.5Y",
    "nav_total_return.1M",
    "nav_total_return.3M",
    "nav_total_return.6M",
    "nav_total_return.YTD",
    "nav_total_return.1Y",
    "nav_total_return.3Y",
    "nav_total_return.5Y",
]

AMERICA_ETF_MOVE_PREDICTION_BASE_PAYLOAD = {
    "columns": list(AMERICA_ETF_MOVE_PREDICTION_COLUMNS),
    "sort": {"sortBy": "aum", "sortOrder": "desc"},
    "filter": [{"left": "is_primary", "operation": "equal", "right": True}],
    "ignore_unknown_fields": True,
    "options": {"lang": "en"},
    "price_conversion": {"to_currency": "usd"},
    "symbols": {},
    "markets": list(TRADING_VIEW_ALL_MARKETS_ARRAY),
    "filter2": deepcopy(AMERICA_ETF_FILTER2),
}

# Stock move-prediction columns that returned no values on the ETF screener
# probe. Kept as an explicit denylist so they are not reintroduced later.
AMERICA_ETF_EMPTY_STOCK_COLUMNS = [
    "market_cap_basic",
    "float_shares_outstanding",
    "float_shares_percent_current",
    "earnings_release_date",
    "earnings_release_next_date",
    "current_ratio",
    "quick_ratio",
    "cash_ratio",
    "cash_n_short_term_invest_fy",
    "cash_n_short_term_invest_fq",
    "short_term_debt_fy",
    "short_term_debt_fq",
    "total_debt",
    "net_debt",
    "debt_to_equity",
    "debt_to_revenue_ttm",
    "altman_z_score_ttm",
    "gross_margin",
    "operating_margin",
    "after_tax_margin",
    "return_on_assets",
    "return_on_equity",
    "return_on_invested_capital",
    "total_revenue_yoy_growth_ttm",
    "total_revenue_qoq_growth_fq",
    "ebitda_yoy_growth_ttm",
    "ebitda_qoq_growth_fq",
    "net_income_yoy_growth_ttm",
    "net_income_qoq_growth_fq",
    "free_cash_flow_yoy_growth_ttm",
    "free_cash_flow_qoq_growth_fq",
    "price_earnings_ttm",
    "price_earnings_growth_ttm",
    "price_sales_current",
    "price_book_fq",
    "price_free_cash_flow_ttm",
    "price_to_cash_f_operating_activities_ttm",
    "enterprise_value_to_revenue_ttm",
    "enterprise_value_to_ebit_ttm",
    "enterprise_value_ebitda_ttm",
    "earnings_yield",
    "free_cash_flow_margin_ttm",
    "buyback_yield",
    "dividends_yield_current",
    "total_revenue",
    "enterprise_value_fq",
    "number_of_employees",
    "earnings_per_share_forecast_next_fq",
    "earnings_per_share_fq",
    "eps_surprise_percent_fq",
    "earnings_per_share_forecast_next_fy",
    "earnings_per_share_diluted_yoy_growth_ttm",
    "ebitda",
    "net_income",
    "gross_profit_margin_fy",
    "price_target_average",
    "price_target_median",
    "price_target_high",
    "price_target_low",
    "price_target_1y",
    "book_value_per_share_fq",
    "sustainable_growth_rate_ttm",
    "piotroski_f_score_ttm",
    "dividends_per_share_fq",
    "dps_common_stock_prim_issue_yoy_growth_fy",
    "enterprise_value_to_free_cash_flow_ttm",
    "enterprise_value_to_gross_profit_ttm",
    "total_debt_to_ebitda_fq",
    "price_earnings_forward_fy",
    "graham_numbers_ttm",
    "graham_numbers_fy",
    "book_tangible_per_share_current",
    "book_tangible_per_share_fq",
    "cash_per_share_current",
    "cash_per_share_fq",
    "free_cash_flow_ttm",
    "cash_f_operating_activities_ttm",
    "operating_cash_flow_per_share_ttm",
    "free_cash_flow_per_share_ttm",
    "ebit_ttm",
    "gross_profit_ttm",
    "enterprise_value_current",
    "total_revenue_cagr_5y",
    "free_cash_flow_cagr_5y",
    "net_income_cagr_5y",
    "earnings_per_share_basic_cagr_5y",
    "earnings_per_share_diluted_5y_growth_fy",
    "gross_margin_ttm",
    "operating_margin_ttm",
    "net_margin_ttm",
    "ebitda_margin_ttm",
    "asset_turnover_current",
    "return_on_capital_employed_fy",
    "return_on_common_equity_ttm",
    "return_on_tang_equity_fy",
    "sloan_ratio_ttm",
    "interst_cover_ttm",
    "ebitda_interst_cover_ttm",
    "cash_dividend_coverage_ratio_ttm",
    "cash_n_short_term_invest_to_total_debt_fq",
    "cash_n_short_term_invest_to_total_debt_fy",
    "cash_n_short_term_invest_to_total_current_liabilities_fq",
    "cash_n_short_term_invest_to_total_current_liabilities_fy",
    "total_debt_to_ebitda_fy",
    "net_debt_to_ebitda_fq",
    "net_debt_to_ebitda_fy",
    "dividend_payout_ratio_ttm",
    "ncavps_ratio_current",
    "ncavps_ratio_fq",
    "zmijewski_score_ttm",
]


class ApiTradingViewClient:
    """Client for interacting with the TradingView API."""

    def __init__(self, user_agent):
        """Initialize the API client with a user agent and headers."""
        self.headers = {
            "User-Agent": user_agent,
            "Accept": "application/json",
            "Content-Type": "text/plain;charset=UTF-8",
        }

    @staticmethod
    def _map_scan_row(scan_row: dict[str, Any], columns: list[str]) -> dict[str, Any]:
        values = scan_row.get("d", [])
        mapped_data = {
            column: values[index] if index < len(values) else None
            for index, column in enumerate(columns)
            if column != "symbol"
        }
        # Always prefer the scanner's ticker (`s`). A requested column named
        # "symbol" is not a TradingView field; mapping it by index would
        # overwrite every ticker with null and collapse downstream merges.
        return {
            **mapped_data,
            "symbol": scan_row.get("s"),
        }

    @staticmethod
    def _attach_mapped_rows(
        response_payload: dict[str, Any], columns: list[str]
    ) -> dict[str, Any]:
        mapped_rows = [
            ApiTradingViewClient._map_scan_row(scan_row, columns)
            for scan_row in response_payload.get("data", [])
        ]
        response_payload["raw_data"] = response_payload.get("data", [])
        response_payload["data"] = mapped_rows
        response_payload["rows"] = mapped_rows
        return response_payload

    @staticmethod
    def _apply_markets_override(
        request_payload: dict[str, Any], markets: list[str] | None
    ) -> None:
        if markets is not None:
            request_payload["markets"] = list(markets)

    @staticmethod
    def _ensure_request_columns(
        request_payload: dict[str, Any], required_columns: list[str]
    ) -> None:
        columns = request_payload.get("columns")
        if not isinstance(columns, list):
            request_payload["columns"] = list(required_columns)
            return
        for column in required_columns:
            if column not in columns:
                columns.append(column)

    @staticmethod
    def _attach_request_metadata(
        response_payload: dict[str, Any],
        request_payload: dict[str, Any],
        request_url: str,
        timeout: int,
    ) -> dict[str, Any]:
        response_payload["request_payload"] = deepcopy(request_payload)
        response_payload["request_metadata"] = {
            "url": request_url,
            "timeout_seconds": timeout,
            "markets": list(request_payload.get("markets", [])),
            "columns": list(request_payload.get("columns", [])),
            "sort": deepcopy(request_payload.get("sort")),
            "filter": deepcopy(request_payload.get("filter", [])),
            "filter2": deepcopy(request_payload.get("filter2")),
            "range": deepcopy(request_payload.get("range")),
        }
        return response_payload

    def scan_main_america_market(
        self,
        timeout: int = 30,
        markets: list[str] | None = None,
        include_mapped_rows: bool = True,
    ) -> dict[str, Any]:
        """
        Execute a TradingView America screener scan.

        By default this uses the built-in payload for the stock screener. Pass a
        custom payload when you need a different column set, filter, or range.
        """
        request_payload = deepcopy(USA_MAIN_LISTING_FOR_INDUSTRIES_PARSE_PAYLOAD)
        self._apply_markets_override(request_payload, markets)
        response = requests.post(
            TRADINGVIEW_AMERICA_SCAN_URL,
            headers=self.headers,
            data=json.dumps(request_payload),
            timeout=timeout,
        )
        response.raise_for_status()

        response_payload = response.json()
        if not include_mapped_rows:
            return response_payload

        columns = request_payload.get("columns", [])
        return self._attach_mapped_rows(response_payload, columns)

    def scan_world_market_all_priceperf_metrics(
        self,
        timeout: int = 30,
        industries: list[str] | None = None,
        markets: list[str] | None = None,
        include_mapped_rows: bool = True,
    ) -> dict[str, Any]:
        """
        Execute a TradingView global market screener scan for all price performance metrics.

        By default this uses the built-in payload for the stock screener. Pass a
        custom payload when you need a different column set, filter, or range.
        """
        request_payload = deepcopy(USA_GLOBAL_MARKET_PRICEPERF_METRICS_PAYLOAD)
        self._apply_markets_override(request_payload, markets)

        # Add industry filter
        if industries:
            request_payload["filter"].append(
                {"left": "industry", "operation": "in_range", "right": industries}
            )

        response = requests.post(
            TRADINGVIEW_GLOBAL_SCAN_URL,
            headers=self.headers,
            data=json.dumps(request_payload),
            timeout=timeout,
        )
        response.raise_for_status()

        response_payload = response.json()
        if not include_mapped_rows:
            return response_payload

        columns = request_payload.get("columns", [])
        return self._attach_mapped_rows(response_payload, columns)

    def scan_global_market_activity_float_attention(
        self,
        timeout: int = 30,
        industries: list[str] | None = None,
        markets: list[str] | None = None,
        min_market_cap_usd: float | None = None,
        max_market_cap_usd: float | None = None,
        include_mapped_rows: bool = True,
    ) -> dict[str, Any]:
        """
        Execute a TradingView global market screener scan for activity, float, and attention metrics.

        Args:
            timeout: Request timeout in seconds
            industries: Optional list of industries to filter by
            markets: Optional list of markets to override the payload markets list
            min_market_cap_usd: Optional inclusive minimum market capitalization
            max_market_cap_usd: Optional inclusive maximum market capitalization
            include_mapped_rows: Whether to map raw scan rows to a friendlier format

        Returns:
            Response payload with optional mapped rows
        """
        request_payload = deepcopy(GLOBAL_MARKET_ACTIVITY_FLOAT_ATTENTION_BASE_PAYLOAD)
        self._apply_markets_override(request_payload, markets)

        if min_market_cap_usd is not None:
            request_payload["filter"].append(
                {
                    "left": "market_cap_basic",
                    "operation": "egreater",
                    "right": min_market_cap_usd,
                }
            )

        if max_market_cap_usd is not None:
            request_payload["filter"].append(
                {
                    "left": "market_cap_basic",
                    "operation": "eless",
                    "right": max_market_cap_usd,
                }
            )

        if industries:
            request_payload["filter"].append(
                {"left": "industry", "operation": "in_range", "right": industries}
            )

        response = requests.post(
            TRADINGVIEW_GLOBAL_SCAN_URL,
            headers=self.headers,
            data=json.dumps(request_payload),
            timeout=timeout,
        )
        response.raise_for_status()

        response_payload = response.json()
        if not include_mapped_rows:
            return response_payload

        columns = request_payload.get("columns", [])
        return self._attach_mapped_rows(response_payload, columns)

    def scan_listed_america_market(
        self,
        timeout: int = 30,
        markets: list[str] | None = None,
        include_mapped_rows: bool = True,
    ) -> dict[str, Any]:
        """
        Execute a TradingView America screener scan.

        By default this uses the built-in payload for the stock screener. Pass a
        custom payload when you need a different column set, filter, or range.
        """
        request_payload = deepcopy(USA_ALL_LISTING_FOR_INDUSTRIES_PARSE_PAYLOAD)
        self._apply_markets_override(request_payload, markets)
        response = requests.post(
            TRADINGVIEW_AMERICA_SCAN_URL,
            headers=self.headers,
            data=json.dumps(request_payload),
            timeout=timeout,
        )
        response.raise_for_status()

        response_payload = response.json()
        if not include_mapped_rows:
            return response_payload

        columns = request_payload.get("columns", [])
        return self._attach_mapped_rows(response_payload, columns)

    def scan_global_market_safety_core(
        self,
        timeout: int = 30,
        industries: list[str] | None = None,
        markets: list[str] | None = None,
        min_market_cap_usd: float | None = None,
        max_market_cap_usd: float | None = None,
        ticker_filter: str | None = None,
        include_mapped_rows: bool = True,
    ) -> dict[str, Any]:
        """
        Execute a TradingView global market screener scan for safety-core metrics.

        Args:
            timeout: Request timeout in seconds
            industries: Optional list of industry names to filter by
            markets: Optional list of markets to override the payload markets list
            min_market_cap_usd: Optional inclusive minimum market capitalization
            max_market_cap_usd: Optional inclusive maximum market capitalization
            include_mapped_rows: Whether to map raw scan rows to a friendlier format

        Returns:
            Response payload with optional mapped rows
        """
        request_payload = deepcopy(GLOBAL_MARKET_SAFETY_CORE_BASE_PAYLOAD)
        self._apply_markets_override(request_payload, markets)

        if min_market_cap_usd is not None:
            request_payload["filter"].append(
                {
                    "left": "market_cap_basic",
                    "operation": "egreater",
                    "right": min_market_cap_usd,
                }
            )

        if max_market_cap_usd is not None:
            request_payload["filter"].append(
                {
                    "left": "market_cap_basic",
                    "operation": "eless",
                    "right": max_market_cap_usd,
                }
            )

        if industries:
            request_payload["filter"].append(
                {"left": "industry", "operation": "in_range", "right": industries}
            )

        if ticker_filter:
            request_payload["filter"].append(
                {
                    "left": "ticker-view-filter",
                    "operation": "match",
                    "right": ticker_filter,
                }
            )
            request_payload["range"] = [0, 5]

        response = requests.post(
            TRADINGVIEW_GLOBAL_SCAN_URL,
            headers=self.headers,
            data=json.dumps(request_payload),
            timeout=timeout,
        )
        response.raise_for_status()

        response_payload = response.json()
        if not include_mapped_rows:
            return response_payload

        columns = request_payload.get("columns", [])
        return self._attach_mapped_rows(response_payload, columns)

    def scan_global_market_move_prediction(
        self,
        timeout: int = 30,
        industries: list[str] | None = None,
        markets: list[str] | None = None,
        min_market_cap_usd: float | None = None,
        max_market_cap_usd: float | None = None,
        ticker_filter: str | None = None,
        include_mapped_rows: bool = True,
    ) -> dict[str, Any]:
        """
        Execute a TradingView global screener scan for the move-prediction feature set.

        This payload combines the strongest fields from activity, price-performance,
        valuation, and safety-style scans so one response can power multi-horizon
        directional analysis.
        """
        return self._run_move_prediction_scan(
            base_payload=GLOBAL_MARKET_MOVE_PREDICTION_BASE_PAYLOAD,
            timeout=timeout,
            industries=industries,
            markets=markets,
            min_market_cap_usd=min_market_cap_usd,
            max_market_cap_usd=max_market_cap_usd,
            ticker_filter=ticker_filter,
            include_mapped_rows=include_mapped_rows,
        )

    def scan_global_market_move_prediction_with_earnings(
        self,
        timeout: int = 30,
        industries: list[str] | None = None,
        markets: list[str] | None = None,
        min_market_cap_usd: float | None = None,
        max_market_cap_usd: float | None = None,
        ticker_filter: str | None = None,
        include_mapped_rows: bool = True,
    ) -> dict[str, Any]:
        """
        Execute the move-prediction scan enriched with the full set of earnings
        calendar columns.

        Identical to ``scan_global_market_move_prediction`` but the underlying
        payload includes the additional fields ``earnings_release_calendar_date``,
        ``earnings_release_next_calendar_date``, ``earnings_release_next_time``,
        ``earnings_release_next_trading_date_fq``,
        ``earnings_release_next_trading_date_fy`` and ``earnings_release_time``.
        Use this when downstream analysis needs to rank, filter, or sort by
        upcoming earnings catalysts.
        """
        return self._run_move_prediction_scan(
            base_payload=GLOBAL_MARKET_MOVE_PREDICTION_WITH_EARNINGS_BASE_PAYLOAD,
            timeout=timeout,
            industries=industries,
            markets=markets,
            min_market_cap_usd=min_market_cap_usd,
            max_market_cap_usd=max_market_cap_usd,
            ticker_filter=ticker_filter,
            include_mapped_rows=include_mapped_rows,
        )

    def _run_move_prediction_scan(
        self,
        base_payload: dict[str, Any],
        timeout: int,
        industries: list[str] | None,
        markets: list[str] | None,
        min_market_cap_usd: float | None,
        max_market_cap_usd: float | None,
        ticker_filter: str | None,
        include_mapped_rows: bool,
    ) -> dict[str, Any]:
        """Shared executor for the move-prediction scan variants.

        Centralizes filter assembly and request handling so the regular and
        earnings-enriched variants stay behaviorally identical apart from the
        column set defined in their base payload.
        """
        request_payload = deepcopy(base_payload)
        self._apply_markets_override(request_payload, markets)
        self._ensure_request_columns(
            request_payload, MOVE_PREDICTION_REQUIRED_CONTEXT_COLUMNS
        )

        if min_market_cap_usd is not None:
            request_payload["filter"].append(
                {
                    "left": "market_cap_basic",
                    "operation": "egreater",
                    "right": min_market_cap_usd,
                }
            )

        if max_market_cap_usd is not None:
            request_payload["filter"].append(
                {
                    "left": "market_cap_basic",
                    "operation": "eless",
                    "right": max_market_cap_usd,
                }
            )

        if industries:
            request_payload["filter"].append(
                {"left": "industry", "operation": "in_range", "right": industries}
            )

        if ticker_filter:
            request_payload["filter"].append(
                {
                    "left": "ticker-view-filter",
                    "operation": "match",
                    "right": ticker_filter,
                }
            )
            request_payload["range"] = [0, 5]

        response = requests.post(
            TRADINGVIEW_GLOBAL_SCAN_URL,
            headers=self.headers,
            data=json.dumps(request_payload),
            timeout=timeout,
        )
        response.raise_for_status()

        response_payload = self._attach_request_metadata(
            response.json(),
            request_payload=request_payload,
            request_url=TRADINGVIEW_GLOBAL_SCAN_URL,
            timeout=timeout,
        )
        if not include_mapped_rows:
            return response_payload

        columns = request_payload.get("columns", [])
        return self._attach_mapped_rows(response_payload, columns)

    def scan_global_market_by_industry(
        self,
        industries: list[str] | None = None,
        markets: list[str] | None = None,
        min_market_cap_usd: float | None = None,
        max_market_cap_usd: float | None = None,
        timeout: int = 30,
        include_mapped_rows: bool = True,
    ) -> dict[str, Any]:
        """
        Execute a TradingView global screener scan filtered by industries.

        Args:
            industries: List of industry names to filter by (e.g., ["Oil & Gas Pipelines"])
            markets: Optional list of markets to override the payload markets list
            timeout: Request timeout in seconds
            include_mapped_rows: Whether to map raw scan rows to a friendlier format

        Returns:
            Response payload with optional mapped rows
        """
        request_payload = deepcopy(GLOBAL_MARKET_INDUSTRIES_VALUATION_PAYLOAD)
        self._apply_markets_override(request_payload, markets)

        if min_market_cap_usd is not None:
            request_payload["filter"].append(
                {
                    "left": "market_cap_basic",
                    "operation": "egreater",
                    "right": min_market_cap_usd,
                }
            )

        if max_market_cap_usd is not None:
            request_payload["filter"].append(
                {
                    "left": "market_cap_basic",
                    "operation": "eless",
                    "right": max_market_cap_usd,
                }
            )

        # Add industry filter
        if industries:
            request_payload["filter"].append(
                {"left": "industry", "operation": "in_range", "right": industries}
            )

        response = requests.post(
            TRADINGVIEW_GLOBAL_SCAN_URL,
            headers=self.headers,
            data=json.dumps(request_payload),
            timeout=timeout,
        )
        response.raise_for_status()

        response_payload = response.json()
        if not include_mapped_rows:
            return response_payload

        columns = request_payload.get("columns", [])
        return self._attach_mapped_rows(response_payload, columns)

    def scan_global_etf_move_prediction(
        self,
        timeout: int = 90,
        industries: list[str] | None = None,
        categories: list[str] | None = None,
        markets: list[str] | None = None,
        min_aum_usd: float | None = None,
        max_aum_usd: float | None = None,
        min_market_cap_usd: float | None = None,
        max_market_cap_usd: float | None = None,
        ticker_filter: str | None = None,
        include_mapped_rows: bool = True,
    ) -> dict[str, Any]:
        """Scan the global ETF screener for world primary listings.

        Uses ``TRADINGVIEW_GLOBAL_ETF_SCAN_URL`` with ``is_primary = true`` and
        all markets by default. Size filters apply to ``aum``.
        ``min_market_cap_usd`` / ``max_market_cap_usd`` are aliases for AUM bounds.
        """
        resolved_min_aum = (
            min_aum_usd if min_aum_usd is not None else min_market_cap_usd
        )
        resolved_max_aum = (
            max_aum_usd if max_aum_usd is not None else max_market_cap_usd
        )
        return self._run_etf_scan(
            base_payload=AMERICA_ETF_MOVE_PREDICTION_BASE_PAYLOAD,
            timeout=timeout,
            industries=industries,
            categories=categories,
            markets=markets,
            min_aum_usd=resolved_min_aum,
            max_aum_usd=resolved_max_aum,
            ticker_filter=ticker_filter,
            include_mapped_rows=include_mapped_rows,
        )

    def scan_america_etf_move_prediction(
        self,
        timeout: int = 90,
        industries: list[str] | None = None,
        categories: list[str] | None = None,
        markets: list[str] | None = None,
        min_aum_usd: float | None = None,
        max_aum_usd: float | None = None,
        min_market_cap_usd: float | None = None,
        max_market_cap_usd: float | None = None,
        ticker_filter: str | None = None,
        include_mapped_rows: bool = True,
    ) -> dict[str, Any]:
        """Alias for :meth:`scan_global_etf_move_prediction` (world primary listings)."""
        return self.scan_global_etf_move_prediction(
            timeout=timeout,
            industries=industries,
            categories=categories,
            markets=markets,
            min_aum_usd=min_aum_usd,
            max_aum_usd=max_aum_usd,
            min_market_cap_usd=min_market_cap_usd,
            max_market_cap_usd=max_market_cap_usd,
            ticker_filter=ticker_filter,
            include_mapped_rows=include_mapped_rows,
        )

    def _run_etf_scan(
        self,
        base_payload: dict[str, Any],
        timeout: int,
        industries: list[str] | None,
        categories: list[str] | None,
        markets: list[str] | None,
        min_aum_usd: float | None,
        max_aum_usd: float | None,
        ticker_filter: str | None,
        include_mapped_rows: bool,
    ) -> dict[str, Any]:
        request_payload = deepcopy(base_payload)
        self._apply_markets_override(request_payload, markets)

        if min_aum_usd is not None:
            request_payload["filter"].append(
                {
                    "left": "aum",
                    "operation": "egreater",
                    "right": min_aum_usd,
                }
            )

        if max_aum_usd is not None:
            request_payload["filter"].append(
                {
                    "left": "aum",
                    "operation": "eless",
                    "right": max_aum_usd,
                }
            )

        if industries:
            request_payload["filter"].append(
                {"left": "industry", "operation": "in_range", "right": industries}
            )

        if categories:
            request_payload["filter"].append(
                {"left": "category", "operation": "in_range", "right": categories}
            )

        if ticker_filter:
            request_payload["filter"].append(
                {
                    "left": "ticker-view-filter",
                    "operation": "match",
                    "right": ticker_filter,
                }
            )
            request_payload["range"] = [0, 5]

        response = requests.post(
            TRADINGVIEW_GLOBAL_ETF_SCAN_URL,
            headers=self.headers,
            data=json.dumps(request_payload),
            timeout=timeout,
        )
        response.raise_for_status()

        response_payload = self._attach_request_metadata(
            response.json(),
            request_payload=request_payload,
            request_url=TRADINGVIEW_GLOBAL_ETF_SCAN_URL,
            timeout=timeout,
        )
        if not include_mapped_rows:
            return response_payload

        columns = request_payload.get("columns", [])
        return self._attach_mapped_rows(response_payload, columns)
