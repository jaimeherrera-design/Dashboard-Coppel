import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import app
from contact_analysis import decision_insights, diagnostic_tables, failure_figure, render_contact_analysis
from test_data_loading import DataLoadingTests


class ContactAnalysisTests(DataLoadingTests):
    def test_unmapped_outcomes_are_unique_and_assignable(self):
        import pandas as pd
        analysis = pd.DataFrame({
            "Campaign Name": ["A", "B", "A", "A"],
            "Call Type": ["Out"] * 4,
            "Call Outcome name": ["New Code", "new code ", "Busy", "Other"],
            "mapped": [False, False, True, False],
            "calls": [5, 7, 100, 1], "contacts": [0, 0, 0, 0],
        })
        table = app.unmapped_outcomes(analysis)
        self.assertEqual(list(table.columns), ["Call Outcome name", "Contacto / No Contacto", "Llamadas", "Alturas de mora"])
        self.assertEqual(table["Call Outcome name"].tolist(), ["new code", "Other"])
        self.assertEqual(table["Llamadas"].tolist(), [12, 1])
        self.assertEqual(table["Alturas de mora"].tolist(), [2, 1])
        self.assertEqual(pd.read_excel(__import__("io").BytesIO(app.unmapped_outcomes_excel(table))).shape, (2, 4))
        self.assertTrue(app.unmapped_outcomes(analysis.loc[analysis["mapped"]]).empty)

    def test_unmapped_mora_campaigns_export_original_names_with_active_filters(self):
        import pandas as pd
        from mora import UNMAPPED_MORA

        master = self.root / "Mae_Mora.xlsx"
        pd.DataFrame({
            "Campaña": ["Cobranza"],
            "ALTURA DE MORA": ["MORA 1"],
            "Posicion": [1],
        }).to_excel(master, index=False)
        self.write_csv("calls.csv", [
            self.row("2026-06-01", campaign="Nueva"),
            self.row("2026-06-02", campaign="nueva"),
            self.row("2026-06-03", campaign="Cobranza"),
            self.row("2026-06-04", campaign="Prueba nueva"),
            self.row("2026-10-01", campaign="Fuera de periodo"),
        ])
        files = app.discover_csv_files(self.root)
        mora_signature = app.file_signature(master)
        filters = {**self.filters, "mora_master": mora_signature}
        app.unmapped_mora_campaigns.clear()
        self.addCleanup(app.unmapped_mora_campaigns.clear)

        table = app.unmapped_mora_campaigns(files, mora_signature, filters)
        self.assertEqual(table.to_dict("records"), [{
            "Campaña": "Nueva",
            "Altura de mora": UNMAPPED_MORA,
            "Llamadas": 2,
        }])
        exported = pd.read_excel(__import__("io").BytesIO(app.unmapped_mora_campaigns_excel(table)))
        self.assertEqual(exported.shape, (1, 3))
        filters["Campaign Name"] = ["MORA 1"]
        self.assertTrue(app.unmapped_mora_campaigns(files, mora_signature, filters).empty)

    def test_diagnostics_preserve_classification_denominators_and_filters(self):
        self.write_csv("calls.csv", [
            self.row("2026-06-01", campaign="Manual"),
            self.row("2026-06-02", outcome="Busy"),
            self.row("2026-06-03", outcome="Unknown"),
            self.row("2026-06-04"),
            self.row("2026-06-05", campaign="Prueba"),
        ])
        self.result()
        with patch.object(app, "read_source_chunks", side_effect=AssertionError("CSV reread")):
            detail = app.aggregate_contact_analysis(app.discover_csv_files(self.root), app.file_signature(self.mapping), self.filters)
        campaigns, outcomes = diagnostic_tables(detail)
        self.assertEqual(campaigns["calls"].sum(), 4)
        self.assertEqual(campaigns["contacts"].sum(), 2)
        self.assertEqual(campaigns["noncontacts"].sum(), 2)
        self.assertEqual(campaigns["share"].sum(), 100)
        self.assertEqual(campaigns["failure_share"].sum(), 100)
        self.assertEqual(campaigns.loc[campaigns["Campaign Name"].eq("Manual"), "rate"].iloc[0], 100)
        self.assertEqual(outcomes.loc[outcomes["Call Outcome name"].eq("Unknown"), "classification"].iloc[0], "Sin clasificación en maestro")
        fig = failure_figure(detail, campaigns)
        for index in range(len(campaigns)):
            self.assertAlmostEqual(sum(trace.x[index] for trace in fig.data), 1)
        self.filters["Campaign Name"] = ["Manual"]
        filtered = app.aggregate_contact_analysis(app.discover_csv_files(self.root), app.file_signature(self.mapping), self.filters)
        self.assertEqual(filtered["calls"].sum(), 1)
        self.write_mapping(["No Contacto", "Contacto"])
        changed = app.aggregate_contact_analysis(app.discover_csv_files(self.root), app.file_signature(self.mapping), self.filters)
        self.assertEqual(changed["contacts"].sum(), 0)

    def test_six_traffic_light_decisions_sorted_by_urgency(self):
        self.write_csv("calls.csv", [
            self.row("2026-06-01 10:00:00"),
            self.row("2026-06-02 11:00:00", outcome="Busy"),
            self.row("2026-06-03 11:00:00", outcome="Busy", campaign="Manual"),
        ])
        result = self.result()
        detail = app.aggregate_contact_analysis(app.discover_csv_files(self.root), app.file_signature(self.mapping), self.filters)
        cards = decision_insights(detail, result)
        self.assertEqual(len(cards), 6)
        order = {"red": 0, "amber": 1, "green": 2}
        self.assertEqual([card["status"] for card in cards], sorted((card["status"] for card in cards), key=order.get))
        self.assertTrue(all(card["do"] and card["dont"] for card in cards))
        self.assertIn("66.7%", next(card["finding"] for card in cards if card["title"] == "Llamadas que nadie contesta"))
        with patch("contact_analysis.st.markdown") as markdown, patch("contact_analysis.report_chart"), \
                patch("contact_analysis.render_interactive_table"):
            render_contact_analysis(detail, result)
        html = " ".join(call.args[0] for call in markdown.call_args_list)
        self.assertEqual(html.count('class="decision-card'), 6)
        self.assertNotIn("Qué explica el KPI", html)

    def test_analysis_tab_is_lazy_and_renders_with_zero_noncontacts(self):
        self.write_csv("calls.csv", [self.row("2026-06-01")])
        with patch.object(app, "DATA_ROOT", self.root), \
                patch.object(app, "discover_dashboard_sources", return_value=self.dashboard_sources()):
            ui = AppTest.from_string("import app\napp.main()", default_timeout=30).run()
            self.assertEqual(len(ui.tabs[3].get("iframe")), 0)
            ui.session_state["dashboard-pages"] = "ANÁLISIS CONTACTABILIDAD"
            ui.run()
        self.assertFalse(ui.exception)
        self.assertFalse(ui.error)
        self.assertEqual(len(ui.tabs[3].get("iframe")), 3)
        self.assertEqual(len(ui.tabs[3].get("plotly_chart")), 2)


if __name__ == "__main__":
    unittest.main()
