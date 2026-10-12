import time

from generic_utils.symbol_identity import (
    SymbolCatalog,
    lookup_mapping,
    parse_symbol,
    resolve_symbol,
    symbol_matches_request,
)


def test_qualified_request_never_matches_another_exchange() -> None:
    assert symbol_matches_request("NYSE:MMS", "NYSE:MMS")
    assert symbol_matches_request("MMS", "NYSE:MMS")
    assert not symbol_matches_request("ASX:MMS", "NYSE:MMS")
    assert not symbol_matches_request("GPW:MMS", "NYSE:MMS")


def test_bare_request_is_allowed_but_must_resolve_uniquely() -> None:
    assert symbol_matches_request("NYSE:MMS", "MMS")
    assert symbol_matches_request("ASX:MMS", "MMS")

    matched, mode, candidates = resolve_symbol(
        ["NYSE:MMS", "ASX:MMS"], "MMS"
    )
    assert matched is None
    assert mode == "ambiguous"
    assert candidates == ("ASX:MMS", "NYSE:MMS")

    matched, mode, candidates = resolve_symbol(["MMS", "NYSE:MMS"], "MMS")
    assert matched is None
    assert mode == "ambiguous"
    assert candidates == ("MMS", "NYSE:MMS")


def test_resolution_prefers_full_exchange_symbol_then_legacy_bare() -> None:
    matched, mode, _ = resolve_symbol(["ASX:MMS", "NYSE:MMS"], "NYSE:MMS")
    assert matched == "NYSE:MMS"
    assert mode == "exact"

    matched, mode, _ = resolve_symbol(["MMS"], "NYSE:MMS")
    assert matched == "MMS"
    assert mode == "legacy-bare"

    matched, mode, candidates = resolve_symbol(["ASX:MMS"], "NYSE:MMS")
    assert matched is None
    assert mode == "not-found"
    assert candidates == ()


def test_catalog_matches_resolve_symbol_and_does_not_cross_exchange() -> None:
    symbols = ["MMS", "NYSE:MMS", "ASX:MMS", "NASDAQ:MU"]
    catalog = SymbolCatalog.build(symbols)
    for requested in ("NYSE:MMS", "MMS", "NASDAQ:MU", "LSE:MMS"):
        assert catalog.resolve(requested) == resolve_symbol(symbols, requested)

    index = {"NYSE:MMS": {"px": 1}, "NASDAQ:MU": {"px": 2}}
    bound = SymbolCatalog.build(index.keys())
    assert lookup_mapping(index, "NYSE:MMS", bound) == {"px": 1}
    assert lookup_mapping(index, "NASDAQ:MU", bound) == {"px": 2}
    assert lookup_mapping(index, "ASX:MMS", bound) is None
    assert lookup_mapping(index, "MMS", bound) == {"px": 1}
    crossed = {"NYSE:MMS": {"px": 1}, "ASX:MMS": {"px": 9}}
    assert lookup_mapping(crossed, "MMS", SymbolCatalog.build(crossed.keys())) is None


def test_lookup_mapping_keeps_exchange_contract_without_a_caller_catalog() -> None:
    index = {"MMS": {"px": 1}, "NYSE:MMS": {"px": 2}, "ASX:MMS": {"px": 9}}
    assert lookup_mapping(index, "NYSE:MMS") == {"px": 2}
    assert lookup_mapping(index, "nyse:mms") == {"px": 2}
    assert lookup_mapping(index, "ASX:MMS") == {"px": 9}
    assert lookup_mapping(index, "MMS") is None
    assert lookup_mapping(index, "LSE:MMS") == {"px": 1}
    qualified_only = {"NYSE:MMS": {"px": 2}, "ASX:MMS": {"px": 9}}
    assert lookup_mapping(qualified_only, "LSE:MMS") is None
    assert lookup_mapping(qualified_only, "MMS") is None

    legacy = {"MMS": {"px": 1}}
    assert lookup_mapping(legacy, "NYSE:MMS") == {"px": 1}
    legacy["LSE:MMS"] = {"px": 4}
    assert lookup_mapping(legacy, "LSE:MMS") == {"px": 4}
    assert lookup_mapping(legacy, "NYSE:MMS") == {"px": 1}


def test_qualified_lookup_does_not_scan_other_keys(monkeypatch) -> None:
    builds = {"n": 0}
    real_build = SymbolCatalog.build

    def counting(symbols):
        builds["n"] += 1
        return real_build(symbols)

    monkeypatch.setattr(SymbolCatalog, "build", counting)
    index = {f"NASDAQ:T{i}": i for i in range(50)}
    index["MMS"] = -1
    assert lookup_mapping(index, "NASDAQ:T1") == 1
    assert lookup_mapping(index, "NASDAQ:T2") == 2
    assert builds["n"] == 0
    catalog = SymbolCatalog.build(index)
    assert lookup_mapping(index, "NYSE:MMS", catalog) == -1
    assert lookup_mapping(index, "LSE:MU", catalog) is None
    assert builds["n"] == 1


def test_repeated_qualified_and_legacy_lookups_stay_cheap() -> None:
    index = {f"NASDAQ:T{i}": i for i in range(3000)}
    index["MMS"] = -1
    started = time.perf_counter()
    for i in range(3000):
        assert lookup_mapping(index, f"NASDAQ:T{i}") == i
    catalog = SymbolCatalog.build(index)
    for _ in range(3000):
        assert lookup_mapping(index, "NYSE:MMS", catalog) == -1
    assert time.perf_counter() - started < 1.0


def test_symbol_parsing_preserves_exchange_and_ticker() -> None:
    identity = parse_symbol(" nyse:mms ")
    assert identity.normalized == "NYSE:MMS"
    assert identity.exchange == "NYSE"
    assert identity.ticker == "MMS"
    assert identity.qualified
