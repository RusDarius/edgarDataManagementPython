from pathlib import Path

import pytest

from run_edge_research_tools import (
    DEFAULT_HIGHLIGHTS_MIN_SHORTLIST_COUNT,
    _build_historical_validation_fields,
    _resolve_highlights_lens_input_csv,
    _resolve_requested_symbol,
    run_edge_symbol_inspection_method,
)


def test_default_highlights_min_shortlist_count_is_2000() -> None:
    assert DEFAULT_HIGHLIGHTS_MIN_SHORTLIST_COUNT == 2000


def test_resolve_highlights_lens_input_csv_prefers_universe_csv() -> None:
    resolved = _resolve_highlights_lens_input_csv(
        {
            "shortlist_csv": "shortlist.csv",
            "universe_csv": "universe.csv",
        }
    )

    assert resolved == Path("universe.csv")


def test_build_historical_validation_fields_flags_strong_support() -> None:
    fields = _build_historical_validation_fields(
        {
            "hist_occurrence_count_in_setup": 5,
            "win_rate_5d_in_setup": 0.66,
            "median_fwd_5d_in_setup": 6.5,
            "any_setup_rate": 0.08,
            "stability_score": 0.62,
        },
        ranking_horizon=5,
        safety_companion_score=0.72,
    )

    assert fields["historical_validation_pass"] == 1
    assert fields["historical_validation_bucket"] == "strong"
    assert fields["historical_validation_score"] > 0.6


def test_resolve_requested_symbol_prefers_exact_exchange_symbol() -> None:
    matched_symbol, match_mode = _resolve_requested_symbol(
        ["NYSE:WDC", "NASDAQ:WDC", "NASDAQ:MRNA"],
        "NASDAQ:WDC",
    )

    assert matched_symbol == "NASDAQ:WDC"
    assert match_mode == "exact"


def test_resolve_requested_symbol_rejects_ambiguous_bare_ticker() -> None:
    with pytest.raises(ValueError):
        _resolve_requested_symbol(
            ["NYSE:WDC", "NASDAQ:WDC", "NASDAQ:MRNA"],
            "WDC",
        )


def test_run_edge_symbol_inspection_method_uses_exact_symbol_match(
    tmp_path: Path,
) -> None:
    parent_dir = tmp_path / "edge_parent_run"
    (parent_dir / "edge_unified_highlights").mkdir(parents=True)
    (parent_dir / "highlights").mkdir()
    (parent_dir / "upside_prediction_lens").mkdir()
    (parent_dir / "forward_upside_valuation_lens").mkdir()
    (parent_dir / "tradeable_safety_lens").mkdir()
    (parent_dir / "parent_run_manifest.json").write_text("{}\n", encoding="utf-8")

    (parent_dir / "edge_unified_highlights" / "edge_unified_highlights.csv").write_text(
        "symbol,unified_edge_highlight_rank,unified_edge_highlight_score,historical_validation_score,historical_validation_bucket\n"
        "NASDAQ:WDC,7,0.81,0.74,strong\n"
        "NYSE:WDC,11,0.55,0.41,thin\n",
        encoding="utf-8",
    )
    (parent_dir / "highlights" / "edge_name_shortlist.csv").write_text(
        "symbol,big_mover_rank,big_mover_score,confidence_rank,confidence_score\n"
        "NASDAQ:WDC,25,0.73,14,0.69\n"
        "NYSE:WDC,55,0.44,61,0.42\n",
        encoding="utf-8",
    )
    (
        parent_dir / "upside_prediction_lens" / "edge_upside_prediction_ranked.csv"
    ).write_text(
        "symbol,upside_prediction_rank,upside_prediction_score\n"
        "NASDAQ:WDC,18,0.71\n",
        encoding="utf-8",
    )
    (
        parent_dir
        / "forward_upside_valuation_lens"
        / "edge_forward_upside_valuation_ranked.csv"
    ).write_text(
        "symbol,forward_upside_rank,forward_upside_score,forward_valuation_upside_pct,historical_validation_score,historical_validation_bucket\n"
        "NASDAQ:WDC,9,0.79,28.5,0.74,strong\n"
        "NYSE:WDC,30,0.51,8.0,0.41,thin\n",
        encoding="utf-8",
    )
    (
        parent_dir / "tradeable_safety_lens" / "edge_tradeable_safety_overlay.csv"
    ).write_text(
        "symbol,tradeable_safety_rank,tradeable_safety_blend_score,historical_validation_score,historical_validation_bucket\n"
        "NASDAQ:WDC,10,0.76,0.74,strong\n",
        encoding="utf-8",
    )
    (
        parent_dir
        / "edge_unified_highlights"
        / "edge_unified_highlights_safety_scored_universe.csv"
    ).write_text(
        "symbol,safety_rank,safety_companion_score\n" "NASDAQ:WDC,40,0.67\n",
        encoding="utf-8",
    )

    result = run_edge_symbol_inspection_method(
        symbol="NASDAQ:WDC",
        run_ref=parent_dir,
        auto_discover_latest=False,
    )

    assert result["matched_symbol"] == "NASDAQ:WDC"
    assert result["match_mode"] == "exact"
    sections = {section["dataset"]: section for section in result["sections"]}
    assert sections["big_mover"]["rank_display"] == "25/2"
    assert sections["forward_valuation"]["historical_validation_bucket"] == "strong"
    assert sections["unified_highlight"]["historical_validation_score"] == "0.74"
