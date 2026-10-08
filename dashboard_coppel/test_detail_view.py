from unittest.mock import Mock, patch
import unittest

import pandas as pd

import app
from detail_view import load_detail_masters, hierarchy_rows, numeric_code, phone_prefix, did_volume_figure, state_figure, detail_table
from interactive_table import table_html
from test_data_loading import DataLoadingTests


class DetailViewTests(DataLoadingTests):
    def test_detail_reports_source_progress_and_reuses_cache(self):
        self.write_csv("v2.csv", [self.row("2026-06-01")])
        files = app.discover_csv_files(self.root)
        status = Mock()
        first = list(app.read_detail_summaries(files, status))
        self.assertTrue(any("lote" in call.kwargs["label"] for call in status.update.call_args_list))
        status.reset_mock()
        with patch.object(app, "read_source_chunks", side_effect=AssertionError("CSV reread")):
            second = list(app.read_detail_summaries(files, status))
        pd.testing.assert_frame_equal(first[0], second[0])
        self.assertTrue(any("resumen guardado" in call.kwargs["label"] for call in status.update.call_args_list))

    def test_totals_use_base_rows_and_weighted_metrics(self):
        table = pd.DataFrame({
            "did": ["A", "A", "B"], "month": ["2026-06", "2026-07", "2026-06"],
            "provider": ["X", "X", "Y"], "calls": [10, 20, 70], "contacts": [10, 0, 7],
            "talk_sum": [100, 200, 700], "wrap_sum": [10, 20, 70], "wait_sum": [20, 40, 140],
        })
        with patch("detail_view.render_interactive_table") as render:
            detail_table(table, ["did", "month", "provider"], "Detalle")
        totals = render.call_args.kwargs["totals"]
        self.assertEqual(totals["calls"], 100)
        self.assertEqual(totals["contacts"], 17)
        self.assertEqual(totals["rate"], 17)
        self.assertEqual(totals["tmo"], 11)
        self.assertEqual(totals["idle"], 2)
        self.assertEqual(totals["label"], "Total")
        html = table_html(render.call_args.args[0], render.call_args.args[1], "Detalle", hierarchy=True, totals=totals)
        self.assertIn(".tree-root{font-weight:700}", html)
        self.assertIn('tfoot id="totals"', html)
        self.assertIn('"totals": {', html)

    def detail(self):
        return app.aggregate_detail(
            app.discover_csv_files(self.root), app.file_signature(self.mapping),
            app.file_signature(self.did_mapping), app.file_signature(self.lada_mapping), self.filters,
        )

    def test_detail_groups_preserve_totals_and_unknown_records(self):
        known = self.row("2026-06-01", talk=4)
        unknown = {**self.row("2026-06-02", outcome="Busy", talk=8), "DDI": "777", "Phone": "123"}
        excluded = self.row("2026-06-03", campaign="Prueba")
        self.write_csv("v2.csv", [known, unknown, excluded])
        self.write_csv("v3.csv", [self.row("2026-09-01")])
        detail = self.detail()
        principal = self.result()
        for key in ["did", "did_month", "did_campaign", "state", "state_month"]:
            for metric in app.GROUP_METRICS:
                expected = principal["total"] if metric == "calls" else principal[metric]
                self.assertEqual(detail[key][metric].sum(), expected)
        self.assertEqual(detail["unmapped_did"], 1)
        self.assertEqual(detail["unmapped_state"], 1)
        self.assertIn("56-IPCOM", detail["did"]["did"].tolist())
        self.filters["Campaign Name"] = ["No such campaign"]
        self.assertTrue(self.detail()["did"].empty)

    def test_normalization_and_longest_lada_match(self):
        self.assertEqual(numeric_code("0056"), "56")
        self.assertEqual(numeric_code("56.0"), "56")
        self.assertEqual(phone_prefix("+52 (55) 1234-5678"), "551")
        self.assertEqual(phone_prefix("5215512345678"), "551")
        self.assertEqual(phone_prefix("596209291"), "")
        pd.DataFrame({"NIR": ["55", "551"], "ESTADO": ["CDMX", "Especial"]}).to_excel(self.lada_mapping, index=False)
        self.write_csv("v2.csv", [self.row("2026-06-01")])
        self.assertEqual(self.detail()["state"]["state"].tolist(), ["Especial"])

    def test_master_updates_reclassify_without_rereading_csv(self):
        self.write_csv("v2.csv", [self.row("2026-06-01")])
        self.detail()
        app.aggregate_detail.clear()
        with patch.object(app, "read_source_chunks", side_effect=AssertionError("CSV reread")):
            pd.DataFrame({"NIR": ["55"], "ESTADO": ["Nuevo estado"]}).to_excel(self.lada_mapping, index=False)
            pd.DataFrame({"DDI": ["56"], "NOMBRE": ["Nuevo"], "HOMOLOGACION": ["56-Nuevo"]}).to_excel(self.did_mapping, index=False)
            self.write_mapping(["No Contacto", "No Contacto"])
            result = self.detail()
        self.assertEqual(result["state"]["state"].tolist(), ["Nuevo estado"])
        self.assertEqual(result["did"]["did"].tolist(), ["56-Nuevo"])
        self.assertEqual(result["did"]["contacts"].sum(), 0)

    def test_detail_reads_only_changed_v3_and_keeps_main_cache(self):
        historical = self.write_csv("v2.csv", [self.row("2026-06-01")])
        self.write_csv("v3.csv", [self.row("2026-09-01")])
        self.result()
        self.detail()
        historical_bytes = historical.read_bytes()
        self.write_csv("v3.csv", [self.row("2026-09-01"), self.row("2026-09-02")])
        original = app.read_source_chunks
        loaded = []

        def spy(files, *args, **kwargs):
            loaded.extend(signature[0] for signature in files)
            return original(files, *args, **kwargs)

        with patch.object(app, "read_source_chunks", side_effect=spy):
            self.assertEqual(self.detail()["did"]["calls"].sum(), 3)
        self.assertEqual(loaded, [str((self.root / "v3.csv").resolve())])
        self.assertEqual(historical.read_bytes(), historical_bytes)
        with patch.object(app, "read_source_chunks", side_effect=AssertionError("CSV reread")):
            list(app.read_source_summaries((app.file_signature(historical),)))

    def test_conflicting_masters_raise_and_missing_fields_raise(self):
        pd.DataFrame({"DDI": ["56", "0056"], "NOMBRE": ["A", "B"], "HOMOLOGACION": ["56-A", "56-B"]}).to_excel(self.did_mapping, index=False)
        with self.assertRaisesRegex(ValueError, "contradictorios"):
            load_detail_masters(app.file_signature(self.did_mapping), app.file_signature(self.lada_mapping))
        pd.DataFrame({"wrong": ["1"]}).to_excel(self.did_mapping, index=False)
        with self.assertRaisesRegex(ValueError, "faltan columnas"):
            load_detail_masters(app.file_signature(self.did_mapping), app.file_signature(self.lada_mapping))

    def test_hierarchy_and_figures_preserve_metrics(self):
        self.write_csv("calls.csv", [self.row("2026-06-01"), self.row("2026-09-01", outcome="Busy")])
        result = self.detail()
        tree = hierarchy_rows(result["did_month"], ["did", "month", "provider"])
        self.assertEqual(tree.loc[tree["_depth"].eq(0), "calls"].sum(), 2)
        self.assertEqual(tree.loc[tree["_depth"].eq(1), "calls"].sum(), 2)
        self.assertEqual(len(set(tree["_id"])), len(tree))
        html = table_html(tree, [("label", "DID"), ("calls", "Llamadas")], "Detalle", hierarchy=True)
        self.assertIn('"hierarchy": true', html)
        self.assertIn("aria-expanded", html)
        fig = did_volume_figure(result["did"])
        self.assertEqual(list(fig.data[0].y), [2])
        self.assertEqual(list(fig.data[1].y), [.5])
        scatter = state_figure(result["state"], self.result())
        self.assertEqual(scatter.data[0].mode, "markers")
        self.assertEqual(scatter.data[0].customdata[0][0], "CDMX")
        self.assertIn("%{customdata[0]}", scatter.data[0].hovertemplate)
        self.assertEqual(list(scatter.data[0].y), [.5])
        self.assertEqual(list(scatter.data[0].x), [3])


if __name__ == "__main__":
    unittest.main()
