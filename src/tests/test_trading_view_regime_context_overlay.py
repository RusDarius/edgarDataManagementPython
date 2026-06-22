import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb

from data_analysis_scripts.trading_view_regime_context_overlay import (
    RegimeContextConfig,
    build_conviction_lookup,
    build_symbol_feature_frame,
    get_regime_record_for_row,
    load_regime_context_config,
    score_regime_context,
    write_regime_context_focus_log,
)


def _test_config(**overrides) -> RegimeContextConfig:
    base = RegimeContextConfig(
        config_id="test_regime",
        regime_label="Test regime",
        scan_period_run_root=Path("."),
        all_fields_root=Path("."),
        all_fields_db_path=None,
        performance_target="period_return_pct",
        stale_all_fields_days=8,
        promoted_predictors=("ATRP|1W",),
        demoted_predictors=("ebitda_ttm",),
        warning_predictors=("ebitda_ttm", "Recommend.MA|1M"),
        bullish_predictors=("ATRP|1W", "relative_volume"),
        playbook_a_min_relative_volume=1.5,
        top_n_focus=30,
        scan_column_aliases={"relative_volume": "relative_volume_10d_calc"},
        scan_proxies_last_resort={"ADRP|1W": "ADR", "ATRP|1W": "ATRP"},
        source_path=Path("test.json"),
    )
    return replace(base, **overrides) if overrides else base


class TestRegimeContextOverlay(unittest.TestCase):
    def test_load_regime_context_config(self):
        config_path = (
            Path(__file__).resolve().parents[2]
            / "config"
            / "regime_context"
            / "mar_jun_2026_risk_on_v1.json"
        )
        config = load_regime_context_config(config_path)
        self.assertEqual(config.config_id, "mar_jun_2026_risk_on")
        self.assertIn("ADRP|15", config.promoted_predictors)
        self.assertEqual(
            config.scan_column_aliases.get("relative_volume"),
            "relative_volume_10d_calc",
        )

    def test_scan_field_resolution(self):
        scan_rows = [
            {
                "symbol": "NASDAQ:HIGH",
                "ATRP": 25.0,
                "relative_volume_10d_calc": 2.0,
            },
            {
                "symbol": "NASDAQ:LOW",
                "ATRP": 5.0,
                "relative_volume_10d_calc": 0.8,
            },
        ]
        config = _test_config()
        frame = build_symbol_feature_frame(
            scan_rows,
            all_fields_db=None,
            config=config,
        )
        high = frame["NASDAQ:HIGH"]["ATRP|1W"]
        self.assertEqual(high.source, "scan_proxy")
        self.assertEqual(high.value, 25.0)
        relvol = frame["NASDAQ:HIGH"]["relative_volume"]
        self.assertEqual(relvol.source, "scan_alias")
        self.assertEqual(relvol.value, 2.0)

    def test_all_fields_backfill(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "all_fields.duckdb"
            con = duckdb.connect(str(db_path))
            con.execute(
                """
                CREATE TABLE run_metadata (
                    run_id VARCHAR,
                    created_at_utc TIMESTAMP
                )
                """
            )
            con.execute(
                """
                CREATE TABLE all_fields_rows (
                    run_id VARCHAR,
                    symbol VARCHAR,
                    "ADRP|15" DOUBLE,
                    "ATRP|1W" DOUBLE,
                    relative_volume DOUBLE
                )
                """
            )
            con.execute(
                "INSERT INTO run_metadata VALUES ('run1', CURRENT_TIMESTAMP)"
            )
            con.execute(
                """
                INSERT INTO all_fields_rows VALUES
                    ('run1', 'NASDAQ:AAA', 12.0, 20.0, 2.2),
                    ('run1', 'NASDAQ:BBB', 4.0, 6.0, 0.9)
                """
            )
            con.close()

            scan_rows = [
                {"symbol": "NASDAQ:AAA", "ADR": 10.0},
                {"symbol": "NASDAQ:BBB", "ADR": 3.0},
            ]
            config = _test_config(
                bullish_predictors=("ADRP|15", "ATRP|1W", "relative_volume"),
                warning_predictors=(),
            )
            frame = build_symbol_feature_frame(
                scan_rows,
                all_fields_db=db_path,
                config=config,
            )
            adrp = frame["NASDAQ:AAA"]["ADRP|15"]
            self.assertEqual(adrp.source, "all_fields")
            self.assertEqual(adrp.value, 12.0)

    def test_playbook_a_requires_relative_volume(self):
        scan_rows = [
            {"symbol": "NASDAQ:A", "ATRP": 30.0, "relative_volume_10d_calc": 2.0},
            {"symbol": "NASDAQ:B", "ATRP": 28.0, "relative_volume_10d_calc": 1.0},
            {"symbol": "NASDAQ:C", "ATRP": 8.0, "relative_volume_10d_calc": 2.5},
        ]
        config = _test_config()
        frame = build_symbol_feature_frame(scan_rows, all_fields_db=None, config=config)
        weights = [("ATRP|1W", 0.9, 0.9), ("relative_volume", 0.5, 0.5)]
        records = score_regime_context(frame, weights, config)
        self.assertTrue(records["NASDAQ:A"].playbook_a_fit)
        self.assertFalse(records["NASDAQ:B"].playbook_a_fit)

    def test_warning_overlay_at_two_flags(self):
        symbols = [f"NASDAQ:S{i}" for i in range(10)]
        scan_rows = []
        for index, symbol in enumerate(symbols):
            scan_rows.append(
                {
                    "symbol": symbol,
                    "ebitda": float(index),
                    "Recommend.MA": float(index),
                    "ATRP": 10.0 + index,
                    "relative_volume_10d_calc": 2.0,
                }
            )
        config = _test_config(
            bullish_predictors=("ATRP|1W",),
            warning_predictors=("ebitda_ttm", "Recommend.MA|1M"),
            scan_proxies_last_resort={
                "ATRP|1W": "ATRP",
                "ebitda_ttm": "ebitda",
                "Recommend.MA|1M": "Recommend.MA",
            },
        )
        frame = build_symbol_feature_frame(scan_rows, all_fields_db=None, config=config)
        weights = [("ATRP|1W", 0.8, 0.8)]
        records = score_regime_context(frame, weights, config)
        high_ebitda = records["NASDAQ:S9"]
        self.assertGreaterEqual(high_ebitda.warning_flag_count, 2)
        self.assertEqual(high_ebitda.active_mgmt_tier, "warning_overlay")

    def test_focus_log_caps_at_top_n(self):
        with TemporaryDirectory() as temp_dir:
            config = _test_config(top_n_focus=5)
            by_symbol = {
                f"NASDAQ:T{i}": type(
                    "Record",
                    (),
                    {
                        "regime_fit_score": float(100 - i),
                        "active_mgmt_tier": "playbook_b_watch",
                        "warning_flag_count": 0,
                        "atrp_1w": 20.0,
                        "relative_volume": 2.0,
                        "playbook_a_fit": False,
                    },
                )()
                for i in range(20)
            }
            overlay = type(
                "Overlay",
                (),
                {
                    "config": config,
                    "by_symbol": by_symbol,
                    "weights": [("ATRP|1W", 0.9, 0.9)],
                    "all_fields_db_path": None,
                    "all_fields_day_label": None,
                    "all_fields_stale_warning": None,
                    "aggregate_db_path": Path("batch_run"),
                },
            )()
            consensus_rows = [
                {
                    "ticker": f"NASDAQ:T{i}",
                    "symbol": f"NASDAQ:T{i}",
                    "horizons": {
                        "weeks": {"risk_adjusted_score": 0.5 - i * 0.01}
                    },
                }
                for i in range(20)
            ]
            log_path = Path(temp_dir) / "focus.log"
            write_regime_context_focus_log(
                log_path,
                run_id="test_run",
                overlay=overlay,
                consensus_rows=consensus_rows,
                profile_names=["breakout_long"],
                profile_log_paths={"breakout_long": Path("profile.log")},
            )
            content = log_path.read_text(encoding="utf-8")
            aligned_section = content.split("CROSS-PROFILE ALIGNED TOP 5", 1)[1]
            data_lines = [
                line
                for line in aligned_section.splitlines()
                if line and not line.startswith("-") and "Ticker" not in line
            ]
            ranked_lines = [
                line
                for line in data_lines
                if line[0].isdigit() and line[1] == " "
            ]
            self.assertLessEqual(len(ranked_lines), 5)

    def test_get_regime_record_matches_bare_ticker(self):
        record = type(
            "Record",
            (),
            {"regime_fit_score": 80.0, "active_mgmt_tier": "playbook_b_watch"},
        )()
        regime_by_symbol = {"NASDAQ:ABUS": record}
        found = get_regime_record_for_row({"symbol": "ABUS"}, regime_by_symbol)
        self.assertIs(found, record)

    def test_per_profile_conviction_section(self):
        with TemporaryDirectory() as temp_dir:
            config = _test_config(top_n_focus=3)
            by_symbol = {
                "NASDAQ:AAA": type(
                    "Record",
                    (),
                    {
                        "regime_fit_score": 90.0,
                        "active_mgmt_tier": "playbook_a_vol_continuation",
                        "warning_flag_count": 0,
                        "atrp_1w": 22.0,
                        "relative_volume": 2.0,
                        "playbook_a_fit": True,
                    },
                )(),
                "NASDAQ:BBB": type(
                    "Record",
                    (),
                    {
                        "regime_fit_score": 70.0,
                        "active_mgmt_tier": "playbook_b_watch",
                        "warning_flag_count": 1,
                        "atrp_1w": 18.0,
                        "relative_volume": 1.8,
                        "playbook_a_fit": False,
                    },
                )(),
            }
            overlay = type(
                "Overlay",
                (),
                {
                    "config": config,
                    "by_symbol": by_symbol,
                    "weights": [("ATRP|1W", 0.9, 0.9)],
                    "all_fields_db_path": None,
                    "all_fields_day_label": None,
                    "all_fields_stale_warning": None,
                    "aggregate_db_path": Path("batch_run"),
                },
            )()
            profile_predictions = {
                "breakout_long": [
                    {
                        "row": {"symbol": "NASDAQ:AAA"},
                        "horizons": {
                            "weeks": {"score": 0.8, "risk_adjusted_score": 0.6}
                        },
                    },
                    {
                        "row": {"symbol": "NASDAQ:BBB"},
                        "horizons": {
                            "weeks": {"score": 0.7, "risk_adjusted_score": 0.5}
                        },
                    },
                ]
            }
            conviction_records = [
                {
                    "symbol": "AAA",
                    "conviction_score": 0.91,
                    "rank_overall": 1,
                    "sleeve": "weeks",
                    "weeks_ras": 0.6,
                    "regime_fit_score": 90.0,
                    "regime_tier": "playbook_a_vol_continuation",
                    "regime_warning_count": 0,
                }
            ]
            log_path = Path(temp_dir) / "focus.log"
            write_regime_context_focus_log(
                log_path,
                run_id="test_run",
                overlay=overlay,
                consensus_rows=[],
                profile_names=["breakout_long"],
                profile_log_paths={"breakout_long": Path("profile.log")},
                profile_predictions_by_name=profile_predictions,
                conviction_records=conviction_records,
            )
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("PROFILE: breakout_long — REGIME + CONVICTION TOP 3", content)
            self.assertIn("AAA", content)
            self.assertIn("0.910", content)
            self.assertIn("ATRP", content)
            self.assertIn("RelVol", content)
            self.assertIn("22.0", content)
            self.assertIn("2.00", content)
            self.assertIn("CONVICTION LEADERS TOP 3", content)
            lookup = build_conviction_lookup(conviction_records)
            self.assertIn("AAA", lookup)


if __name__ == "__main__":
    unittest.main()
