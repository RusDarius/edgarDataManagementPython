import unittest


class TestMesh(unittest.TestCase):
    def test_letters_follow_ledger_rules_in_order(self):
        from generic_utils.mesh import attach_mesh

        rows = [
            {"symbol": "A", "early": 0.5, "rng": 40, "dte": 20, "val_vs_ind": -12, "tech_sma": "ABOVE"},
            {"symbol": "B", "early": 0.1, "rng": 90, "dte": 80, "val_vs_ind": 3, "tech_sma": "ABOVE"},
            {"symbol": "C", "sleeve_tags": "['unpaid', 'forming']", "rng": 70, "val_vs_ind": -4},
        ]
        spans = {"A": {"bo_chg": 0.4, "n": 25}, "B": {"bo_chg": 0.4, "n": 5}}
        out = {r["symbol"]: r for r in attach_mesh(rows, spans=spans)}
        self.assertEqual(out["A"]["mesh"], "FCPDT")
        self.assertEqual(out["A"]["nmesh"], 5)
        self.assertEqual(out["B"]["mesh"], "-")  # paid range, too few weeks, no discount
        self.assertEqual(out["C"]["mesh"], "F")  # -4 is not a DISCOUNT
        self.assertEqual(len(out), 3)  # labels only, nothing dropped


if __name__ == "__main__":
    unittest.main()
