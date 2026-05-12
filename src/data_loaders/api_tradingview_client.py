import json
from copy import deepcopy
from typing import Any

import requests

from constants.trading_view_constants import TRADING_VIEW_ALL_MARKETS_ARRAY

TRADINGVIEW_AMERICA_SCAN_URL = (
    "https://scanner.tradingview.com/america/scan?label-product=screener-stock"
)

TRADINGVIEW_GLOBAL_SCAN_URL = (
    "https://scanner.tradingview.com/global/scan?label-product=screener-stock"
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
        # Camarilla pivots — additional technical anchors (support/resistance).
        "Pivot.M.Camarilla.S1",
        "Pivot.M.Camarilla.S2",
        "Pivot.M.Camarilla.R1",
        "Pivot.M.Camarilla.R2",
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
        }
        return {
            "symbol": scan_row.get("s"),
            **mapped_data,
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
