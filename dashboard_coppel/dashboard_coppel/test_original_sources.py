import tempfile
import unittest
import json
from datetime import date
from unittest.mock import patch
from pathlib import Path

import app
from split_by_dates import split_by_dates


class OriginalSourcesTests(unittest.TestCase):
    def test_filtered_replacement_and_failed_publication_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "project"
            root.mkdir()
            source = root / "3meses_v2.csv"
            content = b"Call end;value\n2026-06-01 10:00:00;x\n2026-10-07 10:00:00;y\n"
            source.write_bytes(content)
            target = Path(folder) / "downloads" / "days"
            split_by_dates(root, target.parent, repository_only=True, output_dir=target)
            previous = {p.name: p.read_bytes() for p in target.iterdir()}
            rename = Path.rename

            def fail_publish(path, destination):
                if path.parent.name.startswith(".csv_split_dates_") and path.suffix == ".csv":
                    raise OSError("publication failed")
                return rename(path, destination)

            with patch.object(Path, "rename", fail_publish):
                with self.assertRaisesRegex(OSError, "publication failed"):
                    split_by_dates(root, target.parent, repository_only=True, output_dir=target,
                                   start=date(2026, 6, 1), end=date(2026, 10, 6), replace=True)
            self.assertEqual(previous, {p.name: p.read_bytes() for p in target.iterdir()})
            split_by_dates(root, target.parent, repository_only=True, output_dir=target,
                           start=date(2026, 6, 1), end=date(2026, 10, 6), replace=True)
            manifest = json.loads((target / "manifest.json").read_text())
            self.assertEqual(manifest["records"], 1)
            self.assertEqual(manifest["parts"][0]["end"], "2026-06-01")
            self.assertEqual(source.read_bytes(), content)
            self.assertEqual(len(list(target.glob("*.csv"))), 1)

    def test_dashboard_uses_all_root_parquet_and_ignores_csv_and_subfolders(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ["3meses_v2.csv", "3meses_v3.csv", "llamadas_parte.csv"]:
                (root / name).write_text("Call end;value\n2026-06-01 10:00:00;x\n")
            (root / "csv_repositorio").mkdir()
            (root / "csv_repositorio" / "part.csv").write_text("ignored")
            (root / "csv_repositorio" / "part.parquet").write_text("ignored")
            for name in ["3meses_v2.parquet", "3meses_v3.parquet", "new.PARQUET"]:
                (root / name).touch()
            sources = app.discover_dashboard_files(root)
            self.assertEqual([Path(signature[0]).name for signature in sources], ["3meses_v2.parquet", "3meses_v3.parquet", "new.PARQUET"])
            for signature in sources:
                Path(signature[0]).unlink()
            with self.assertRaisesRegex(ValueError, "No se encontraron archivos Parquet"):
                app.discover_dashboard_files(root)

    def test_repository_parts_leave_original_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "3meses_v2.csv"
            content = b"Call end;value\n2026-07-01 10:00:00;x\n2026-06-01 10:00:00;y\n"
            source.write_bytes(content)
            destination = split_by_dates(root, root / "downloads", repository_only=True)
            self.assertEqual(source.read_bytes(), content)
            self.assertEqual(destination.name, "csv_repositorio")
            self.assertEqual(len(list(destination.glob("*.csv"))), 1)
            self.assertFalse((root / "downloads").exists())

    def test_custom_output_preserves_originals_and_existing_intermediates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "project"
            root.mkdir()
            content = b"Call end;value\n2026-06-01 10:00:00;x\n"
            source = root / "3meses_v2.csv"
            source.write_bytes(content)
            target = Path(folder) / "downloads" / "days"
            target.mkdir(parents=True)
            intermediate = target / "2026-06-01.rows"
            intermediate.write_bytes(b"existing")
            result = split_by_dates(root, target.parent, repository_only=True, output_dir=target)
            self.assertEqual(result, target)
            self.assertEqual(source.read_bytes(), content)
            self.assertEqual(intermediate.read_bytes(), b"existing")
            self.assertEqual(len(list(target.glob("*.csv"))), 1)
            with self.assertRaises(FileExistsError):
                split_by_dates(root, target.parent, repository_only=True, output_dir=target)


if __name__ == "__main__":
    unittest.main()
