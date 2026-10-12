from portfolio_performance_tracking.benchmark_comparator import (
    fetch_benchmark_price_from_scan,
)


def test_benchmark_price_respects_qualified_exchange():
    rows = [
        {"ticker-view": "ASX:MMS", "close": 2.0},
        {"ticker-view": "NYSE:MMS", "close": 53.0},
    ]
    assert fetch_benchmark_price_from_scan(rows, "NYSE:MMS") == 53.0
    assert fetch_benchmark_price_from_scan(rows, "NASDAQ:MMS") is None
    assert fetch_benchmark_price_from_scan(rows, "MMS") is None


def test_benchmark_price_allows_legacy_bare_fallback():
    rows = [{"ticker-view": "MMS", "close": 53.0}]
    assert fetch_benchmark_price_from_scan(rows, "NYSE:MMS") == 53.0
