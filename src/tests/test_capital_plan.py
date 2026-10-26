import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


BOOK = [
    {"ticker": "MU", "value_usd": 18000, "ind": "Semiconductors"},
    {"ticker": "BIDU", "value_usd": 6000, "ind": "Internet Software/Services"},
    {"ticker": "UNH", "value_usd": 16000, "ind": "Managed Health Care"},
]


class TestCapitalPlan(unittest.TestCase):
    def test_sells_fund_buys_and_cash_reconciles(self):
        from generic_utils.capital_plan import plan_rows

        plan = [
            {"symbol": "BIDU", "course": "EXIT"},
            {"symbol": "MU", "course": "TRIM", "frac": "0.5"},
            {"symbol": "NYSE:BRBR", "course": "NEW", "px": "9.30", "ind": "Food: Specialty/Candy"},
            {"symbol": "UNH", "course": "ADD", "px": "400"},
            {"symbol": "VUAA", "course": "HOLD"},
        ]
        out = plan_rows(plan, BOOK, cash=10000)
        s = out["summary"]
        self.assertEqual(s["nav_usd"], 50000)
        self.assertEqual(s["cash_from_sells_usd"], 15000)
        brbr = next(r for r in out["plan"] if r["symbol"] == "NYSE:BRBR")
        self.assertEqual(brbr["shares"], 537)  # floor(5000 / 9.30)
        self.assertEqual(brbr["size_note"], "")  # rounding to whole shares is not undersized
        unh = next(r for r in out["plan"] if r["symbol"] == "UNH")
        self.assertEqual(unh["shares"], 2)  # add hint 1000 / 400
        self.assertAlmostEqual(s["cash_end_usd"], 10000 + 15000 - brbr["cash_out_usd"] - 800, places=2)
        semis = next(r for r in out["industry"] if r["ind"] == "Semiconductors")
        self.assertEqual(semis["nav_pct_now"], 36.0)
        self.assertEqual(semis["nav_pct_after"], 18.0)

    def test_small_new_line_is_flagged_not_blocked(self):
        from generic_utils.capital_plan import plan_rows

        out = plan_rows([{"symbol": "ABC", "course": "NEW", "usd": "2000", "px": "10"}], BOOK, cash=10000)
        row = out["plan"][0]
        self.assertEqual(row["size_note"], "under_starter_hint")
        self.assertEqual(row["cash_out_usd"], 2000)

    def test_write_capital_plan_exports_three_tables(self):
        from generic_utils.capital_plan import write_capital_plan

        with TemporaryDirectory() as tmp:
            payload = write_capital_plan([{"symbol": "BIDU", "course": "EXIT"}], BOOK, tmp, cash=1000)
            self.assertTrue((Path(tmp) / "capital_plan.duckdb").exists())
            self.assertTrue((Path(tmp) / "queries.txt").exists())
        self.assertEqual(payload["summary"]["cash_end_usd"], 7000)


if __name__ == "__main__":
    unittest.main()
