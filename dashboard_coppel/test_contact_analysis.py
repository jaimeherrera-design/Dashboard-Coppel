import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import app
from contact_analysis import diagnostic_tables, failure_figure
from test_data_loading import DataLoadingTests


class ContactAnalysisTests(DataLoadingTests):
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

    def test_analysis_tab_is_lazy_and_renders_with_zero_noncontacts(self):
        self.write_csv("calls.csv", [self.row("2026-06-01")])
        with patch.object(app, "DATA_ROOT", self.root), patch.object(app, "CONTACT_MAPPING_FILE", self.mapping):
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
