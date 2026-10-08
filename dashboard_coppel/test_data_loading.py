import os
import tempfile
import sqlite3
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import app


class DataLoadingTests(unittest.TestCase):
    def setUp(self):
        discovery = patch.object(app, "discover_dashboard_files", app.discover_csv_files)
        discovery.start()
        self.addCleanup(discovery.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.mapping = self.root / "Mae_contacto.xlsx"
        self.did_mapping = self.root / "Mae_did.xlsx"
        self.lada_mapping = self.root / "Mae_lada.xlsx"
        pd.DataFrame({"DDI": ["56"], "NOMBRE": ["IPCOM"], "HOMOLOGACION": ["56-IPCOM"]}).to_excel(self.did_mapping, index=False)
        pd.DataFrame({" ESTADO": ["CDMX"], " NIR": ["55"]}).to_excel(self.lada_mapping, index=False)
        self.write_mapping(["Contacto", "No Contacto"])
        self.filters = {
            "start": date(2026, 6, 1),
            "end": date(2026, 9, 30),
            "Call Type": [],
            "Campaign Name": [],
            "Term Reason": [],
        }
        app.aggregate.clear()
        app.load_contact_outcomes.clear()
        self.addCleanup(app.aggregate.clear)
        self.addCleanup(app.load_contact_outcomes.clear)

    def write_mapping(self, flags):
        pd.DataFrame({
            "Call Outcome name": [" ReCoRdAtOrIo ", "Busy"],
            "Contacto / No Contacto": flags,
        }).to_excel(self.mapping, index=False)
        info = self.mapping.stat()
        os.utime(self.mapping, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))

    def row(self, ended, outcome="recordatorio", talk=0, campaign="Cobranza"):
        return {
            "Call end": ended,
            "Talk Time": talk,
            "Wait Time": 2,
            "Wrap up time": 3,
            "Call Type": "Outbound",
            "Campaign Name": campaign,
            "Term Reason": "Completed",
            "Call Outcome name": outcome,
            "DDI": "0056",
            "Phone": "5512345678",
        }

    def write_csv(self, name, rows):
        path = self.root / name
        pd.DataFrame(rows, columns=app.REQUIRED_COLUMNS + ["DDI", "Phone"]).to_csv(path, sep=";", index=False)
        return path

    def result(self):
        return app.aggregate(
            app.discover_csv_files(self.root),
            app.file_signature(self.mapping),
            app.FILTER_VERSION,
            self.filters,
        )

    def test_sums_every_csv_and_preserves_filters_and_dimensions(self):
        self.write_csv("first.csv", [
            self.row("2026-06-01 10:00:00"),
            self.row("2026-06-02 11:00:00", "Busy", 10),
            self.row("2026-06-03 12:00:00", campaign="Prueba"),
            self.row("invalid"),
        ])
        self.write_csv("second.CSV", [
            self.row("2026-09-01 15:00:00", " RECORDATORIO ", 4),
            self.row("2026-09-02 16:00:00", "Unknown", 30),
            self.row("2026-10-01 17:00:00"),
        ])
        self.write_csv("ignored.txt", [self.row("2026-05-01")])
        with patch.object(app, "CHUNK_SIZE", 1):
            result = self.result()
            metadata = app.scan_metadata(app.discover_csv_files(self.root))
        self.assertEqual(result["total"], 4)
        self.assertEqual(result["contacts"], 2)
        self.assertEqual(result["talk_sum"], 44)
        self.assertEqual(result["wait_sum"], 8)
        self.assertEqual(result["wrap_sum"], 12)
        for dimension in ["month", "day", "day_of_month", "weekday", "hour", "call_type", "campaign", "term", "heat"]:
            self.assertEqual(result[dimension]["calls"].sum(), 4, dimension)
            self.assertEqual(result[dimension]["contacts"].sum(), 2, dimension)
        self.assertEqual(metadata["minimum"].date(), date(2026, 6, 1))
        self.assertEqual(metadata["maximum"].date(), date(2026, 10, 1))
        self.assertEqual(metadata["options"]["Campaign Name"], ["Cobranza"])
        self.filters["start"] = date(2026, 9, 1)
        self.assertEqual(self.result()["total"], 2)
        self.filters["Campaign Name"] = ["Another campaign"]
        self.assertEqual(self.result()["total"], 0)

    def test_cache_detects_csv_added_modified_and_removed(self):
        first = self.write_csv("first.csv", [self.row("2026-06-01")])
        self.assertEqual(self.result()["total"], 1)
        second = self.write_csv("second.csv", [self.row("2026-09-01")])
        self.assertEqual(self.result()["total"], 2)
        self.write_csv(first.name, [self.row("2026-06-01"), self.row("2026-06-02")])
        self.assertEqual(self.result()["total"], 3)
        second.unlink()
        self.assertEqual(self.result()["total"], 2)

    def test_master_update_invalidates_cached_contacts_without_talk_fallback(self):
        self.write_csv("calls.csv", [self.row("2026-06-01", talk=20)])
        self.assertEqual(self.result()["contacts"], 1)
        self.write_mapping(["No Contacto", "No Contacto"])
        self.assertEqual(self.result()["contacts"], 0)
        self.assertEqual(
            app.load_contact_outcomes(app.file_signature(self.mapping)), set()
        )

    def test_invalid_sources_raise_explicit_errors(self):
        with self.assertRaisesRegex(ValueError, "No se encontraron"):
            app.discover_csv_files(self.root)
        pd.DataFrame({"wrong": [1]}).to_csv(self.root / "bad.csv", sep=";", index=False)
        with self.assertRaisesRegex(ValueError, "bad.csv"):
            app.scan_metadata(app.discover_csv_files(self.root))
        pd.DataFrame({"wrong": [1]}).to_excel(self.mapping, index=False)
        with self.assertRaisesRegex(ValueError, "columnas requeridas"):
            app.load_contact_outcomes(app.file_signature(self.mapping))
        self.write_mapping(["Invalid", "No Contacto"])
        with self.assertRaisesRegex(ValueError, "clasificar"):
            app.load_contact_outcomes(app.file_signature(self.mapping))
        pd.DataFrame({
            "Call Outcome name": ["Busy", " BUSY "],
            "Contacto / No Contacto": ["Contacto", "No Contacto"],
        }).to_excel(self.mapping, index=False)
        with self.assertRaisesRegex(ValueError, "contradictorias"):
            app.load_contact_outcomes(app.file_signature(self.mapping))

    def test_file_changed_during_loading_is_rejected(self):
        self.write_csv("calls.csv", [self.row("2026-06-01")])
        files = app.discover_csv_files(self.root)
        self.write_csv("calls.csv", [self.row("2026-06-01"), self.row("2026-06-02")])
        with self.assertRaisesRegex(ValueError, "durante la carga"):
            app.scan_metadata(files)

    def test_empty_or_excluded_data_has_no_date_range(self):
        self.write_csv("empty.csv", [])
        self.write_csv("excluded.csv", [self.row("2026-06-01", campaign="Test")])
        metadata = app.scan_metadata(app.discover_csv_files(self.root))
        self.assertIsNone(metadata["minimum"])
        self.assertIsNone(metadata["maximum"])
        self.assertEqual(self.result()["total"], 0)

    def test_persistent_summary_survives_memory_cache_reset_and_filter_changes(self):
        self.write_csv("v2.csv", [
            self.row("2026-06-01 10:01:00", talk=2),
            self.row("2026-06-01 10:59:59", talk=8),
            self.row("2026-06-01 10:30:00", outcome="Busy", talk=4),
            self.row("2026-09-01 23:59:59", talk=6),
        ])
        result = self.result()
        self.assertEqual(result["total"], 4)
        self.assertEqual(result["contacts"], 3)
        self.assertEqual(result["talk_sum"], 20)
        summary = next(app.read_source_summaries(app.discover_csv_files(self.root)))
        self.assertEqual(len(summary), 3)
        app.aggregate.clear()
        with patch.object(app, "read_source_chunks", side_effect=AssertionError("CSV reread")):
            self.assertEqual(self.result()["contacts"], 3)
            metadata = app.scan_metadata(app.discover_csv_files(self.root))
            self.assertEqual(metadata["maximum"].date(), date(2026, 9, 1))
            self.filters["start"] = self.filters["end"] = date(2026, 9, 1)
            self.assertEqual(self.result()["total"], 1)
            self.write_mapping(["No Contacto", "Contacto"])
            self.filters["start"] = date(2026, 6, 1)
            self.assertEqual(self.result()["contacts"], 1)

    def test_only_modified_v3_is_read_and_v2_remains_on_disk(self):
        historical = self.write_csv("v2.csv", [self.row("2026-06-01")])
        self.write_csv("v3.csv", [self.row("2026-09-01")])
        original_signature = app.file_signature(historical)
        self.assertEqual(self.result()["total"], 2)
        self.write_csv("v3.csv", [self.row("2026-09-01"), self.row("2026-09-02")])
        app.aggregate.clear()
        with patch.object(app, "read_source_chunks", wraps=app.read_source_chunks) as reader:
            self.assertEqual(self.result()["total"], 3)
            self.assertEqual(reader.call_count, 1)
            self.assertEqual(Path(reader.call_args.args[0][0][0]).name, "v3.csv")
        self.assertEqual(app.file_signature(historical), original_signature)

    def test_failed_rebuild_preserves_previous_summary_and_cleans_temporary_files(self):
        source = self.write_csv("v3.csv", [self.row("2026-09-01")])
        self.assertEqual(self.result()["total"], 1)
        cache = next((self.root / ".dashboard_cache").glob("*.sqlite"))
        saved = cache.read_bytes()
        pd.DataFrame({"wrong": [1]}).to_csv(source, sep=";", index=False)
        with self.assertRaisesRegex(ValueError, "v3.csv"):
            self.result()
        self.assertEqual(cache.read_bytes(), saved)
        self.assertEqual(list(cache.parent.iterdir()), [cache])

    def test_corrupt_summary_reports_error_instead_of_returning_empty_totals(self):
        self.write_csv("v2.csv", [self.row("2026-06-01")])
        self.result()
        cache = next((self.root / ".dashboard_cache").glob("*.sqlite"))
        cache.write_bytes(b"invalid database")
        app.aggregate.clear()
        with self.assertRaises(sqlite3.DatabaseError):
            self.result()


if __name__ == "__main__":
    unittest.main()
