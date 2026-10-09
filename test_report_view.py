import unittest
from unittest.mock import patch
from types import SimpleNamespace

import pandas as pd
from streamlit.testing.v1 import AppTest

import app
from report_view import heat_table, presentation_metrics, render_report, report_table, report_chart, campaign_comparison, rate_line
import test_data_loading
from interactive_table import table_html


class ReportViewTests(test_data_loading.DataLoadingTests):
    def test_visual_rates_and_hourly_detail_preserve_totals(self):
        self.write_csv("calls.csv", [
            self.row("2026-06-01 10:01:00", talk=4),
            self.row("2026-06-01 10:45:00", outcome="Busy", talk=10),
            self.row("2026-06-02 11:00:00", talk=2),
        ])
        result = self.result()
        before = {key: value.copy(deep=True) for key, value in result.items() if isinstance(value, pd.DataFrame)}
        hourly = app.contact_hourly_days(
            app.discover_csv_files(self.root), app.file_signature(self.mapping), self.filters
        )
        self.assertEqual(hourly["calls"].sum(), result["total"])
        self.assertEqual(hourly["contacts"].sum(), result["contacts"])
        self.assertEqual(hourly.loc[hourly["hour"].eq(10), "calls"].iloc[0], 2)
        self.assertEqual(hourly.loc[hourly["hour"].eq(10), "contacts"].iloc[0], 1)
        metrics = presentation_metrics(result["month"])
        self.assertAlmostEqual(metrics["rate"].iloc[0], 2 / 3)
        self.assertAlmostEqual(metrics["tmo"].iloc[0], 25 / 3)
        with patch("report_view.st.plotly_chart"), patch("report_view.st.markdown"), patch("report_view.st.caption"):
            render_report(result, hourly)
        for key, expected in before.items():
            pd.testing.assert_frame_equal(result[key], expected)
        with patch("report_view.st.markdown") as markdown, patch("report_view.st.caption"):
            heat_table(hourly, "day_of_month", [1, 2, 3], "Heat")
        html = markdown.call_args.args[0]
        self.assertIn("50.00%", html)
        self.assertIn("100.00%", html)
        self.assertIn('class="no-data"', html)

    def test_sortable_tables_preserve_numeric_values_and_literal_campaign_names(self):
        self.write_csv("calls.csv", [self.row("2026-06-01", campaign="<script>alert(1)</script>")])
        result = self.result()
        with patch("report_view.render_interactive_table") as dataframe, patch("report_view.st.markdown"):
            report_table(result["campaign"], "Campaign Name", "Campaign", result)
        display = dataframe.call_args.args[0]
        self.assertEqual(display["Campaign Name"].iloc[0], "<script>alert(1)</script>")
        self.assertEqual(display["calls"].iloc[0], 1)
        self.assertEqual(display["contacts"].iloc[0], 1)
        self.assertEqual(display["rate"].iloc[0], 100)
        self.assertTrue(pd.api.types.is_numeric_dtype(display["calls"]))
        self.assertTrue(pd.api.types.is_numeric_dtype(display["contacts"]))
        self.assertTrue(pd.api.types.is_numeric_dtype(display["rate"]))
        html = table_html(display, dataframe.call_args.args[1], "Campaign")
        self.assertIn("\\u003cscript\\u003e", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("overflow:scroll", html)
        self.assertIn("background:#23458f;color:white", html)
        self.assertIn("minimumFractionDigits:2", html)

    def test_zoom_toolbar_always_visible(self):
        import plotly.graph_objects as go
        with patch("report_view.st.plotly_chart") as chart:
            report_chart(go.Figure())
        self.assertTrue(chart.call_args.kwargs["config"]["displayModeBar"])

    def test_principal_tables_include_weighted_totals(self):
        self.write_csv("calls.csv", [
            self.row("2026-06-01", talk=2),
            self.row("2026-07-01", outcome="Busy", talk=6),
        ])
        result = self.result()
        for key, dimension in [("month", "month"), ("campaign", "Campaign Name")]:
            with patch("report_view.render_interactive_table") as render:
                report_table(result[key], dimension, "Tabla", result, performance=True)
            totals = render.call_args.kwargs["totals"]
            self.assertEqual(totals[dimension], "Total")
            self.assertEqual(totals["calls"], 2)
            self.assertEqual(totals["contacts"], 1)
            self.assertEqual(totals["rate"], 50)
            self.assertEqual(totals["tmo"], 7)
            self.assertEqual(totals["idle"], 2)

    def test_rate_lines_are_taller_and_leave_room_for_labels(self):
        table = pd.DataFrame({
            "weekday": ["Lun", "Mar"], "calls": [100, 100], "contacts": [4, 8],
            "talk_sum": [100, 100], "wrap_sum": [0, 0], "wait_sum": [0, 0],
        })
        with patch("report_view.st.plotly_chart") as chart:
            rate_line(table, "weekday", "Contactabilidad", .06, ["Lun", "Mar"])
        figure = chart.call_args.args[0]
        self.assertEqual(figure.layout.height, 344)
        self.assertEqual(list(figure.data[0].y), [.04, .08])
        self.assertGreater(figure.layout.yaxis.range[1], .08)
        self.assertLess(figure.layout.yaxis.range[0], .04)

    def test_campaign_comparison_keeps_independent_metrics_and_unique_rows(self):
        names = ["Campaign with the same long prefix for comparison A", "Campaign with the same long prefix for comparison B"]
        table = pd.DataFrame({
            "Campaign Name": names, "calls": [100, 10], "contacts": [5, 10],
            "talk_sum": [100, 20], "wrap_sum": [20, 5], "wait_sum": [10, 2],
        })
        original = table.copy(deep=True)
        fig = campaign_comparison(table, 200)
        self.assertEqual(list(fig.data[0].x), [.5, .05])
        self.assertEqual(list(fig.data[1].x), [.05, 1])
        self.assertEqual(list(fig.data[0].y), [0, 1])
        self.assertEqual(list(fig.data[1].y), [0, 1])
        self.assertEqual(fig.data[0].xaxis, "x")
        self.assertEqual(fig.data[1].xaxis, "x2")
        self.assertEqual(fig.data[0].marker.color, "#168cff")
        self.assertEqual(fig.data[1].marker.color, "#23458f")
        self.assertEqual(fig.data[1].text[1], "100.00%")
        self.assertEqual(fig.data[0].textposition, "outside")
        self.assertEqual(fig.data[1].customdata[1][0], names[1])
        pd.testing.assert_frame_equal(table, original)

    def test_ui_keeps_exact_kpi_labels_and_values(self):
        self.write_csv("calls.csv", [
            self.row("2026-06-01 10:00:00", talk=2),
            self.row("2026-06-02 11:00:00", outcome="Busy", talk=6),
        ])
        sources = SimpleNamespace(
            parquet=app.discover_csv_files(self.root),
            masters={
                "Mae_contacto.xlsx": app.file_signature(self.mapping),
                "Mae_did.xlsx": app.file_signature(self.did_mapping),
                "Mae_lada.xlsx": app.file_signature(self.lada_mapping),
            },
        )
        with patch.object(app, "DATA_ROOT", self.root), patch.object(app, "discover_drive_sources", return_value=sources):
            ui = AppTest.from_string("import app\napp.main()", default_timeout=30).run()
        self.assertFalse(ui.exception)
        self.assertFalse(ui.error)
        banners = [element.value for element in ui.markdown if '<header class="executive-banner">' in element.value]
        self.assertEqual(len(banners), 1)
        self.assertIn('alt="Coppel"', banners[0])
        self.assertIn("data:image/png;base64,", banners[0])
        self.assertIn("01/06/2026", banners[0])
        self.assertIn("02/06/2026", banners[0])
        cards = [element.value for element in ui.markdown if 'class="kpi-card' in element.value]
        self.assertEqual(len(cards), 5)
        icons = [html.split('<div class="kpi-icon">', 1)[1] for html in cards]
        self.assertEqual(len(set(icons)), 5)
        self.assertTrue(all('aria-hidden="true"' in icon for icon in icons))
        for html, label, value in zip(
            cards,
            ["Total llamadas", "Contactos", "% de contactos", "Idle time promedio", "TMO"],
            ["2", "1", "50.00%", "2.0 s", "7.0 s"],
        ):
            self.assertIn(f">{label}</div>", html)
            self.assertIn(f">{value}</div>", html)
        self.assertEqual([tab.label for tab in ui.tabs], ["DASHBOARD PRINCIPAL", "DETALLE DID / ESTADO", "RESULTADO LLAMADAS", "ANÁLISIS CONTACTABILIDAD"])
        self.assertEqual(len(ui.tabs[0].get("plotly_chart")), 6)
        self.assertEqual(len(ui.tabs[0].get("iframe")), 2)
        self.assertEqual(len(ui.tabs[1].get("iframe")), 0)
        self.assertTrue(any("DÍA DEL MES Y HORA" in item.value for item in ui.markdown))
        with patch.object(app, "DATA_ROOT", self.root), patch.object(app, "discover_drive_sources", return_value=sources):
            ui.session_state["dashboard-pages"] = "DETALLE DID / ESTADO"
            ui.run()
        self.assertFalse(ui.exception)
        self.assertFalse(ui.error)
        self.assertEqual(len(ui.tabs[1].get("plotly_chart")), 0)
        self.assertEqual(len(ui.tabs[1].get("iframe")), 1)
        self.assertEqual(len(ui.checkbox), 0)
        self.assertEqual(ui.selectbox(key="detail-render-section").value, "DID / Mes / Proveedor")
        detail_cards = [element.value for element in ui.markdown if 'class="kpi-card' in element.value]
        self.assertEqual(detail_cards, cards)

    def test_empty_selection_renders_without_new_kpis(self):
        self.write_csv("calls.csv", [self.row("2026-06-01")])
        self.filters["Campaign Name"] = ["No such campaign"]
        result = self.result()
        with patch("report_view.st.info") as info:
            render_report(result, pd.DataFrame())
        info.assert_called_once()

    def test_detail_master_error_does_not_hide_principal(self):
        self.write_csv("calls.csv", [self.row("2026-06-01")])
        with patch.object(app, "DATA_ROOT", self.root), patch.object(app, "CONTACT_MAPPING_FILE", self.mapping), patch.object(app, "DID_MAPPING_FILE", self.root / "missing.xlsx"), patch.object(app, "LADA_MAPPING_FILE", self.lada_mapping):
            ui = AppTest.from_string("import app\napp.main()", default_timeout=30).run()
            self.assertFalse(ui.error)
            self.assertEqual(len(ui.tabs[0].get("plotly_chart")), 6)
            ui.session_state["dashboard-pages"] = "DETALLE DID / ESTADO"
            ui.run()
            self.assertFalse(ui.exception)
            self.assertEqual(len(ui.error), 1)
            self.assertIn("No se pudo cargar el detalle DID / Estado", ui.error[0].value)
            ui.session_state["dashboard-pages"] = "DASHBOARD PRINCIPAL"
            ui.run()
            self.assertFalse(ui.error)
            self.assertEqual(len(ui.tabs[0].get("iframe")), 2)


if __name__ == "__main__":
    unittest.main()
