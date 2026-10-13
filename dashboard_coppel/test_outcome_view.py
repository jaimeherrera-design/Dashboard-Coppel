from unittest.mock import patch
import unittest

import pandas as pd
from streamlit.testing.v1 import AppTest

import app
from data_cache import SUMMARY_KEYS
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
        result = app.aggregate_outcomes(files, signature, self.filters)
        with patch.object(app, "read_source_chunks", side_effect=AssertionError("CSV reread")):
            cached = list(app.read_outcome_summaries(files))
        self.assertEqual(len(cached), 1)
        pd.testing.assert_frame_equal(result, app.aggregate_outcomes(files, signature, self.filters))
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

    def test_state_filter_and_grouping_preserve_totals(self):
        other = {**self.row("2026-06-01 12:00:00", outcome="Busy"), "Phone": "8112345678"}
        self.write_csv("calls.csv", [self.row("2026-06-01 10:00:00"), self.row("2026-07-02 11:00:00"), other])
        files = app.discover_csv_files(self.root)
        signature = app.file_signature(self.mapping)
        masters = (app.file_signature(self.did_mapping), app.file_signature(self.lada_mapping))
        base = self.result()
        self.assertEqual(base["total"], 3)
        states = app.aggregate_outcome_states(files, signature, masters, self.filters)
        self.assertEqual(states["calls"].sum(), 3)
        self.assertEqual(states.groupby("state")["calls"].sum().to_dict(), {"CDMX": 2, "Sin cruce": 1})
        by_state = {}
        for state in ["CDMX", "Sin cruce"]:
            filters = {**self.filters, "State": [state], "detail_masters": masters}
            by_state[state] = app.aggregate(files, signature, app.FILTER_VERSION, filters)
            self.assertEqual(
                app.aggregate_outcomes(files, signature, filters)["calls"].sum(), by_state[state]["total"],
            )
        self.assertEqual(by_state["CDMX"]["total"], 2)
        self.assertEqual(by_state["CDMX"]["contacts"], 2)
        self.assertEqual(by_state["Sin cruce"]["total"], 1)
        self.assertEqual(sum(result["total"] for result in by_state.values()), base["total"])
        outcome = app.aggregate_outcomes(files, signature, self.filters)
        with patch("outcome_view.st.radio", return_value="Estado / Resultado"), \
                patch("outcome_view.render_interactive_table") as render, patch("outcome_view.report_chart"):
            render_outcomes(outcome, lambda: states)
        roots = render.call_args.args[0]
        self.assertEqual(roots.loc[roots["_depth"].eq(0), "label"].tolist(), ["CDMX", "Sin cruce"])
        self.assertEqual(render.call_args.kwargs["totals"]["calls"], 3)

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

    def test_daily_outcome_summaries_preserve_metrics_and_reduce_rows(self):
        rows = [self.row(f"2026-06-01 {hour:02}:15:00", talk=hour / 10) for hour in range(24)]
        rows += [
            self.row("2026-06-02 23:59:59", outcome="Busy", campaign="Otra"),
            self.row("2026-06-02 08:00:00", outcome="Busy", campaign="Otra"),
            self.row("2026-06-02 09:00:00", outcome="Busy", campaign="Otra"),
            self.row("2026-06-01 12:00:00", campaign="Prueba"),
        ]
        self.write_csv("calls.csv", rows)
        files = app.discover_csv_files(self.root)
        hourly = pd.concat(list(app.read_source_summaries(files)), ignore_index=True)
        daily = pd.concat(list(app.read_outcome_summaries(files)), ignore_index=True)

        self.assertLess(len(daily), len(hourly))
        self.assertEqual(len(daily), 3)
        metrics = ["calls", "talk_sum", "wrap_sum", "wait_sum"]
        pd.testing.assert_series_equal(
            hourly[metrics].sum().sort_index(),
            daily[metrics].sum().sort_index(),
        )
        self.assertEqual(daily["Call end"].dt.strftime("%H:%M:%S").unique().tolist(), ["00:00:00"])
        with patch.object(app, "read_source_chunks", side_effect=AssertionError("CSV reread")):
            reused = list(app.read_outcome_summaries(files))
        pd.testing.assert_frame_equal(daily, pd.concat(reused, ignore_index=True))

    def test_daily_outcome_state_summaries_preserve_totals_and_state_mapping(self):
        rows = [
            self.row(f"2026-06-01 {hour:02}:15:00", talk=hour / 10)
            for hour in range(24)
        ]
        rows += [
            {**self.row("2026-06-02 10:00:00", outcome="Busy"), "Phone": "8112345678"},
        ]
        self.write_csv("calls.csv", rows)
        files = app.discover_csv_files(self.root)
        _, ladas = app.load_detail_masters(
            app.file_signature(self.did_mapping), app.file_signature(self.lada_mapping),
        )
        hourly = pd.concat(list(app.read_state_summaries(files, ladas)), ignore_index=True)
        daily = pd.concat(list(app.read_outcome_state_summaries(files, ladas)), ignore_index=True)

        self.assertLess(len(daily), len(hourly))
        self.assertEqual(daily.groupby("state")["calls"].sum().to_dict(), {"CDMX": 24, "Sin cruce": 1})
        metrics = ["calls", "talk_sum", "wrap_sum", "wait_sum"]
        pd.testing.assert_series_equal(
            hourly[metrics].sum().sort_index(),
            daily[metrics].sum().sort_index(),
        )


if __name__ == "__main__":
    unittest.main()
