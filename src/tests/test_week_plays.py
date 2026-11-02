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


def _events() -> list[dict]:
    out = []
    for i in range(20):
        out.append({"symbol": f"NYSE:E{i}", "print_date": "2026-05-01", "slot": "BMO", "react": i - 10, "react_fx": i - 10,
                    "runup": (i % 5) - 2, "eps_surprise": (i % 4) - 1, "rev_surprise": 1, "prev_react": (i % 3) - 1,
                    "eps_rev_3m_pre": i - 5, "pt_up_pre": i, "surprise_mix": "B/B", "drift_fx_28d": 1.0, "hold_fx_28d": 2.0})
    return out


class WeekPlaysEventTests(unittest.TestCase):
    def test_phase_and_event_mapping(self):
        from generic_utils.week_plays import phase_of, post_event, pre_event

        self.assertEqual(phase_of(None), "none")
        self.assertEqual(phase_of({"printed": "False"}), "pre")
        self.assertEqual(phase_of({"printed": "True"}), "post")
        pre = pre_event({"runup": "-12", "react_last": "-7", "eps_rev_3m": "1.5", "pt_up": "30"})
        self.assertEqual((pre["runup"], pre["prev_react"], pre["eps_rev_3m_pre"], pre["pt_up_pre"]), (-12.0, -7.0, 1.5, 30.0))
        row = {"react_on": "2026-10-27", "day": "-8", "post_chg": "2", "last_surprise": "4", "rev_surprise": "1", "surprise_mix": "B/B"}
        self.assertEqual(post_event(row, scan_day="2026-10-27", mkt_day=0.5)["react_fx"], -8.5)
        self.assertEqual(post_event({**row, "react_on": "2026-10-28"}, scan_day="2026-10-27", mkt_day=0.5)["react"], 2.0)
        self.assertIsNone(post_event({**row, "react_on": "2026-10-26"}, scan_day="2026-10-27", mkt_day=0.5)["react_fx"])

    def test_print_night_carries_the_after_hours_read_and_set_up(self):
        from generic_utils.week_plays import attach_tail_usd, post_event

        row = {"react_on": "2026-10-29", "slot": "AMC", "day": "1.6", "post_chg": "-9.4", "runup": "-7.7", "react_last": "-7.1"}
        night = post_event(row, scan_day="2026-10-28", mkt_day=0.4)
        self.assertEqual((night["ah_read"], night["runup"], night["prev_react"], night["react_fx"]), (-9.4, -7.7, -7.1, -9.8))
        day = post_event({**row, "react_on": "2026-10-28"}, scan_day="2026-10-28", mkt_day=0.4)
        self.assertEqual((day["ah_read"], day["runup"], day["prev_react"]), (None, None, None))
        self.assertIsNone(post_event({**row, "slot": "BMO"}, scan_day="2026-10-28", mkt_day=0.4)["ah_read"])
        pending = post_event({**row, "surprise_mix": "M/-", "rev_posted": "False"}, scan_day="2026-10-28", mkt_day=0.4)
        self.assertIsNone(pending["surprise_mix"])  # revenue not posted: no mix cell
        self.assertEqual(post_event({**row, "surprise_mix": "M/B", "rev_posted": "False"}, scan_day="2026-10-28", mkt_day=0.4)["surprise_mix"], "M/B")
        rec = {"book_usd": "3000", "setup_rx_q10": -8.2}
        attach_tail_usd(rec, "setup_rx")
        self.assertEqual(rec["rx_tail_usd"], -246.0)
        book = {"value_usd": 2000.0, "setup_rx_q10": -10.0}
        attach_tail_usd(book, "setup_rx")
        self.assertEqual(book["rx_tail_usd"], -200.0)
        attach_tail_usd(book, None)
        self.assertIsNone(book["rx_tail_usd"])

    def test_cells_match_signal_lab_labels(self):
        from generic_utils.signal_lab import load_lab_recipe, print_cell_studies, print_cells
        from generic_utils.week_plays import attach_cells

        events = _events()
        lab = load_lab_recipe()
        keys = {name: key for name, key, _ in print_cell_studies(events, lab)}
        measured = print_cells(events, lab)
        lookup = {(r["study"], r["cell"]): r for r in measured}
        specs = [{"col": "setup", "study": "run-up x previous reaction", "phase": "pre", "measure": "hold_fx_28d"},
                 {"col": "mix", "study": "surprise mix (EPS/revenue)", "phase": "post", "measure": "drift_fx_28d"}]
        rec: dict = {}
        attach_cells(rec, {"runup": -3.0, "prev_react": -1.0, "eps_rev_3m_pre": 0.0, "pt_up_pre": 5.0}, "pre", specs, keys, lookup)
        self.assertEqual(rec["setup_cell"], "sold into & previous print down")
        self.assertEqual(rec["setup_mean"], 2.0)
        self.assertIsNone(rec["mix_cell"])
        self.assertIn(("run-up x previous reaction", rec["setup_cell"]), lookup)

    def test_distances_are_percent_of_band_middle(self):
        from generic_utils.week_plays import attach_distances

        rec = {"close": 110.0, "bounce_low": 95.0, "bounce_high": 105.0, "bounce_kill": 90.0, "pt": 150.0,
               "b7d_q20": 99.0, "b7d_q80": 121.0, "b28d_q20": None, "b28d_q80": 132.0}
        attach_distances(rec)
        self.assertEqual(rec["bmid"], 100.0)
        self.assertEqual(rec["bkill_vs_bmid_pct"], -10.0)
        self.assertEqual(rec["street_vs_bmid_pct"], 50.0)
        self.assertEqual(rec["q80_7d_vs_bmid_pct"], 21.0)
        self.assertEqual(rec["q80_7d_pct"], 10.0)
        self.assertIsNone(rec["q20_28d_pct"])


class WeekPlaysBuildTests(unittest.TestCase):
    def test_build_on_a_small_pack(self):
        from generic_utils.week_plays import build_week_plays, load_recipe, write_week_plays

        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / "briefing_pack_test"
            (pack).mkdir()
            (pack / "briefing_pack.json").write_text(json.dumps({"created_at_utc": "2026-10-27T17:12:00+00:00"}), encoding="utf-8")
            _write_csv(pack / "earnings_week" / "calendar.csv", [
                {"symbol": "NASDAQ:AAA", "week": "W0", "printed": "False", "react_on": "2026-10-28", "close": "100", "day": "1",
                 "runup": "-10", "react_last": "-5", "eps_rev_3m": "1", "pt_up": "20", "pt": "120", "last_surprise": "", "rev_surprise": "",
                 "surprise_mix": "", "post_chg": ""},
                {"symbol": "NYSE:BBB", "week": "W0", "printed": "True", "react_on": "2026-10-27", "close": "50", "day": "-8",
                 "runup": "2", "react_last": "1", "eps_rev_3m": "3", "pt_up": "10", "pt": "55", "last_surprise": "5", "rev_surprise": "2",
                 "surprise_mix": "B/B", "post_chg": ""},
            ])
            _write_csv(pack / "alpha_model" / "live.csv", [{"symbol": "NASDAQ:AAA", "dec_7d": "9", "dec_28d": "10", "b7d_q20": "95", "b7d_q80": "112"}])
            _write_csv(pack / "signal_lab" / "print_events.csv", _events())
            from generic_utils.signal_lab import load_lab_recipe, print_cells
            _write_csv(pack / "signal_lab" / "print_cells.csv", print_cells(_events(), load_lab_recipe()))
            _write_csv(pack / "movers" / "us2b_tape.csv", [{"symbol": "NYSE:CCC", "day": "0.5"}, {"symbol": "NYSE:DDD", "day": "1.5"}])
            payload = build_week_plays(pack, recipe=load_recipe())
            plays = {r["symbol"]: r for r in payload["plays"]}
            self.assertEqual(plays["NASDAQ:AAA"]["phase"], "pre")
            self.assertEqual(plays["NASDAQ:AAA"]["setup_cell"], "sold into & previous print down")
            self.assertEqual(plays["NASDAQ:AAA"]["dec_28d"], "10")
            self.assertEqual(plays["NYSE:BBB"]["phase"], "post")
            self.assertEqual(plays["NYSE:BBB"]["react_fx"], -9.0)
            self.assertEqual(plays["NYSE:BBB"]["mix_cell"], "B/B")
            self.assertIsNone(plays["NYSE:BBB"]["setup_cell"])
            self.assertEqual(payload["book"], [])
            self.assertEqual(payload["spec"]["mkt_day"], 1.0)
            exported = write_week_plays(payload, pack / "week_plays")
            self.assertEqual(exported["tables"]["plays"], 2)
            self.assertTrue(Path(exported["duckdb"]).exists())


if __name__ == "__main__":
    unittest.main()
