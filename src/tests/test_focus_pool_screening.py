import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from focus_pool_screening.config import load_focus_pool_config
from focus_pool_screening.engine import run_focus_pool_screening
from focus_pool_screening.scoring import (
    apply_valid_range,
    band_score,
    clip_value,
    coerce_float,
    evaluate_condition,
    percentile_scores,
    weighted_average,
)


def _duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


class TestCoercion(unittest.TestCase):
    def test_coerce_float_handles_strings_and_blanks(self):
        self.assertEqual(coerce_float("1,234.5"), 1234.5)
        self.assertEqual(coerce_float("12.5%"), 12.5)
        self.assertIsNone(coerce_float(""))
        self.assertIsNone(coerce_float("n/a"))
        self.assertIsNone(coerce_float(None))
        self.assertEqual(coerce_float(True), 1.0)

    def test_valid_range_drops_outliers_instead_of_clipping(self):
        self.assertIsNone(apply_valid_range(-5.0, (0.1, None)))
        self.assertEqual(apply_valid_range(5.0, (0.1, None)), 5.0)
        self.assertIsNone(apply_valid_range(500.0, (None, 100.0)))
        self.assertEqual(apply_valid_range(5.0, None), 5.0)

    def test_clip_winsorizes(self):
        self.assertEqual(clip_value(500.0, (0, 80)), 80)
        self.assertEqual(clip_value(-5.0, (0, 80)), 0)
        self.assertIsNone(clip_value(None, (0, 80)))


class TestPercentileScores(unittest.TestCase):
    def test_higher_better_ranking(self):
        scores = percentile_scores({"a": 1.0, "b": 2.0, "c": 3.0})
        self.assertEqual(scores["a"], 0.0)
        self.assertEqual(scores["b"], 50.0)
        self.assertEqual(scores["c"], 100.0)

    def test_lower_better_inverts(self):
        scores = percentile_scores({"a": 1.0, "b": 2.0, "c": 3.0}, direction="lower_better")
        self.assertEqual(scores["a"], 100.0)
        self.assertEqual(scores["c"], 0.0)

    def test_ties_share_average_rank(self):
        scores = percentile_scores({"a": 1.0, "b": 2.0, "c": 2.0, "d": 5.0})
        self.assertEqual(scores["b"], scores["c"])
        self.assertAlmostEqual(scores["b"], 50.0)
        self.assertEqual(scores["d"], 100.0)

    def test_none_stays_none_and_single_observation_is_neutral(self):
        scores = percentile_scores({"a": None, "b": 7.0})
        self.assertIsNone(scores["a"])
        self.assertEqual(scores["b"], 50.0)

    def test_input_order_does_not_matter(self):
        first = percentile_scores({"a": 3.0, "b": 1.0, "c": 2.0})
        second = percentile_scores({"c": 2.0, "a": 3.0, "b": 1.0})
        self.assertEqual(first, second)


class TestBandScore(unittest.TestCase):
    def test_inside_band_is_100(self):
        self.assertEqual(band_score(50.0, 45.0, 70.0), 100.0)
        self.assertEqual(band_score(70.0, 45.0, 70.0), 100.0)

    def test_linear_decay_outside_band(self):
        # band width 25 -> default decay span 25
        self.assertEqual(band_score(95.0, 45.0, 70.0), 0.0)
        self.assertEqual(band_score(82.5, 45.0, 70.0), 50.0)
        self.assertEqual(band_score(32.5, 45.0, 70.0), 50.0)

    def test_none_and_custom_span(self):
        self.assertIsNone(band_score(None, 45.0, 70.0))
        self.assertEqual(band_score(80.0, 45.0, 70.0, decay_span=50.0), 80.0)


class TestWeightedAverage(unittest.TestCase):
    def test_coverage_and_renormalization(self):
        score, coverage = weighted_average([(1.0, 80.0), (1.0, None)])
        self.assertEqual(score, 80.0)
        self.assertEqual(coverage, 0.5)

    def test_all_missing(self):
        score, coverage = weighted_average([(1.0, None), (2.0, None)])
        self.assertIsNone(score)
        self.assertEqual(coverage, 0.0)


class TestEvaluateCondition(unittest.TestCase):
    def test_leaf_ops(self):
        row = {"perf": 10.0}
        self.assertTrue(evaluate_condition({"field": "perf", "op": ">=", "value": 8.0}, row))
        self.assertFalse(evaluate_condition({"field": "perf", "op": "<=", "value": 8.0}, row))
        self.assertTrue(
            evaluate_condition({"field": "perf", "op": "between", "value": [5.0, 12.0]}, row)
        )

    def test_missing_value_is_false(self):
        self.assertFalse(evaluate_condition({"field": "nope", "op": ">=", "value": 0.0}, {}))

    def test_any_all_nesting(self):
        row = {"perf": -8.0, "value": 70.0}
        node = {
            "all": [
                {"any": [
                    {"field": "perf", "op": "<=", "value": -6.0},
                    {"field": "perf", "op": ">=", "value": 8.0},
                ]},
                {"field": "value", "op": ">=", "value": 60.0},
            ]
        }
        self.assertTrue(evaluate_condition(node, row))
        self.assertFalse(evaluate_condition(node, {"perf": -8.0, "value": 10.0}))


class TestConfigValidation(unittest.TestCase):
    def test_default_config_loads(self):
        config = load_focus_pool_config()
        self.assertEqual(config.schema_version, "focus_pool_screening_v1")
        self.assertEqual(config.config_id, "default_wide_pool_v1")
        self.assertGreaterEqual(len(config.families), 4)
        self.assertGreaterEqual(len(config.lanes), 3)

    def test_bad_schema_version_rejected(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(json.dumps({"schema_version": "nope"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_focus_pool_config(path)

    def test_field_needs_exactly_one_of_column_or_derived(self):
        payload = {
            "schema_version": "focus_pool_screening_v1",
            "families": {
                "value": {
                    "weight": 1.0,
                    "fields": [
                        {"key": "bad", "column": "x", "derived": "y", "direction": "higher_better"}
                    ],
                }
            },
        }
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad_field.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_focus_pool_config(path)


def _write_prediction_db(path: Path) -> str:
    import duckdb

    run_id = "move_prediction_20260101_0000_utc_testtest"
    conn = duckdb.connect(str(path))
    try:
        conn.execute("CREATE TABLE run_metadata (run_id VARCHAR, created_at_utc TIMESTAMP)")
        conn.execute("INSERT INTO run_metadata VALUES (?, '2026-01-01 00:00:00')", [run_id])
        conn.execute(
            """
            CREATE TABLE raw_scan_rows (
                run_id VARCHAR, row_number BIGINT, symbol VARCHAR,
                "Company" VARCHAR, market VARCHAR, market_cap_basic VARCHAR,
                close VARCHAR, "Perf.1M" VARCHAR, price_earnings_ttm VARCHAR
            )
            """
        )
        raw_rows = [
            (run_id, 1, "NASDAQ:AAA", "Alpha", "america", "1000000000", "10", "-10", "10"),
            (run_id, 2, "NASDAQ:BBB", "Beta", "america", "1000000000", "10", "5", "20"),
            (run_id, 3, "NYSE:CCC", "Gamma", "america", "1000000000", "10", "-8", None),
            (run_id, 4, "NASDAQ:DDD", "Delta", "america", "1000000000", "10", "30", "40"),
            (run_id, 5, "OTC:EEE", "Epsilon", "otc", "1000000000", "10", "50", "5"),
            (run_id, 6, "NASDAQ:FFF", "Zeta", "america", "1000000000", "10", "9", "9"),
        ]
        conn.executemany("INSERT INTO raw_scan_rows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", raw_rows)
        conn.execute(
            """
            CREATE TABLE consensus_horizon_scores (
                run_id VARCHAR, symbol VARCHAR, horizon_name VARCHAR,
                score DOUBLE, risk_adjusted_score DOUBLE, direction VARCHAR,
                confidence DOUBLE, agreement_ratio DOUBLE, manager_action_signal VARCHAR
            )
            """
        )
        consensus_rows = [
            (run_id, "NASDAQ:AAA", "weeks", 1.0, 1.0, "Up", 90.0, 0.8, "add"),
            (run_id, "NASDAQ:BBB", "weeks", -1.0, -1.0, "Down", 90.0, 0.8, "trim"),
            (run_id, "NYSE:CCC", "weeks", 0.0, 0.0, "Neutral", 90.0, 0.5, "hold"),
            (run_id, "NASDAQ:DDD", "weeks", 0.0, 0.0, "Neutral", 90.0, 0.5, "hold"),
            (run_id, "NASDAQ:FFF", "weeks", 0.0, 0.0, "Neutral", 90.0, 0.5, "hold"),
        ]
        conn.executemany(
            "INSERT INTO consensus_horizon_scores VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            consensus_rows,
        )
    finally:
        conn.close()
    return run_id


def _write_all_fields_db(path: Path) -> str:
    import duckdb

    run_id = "tradingview_all_fields_20260101_0000_utc_afafafaf"
    conn = duckdb.connect(str(path))
    try:
        conn.execute("CREATE TABLE run_metadata (run_id VARCHAR, created_at_utc TIMESTAMP)")
        conn.execute("INSERT INTO run_metadata VALUES (?, '2026-01-01 01:00:00')", [run_id])
        conn.execute(
            """
            CREATE TABLE all_fields_rows (
                run_id VARCHAR, symbol VARCHAR,
                price_earnings_ttm VARCHAR, "Perf.1M" VARCHAR
            )
            """
        )
        rows = [
            (run_id, "NASDAQ:AAA", "8", "-9"),
            (run_id, "NYSE:CCC", "5", "-8"),
            (run_id, "NASDAQ:DDD", "41", "30"),
        ]
        conn.executemany("INSERT INTO all_fields_rows VALUES (?, ?, ?, ?)", rows)
    finally:
        conn.close()
    return run_id


def _write_test_config(path: Path, pred_db: Path, af_db: Path, out_root: Path) -> None:
    payload = {
        "schema_version": "focus_pool_screening_v1",
        "config_id": "test_mini",
        "sources": {
            "move_prediction": {
                "enabled": True,
                "database_path": str(pred_db),
                "consensus_horizon": "weeks",
                "profile_columns": [],
                "include_conviction": False,
            },
            "all_fields": {"enabled": True, "database_path": str(af_db)},
            "edge_research": {"enabled": False},
        },
        "universe": {
            "min_market_cap_usd": 0,
            "markets": ["america"],
            "min_close": 0,
            "min_avg_dollar_traded_10d": 0,
            "exclude_symbols": ["NASDAQ:FFF"],
        },
        "families": {
            "value": {
                "weight": 1.0,
                "fields": [
                    {"key": "pe_ttm", "column": "price_earnings_ttm", "direction": "lower_better", "weight": 1.0},
                    {"key": "fwd_pe", "column": "price_earnings_forward_fy", "direction": "lower_better", "weight": 1.0},
                ],
            },
            "momentum": {
                "weight": 1.0,
                "fields": [
                    {"key": "perf_1m", "column": "Perf.1M", "direction": "higher_better", "weight": 1.0},
                ],
            },
        },
        "overlays": {
            "prediction": {"weight": 1.0, "score_min": -2.0, "score_max": 2.0},
            "edge": {"weight": 0.0},
        },
        "lanes": {
            "drops": {
                "enabled": True,
                "conditions": {
                    "all": [
                        {"field": "perf_1m", "op": "<=", "value": -5.0},
                        {"field": "value_score", "op": ">=", "value": 50.0},
                    ]
                },
                "order_by": [{"key": "value_score", "dir": "desc"}],
                "top_n": 10,
            }
        },
        "output": {
            "root_dir": str(out_root),
            "write_csv": True,
            "top_n_focus_section": 10,
            "min_composite_coverage": 0.0,
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


@unittest.skipUnless(_duckdb_available(), "duckdb not installed")
class TestEndToEndMiniPipeline(unittest.TestCase):
    def _run_once(self, tmp: str):
        import duckdb

        root = Path(tmp)
        pred_db = root / "pred.duckdb"
        af_db = root / "af.duckdb"
        _write_prediction_db(pred_db)
        _write_all_fields_db(af_db)
        config_path = root / "config.json"
        _write_test_config(config_path, pred_db, af_db, root / "out")

        result = run_focus_pool_screening(config_path=config_path)
        conn = duckdb.connect(str(result["duckdb_path"]), read_only=True)
        try:
            rows = conn.execute(
                "SELECT * FROM focus_pool_rows ORDER BY focus_rank"
            ).fetchall()
            columns = [c[0] for c in conn.description]
            dictionary = conn.execute(
                "SELECT key, included FROM field_dictionary"
            ).fetchall()
        finally:
            conn.close()
        return result, [dict(zip(columns, row)) for row in rows], dictionary

    def test_pipeline_end_to_end(self):
        with TemporaryDirectory() as tmp:
            result, rows, dictionary = self._run_once(tmp)

            symbols = [row["symbol"] for row in rows]
            # OTC:EEE filtered by markets, NASDAQ:FFF excluded by config
            self.assertEqual(sorted(symbols), ["NASDAQ:AAA", "NASDAQ:BBB", "NASDAQ:DDD", "NYSE:CCC"])

            by_symbol = {row["symbol"]: row for row in rows}

            # all_fields wins over prediction raw for AAA (8 vs 10)
            self.assertEqual(by_symbol["NASDAQ:AAA"]["pe_ttm"], 8.0)
            # prediction raw fallback for BBB (missing in all_fields)
            self.assertEqual(by_symbol["NASDAQ:BBB"]["pe_ttm"], 20.0)

            # focus ordering: CCC > DDD > AAA > BBB (hand-computed)
            self.assertEqual(
                [row["symbol"] for row in rows],
                ["NYSE:CCC", "NASDAQ:DDD", "NASDAQ:AAA", "NASDAQ:BBB"],
            )
            self.assertAlmostEqual(by_symbol["NYSE:CCC"]["focus_score"], 61.11, places=1)

            # lane membership: droppers with value_score >= 50
            self.assertTrue(by_symbol["NASDAQ:AAA"]["lane_drops"])
            self.assertTrue(by_symbol["NYSE:CCC"]["lane_drops"])
            self.assertFalse(by_symbol["NASDAQ:DDD"]["lane_drops"])
            # lane rank ordering by value_score desc
            self.assertEqual(by_symbol["NYSE:CCC"]["lane_drops_rank"], 1)
            self.assertEqual(by_symbol["NASDAQ:AAA"]["lane_drops_rank"], 2)

            # missing-everywhere column excluded in the dictionary
            included = {key: flag for key, flag in dictionary}
            self.assertFalse(included["fwd_pe"])
            self.assertTrue(included["pe_ttm"])

            # outputs exist
            self.assertTrue(Path(result["duckdb_path"]).exists())
            self.assertTrue(Path(result["overview_log"]).exists())
            self.assertTrue(Path(result["csv_path"]).exists())
            self.assertEqual(result["lane_counts"], {"drops": 2})

    def test_determinism_across_runs(self):
        with TemporaryDirectory() as tmp_a, TemporaryDirectory() as tmp_b:
            _, rows_a, _ = self._run_once(tmp_a)
            _, rows_b, _ = self._run_once(tmp_b)
            order_a = [(r["symbol"], r["focus_rank"], round(r["focus_score"], 6)) for r in rows_a]
            order_b = [(r["symbol"], r["focus_rank"], round(r["focus_score"], 6)) for r in rows_b]
            self.assertEqual(order_a, order_b)


if __name__ == "__main__":
    unittest.main()
