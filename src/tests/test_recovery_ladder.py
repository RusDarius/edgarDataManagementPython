from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


class RecoveryMathTests(unittest.TestCase):
    def test_average_after_add_and_the_add_that_breaks_even(self):
        from generic_utils.recovery_ladder import add_to_break_even_at, average_after_add

        shares, avg = 100.0, 50.0  # $5,000 cost, price now 40
        new_shares, new_avg = average_after_add(shares, avg, 2000.0, 40.0)
        self.assertEqual((new_shares, round(new_avg, 4)), (150.0, 46.6667))
        add = add_to_break_even_at(shares, avg, 40.0, 45.0)
        self.assertAlmostEqual(average_after_add(shares, avg, add, 40.0)[1], 45.0)
        self.assertEqual(add_to_break_even_at(shares, avg, 40.0, 55.0), 0.0)  # target above the average
        self.assertIsNone(add_to_break_even_at(shares, avg, 40.0, 39.0))  # target under the buy price


class RecoveryBuildTests(unittest.TestCase):
    def test_lines_and_ladder_from_a_small_pack(self):
        from generic_utils.recovery_ladder import build_recovery_ladder

        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp)
            _write_csv(pack / "book_risk" / "book_risk.csv", [
                {"symbol": "NYSE:DOWN", "industry": "Chips", "shares": 100, "average_price": 50, "close_quote": 40, "price_currency": "USD",
                 "fx_rate_price_to_usd": 1.0, "value_usd": 4000, "cost_usd": 5000},
                {"symbol": "OMXCOP:EU", "industry": "Pharma", "shares": 10, "average_price": 300, "close_quote": 250, "price_currency": "DKK",
                 "fx_rate_price_to_usd": 0.15, "value_usd": 375, "cost_usd": 450}])
            _write_csv(pack / "deterioration" / "names.csv", [
                {"symbol": "NYSE:DOWN", "sma50": 45, "half_px": 55, "street_px": 60, "bounce_high": 40, "bounce_low": 38, "bounce_kill": 36,
                 "det_seen": "-", "let_fx_28d_mean": 6.5},
                {"symbol": "OMXCOP:EU", "sma50": 270, "half_px": "", "street_px": 46, "bounce_high": 245, "bounce_low": 240, "bounce_kill": 230,
                 "det_seen": "", "let_fx_28d_mean": ""}])
            payload = build_recovery_ladder(pack, recipe={"add_usd": [1000], "add_levels": ["bounce_high"], "gain_pct": [10],
                                                          "kill_fields": ["bounce_kill"], "solve_targets": ["sma50", "street_px"],
                                                          "solve_at": ["bounce_high"],
                                                          "level_fields": ["sma50", "half_px", "street_px", "bounce_high", "bounce_low", "bounce_kill"],
                                                          "state_fields": ["det_seen"]})
        lines = {r["symbol"]: r for r in payload["lines"]}
        down = lines["NYSE:DOWN"]
        self.assertEqual((down["pnl_usd"], down["be_from_close_pct"], down["gain10_px"]), (-1000, 25.0, 55.0))
        self.assertAlmostEqual(down["add_be_at_sma50_usd"], 4000, places=0)  # (5000 - 4500) / (45 / 40 - 1)
        self.assertEqual(down["add_be_at_street_px_usd"], 0)
        self.assertIsNone(lines["OMXCOP:EU"]["street_px"])  # a USD target is not set beside DKK levels
        rung = next(r for r in payload["ladder"] if r["symbol"] == "NYSE:DOWN")
        self.assertEqual((rung["add_px"], rung["new_avg"], rung["add_kill_usd"], rung["line_kill_usd"]), (40.0, 48.0, -100, -500))
        self.assertAlmostEqual(rung["be_from_add_pct"], 20.0)
        eu = next(r for r in payload["ladder"] if r["symbol"] == "OMXCOP:EU")
        self.assertAlmostEqual(eu["new_avg"], (3000 + 1000 / 0.15) / (10 + 1000 / 0.15 / 245), places=2)


if __name__ == "__main__":
    unittest.main()
