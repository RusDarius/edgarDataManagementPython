import csv
import tempfile
import unittest
from pathlib import Path

from data_analysis_scripts.trading_view_move_prediction_history_aggregator import (
    build_move_prediction_history_inputs_from_folder_names,
    run_move_prediction_history_aggregation,
)


CSV_HEADERS = [
    "symbol",
    "Company",
    "name",
    "exchange",
    "country",
    "sector",
    "industry",
    "market",
    "scoring_profile",
    "market_cap_basic",
    "close",
    "Perf.5D",
    "Perf.W",
    "Perf.1M",
    "Perf.YTD",
    "Perf.Y",
    "Perf.5Y",
    "days_score",
    "days_direction",
    "days_confidence",
    "days_coverage",
    "days_setup",
    "weeks_score",
    "weeks_direction",
    "weeks_confidence",
    "weeks_coverage",
    "weeks_setup",
    "months_score",
    "months_direction",
    "months_confidence",
    "months_coverage",
    "months_setup",
    "years_score",
    "years_direction",
    "years_confidence",
    "years_coverage",
    "years_setup",
]


def _write_prediction_csv(csv_path: Path, rows: list[list[object]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(CSV_HEADERS)
        writer.writerows(rows)


def _read_csv_dicts(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


class TestTradingViewMovePredictionHistoryAggregator(unittest.TestCase):
    def test_aggregates_profile_history_across_date_and_session_folders(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            input_root = temp_root / "inputs"
            output_root = temp_root / "outputs"

            file_1 = (
                input_root
                / "16_04_2026"
                / "marketOpen"
                / "tradingview_move_prediction__all_industries__min_1000000000__max_none__profile_breakout_long.csv"
            )
            file_2 = (
                input_root
                / "16_04_2026"
                / "marketClose"
                / "tradingview_move_prediction__all_industries__min_1000000000__max_none__profile_breakout_long.csv"
            )
            file_3 = (
                input_root
                / "17_04_2026"
                / "marketOpen"
                / "tradingview_move_prediction__all_industries__min_1000000000__max_none__profile_breakout_long.csv"
            )
            raw_data_file = (
                input_root
                / "17_04_2026"
                / "marketOpen"
                / "tradingview_move_prediction__all_industries__min_1000000000__max_none__profile_breakout_long__raw_data.csv"
            )
            (input_root / "17_04_2026" / "marketClose").mkdir(
                parents=True, exist_ok=True
            )

            _write_prediction_csv(
                file_1,
                [
                    [
                        "NASDAQ:AAA",
                        "Alpha Holdings",
                        "AAA",
                        "NASDAQ",
                        "United States",
                        "Technology",
                        "Software",
                        "america",
                        "breakout_long",
                        1_500_000_000,
                        100,
                        3,
                        4,
                        5,
                        10,
                        12,
                        30,
                        1.20,
                        "Up",
                        90,
                        1.0,
                        "opening strength",
                        1.00,
                        "Up",
                        88,
                        1.0,
                        "opening strength",
                        0.70,
                        "Up",
                        85,
                        1.0,
                        "opening strength",
                        0.40,
                        "Up",
                        80,
                        1.0,
                        "opening strength",
                    ],
                    [
                        "NASDAQ:BBB",
                        "Beta Industries",
                        "BBB",
                        "NASDAQ",
                        "United States",
                        "Technology",
                        "Hardware",
                        "america",
                        "breakout_long",
                        1_200_000_000,
                        20,
                        1,
                        2,
                        3,
                        5,
                        7,
                        10,
                        0.80,
                        "Neutral",
                        80,
                        1.0,
                        "coiling",
                        0.90,
                        "Up",
                        81,
                        1.0,
                        "coiling",
                        0.60,
                        "Up",
                        79,
                        1.0,
                        "coiling",
                        0.30,
                        "Neutral",
                        75,
                        1.0,
                        "coiling",
                    ],
                ],
            )
            _write_prediction_csv(
                file_2,
                [
                    [
                        "NASDAQ:AAA",
                        "Alpha Holdings",
                        "AAA",
                        "NASDAQ",
                        "United States",
                        "Technology",
                        "Software",
                        "america",
                        "breakout_long",
                        1_550_000_000,
                        102,
                        4,
                        5,
                        7,
                        12,
                        14,
                        32,
                        1.00,
                        "Up",
                        88,
                        1.0,
                        "holding gains",
                        1.05,
                        "Up",
                        87,
                        1.0,
                        "holding gains",
                        0.75,
                        "Up",
                        84,
                        1.0,
                        "holding gains",
                        0.45,
                        "Up",
                        79,
                        1.0,
                        "holding gains",
                    ],
                    [
                        "NASDAQ:BBB",
                        "Beta Industries",
                        "BBB",
                        "NASDAQ",
                        "United States",
                        "Technology",
                        "Hardware",
                        "america",
                        "breakout_long",
                        1_250_000_000,
                        22,
                        3,
                        6,
                        9,
                        11,
                        13,
                        16,
                        1.40,
                        "Strong Up",
                        93,
                        1.0,
                        "closing breakout",
                        1.20,
                        "Strong Up",
                        90,
                        1.0,
                        "closing breakout",
                        0.85,
                        "Up",
                        86,
                        1.0,
                        "closing breakout",
                        0.50,
                        "Up",
                        82,
                        1.0,
                        "closing breakout",
                    ],
                ],
            )
            _write_prediction_csv(
                file_3,
                [
                    [
                        "NASDAQ:AAA",
                        "Alpha Holdings",
                        "AAA",
                        "NASDAQ",
                        "United States",
                        "Technology",
                        "Software",
                        "america",
                        "breakout_long",
                        1_580_000_000,
                        108,
                        6,
                        8,
                        12,
                        18,
                        20,
                        36,
                        1.60,
                        "Strong Up",
                        95,
                        1.0,
                        "follow-through",
                        1.30,
                        "Strong Up",
                        91,
                        1.0,
                        "follow-through",
                        0.95,
                        "Up",
                        89,
                        1.0,
                        "follow-through",
                        0.60,
                        "Up",
                        84,
                        1.0,
                        "follow-through",
                    ],
                    [
                        "NASDAQ:BBB",
                        "Beta Industries",
                        "BBB",
                        "NASDAQ",
                        "United States",
                        "Technology",
                        "Hardware",
                        "america",
                        "breakout_long",
                        1_210_000_000,
                        24,
                        2,
                        4,
                        8,
                        10,
                        11,
                        15,
                        1.10,
                        "Up",
                        86,
                        1.0,
                        "cooling but positive",
                        1.00,
                        "Up",
                        85,
                        1.0,
                        "cooling but positive",
                        0.80,
                        "Up",
                        83,
                        1.0,
                        "cooling but positive",
                        0.55,
                        "Up",
                        80,
                        1.0,
                        "cooling but positive",
                    ],
                ],
            )
            _write_prediction_csv(raw_data_file, [])

            selected_folders = build_move_prediction_history_inputs_from_folder_names(
                base_dir=input_root,
                folder_names=["16_04_2026", "17_04_2026"],
            )

            result = run_move_prediction_history_aggregation(
                input_paths=selected_folders,
                output_dir=output_root,
            )

            profile_result = result["profiles"]["breakout_long"]
            all_history_rows = _read_csv_dicts(result["history_csv"])
            history_rows = _read_csv_dicts(profile_result["history_csv"])
            summary_rows = _read_csv_dicts(profile_result["summary_csv"])
            manifest_rows = _read_csv_dicts(result["manifest_csv"])

            self.assertEqual(6, len(history_rows))
            self.assertEqual(2, len(summary_rows))
            self.assertEqual(3, len(manifest_rows))
            self.assertEqual(
                [
                    "2026-04-16 marketOpen",
                    "2026-04-16 marketClose",
                    "2026-04-17 marketOpen",
                ],
                [row["snapshot_label"] for row in manifest_rows],
            )

            all_history_snapshot_labels: list[str] = []
            for row in all_history_rows:
                snapshot_label = row["snapshot_label"]
                if (
                    not all_history_snapshot_labels
                    or all_history_snapshot_labels[-1] != snapshot_label
                ):
                    all_history_snapshot_labels.append(snapshot_label)
            self.assertEqual(
                [
                    "2026-04-16 marketOpen",
                    "2026-04-16 marketClose",
                    "2026-04-17 marketOpen",
                ],
                all_history_snapshot_labels,
            )

            bbb_history = [row for row in history_rows if row["symbol"] == "NASDAQ:BBB"]
            self.assertEqual(
                [
                    "2026-04-16 marketOpen",
                    "2026-04-16 marketClose",
                    "2026-04-17 marketOpen",
                ],
                [row["snapshot_label"] for row in bbb_history],
            )
            self.assertEqual("2", bbb_history[0]["days_rank"])
            self.assertEqual("1", bbb_history[1]["days_rank"])
            self.assertEqual("1", bbb_history[1]["days_rank_improvement_vs_previous"])
            self.assertAlmostEqual(
                0.6,
                float(bbb_history[1]["days_score_delta_vs_previous"]),
                places=6,
            )
            self.assertAlmostEqual(
                (24 - 22) / 22 * 100,
                float(bbb_history[2]["close_return_pct_vs_previous_snapshot"]),
                places=6,
            )

            aaa_summary = next(
                row for row in summary_rows if row["symbol"] == "NASDAQ:AAA"
            )
            self.assertEqual("3", aaa_summary["snapshots_seen"])
            self.assertEqual("1", aaa_summary["days_best_rank"])
            self.assertAlmostEqual(
                1.6,
                float(aaa_summary["days_last_score"]),
                places=6,
            )
            self.assertAlmostEqual(
                8.0,
                float(aaa_summary["Perf.W_last"]),
                places=6,
            )
            self.assertAlmostEqual(
                8.0,
                float(aaa_summary["close_return_pct_total"]),
                places=6,
            )

    def test_folder_name_resolution_requires_marketopen_and_marketclose(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            base_dir = temp_root / "history_root"
            snapshot_dir = base_dir / "16_04_2026"
            (snapshot_dir / "marketOpen").mkdir(parents=True, exist_ok=True)

            with self.assertRaises(ValueError):
                build_move_prediction_history_inputs_from_folder_names(
                    base_dir=base_dir,
                    folder_names=["16_04_2026"],
                )


if __name__ == "__main__":
    unittest.main()
