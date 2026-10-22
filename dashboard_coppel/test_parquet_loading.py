from pathlib import Path
from unittest.mock import patch

import pandas as pd

import app
from drive_sources import DriveSources
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

    def test_crossed_drive_parquet_reclassifies_unassigned_values_from_all_masters(self):
        rows = [
            {
                "Hora": "2026-06-01 10:00:00", "Talk Time": 6, "Wait Time": 4,
                "Wrap up time": 2, "Call Type": "dialer", "ALTURA DE MORA": "Sin altura de mora",
                "Posicion": "Pendiente asignacion", "Term Reason": "DIALER", "Call Outcome name sin homologar": "recordatorio",
                "Contacto / No Contacto": "Pendiente asignacion", "Campaign Name sin homologar": "Cobranza",
                "DDI sin homologar": "0056", "NIR 3 digitos sin homologar": "551",
                "HOMOLOGACION": "Pendiente asignacion", "Estado": "Pendiente asignacion", "conteo_registros": 2,
            },
            {
                "Hora": "2026-06-02 10:00:00", "Talk Time": 9, "Wait Time": 6,
                "Wrap up time": 3, "Call Type": "manual", "ALTURA DE MORA": "Sin altura de mora",
                "Posicion": "Pendiente asignacion", "Term Reason": "MANUAL", "Call Outcome name sin homologar": "Busy",
                "Contacto / No Contacto": "Pendiente asignacion", "Campaign Name sin homologar": "Cobranza",
                "DDI sin homologar": "0056", "NIR 3 digitos sin homologar": "551",
                "HOMOLOGACION": "Pendiente asignacion", "Estado": "Pendiente asignacion", "conteo_registros": 3,
            },
            {
                "Hora": "2026-06-03 10:00:00", "Talk Time": 2, "Wait Time": 1,
                "Wrap up time": 1, "Call Type": "manual", "ALTURA DE MORA": "Sin altura de mora",
                "Posicion": "Pendiente asignacion", "Term Reason": "MANUAL", "Call Outcome name sin homologar": "Other",
                "Contacto / No Contacto": "Pendiente asignacion", "Campaign Name sin homologar": "Cobranza",
                "DDI sin homologar": "0056", "NIR 3 digitos sin homologar": "551",
                "HOMOLOGACION": "Pendiente asignacion", "Estado": "Pendiente asignacion", "conteo_registros": 1,
            },
        ]
        path = self.root / "crossed.parquet"
        pd.DataFrame(rows).to_parquet(path, index=False)
        files = (app.file_signature(path),)

        mapping = app.file_signature(self.mapping)
        did = app.file_signature(self.did_mapping)
        lada = app.file_signature(self.lada_mapping)
        mora = app.file_signature(self.mora_mapping)
        filters = {
            **self.filters, "detail_masters": (did, lada), "mora_master": mora,
        }
        result = app.aggregate(files, mapping, app.FILTER_VERSION, filters)
        self.assertEqual(result["total"], 6)
        self.assertEqual(result["contacts"], 2)
        self.assertEqual(
            result["campaign"]["Campaign Name"].tolist(),
            ["MORA 1"],
        )

        outcomes = app.aggregate_outcomes(files, mapping, filters)
        self.assertEqual(outcomes["calls"].sum(), 6)
        self.assertEqual(outcomes["contacts"].sum(), 2)
        analysis = app.aggregate_contact_analysis(files, mapping, filters)
        self.assertEqual(analysis["calls"].sum(), 6)
        pending = analysis.loc[analysis["Call Outcome name"].eq("Other")].iloc[0]
        self.assertFalse(pending["mapped"])
        self.assertEqual(pending["contacts"], 0)

        detail = app.aggregate_detail(
            files, mapping, did, lada, filters,
        )
        self.assertEqual(detail["did"]["calls"].sum(), 6)
        self.assertEqual(detail["did_month"]["provider"].tolist(), ["IPCOM"])
        self.assertEqual(detail["state"]["calls"].sum(), 6)
        self.assertEqual(detail["unmapped_did"], 0)
        self.assertEqual(detail["unmapped_state"], 0)

    def test_local_consolidated_schema_uses_weighted_calls_and_preserves_filters(self):
        rows = [
            {
                "Hora": "2026-06-01 10:00:00", "Talk Time": "6", "Wait Time": "4",
                "Wrap up time": "2", "Call Type": "dialer",
                "Campaign Name sin homologar": "Cobranza", "Term Reason": "DIALER",
                "Call Outcome name sin homologar": "recordatorio",
                "DDI sin homologar": "0056", "NIR 3 digitos sin homologar": "55",
                "Estado": "CDMX", "conteo_registros": "2",
            },
            {
                "Hora": "2026-09-01 11:00:00", "Talk Time": "9", "Wait Time": "6",
                "Wrap up time": "3", "Call Type": "manual",
                "Campaign Name sin homologar": "Cobranza", "Term Reason": "MANUAL",
                "Call Outcome name sin homologar": "Busy",
                "DDI sin homologar": "0056", "NIR 3 digitos sin homologar": "55",
                "Estado": "CDMX", "conteo_registros": "3",
            },
            {
                "Hora": "2026-06-01 12:00:00", "Talk Time": "100", "Wait Time": "100",
                "Wrap up time": "100", "Call Type": "dialer",
                "Campaign Name sin homologar": "CP_PRUEBA", "Term Reason": "DIALER",
                "Call Outcome name sin homologar": "recordatorio",
                "DDI sin homologar": "0056", "NIR 3 digitos sin homologar": "55",
                "Estado": "CDMX", "conteo_registros": "7",
            },
        ]
        path = self.root / "consolidado.parquet"
        pd.DataFrame(rows).to_parquet(path, index=False)
        files = (app.file_signature(path),)
        mapping = app.file_signature(self.mapping)
        result = app.aggregate(files, mapping, app.FILTER_VERSION, self.filters)
        self.assertEqual(result["total"], 5)
        self.assertEqual(result["contacts"], 2)
        self.assertEqual(result["talk_sum"], 15)
        self.assertEqual(result["wait_sum"], 10)
        self.assertEqual(result["wrap_sum"], 5)
        self.assertEqual(result["day"]["calls"].sum(), 5)
        self.assertEqual(
            dict(zip(result["day"]["day"], result["day"]["calls"])),
            {"2026-06-01": 2, "2026-09-01": 3},
        )

        expanded_rows = []
        for ended, count, outcome, call_type, term in [
            ("2026-06-01 10:00:00", 2, "recordatorio", "dialer", "DIALER"),
            ("2026-09-01 11:00:00", 3, "Busy", "manual", "MANUAL"),
        ]:
            for _ in range(count):
                row = self.row(ended, outcome=outcome, talk=3, campaign="Cobranza")
                row.update({"Wait Time": 2, "Wrap up time": 1, "Call Type": call_type, "Term Reason": term})
                expanded_rows.append(row)
        for _ in range(7):
            row = self.row("2026-06-01 12:00:00", campaign="CP_PRUEBA", talk=100)
            row.update({"Wait Time": 100, "Wrap up time": 100})
            expanded_rows.append(row)
        self.write_csv("expanded.csv", expanded_rows)
        csv_files = app.discover_csv_files(self.root)
        expected = app.aggregate(csv_files, mapping, app.FILTER_VERSION, self.filters)
        for key, value in expected.items():
            if isinstance(value, pd.DataFrame):
                pd.testing.assert_frame_equal(result[key], value)
            else:
                self.assertEqual(result[key], value, key)
        outcome_detail_masters = (
            app.file_signature(self.did_mapping), app.file_signature(self.lada_mapping),
        )
        outcome_filters = {**self.filters, "detail_masters": outcome_detail_masters}
        pd.testing.assert_frame_equal(
            app.aggregate_outcomes(files, mapping, outcome_filters),
            app.aggregate_outcomes(csv_files, mapping, outcome_filters),
        )
        pd.testing.assert_frame_equal(
            app.aggregate_contact_analysis(files, mapping, self.filters),
            app.aggregate_contact_analysis(csv_files, mapping, self.filters),
        )
        detail_masters = (
            mapping, app.file_signature(self.did_mapping),
            app.file_signature(self.lada_mapping), self.filters,
        )
        for key, value in app.aggregate_detail(files, *detail_masters).items():
            expected_value = app.aggregate_detail(csv_files, *detail_masters)[key]
            if isinstance(value, pd.DataFrame):
                pd.testing.assert_frame_equal(value, expected_value)
            else:
                self.assertEqual(value, expected_value, key)

        date_filters = {**self.filters, "start": pd.Timestamp("2026-09-01").date(),
                        "end": pd.Timestamp("2026-09-01").date()}
        self.assertEqual(app.aggregate(files, mapping, app.FILTER_VERSION, date_filters)["total"], 3)
        type_filters = {**self.filters, "Call Type": ["manual"]}
        self.assertEqual(app.aggregate(files, mapping, app.FILTER_VERSION, type_filters)["total"], 3)
        campaign_filters = {**self.filters, "Campaign Name": ["No existe"]}
        self.assertEqual(app.aggregate(files, mapping, app.FILTER_VERSION, campaign_filters)["total"], 0)
        metadata = app.scan_metadata(files)
        self.assertEqual(metadata["options"]["Call Type"], ["dialer", "manual"])
        self.assertEqual(metadata["options"]["State"], ["CDMX"])

        state_filters = {
            **self.filters, "State": ["CDMX"],
            "detail_masters": (app.file_signature(self.did_mapping), app.file_signature(self.lada_mapping)),
        }
        state_result = app.aggregate(files, mapping, app.FILTER_VERSION, state_filters)
        self.assertEqual(state_result["total"], 5)

        detail = app.aggregate_detail(
            files, mapping, app.file_signature(self.did_mapping),
            app.file_signature(self.lada_mapping), self.filters,
        )
        self.assertEqual(detail["did"]["calls"].sum(), 5)
        self.assertEqual(detail["state"]["calls"].sum(), 5)
        self.assertEqual(detail["state"]["state"].tolist(), ["CDMX"])
        self.assertEqual(detail["unmapped_did"], 0)
        self.assertEqual(detail["unmapped_state"], 0)

        outcome = app.aggregate_outcomes(files, mapping, self.filters)
        self.assertEqual(outcome["calls"].sum(), 5)
        analysis = app.aggregate_contact_analysis(files, mapping, self.filters)
        self.assertEqual(analysis["calls"].sum(), 5)
        self.assertEqual(analysis["contacts"].sum(), 2)

    def test_local_consolidated_requires_valid_record_counts(self):
        row = {
            "Hora": "2026-06-01 10:00:00", "Talk Time": "6", "Wait Time": "4",
            "Wrap up time": "2", "Call Type": "dialer",
            "Campaign Name sin homologar": "Cobranza", "Term Reason": "DIALER",
            "Call Outcome name sin homologar": "recordatorio", "conteo_registros": "1.5",
        }
        path = self.root / "consolidado.parquet"
        pd.DataFrame([row]).to_parquet(path, index=False)
        with self.assertRaisesRegex(ValueError, "conteo_registros"):
            list(app.read_source_chunks((app.file_signature(path),)))

    def test_dashboard_sources_always_use_drive_and_ignore_local_parquets(self):
        self.write_parquet("local-only.parquet", [self.row("2026-06-01")])
        drive_sources = DriveSources(
            parquet=(("drive.parquet", 1, 10),),
            masters={name: (name, 2, 20) for name in app.DRIVE_MASTER_NAMES},
        )
        with patch.object(app, "discover_drive_sources", return_value=drive_sources) as discover, \
                patch.object(app, "is_crossed_source", return_value=True):
            files, masters = app.discover_dashboard_sources(
                self.root, service_account_info={"type": "service_account"},
            )
        self.assertEqual(files, drive_sources.parquet)
        self.assertEqual(masters, drive_sources.masters)
        discover.assert_called_once_with(
            self.root, None, service_account_info={"type": "service_account"},
            master_names=app.DRIVE_MASTER_NAMES,
        )
