from pathlib import Path
from unittest.mock import patch

import pandas as pd

import app
from test_data_loading import DataLoadingTests


class ParquetLoadingTests(DataLoadingTests):
    def setUp(self):
        super().setUp()
        for function in (app.aggregate_detail, app.aggregate_outcomes, app.aggregate_contact_analysis):
            function.clear()
            self.addCleanup(function.clear)

    def files(self):
        return tuple(app.file_signature(path) for path in sorted(self.root.iterdir())
                     if path.suffix.casefold() == ".parquet")

    def write_parquet(self, name, rows):
        path = self.root / name
        pd.DataFrame(rows, columns=app.REQUIRED_COLUMNS + ["DDI", "Phone"]).astype(str).to_parquet(path, index=False)
        return path

    def parquet_result(self):
        return app.aggregate(self.files(), app.file_signature(self.mapping), app.FILTER_VERSION, self.filters)

    def test_parquet_matches_csv_in_all_four_tabs(self):
        rows = [
            self.row("2026-06-01 10:00:00", talk=4),
            self.row("2026-09-01 11:00:00", outcome="Busy", talk=8),
            self.row("2026-06-02", campaign="Test"),
            self.row("invalid"),
        ]
        self.write_csv("calls.csv", rows)
        self.write_parquet("calls.parquet", rows)
        csv_files = app.discover_csv_files(self.root)
        mapping = app.file_signature(self.mapping)
        for key, expected in self.result().items():
            actual = self.parquet_result()[key]
            if isinstance(expected, pd.DataFrame):
                pd.testing.assert_frame_equal(actual, expected)
            else:
                self.assertEqual(actual, expected, key)
        for function in (app.aggregate_outcomes, app.aggregate_contact_analysis):
            pd.testing.assert_frame_equal(
                function(self.files(), mapping, self.filters),
                function(csv_files, mapping, self.filters),
            )
        masters = (mapping, app.file_signature(self.did_mapping), app.file_signature(self.lada_mapping), self.filters)
        csv_detail = app.aggregate_detail(csv_files, *masters)
        parquet_detail = app.aggregate_detail(self.files(), *masters)
        for key, expected in csv_detail.items():
            if isinstance(expected, pd.DataFrame):
                pd.testing.assert_frame_equal(parquet_detail[key], expected)
            else:
                self.assertEqual(parquet_detail[key], expected)
        self.assertEqual(list(app.read_source_chunks(self.files(), detail=True))[0]["DDI"].iloc[0], "0056")

    def test_new_changed_removed_parquet_and_persistent_cache(self):
        self.write_parquet("first.parquet", [self.row("2026-06-01")])
        self.assertEqual(self.parquet_result()["total"], 1)
        second = self.write_parquet("second.PARQUET", [self.row("2026-09-01")])
        self.assertEqual(self.parquet_result()["total"], 2)
        with patch.object(app, "read_source_chunks", side_effect=AssertionError("Unexpected reread")):
            app.aggregate.clear()
            self.filters["start"] = pd.Timestamp("2026-09-01").date()
            self.assertEqual(self.parquet_result()["total"], 1)
        self.write_parquet(second.name, [self.row("2026-09-01"), self.row("2026-09-02")])
        self.assertEqual(self.parquet_result()["total"], 2)
        second.unlink()
        self.assertEqual(self.parquet_result()["total"], 0)

    def test_corrupt_missing_columns_and_changed_source_errors(self):
        path = self.root / "invalid.parquet"
        path.write_bytes(b"not parquet")
        with self.assertRaisesRegex(ValueError, "invalid.parquet"):
            list(app.read_source_chunks(self.files()))
        pd.DataFrame({"wrong": [1]}).to_parquet(path)
        with self.assertRaisesRegex(ValueError, "columnas requeridas"):
            list(app.read_source_chunks(self.files()))
        self.write_parquet(path.name, [self.row("2026-06-01")])
        files = self.files()
        self.write_parquet(path.name, [self.row("2026-06-02"), self.row("2026-06-03")])
        with self.assertRaisesRegex(ValueError, "durante la carga"):
            list(app.read_source_chunks(files))

    def test_empty_parquet_has_no_date_range(self):
        self.write_parquet("empty.parquet", [])
        self.assertIsNone(app.scan_metadata(self.files())["maximum"])
        self.assertEqual(self.parquet_result()["total"], 0)

    def test_only_changed_source_is_read_for_main_and_detail(self):
        self.write_parquet("first.parquet", [self.row("2026-06-01")])
        self.write_parquet("second.parquet", [self.row("2026-09-01")])
        masters = (app.file_signature(self.mapping), app.file_signature(self.did_mapping),
                   app.file_signature(self.lada_mapping), self.filters)
        self.parquet_result()
        app.aggregate_detail(self.files(), *masters)
        self.write_parquet("second.parquet", [self.row("2026-09-01"), self.row("2026-09-02")])
        original = app.read_source_chunks
        loaded = []

        def spy(files, *args, **kwargs):
            loaded.extend(Path(signature[0]).name for signature in files)
            return original(files, *args, **kwargs)

        with patch.object(app, "read_source_chunks", side_effect=spy):
            self.assertEqual(self.parquet_result()["total"], 3)
            self.assertEqual(app.aggregate_detail(self.files(), *masters)["did"]["calls"].sum(), 3)
        self.assertEqual(loaded, ["second.parquet", "second.parquet"])
