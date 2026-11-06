#!/usr/bin/env python3
"""test_history.py — the hourly snapshot and the week-over-week trend behind 'getting worse'."""
import os
import tempfile
import unittest

import history as H

DAY = 86400


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "sub", "history.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def test_records_at_most_hourly_and_keeps_only_well_formed_rows(self):
        t0 = 1_700_000_000
        self.assertTrue(H.record(self.path, t0, {"Docs": [0, 100, 0, 2, 0], "junk": [1, 2]}))
        self.assertFalse(H.record(self.path, t0 + 600, {"Docs": [0, 101, 0, 2, 0]}))   # too soon
        self.assertTrue(H.record(self.path, t0 + 3600, {"Docs": [1, 101, 1, 2, 0], 7: [0, 0, 0, 0, 0]}))
        rows = H.load(self.path)
        self.assertEqual([r["a"] for r in rows], [{"Docs": [0, 100, 0, 2, 0]}, {"Docs": [1, 101, 1, 2, 0]}])
        with open(self.path, "a") as handle:
            handle.write("not json\n{\"t\": \"x\"}\n")
        self.assertEqual(len(H.load(self.path)), 2)

    def test_trend_compares_now_with_a_week_ago_and_names_why(self):
        t0 = 1_700_000_000
        H.record(self.path, t0, {"Docs": [0, 100, 0, 2, 0], "api": [20, 50, 0, 0, 0], "ok": [0, 9, 1, 1, 1]})
        H.record(self.path, t0 + 3 * DAY, {"Docs": [0, 100, 1, 2, 0], "api": [23, 50, 0, 0, 0], "ok": [0, 9, 0, 0, 0]})
        H.record(self.path, t0 + 8 * DAY, {"Docs": [0, 100, 3, 4, 1], "api": [28, 50, 0, 0, 0], "ok": [0, 9, 0, 0, 0], "new": [0, 1, 0, 0, 0]})
        tr = H.trend(self.path, t0 + 8 * DAY)
        self.assertTrue(tr["Docs"]["worse"])
        self.assertEqual((tr["Docs"]["dstale"], tr["Docs"]["dtrouble"], tr["Docs"]["dfailed"]), (3, 2, 1))
        self.assertIn("+3 stale cron lines", tr["Docs"]["why"])
        self.assertTrue(tr["api"]["worse"])              # cooling: untouched for weeks and still drifting
        self.assertIn("untouched 28d", tr["api"]["why"])
        self.assertTrue(tr["ok"]["better"])
        self.assertNotIn("new", tr)                       # no earlier row to compare with
        self.assertEqual(tr["Docs"]["span_days"], 8.0)

    def test_planets_that_sit_still_on_purpose_are_not_untouched(self):
        t0 = 1_700_000_000
        now = t0 + 8 * DAY
        H.record(self.path, t0, {"Old": [8, 40, 0, 0, 0]})
        H.record(self.path, now, {"Old": [40, 40, 0, 0, 0]})      # idle for 40 days, nothing else
        for kind in ("archive", "reference", "knowledge", "bus"):
            tr = H.trend(self.path, now, kinds={"Old": kind})["Old"]
            self.assertFalse(tr["worse"], kind)
            self.assertNotIn("untouched", tr["why"], kind)
        for kind in ("code", "shared", "config", None):
            tr = H.trend(self.path, now, kinds={"Old": kind})["Old"]
            self.assertTrue(tr["worse"], kind)
            self.assertIn("untouched 40d", tr["why"], kind)
        self.assertTrue(H.trend(self.path, now)["Old"]["worse"])       # no kinds given: as before
        other = os.path.join(self.tmp.name, "other.jsonl")
        H.record(other, t0, {"Old": [8, 40, 0, 0, 0]})
        H.record(other, now, {"Old": [40, 40, 2, 0, 0]})          # two cron lines went stale too
        tr = H.trend(other, now, kinds={"Old": "archive"})["Old"]
        self.assertTrue(tr["worse"])                              # a real signal still counts
        self.assertEqual(tr["why"], "+2 stale cron lines")

    def test_a_short_or_empty_record_gives_no_trend(self):
        self.assertEqual(H.trend(self.path, 0), {})
        H.record(self.path, 1_700_000_000, {"Docs": [0, 1, 0, 0, 0]})
        H.record(self.path, 1_700_000_000 + 7200, {"Docs": [0, 1, 5, 0, 0]})
        self.assertEqual(H.trend(self.path, 1_700_000_000 + 7200), {})   # two hours apart says nothing yet


if __name__ == "__main__":
    unittest.main()
