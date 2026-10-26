from generic_utils.derive import leftover_pct
from generic_utils.movement_opportunity import (
    build_movement_tables,
    path_class,
    prepare_tape,
    rating_1_10,
    research_evidence_rows,
    score_row,
    vol_class,
)
from generic_utils.series import series_span, series_step_stats


def test_path_class_matches_analysis_style_rules() -> None:
    opening = {
        "n": 10,
        "close_pct": 2.0,
        "pt_pct": 0.5,
        "left": 22,
        "rng": 40,
        "pe_pct": -1,
    }
    assert path_class(opening) == "OPENING"
    dying = {
        "n": 10,
        "close_pct": 12.0,
        "pt_pct": 1.0,
        "left": 22,
        "rng": 40,
        "pe_pct": -12,
    }
    assert path_class(dying) == "DYING"
    cheaper = {
        "n": 10,
        "close_pct": 6.0,
        "pt_pct": 4.0,
        "left": 22,
        "rng": 50,
        "pe_pct": -12,
    }
    assert path_class(cheaper) == "CHEAPER"
    assert path_class({"n": 3, "close_pct": 1, "pt_pct": 1, "left": 30}) == "THIN_HIST"
    flat = {
        "n": 10,
        "close_pct": 6.0,
        "pt_pct": 3.0,
        "left": 22,
        "rng": 50,
        "pe_pct": -1,
    }
    assert path_class(flat) == "FLAT"


def test_rating_1_10_is_auditable_and_caps_thin_coverage() -> None:
    assert rating_1_10(0.0, coverage_frac=1.0) == 1
    assert rating_1_10(1.0, coverage_frac=1.0) == 10
    assert rating_1_10(0.5, coverage_frac=1.0) == 6
    assert rating_1_10(0.95, coverage_frac=0.3) == 6
    assert rating_1_10(None, coverage_frac=1.0) is None


def test_vol_class_prefers_realized_step_then_atrp() -> None:
    assert vol_class({"close_step_abs_mean_pct": 1.2}) == "coiled"
    assert vol_class({"close_step_abs_mean_pct": 3.5}) == "active_swing"
    assert vol_class({"close_step_abs_mean_pct": 7.0}) == "high_velocity"
    assert vol_class({"close_step_abs_mean_pct": 11.0}) == "extreme"
    assert vol_class({"atrp": 1.5}) == "coiled"
    assert vol_class({}) == "thin"


def test_score_row_keeps_leftover_and_components_separate() -> None:
    row = score_row(
        {
            "symbol": "NASDAQ:OPEN",
            "ind": "Semiconductors",
            "close": 80,
            "pt": 110,
            "edge_left": 20,
            "street_left": leftover_pct(80, 110, None),
            "left": leftover_pct(80, 110, 20),
            "rng": 30,
            "rr": 1.2,
            "rsi": 42,
            "vs50": -2,
            "peer": "DISCOUNT",
            "val_vs_ind": -12,
            "bo": 0.8,
            "cont": 0.6,
            "fwd": 0.5,
            "recov": 0.4,
            "mix": "add_discount",
            "mtp": "ENTER_SMALL",
            "dte": 12,
            "atrp": 2.5,
            "relvol": 1.1,
            "n": 12,
            "close_pct": 1.5,
            "pt_pct": 0.5,
            "pe_pct": -8,
            "close_step_abs_mean_pct": 1.4,
            "fp_rev_cagr_own": 18,
        }
    )
    assert row["left"] == leftover_pct(80, 110, 20)
    assert row["path_class"] == "OPENING"
    assert row["vol_class"] == "coiled"
    assert row["up_asymmetric_payoff"] is not None
    assert row["up_structure_conviction"] is not None
    assert row["up_valuation_growth"] is not None
    assert row["up_timing_catalyst"] is not None
    assert row["up_move_fuel"] is not None
    assert row["upside_10"] is not None
    assert 1 <= row["upside_10"] <= 10
    assert row["exp_move_20d"] == round(2.5 * (20 ** 0.5), 4)
    assert "up_asymmetric_payoff" in row
    assert row["upside_10"] != row["left"]


def test_dying_paid_name_rates_downside_above_upside() -> None:
    row = score_row(
        {
            "symbol": "NASDAQ:CHASE",
            "ind": "Software",
            "close": 120,
            "left": 4,
            "street_left": 4,
            "rng": 92,
            "rr": 0.04,
            "rsi": 78,
            "vs50": 12,
            "peer": "PREMIUM",
            "val_vs_ind": 18,
            "bo": 0.2,
            "cont": 0.1,
            "fwd": 0.1,
            "frag": 0.7,
            "exh": 0.8,
            "mix": "avoid_value_trap",
            "mtp": "AVOID_CHASE",
            "lean": "AVOID_DOWNSIDE_RISK",
            "atrp": 8.0,
            "n": 12,
            "close_pct": 16,
            "pt_pct": 1,
            "pe_pct": 8,
            "close_step_abs_mean_pct": 8.5,
            "d5": 6,
            "m1": 22,
        }
    )
    assert row["path_class"] == "DYING"
    assert row["vol_class"] == "high_velocity"
    assert row["downside_10"] is not None
    assert row["upside_10"] is not None
    assert row["downside_10"] >= row["upside_10"]
    assert row["dn_valuation_ceiling"] is not None
    assert row["dn_exhaustion_reversal"] is not None


def test_thin_coverage_cannot_print_a_nine() -> None:
    row = score_row(
        {
            "symbol": "NASDAQ:THIN",
            "close": 10,
            "atrp": 2,
        }
    )
    assert row["thin_up"] is True or row["up_cover_frac"] < 0.8
    if row["upside_10"] is not None and row["thin_up"]:
        assert row["upside_10"] <= 6


def test_tables_keep_overflow_and_bounce_separate() -> None:
    names = []
    for idx in range(6):
        names.append(
            score_row(
                {
                    "symbol": f"NASDAQ:U{idx}",
                    "ind": "Chips",
                    "left": 30 - idx,
                    "street_left": 30 - idx,
                    "rng": 25,
                    "rr": 1.1,
                    "rsi": 40,
                    "peer": "DISCOUNT",
                    "val_vs_ind": -10,
                    "bo": 0.7,
                    "cont": 0.5,
                    "fwd": 0.4,
                    "mix": "add",
                    "mtp": "WATCH",
                    "atrp": 2.0,
                    "n": 10,
                    "close_pct": 1,
                    "pt_pct": 0,
                    "pe_pct": -6,
                    "d5": -9,
                    "vs50": -6,
                }
            )
        )
    names.append(
        score_row(
            {
                "symbol": "NASDAQ:OTHER",
                "ind": "Retail",
                "left": 20,
                "street_left": 20,
                "rng": 30,
                "rr": 0.8,
                "rsi": 45,
                "peer": "INLINE",
                "bo": 0.4,
                "mix": "add",
                "atrp": 2.0,
                "n": 10,
                "close_pct": 1,
                "pt_pct": 0,
                "d5": -6,
                "vs50": -5,
            }
        )
    )
    tables = build_movement_tables(
        names,
        {
            "top_upside": 3,
            "top_downside": 3,
            "top_bounce": 3,
            "group_field": "ind",
            "cap": 2,
            "upside_weights": {
                "asymmetric_payoff": 0.25,
                "structure_conviction": 0.25,
                "valuation_growth": 0.20,
                "timing_catalyst": 0.15,
                "move_fuel": 0.15,
            },
            "downside_weights": {
                "fragility_trend": 0.25,
                "valuation_ceiling": 0.25,
                "exhaustion_reversal": 0.20,
                "edge_risk": 0.15,
                "vol_amplification": 0.15,
            },
        },
    )
    assert len(tables["upside_50"]) <= 3
    assert tables["upside_overflow"]
    bounce_ids = {row["symbol"] for row in tables["bounce"]}
    for row in tables["bounce"]:
        assert row["down_class"] == "BOUNCE"
    assert bounce_ids
    assert "evidence" in tables
    assert tables["evidence"]


def test_evidence_rows_keep_cited_ic_and_accept_live_metrics() -> None:
    rows = research_evidence_rows(
        [
            {
                "family": "expected_move_proxy_pct",
                "horizon": "20d",
                "spearman_ic": 0.19,
                "n_days": 8,
            }
        ]
    )
    proxy = next(row for row in rows if row["family"] == "expected_move_proxy_pct")
    assert proxy["ic_cited"] == 0.233
    assert proxy["ic_live"] == 0.19
    assert "not leftover" in proxy["read"]


def test_runner_up_is_not_a_bounce() -> None:
    row = score_row(
        {
            "symbol": "NASDAQ:APP",
            "left": 74,
            "street_left": 74,
            "rng": 90,
            "rsi": 45,
            "d5": 5.0,
            "m1": 20,
            "vs50": 15,
            "peer": "DISCOUNT",
            "atrp": 3,
            "n": 10,
            "close_pct": 12,
            "pt_pct": 4,
        }
    )
    assert row["down_class"] is None


def test_prepare_tape_drops_non_us_when_the_recipe_says_so() -> None:
    rows = [
        {"symbol": "BME:AMS", "industry": "Software", "close": 10, "country": "Spain"},
        {
            "symbol": "NASDAQ:MU",
            "industry": "Semiconductors",
            "close": 100,
            "price_target_average": 120,
            "country": "United States",
        },
    ]
    out = prepare_tape(rows)
    ids = {row["symbol"] for row in out}
    assert "NASDAQ:MU" in ids
    assert "BME:AMS" not in ids


def test_prepare_tape_keeps_pack_leftover_formula() -> None:
    rows = [
        {
            "symbol": "NASDAQ:MU",
            "industry": "Semiconductors",
            "close": 100,
            "price_target_average": 130,
            "market_cap_basic": 80_000_000_000,
            "price_52_week_low": 60,
            "price_52_week_high": 140,
            "RSI": 45,
            "SMA50": 98,
            "ATRP": 2.5,
        }
    ]
    out = prepare_tape(
        rows,
        edge={"NASDAQ:MU": {"forward_valuation_upside_pct": 40}},
        pred={"NASDAQ:MU": {"bo": 0.7, "cont": 0.5, "fwd": 0.4}},
    )
    assert out[0]["left"] == leftover_pct(100, 130, 40)
    assert out[0]["street_left"] == leftover_pct(100, 130, None)
    assert out[0]["left_carrier"] == "both"
    assert out[0]["upside_10"] is not None
    assert out[0]["bo"] == 0.7
    assert "up_asymmetric_payoff" in out[0]


def test_series_step_stats_are_opt_in() -> None:
    rows = [
        {"symbol": "NASDAQ:MU", "as_of": "2026-10-01", "close": 100},
        {"symbol": "NASDAQ:MU", "as_of": "2026-10-02", "close": 110},
        {"symbol": "NASDAQ:MU", "as_of": "2026-10-03", "close": 99},
    ]
    plain = series_span(rows, id_field="symbol", fields=("close",))
    assert "close_step_n" not in plain[0]
    stepped = series_step_stats(rows, id_field="symbol", fields=("close",))
    assert stepped[0]["close_step_n"] == 2
    assert stepped[0]["close_step_max_up_pct"] == 10.0
    assert stepped[0]["close_step_max_down_pct"] == -10.0
    assert stepped[0]["close_streak_down"] == 1
    assert stepped[0]["close_max_drawdown_pct"] == -10.0
    assert stepped[0]["close_rebound_from_min_pct"] == 0.0
    opt = series_span(rows, id_field="symbol", fields=("close",), include_steps=True)
    assert opt[0]["close_step_n"] == 2
