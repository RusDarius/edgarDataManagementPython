from generic_utils.decision_review import (
    build_decision_review,
    complete_top,
    format_highlights,
    load_signal_support,
    spearman,
    suggestion_matrix,
    DEFAULT_SPEC,
)


def _rank(symbol: str, rank: int, **fields):
    row = {
        "symbol": symbol,
        "ticker": symbol.split(":")[-1],
        "rank": rank,
        "rank_source": "sleeve",
        "close": 100.0,
        "left": 20.0,
        "street_left": 20.0,
        "carrier": "street",
        "rr": 1.0,
        "bo": 0.6,
        "rng": 20.0,
        "peer": "DISCOUNT",
        "mix": "",
    }
    row.update(fields)
    return row


def test_top_stays_on_the_published_order_and_fills_only_the_shortfall() -> None:
    sleeve = [
        {"symbol": "NASDAQ:A", "rr": 0.1},
        {"symbol": "NASDAQ:B", "rr": 9.0},
    ]
    tape = [
        {"symbol": "NASDAQ:B", "rr": 9.0},
        {"symbol": "NASDAQ:C", "rr": 3.0},
        {"symbol": "NASDAQ:D", "rr": 2.0},
        {"symbol": "NASDAQ:E", "rr": 1.0},
    ]
    rows, source = complete_top(sleeve, tape, 4)
    assert source == "sleeve+tape_fill"
    assert [row["symbol"] for row in rows] == ["NASDAQ:A", "NASDAQ:B", "NASDAQ:C", "NASDAQ:D"]
    assert rows[0]["rank_source"] == "sleeve"
    assert rows[2]["rank_source"] == "tape_fill"
    full, full_source = complete_top(sleeve + tape, None, 2)
    assert full_source == "sleeve"
    assert [row["symbol"] for row in full] == ["NASDAQ:A", "NASDAQ:B"]


def test_head_that_lags_the_tail_is_a_change() -> None:
    ranks = []
    for rank in range(1, 21):
        ranks.append(_rank(f"NASDAQ:H{rank}", rank, close=100))
    for rank in range(81, 101):
        ranks.append(_rank(f"NASDAQ:T{rank}", rank, close=100))
    marks = {
        "2026-09-01": {row["symbol"]: 100.0 for row in ranks},
        "2026-10-01": {},
    }
    for row in ranks:
        if row["rank"] <= 20:
            marks["2026-10-01"][row["symbol"]] = 90.0
        else:
            marks["2026-10-01"][row["symbol"]] = 120.0
    payload = build_decision_review(
        [{"day": "2026-09-01", "pack": "p", "rank_source": "sleeve", "ranks": ranks}],
        marks,
        spec=DEFAULT_SPEC,
        latest_day="2026-10-01",
    )
    matrix = {row["lever"]: row for row in payload["matrix"]}
    assert matrix["rank_head"]["call"] == "CHANGE"
    assert "tail beat" in matrix["rank_head"]["suggestion"]
    assert payload["quality"][0]["head_minus_tail"] < 0
    rho = payload["quality"][0]["spearman_rank_vs_forward"]
    assert rho is not None and rho > 0
    page = format_highlights(payload)
    assert "CHANGE" in page
    assert "NASDAQ:H1" not in page


def test_live_street_quote_that_beats_edge_only_is_a_keep() -> None:
    ranks = []
    for index in range(1, 16):
        ranks.append(_rank(f"NASDAQ:S{index}", index, carrier="street", street_left=10, left=10))
    for index in range(16, 31):
        ranks.append(
            _rank(
                f"NASDAQ:E{index}",
                index,
                carrier="edge_only",
                street_left=-5,
                left=30,
            )
        )
    marks = {"2026-09-01": {}, "2026-10-01": {}}
    for row in ranks:
        marks["2026-09-01"][row["symbol"]] = 100.0
        marks["2026-10-01"][row["symbol"]] = 110.0 if row["carrier"] == "street" else 90.0
    payload = build_decision_review(
        [{"day": "2026-09-01", "pack": "p", "rank_source": "sleeve", "ranks": ranks}],
        marks,
        spec={**DEFAULT_SPEC, "min_n": 12},
    )
    row = next(item for item in payload["matrix"] if item["lever"] == "street_vs_edge_only")
    assert row["call"] == "KEEP"
    assert row["favored_n"] == 15
    assert row["other_n"] == 15


def test_a_thin_slice_does_not_invent_a_change() -> None:
    ranks = [
        _rank("NASDAQ:A", 1, rng=10),
        _rank("NASDAQ:B", 2, rng=90),
    ]
    matrix = suggestion_matrix(
        [
            {**ranks[0], "position": "head", "ret_pct": 20.0},
            {**ranks[1], "position": "tail", "ret_pct": -20.0},
        ],
        {**DEFAULT_SPEC, "min_n": 12},
    )
    assert all(row["call"] == "THIN" for row in matrix)


def test_spearman_is_negative_when_rank_one_earns_the_higher_return() -> None:
    rho = spearman([(1, 10), (2, 8), (3, 6), (4, 4), (5, 2), (6, 1), (7, 0), (8, -1)])
    assert rho is not None and rho < 0


def test_signal_support_keeps_ic_and_caps_a_family(tmp_path) -> None:
    path = tmp_path / "family_metrics.csv"
    path.write_text(
        "family,signal_name,horizon_days,pair_count,day_count,spearman_ic,top_20_lift_pct\n"
        "edge,upside,10,10,8,0.20,99999\n"
        "edge,magnitude,10,10,8,0.15,1\n"
        "edge,third,10,10,8,0.14,1\n"
        "edge,fourth,10,10,8,0.13,1\n"
        "weeks,bo,10,10,8,0.05,1\n"
        "all_fields,adrp,10,10,8,-0.10,50000\n"
        "edge,upside,20,10,8,0.11,1\n",
        encoding="utf-8",
    )
    rows = load_signal_support(path, per_horizon=6, family_cap=3)
    ten = [row for row in rows if row["horizon_days"] == 10]
    assert len([row for row in ten if row["family"] == "edge"]) == 3
    assert "top_20_lift_pct" not in ten[0]
    assert ten[0]["signal_name"] == "upside"
