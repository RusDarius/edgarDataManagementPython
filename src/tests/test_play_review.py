from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _pack(root: Path, name: str, created: str, closes: dict[str, float]) -> Path:
    pack = root / name
    pack.mkdir(parents=True)
    (pack / "briefing_pack.json").write_text(json.dumps({"created_at_utc": created}), encoding="utf-8")
    _write_csv(pack / "movers" / "us2b_tape.csv", [{"symbol": s, "close": c} for s, c in closes.items()])
    return pack


class PlayReviewTests(unittest.TestCase):
    def test_band_state(self):
        from generic_utils.play_review import band_state

        self.assertEqual(band_state(110, 95, 105, 90), "ABOVE_BAND")
        self.assertEqual(band_state(100, 95, 105, 90), "IN_BAND")
        self.assertEqual(band_state(92, 95, 105, 90), "UNDER_BAND")
        self.assertEqual(band_state(89, 95, 105, 90), "UNDER_KILL")
        self.assertIsNone(band_state(100, None, 105, 90))

    def test_market_ret_is_the_median_over_both_tapes(self):
        from generic_utils.play_review import market_ret

        prior = {"A": {"close": 100}, "B": {"close": 100}, "C": {"close": 100}, "D": {"close": 50}}
        later = {"A": {"close": 101}, "B": {"close": 103}, "C": {"close": 90}}
        self.assertAlmostEqual(market_ret(prior, later), 1.0)

    def test_build_on_two_small_packs(self):
        from generic_utils.play_review import build_play_review, prior_judgment_path, write_play_review

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prior = _pack(root, "briefing_pack_20261027", "2026-10-27T17:12:00+00:00",
                          {"NASDAQ:AAA": 100.0, "NYSE:BBB": 50.0, "NYSE:CCC": 20.0})
            _write_csv(prior / "week_plays" / "plays.csv", [
                {"symbol": "NASDAQ:AAA", "bounce_low": "92", "bounce_high": "96", "bounce_kill": "88", "dec_28d": "10", "phase": "pre"}])
            later = _pack(root, "briefing_pack_20261028", "2026-10-28T17:12:00+00:00",
                          {"NASDAQ:AAA": 94.0, "NYSE:BBB": 51.0, "NYSE:CCC": 20.2})
            _write_csv(later / "week_plays" / "plays.csv", [
                {"symbol": "NASDAQ:AAA", "phase": "post", "react_fx": "-7", "surprise_mix": "B/M", "dec_28d": "9"}])
            canvases = root / "canvases"
            (canvases / "20261027").mkdir(parents=True)
            judgment = canvases / "20261027" / "judgment.json"
            judgment.write_text(json.dumps({"pack": prior.as_posix(), "courses": {
                "NASDAQ:AAA": {"Course": "BUY_PRE", "Grade": "Med"}, "NYSE:BBB": {"Course": "PASS"}},
                "pages": {"week_plays": {"play_ids": ["NASDAQ:AAA"]}}}), encoding="utf-8")
            self.assertEqual(prior_judgment_path("20261028", root=canvases), judgment)
            self.assertIsNone(prior_judgment_path("20261027", root=canvases))
            payload = build_play_review(judgment, pack_dir=later, history_root=root / "no_day_files")
            calls = {r["symbol"]: r for r in payload["calls"]}
            self.assertEqual(payload["spec"]["mkt_ret"], 1.0)
            self.assertEqual(calls["NASDAQ:AAA"]["ret_pct"], -6.0)
            self.assertEqual(calls["NASDAQ:AAA"]["ret_fx"], -7.0)
            self.assertEqual(calls["NASDAQ:AAA"]["close_state"], "IN_BAND")
            self.assertEqual(calls["NASDAQ:AAA"]["play_rank"], 1)
            self.assertEqual(calls["NASDAQ:AAA"]["now_surprise_mix"], "B/M")
            self.assertIsNone(calls["NYSE:BBB"]["close_state"])
            courses = {r["course"]: r for r in payload["by_course"]}
            self.assertEqual(courses["(week plays list)"]["n"], 1)
            self.assertEqual(courses["PASS"]["ret_fx_mean"], 1.0)
            exported = write_play_review(payload, later / "play_review")
            self.assertTrue(Path(exported["duckdb"]).exists())


if __name__ == "__main__":
    unittest.main()
