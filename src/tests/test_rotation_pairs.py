from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path


def _store(path: Path, closes: dict[tuple[str, str], list[float]], first: date) -> None:
    """A tiny panel store: (symbol, industry) -> daily closes; every name $10B, one split sequence."""
    import duckdb

    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE panel (as_of DATE, symbol VARCHAR, exchange VARCHAR, type VARCHAR, ind VARCHAR, mcap DOUBLE, close DOUBLE)")
    con.execute("CREATE TABLE series (symbol VARCHAR, as_of DATE, split_flag BOOLEAN, split_seq INTEGER)")
    for (sym, ind), series in closes.items():
        for i, c in enumerate(series):
            d = first + timedelta(days=i)
            con.execute("INSERT INTO panel VALUES (?, ?, ?, 'stock', ?, 1e10, ?)", [d, sym, sym.split(":")[0], ind, c])
            con.execute("INSERT INTO series VALUES (?, ?, FALSE, 0)", [sym, d])
    con.close()


class RotationPairsTests(unittest.TestCase):
    def test_pure_reads(self):
        from generic_utils.rotation_pairs import extreme_after, rolling_returns, thin, window_return

        lv = [100.0, 110.0, 121.0]
        self.assertAlmostEqual(window_return(lv, 2), 21.0)
        self.assertIsNone(window_return(lv, 3))
        self.assertEqual([round(v, 6) for v in rolling_returns(lv, 1)], [10.0, 10.0])
        self.assertEqual(thin([0, 1, 2, 5, 6, 11], 5), 3)  # 0, 5, 11
        # a saw-tooth line: every top-tail start is followed by a fall (it reverts)
        saw = [100.0 + (10.0 if (k // 5) % 2 else 0.0) for k in range(60)]
        out = extreme_after(saw, 5, 0.1, 5)
        self.assertGreater(out["hi_n_5"], 0)
        self.assertEqual(out["hi_rev_5"], 100.0)

    def test_build_on_a_small_store(self):
        from generic_utils import rotation_pairs as rp

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            n = 80
            up = [100.0 * (1.01 ** i) for i in range(n)]
            down = [100.0 * (0.99 ** i) for i in range(n)]
            flat = [100.0 + (0.5 if i % 2 else 0.0) for i in range(n)]
            closes = {("NASDAQ:AAA", "Semiconductors"): up, ("NASDAQ:BBB", "Semiconductors"): up,
                      ("NYSE:CCC", "Household/Personal Care"): down, ("NYSE:DDD", "Household/Personal Care"): down,
                      ("NYSE:EEE", "Banks"): flat}
            _store(root / "store.duckdb", closes, date(2026, 1, 1))
            recipe = {"floor_mcap": 2e9, "exchanges": ["NASDAQ", "NYSE"], "types": ["stock"], "clip_pct": [-60, 150],
                      "baskets": {"chips": {"industries": ["Semiconductors"]}, "staples": {"industries": ["Household/Personal Care"]},
                                  "one": {"ids": ["NYSE:EEE"]}},
                      "pairs": [["chips", "staples"], ["chips", "missing"]], "windows": [5, 21], "z_window": 10,
                      "recent_steps": 30, "extreme_q": 0.1, "after_steps": [5], "industries": {"min_names": 2, "min_cap_b": 1, "top_pairs": 5},
                      "series_baskets": ["chips", "staples"]}
            payload = rp.build_rotation_pairs(recipe=recipe, store=root / "store.duckdb")
            b = {r["basket"]: r for r in payload["baskets"]}
            self.assertEqual(b["chips"]["n"], 2)
            self.assertAlmostEqual(b["chips"]["r_5"], (1.01 ** 5 - 1) * 100, places=2)
            self.assertEqual(b["one"]["n"], 1)
            p = {r["pair"]: r for r in payload["pairs"]}
            self.assertGreater(p["chips / staples"]["rel_21"], 0)  # chips beat staples every step
            self.assertEqual(p["chips / missing"]["n_steps"], 0)
            self.assertEqual(len(payload["ind_pairs"]), 1)  # two industries with 2+ names
            kinds = {r["kind"] for r in payload["series"]}
            self.assertEqual(kinds, {"floor", "basket", "pair"})
            out = rp.write_rotation_pairs(payload, root / "out")
            self.assertTrue(Path(out["highlights"]).exists())


if __name__ == "__main__":
    unittest.main()
