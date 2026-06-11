import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from data_analysis_scripts.trading_view_move_prediction_pattern_discovery import (
    PatternDiscoveryPaths,
    _read_sql_blocks,
    _symbol_join,
    discover_latest_prediction_db,
)


class TestPatternDiscoveryHelpers(unittest.TestCase):
    def test_symbol_join_supports_exchange_prefixes(self):
        join_sql = _symbol_join("h", "a")
        self.assertIn("h.symbol", join_sql)
        self.assertIn("a.symbol", join_sql)
        self.assertIn("LIKE '%:'", join_sql)

    def test_read_sql_blocks_parses_named_blocks(self):
        session_path = (
            Path(__file__).resolve().parents[2]
            / "sql_connections_space"
            / "move_prediction_pattern_discovery.session.sql"
        )
        blocks = _read_sql_blocks(session_path)
        self.assertIn("q_pattern_join_preview", blocks)
        self.assertIn("q_pattern_false_positive_autopsy", blocks)
        self.assertIn("q_pattern_post_breakout_exhaustion", blocks)

    def test_discover_latest_prediction_db_returns_none_when_missing(self):
        with TemporaryDirectory() as temp_dir:
            self.assertIsNone(discover_latest_prediction_db(Path(temp_dir)))

    def test_pattern_discovery_paths_dataclass(self):
        with TemporaryDirectory() as temp_dir:
            prediction = Path(temp_dir) / "move_prediction_test.duckdb"
            prediction.touch()
            paths = PatternDiscoveryPaths(prediction_db=prediction)
            self.assertEqual(paths.prediction_db, prediction)


class TestPatternDiscoverySuiteGuardrails(unittest.TestCase):
    def test_suite_raises_when_prediction_db_missing(self):
        from data_analysis_scripts.trading_view_move_prediction_pattern_discovery import (
            run_pattern_discovery_suite,
        )

        with self.assertRaises(FileNotFoundError):
            run_pattern_discovery_suite(
                prediction_db=Path("nonexistent_move_prediction.duckdb")
            )

    @patch(
        "data_analysis_scripts.trading_view_move_prediction_pattern_discovery.discover_latest_history_db",
        return_value=None,
    )
    @patch(
        "data_analysis_scripts.trading_view_move_prediction_pattern_discovery.discover_latest_all_fields_db",
        return_value=None,
    )
    @patch(
        "data_analysis_scripts.trading_view_move_prediction_pattern_discovery.run_prediction_all_fields_join_preview",
        return_value=[],
    )
    @patch(
        "data_analysis_scripts.trading_view_move_prediction_pattern_discovery.run_profile_cohort_field_comparison",
        return_value=[],
    )
    def test_suite_writes_exports_with_mocked_queries(
        self,
        _mock_comparison,
        _mock_join,
        _mock_all_fields,
        _mock_history,
    ):
        from data_analysis_scripts.trading_view_move_prediction_pattern_discovery import (
            run_pattern_discovery_suite,
        )

        with TemporaryDirectory() as temp_dir:
            prediction = Path(temp_dir) / "move_prediction_test.duckdb"
            prediction.write_bytes(b"")
            result = run_pattern_discovery_suite(
                prediction_db=prediction,
                output_dir=Path(temp_dir) / "exports",
            )
            self.assertIn("join_preview_csv", result)
            self.assertTrue(Path(result["overview_log"]).exists())


if __name__ == "__main__":
    unittest.main()
