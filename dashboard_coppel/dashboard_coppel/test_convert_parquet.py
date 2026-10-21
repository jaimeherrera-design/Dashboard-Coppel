import tempfile
import unittest
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq

from convert_parquet import convert_originals


class ParquetConversionTests(unittest.TestCase):
    def test_excludes_whole_day_preserves_columns_values_and_originals(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            content = (
                b'\xef\xbb\xbfCall end;DDI;Phone;notes\n'
                b'2026-10-06 23:59:59;0056;00123;"a\nb"\n'
                b'2026-10-07 00:00:00;0056;2;excluded\n'
                b'2026-10-07T23:59:59;0056;3;excluded\n'
                b'2026-10-08 00:00:00;0056;4;NA\n'
                b'invalid;;;null\n'
            )
            for name in ("3meses_v2.csv", "3meses_v3.csv"):
                (root / name).write_bytes(content)
            results = convert_originals(root, date(2026, 10, 7))
            for result in results:
                self.assertEqual((result["read"], result["excluded"], result["kept"]), (5, 2, 3))
            table = pq.read_table(root / "3meses_v2.parquet")
            self.assertEqual(table.column_names, ["Call end", "DDI", "Phone", "notes"])
            self.assertEqual(table["DDI"].to_pylist(), ["0056", "0056", ""])
            self.assertEqual(table["Phone"].to_pylist(), ["00123", "4", ""])
            self.assertEqual(table["notes"].to_pylist(), ["a\nb", "NA", "null"])
            for name in ("3meses_v2.csv", "3meses_v3.csv"):
                self.assertEqual((root / name).read_bytes(), content)
            with self.assertRaises(FileExistsError):
                convert_originals(root, date(2026, 10, 7))

    def test_invalid_source_does_not_publish_partial_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "3meses_v2.csv").write_text("Call end;value\n2026-10-06;x\n")
            (root / "3meses_v3.csv").write_text("wrong;value\n2026-10-06;x\n")
            with self.assertRaises(ValueError):
                convert_originals(root, date(2026, 10, 7))
            self.assertFalse(list(root.glob("*.parquet")))


if __name__ == "__main__":
    unittest.main()
