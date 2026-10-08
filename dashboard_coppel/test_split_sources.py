import hashlib
import tempfile
import unittest
from pathlib import Path

from split_sources import split_file, split_sources


class SplitSourcesTests(unittest.TestCase):
    def test_parts_preserve_bytes_headers_and_multiline_records(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.csv"
            original = b'"a";"b"\r\n"1";"multi\r\nline"\r\n"2";"quoted ""value"""\r\n"3";"last"'
            source.write_bytes(original)
            stage = root / "stage"
            stage.mkdir()
            manifest = split_file(source, stage, limit=42)
            header = b'"a";"b"\r\n'
            reconstructed = header + b"".join((stage / part["name"]).read_bytes()[len(header):] for part in manifest["parts"])
            self.assertEqual(reconstructed, original)
            self.assertEqual(manifest["records"], 3)
            self.assertTrue(all(part["bytes"] <= 42 for part in manifest["parts"]))
            self.assertEqual(manifest["sha256"], hashlib.sha256(original).hexdigest())

    def test_moves_originals_only_after_verification_and_refuses_resplitting(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "root"
            downloads = Path(folder) / "downloads"
            root.mkdir()
            source = root / "original.csv"
            original = b"a;b\n1;2\n"
            source.write_bytes(original)
            backup = split_sources(root, downloads)
            self.assertFalse(source.exists())
            self.assertEqual((backup / source.name).read_bytes(), original)
            self.assertEqual((root / "original_parte_0001.csv").read_bytes(), original)
            with self.assertRaisesRegex(ValueError, "Ya hay partes"):
                split_sources(root, downloads)

    def test_rejects_oversized_record_without_moving_original(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.csv"
            source.write_bytes(b"a;b\n" + b"x" * 100 + b"\n")
            with self.assertRaisesRegex(ValueError, "supera"):
                split_file(source, root, limit=20)
            self.assertTrue(source.exists())


if __name__ == "__main__":
    unittest.main()
