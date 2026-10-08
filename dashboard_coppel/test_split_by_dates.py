import tempfile
import unittest
from datetime import date
from pathlib import Path

from split_by_dates import prepare_parts


class DateSplitTests(unittest.TestCase):
    def test_inclusive_date_filter_preserves_entire_last_day(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.csv"
            source.write_bytes(
                b'Call end;value\n2026-05-31 23:59:59;before\n'
                b'2026-06-01 00:00:00;"first\nline"\n'
                b'2026-10-06 23:59:59;last\n2026-10-07 00:00:00;after\ninvalid;unknown\n'
            )
            stage = root / "stage"
            stage.mkdir()
            manifest = prepare_parts([source], stage, limit=100,
                                     start=date(2026, 6, 1), end=date(2026, 10, 6))
            self.assertEqual(manifest["records"], 2)
            self.assertEqual(manifest["sources"][0]["excluded_records"], 3)
            self.assertEqual(manifest["parts"][0]["start"], "2026-06-01")
            self.assertEqual(manifest["parts"][-1]["end"], "2026-10-06")
            self.assertTrue(all(p["bytes"] <= 100 for p in manifest["parts"]))
            with self.assertRaises(ValueError):
                prepare_parts([source], stage, start=date(2026, 10, 6), end=date(2026, 6, 1))

    def test_exact_ranges_limits_and_invalid_dates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.csv"
            source.write_bytes(b'Call end;value\n2026-07-02 10:00:00;"a\nb"\n2026-06-01 10:00:00;x\ninvalid;y\n2026-06-01 11:00:00;z\n')
            stage = root / "stage"
            stage.mkdir()
            manifest = prepare_parts([source], stage, limit=65)
            self.assertEqual(manifest["records"], 4)
            self.assertTrue(all(part["bytes"] <= 65 for part in manifest["parts"]))
            self.assertEqual(manifest["parts"][0]["start"], "2026-06-01")
            self.assertEqual(manifest["parts"][-1]["start"], "sin_fecha")
            self.assertTrue(all(part["start"] in part["name"] for part in manifest["parts"]))


if __name__ == "__main__":
    unittest.main()
