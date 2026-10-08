from unittest.mock import patch
import unittest

from streamlit.testing.v1 import AppTest

import app
from outcome_view import outcome_figure, render_outcomes
from test_data_loading import DataLoadingTests


class OutcomeViewTests(DataLoadingTests):
    def test_grouping_shares_and_totals_preserve_filtered_calls(self):
        self.write_csv("calls.csv", [
            self.row("2026-06-01 10:00:00"),
            self.row("2026-06-01 11:00:00"),
            self.row("2026-07-02 10:00:00", outcome="Busy"),
            self.row("2026-07-02 11:00:00", campaign="Test"),
        ])
        files = app.discover_csv_files(self.root)
        signature = app.file_signature(self.mapping)
        main = self.result()
        with patch.object(app, "read_source_chunks", side_effect=AssertionError("CSV reread")):
            result = app.aggregate_outcomes(files, signature, self.filters)
        self.assertEqual(result["calls"].sum(), main["total"])
        self.assertEqual(result["contacts"].sum(), main["contacts"])
        self.assertEqual(result["date"].tolist(), ["2026-07-02", "2026-06-01"])
        fig = outcome_figure(result)
        self.assertEqual(list(fig.data[0].x), [2 / 3, 1 / 3])
        self.assertEqual([row[0] for row in fig.data[0].customdata], ["recordatorio", "Busy"])
        with patch("outcome_view.render_interactive_table") as render, patch("outcome_view.report_chart"):
            render_outcomes(result)
        self.assertEqual(render.call_args.kwargs["totals"]["calls"], 3)
        self.assertTrue(render.call_args.kwargs["compact"])
        roots = render.call_args.args[0]
        self.assertEqual(roots.loc[roots["_depth"].eq(0), "calls"].sum(), 3)
        self.assertEqual(set(roots["_depth"]), {0, 1, 2})
        self.filters["Campaign Name"] = ["Missing"]
        self.assertTrue(app.aggregate_outcomes(files, signature, self.filters).empty)

    def test_outcome_tab_loads_lazily(self):
        self.write_csv("calls.csv", [self.row("2026-06-01")])
        with patch.object(app, "DATA_ROOT", self.root), patch.object(app, "CONTACT_MAPPING_FILE", self.mapping):
            ui = AppTest.from_string("import app\napp.main()", default_timeout=30).run()
            self.assertEqual(len(ui.tabs[2].get("iframe")), 0)
            ui.session_state["dashboard-pages"] = "RESULTADO LLAMADAS"
            ui.run()
        self.assertFalse(ui.exception)
        self.assertFalse(ui.error)
        self.assertEqual(len(ui.tabs[2].get("iframe")), 1)
        self.assertEqual(len(ui.tabs[2].get("plotly_chart")), 1)


if __name__ == "__main__":
    unittest.main()
