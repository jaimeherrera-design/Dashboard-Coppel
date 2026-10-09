from unittest.mock import Mock, patch
import unittest
from io import BytesIO
import inspect

import pandas as pd
from streamlit.testing.v1 import AppTest

import app
from detail_view import load_detail_masters, hierarchy_rows, numeric_code, phone_prefix, did_volume_figure, state_figure, detail_table, render_detail, render_detail_page, select_detail_rows, detail_csv, detail_page_html
from data_cache import iter_summary, load_summary, SUMMARY_KEYS
from interactive_table import table_html
from test_data_loading import DataLoadingTests


class DetailViewTests(DataLoadingTests):
    def test_pagination_preserves_full_table_and_weighted_totals(self):
        table = pd.DataFrame({
            "did": [f"DID-{index}" for index in range(205)],
            "month": ["2026-06"] * 205, "provider": ["X"] * 205,
            "calls": list(range(1, 206)), "contacts": [1] * 205,
            "talk_sum": [10] * 205, "wrap_sum": [2] * 205, "wait_sum": [3] * 205,
        })
        original = table.copy(deep=True)
        with patch("detail_view.render_interactive_table") as render, patch("detail_view.render_detail_page") as server:
            detail_table(table, ["did", "month", "provider"], "Detalle")
            baseline = render.call_args
            detail_table(table, ["did", "month", "provider"], "Detalle", page_size=100)
            trial = server.call_args
            self.assertEqual(render.call_count, 1)
        pd.testing.assert_frame_equal(baseline.args[0], trial.args[0])
        pd.testing.assert_frame_equal(table, original)
        self.assertEqual(baseline.kwargs["totals"], trial.args[3])
        self.assertEqual(trial.args[3]["calls"], sum(range(1, 206)))
        self.assertEqual(trial.args[3]["contacts"], 205)
        display, columns, title, totals, height, page_size, levels = trial.args
        for page, expected in [(1, 101), (5, 11)]:
            with self.subTest(page=page), \
                    patch("detail_view.st.text_input", return_value=""), \
                    patch("detail_view.st.selectbox", side_effect=[2, "calls", "Mayor a menor"]), \
                    patch("detail_view.st.number_input", return_value=page), \
                    patch("detail_view.st.session_state", {}), \
                    patch("detail_view.st.button", return_value=False), \
                    patch("detail_view.st.html") as html:
                inspect.unwrap(render_detail_page)(display, columns, title, totals, height, page_size, levels)
            shown = html.call_args.args[0]
            self.assertEqual(shown.count("<tr"), expected + 1)
            self.assertLessEqual(shown.count("<tr"), 102)
            self.assertIn(f'{totals["calls"]:,.0f}', shown.split("<tfoot>")[1])
            self.assertIn(f'height:{height}px', shown)
        selected = select_detail_rows(display, "DID-204", 2, "calls", False)
        self.assertEqual(selected.iloc[0]["label"], "DID-204")
        full = select_detail_rows(display, "", 3, "calls", False)
        self.assertEqual(len(full), 615)
        exported = pd.read_csv(BytesIO(detail_csv(full, columns)), sep=";")
        self.assertEqual(len(exported), 615)
        self.assertTrue(exported.iloc[-1][title].endswith("/ X"))

    def test_styled_table_pages_search_and_download_without_iframes(self):
        script = """
import pandas as pd
from detail_view import detail_table
table = pd.DataFrame({
    "did": [f"DID-{i:03}" for i in range(205)],
    "month": ["2026-06"] * 205, "provider": ["X"] * 205,
    "calls": [1] * 205, "contacts": [1] * 205,
    "talk_sum": [10] * 205, "wrap_sum": [2] * 205, "wait_sum": [3] * 205,
})
detail_table(table, ["did", "month", "provider"], "Detalle", height=700, page_size=100)
"""
        ui = AppTest.from_string(script, default_timeout=30).run()
        self.assertFalse(ui.exception)
        self.assertEqual(len(ui.get("iframe")), 0)
        self.assertEqual(ui.get("html")[0].proto.body.count("<tr"), 102)
        self.assertIn("205", ui.get("html")[0].proto.body.split("<tfoot>")[1])
        ui.number_input(key="Detalle-page").set_value(5).run()
        self.assertFalse(ui.exception)
        self.assertEqual(ui.get("html")[0].proto.body.count("<tr"), 12)
        ui.text_input(key="Detalle-search").set_value("DID-204").run()
        self.assertFalse(ui.exception)
        self.assertEqual(ui.number_input(key="Detalle-page").value, 1)
        self.assertIn("DID-204", ui.get("html")[0].proto.body)
        self.assertIn("205", ui.get("html")[0].proto.body.split("<tfoot>")[1])
        ui.text_input(key="Detalle-search").set_value("").run()
        with patch("detail_view.detail_csv", wraps=detail_csv) as csv:
            ui.button(key="Detalle-prepare-csv").click().run()
        self.assertFalse(ui.exception)
        self.assertEqual(len(csv.call_args.args[0]), 615)
        self.assertEqual(len(ui.get("download_button")), 1)

    def test_table_styles_headers_indentation_subtotals_and_escapes_labels(self):
        table = pd.DataFrame({
            "did": ["<script>unsafe</script>"], "month": ["2026-06"], "provider": ["X"],
            "calls": [10], "contacts": [1], "talk_sum": [10], "wrap_sum": [0], "wait_sum": [0],
        })
        rows = hierarchy_rows(table, ["did", "month", "provider"])
        for level in [2, 3]:
            with self.subTest(level=level):
                selected = select_detail_rows(rows, "", level, "calls", False)
                html = detail_page_html(selected, [("label", "Detalle"), ("calls", "Llamadas")],
                                        {"label": "Total", "calls": 10}, 700, 3)
                self.assertEqual(html.count('class="subtotal"'), 2)
                self.assertIn("font-weight:700", html)
                self.assertIn("background:#23458f;color:#fff", html)
                self.assertIn('padding-left:64px', html)
                if level == 3:
                    self.assertIn('padding-left:112px', html)
                self.assertNotIn("<script>", html)
                self.assertIn("&lt;script&gt;", html)
                self.assertNotIn("<iframe", html)

    def test_daily_batches_match_hourly_detail_for_all_filters(self):
        rows = [self.row(f"2026-06-01 {hour:02}:15:00", talk=hour / 10) for hour in range(24)]
        rows += [
            self.row("2026-06-02 23:59:59", outcome="Busy", campaign="Otra"),
            {**self.row("2026-07-01"), "DDI": "777", "Phone": "123"},
            self.row("invalid"), self.row("2026-06-01", campaign="Prueba"),
        ]
        self.write_csv("calls.csv", rows)
        files = app.discover_csv_files(self.root)
        def old_summaries(files, status=None):
            for signature in files:
                def chunks():
                    for raw in app.read_source_chunks((signature,), detail=True):
                        chunk = app.clean_chunk(raw)
                        chunk["DDI"] = chunk["DDI"].fillna("").map(numeric_code)
                        chunk["phone_prefix"] = chunk["Phone"].fillna("").map(phone_prefix)
                        yield chunk.drop(columns="Phone")
                summary = load_summary(signature, chunks, keys=SUMMARY_KEYS + ["DDI", "phone_prefix"],
                                       version="legacy-hourly-test", namespace="legacy-hourly-test")
                summary["Call end"] = pd.to_datetime(summary["Call end"], errors="coerce")
                yield summary
        hourly = pd.concat(list(old_summaries(files)))
        daily = pd.concat(list(app.read_detail_summaries(files)))
        self.assertEqual(len(hourly), 28)
        self.assertEqual(len(daily), 5)
        metrics = ["calls", "talk_sum", "wrap_sum", "wait_sum"]
        pd.testing.assert_series_equal(hourly[metrics].sum(), daily[metrics].sum())
        base_filters = dict(self.filters)
        selections = [
            {}, {"start": pd.Timestamp("2026-06-02").date(), "end": pd.Timestamp("2026-06-02").date()},
            {"Campaign Name": ["Otra"]}, {"Call Type": ["Outbound"]},
            {"Term Reason": ["Completed"]}, {"Campaign Name": ["No such campaign"]},
        ]
        for selection in selections:
            self.filters = {**base_filters, **selection}
            app.aggregate_detail.clear()
            with patch.object(app, "read_detail_summaries", old_summaries):
                before = self.detail()
            app.aggregate_detail.clear()
            after = self.detail()
            for key, expected in before.items():
                if isinstance(expected, pd.DataFrame):
                    pd.testing.assert_frame_equal(after[key], expected, check_exact=False, rtol=1e-12)
                else:
                    self.assertEqual(after[key], expected)
        with patch.object(app, "read_source_chunks", side_effect=AssertionError("Unexpected reread")):
            list(app.read_detail_summaries(files))

    def test_daily_summary_never_reads_all_rows_and_detects_changed_source(self):
        path = self.write_csv("calls.csv", [self.row(f"2026-06-{day:02}") for day in range(1, 6)])
        signature = app.file_signature(path)
        def chunks():
            yield app.clean_chunk(pd.read_csv(path, sep=";"))
        reader = pd.read_sql_query
        with patch("data_cache.pd.read_sql_query", wraps=reader) as sql:
            batches = list(iter_summary(signature, chunks, keys=SUMMARY_KEYS,
                                        version="daily-test", namespace="daily-test", batch_size=2))
        self.assertEqual([len(batch) for batch in batches], [2, 2, 1])
        self.assertTrue(all(call.kwargs["chunksize"] == 2 for call in sql.call_args_list))
        stream = iter_summary(signature, chunks, keys=SUMMARY_KEYS,
                              version="daily-test", namespace="daily-test", batch_size=2)
        next(stream)
        self.write_csv("calls.csv", [self.row("2026-06-10")])
        with self.assertRaisesRegex(ValueError, "durante la carga"):
            next(stream)

    def test_search_preserves_ancestors_and_export_escapes_formulas(self):
        table = pd.DataFrame({
            "did": ["A", "B"], "month": ["2026-06", "2026-07"],
            "provider": ["=SUM(1)", "Y"], "calls": [10, 20], "contacts": [1, 2],
            "talk_sum": [10, 20], "wrap_sum": [0, 0], "wait_sum": [0, 0],
        })
        display = hierarchy_rows(table, ["did", "month", "provider"])
        selected = select_detail_rows(display, "=SUM(1)", 1, "calls", False)
        self.assertEqual(len(selected), 3)
        self.assertEqual(selected.iloc[-1]["label"], "A / 2026-06 / =SUM(1)")
        csv = detail_csv(pd.DataFrame({"label": ["=SUM(1)"], "calls": [10]}),
                         [("label", "Detalle"), ("calls", "Llamadas")]).decode("utf-8-sig")
        self.assertIn("'=SUM(1)", csv)

    def test_detail_renders_only_selected_component(self):
        self.write_csv("calls.csv", [self.row("2026-06-01")])
        result = self.detail()
        totals = self.result()
        original = {key: value.copy(deep=True) for key, value in result.items()
                    if isinstance(value, pd.DataFrame)}
        sections = [
            ("DID / Mes / Proveedor", "did_month"),
            ("DID / Campaña / Mes", "did_campaign"),
            ("Volumen por DID", None),
            ("Estado / Mes / Proveedor", "state_month"),
            ("TMO / Contactabilidad por estado", None),
        ]
        for section, table_key in sections:
            with self.subTest(section=section), \
                    patch("detail_view.st.checkbox") as checkbox, \
                    patch("detail_view.st.caption") as caption, \
                    patch("detail_view.st.selectbox", return_value=section), \
                    patch("detail_view.detail_table") as table, \
                    patch("detail_view.report_chart") as chart:
                render_detail(result, totals)
                checkbox.assert_not_called()
                caption.assert_not_called()
                self.assertEqual(table.call_count, int(table_key is not None))
                self.assertEqual(chart.call_count, int(table_key is None))
                if table_key is not None:
                    self.assertIs(table.call_args.args[0], result[table_key])
                    self.assertEqual(table.call_args.kwargs["page_size"], 100)
                    self.assertEqual(table.call_args.kwargs["height"], 700)
        for key, frame in original.items():
            pd.testing.assert_frame_equal(frame, result[key])

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
