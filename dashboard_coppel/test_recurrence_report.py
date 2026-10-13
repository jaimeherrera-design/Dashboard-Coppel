import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa

from recurrence_report import analyze, build_insights, build_pdf, normalize_phones, read_calls


def calls(rows):
    frame = pd.DataFrame(rows, columns=["phone", "ts", "contact", "invalid"])
    return {
        "phone": frame["phone"].to_numpy(np.int64),
        "ts": pd.to_datetime(frame["ts"]).to_numpy().astype("datetime64[s]").astype(np.int64),
        "contact": frame["contact"].to_numpy(bool),
        "invalid": frame["invalid"].to_numpy(bool),
        "campaign": np.zeros(len(frame), dtype=np.int32),
    }


class RecurrenceReportTests(unittest.TestCase):
    def test_normalize_phones(self):
        result = normalize_phones(pa.array(["5512345678", "525512345678", "5215512345678", "55 1234-5678", "123", None, "5512345678.0"]))
        self.assertEqual(result.to_pylist(), [5512345678, 5512345678, 5512345678, 5512345678, None, None, 5512345678])

    def test_attempts_until_first_contact_and_unreachable(self):
        rows = [
            (1, "2026-10-01 10:00", False, False), (1, "2026-10-01 09:00", False, False),
            (1, "2026-10-02 09:00", True, False), (1, "2026-10-03 09:00", False, False),
            (2, "2026-10-01 08:00", True, False),
        ] + [(3, f"2026-10-01 0{hour}:00", False, hour == 1) for hour in range(1, 7)] + [
            (3, f"2026-10-0{day} 09:00", False, False) for day in range(2, 6)
        ]
        m = analyze(calls(rows), np.array(["CAMP"], dtype=object))
        self.assertEqual((m["calls"], m["phones"], m["contacted"], m["never"], m["chronic"]), (15, 3, 2, 1, 1))
        self.assertEqual(m["chronic_calls"], 10)
        self.assertEqual(m["first_contact_median"], 2.0)
        self.assertEqual(m["calls_after_contact"], 1)
        self.assertEqual(m["invalid_phones"], 1)
        self.assertEqual((m["phone_days"], m["heavy_days"], m["heavy_phones"]), (9, 1, 1))
        curve = m["curve"].set_index("intento")
        self.assertAlmostEqual(curve.loc[1, "tasa"], 1 / 3)
        self.assertAlmostEqual(curve.loc[3, "tasa"], 1 / 2)
        self.assertEqual(curve.loc[3, "en_riesgo"], 2)
        unreachable = m["never_list"].iloc[0]
        self.assertEqual((unreachable["telefono"], unreachable["intentos"], unreachable["dias_marcados"], unreachable["max_intentos_un_dia"]), ("3", 10, 5, 6))
        self.assertEqual(m["hard_contacts"].iloc[0]["intentos_hasta_contacto"], 3)
        insights = build_insights(m)
        self.assertEqual(len(insights), 6)
        self.assertTrue(all(item["status"] in {"green", "amber", "red"} for item in insights))

    def test_read_calls_uses_dashboard_exclusions_and_builds_pdf(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "calls.parquet"
            pd.DataFrame({
                "Phone": ["5512345678", "525512345678", "5598765432", "999", "5511111111"],
                "Call end": ["2026-10-01 09:00:00", "2026-10-01 10:00:00", "2026-10-01 11:00:00", "2026-10-01 12:00:00", "2026-10-01 13:00:00"],
                "Campaign Name": ["CAMP", "CAMP", "CAMP", "CAMP", "Prueba X"],
                "Call Outcome name": ["No Answer", "1.Convenio", "Invalid Number", "1.Convenio", "1.Convenio"],
            }).to_parquet(path)
            arrays, names, totals = read_calls([path], {"1.convenio"}, None, None, log=lambda *_: None)
            self.assertEqual(totals, {"calls": 4, "invalid_phone_calls": 1})
            m = analyze(arrays, names)
            self.assertEqual((m["phones"], m["contacted"], m["invalid_phones"]), (2, 1, 1))
            m["invalid_phone_calls_source"] = totals["invalid_phone_calls"]
            output = Path(folder) / "reporte.pdf"
            build_pdf(m, build_insights(m), output, ["calls.parquet"], "lista.csv")
            self.assertTrue(output.read_bytes().startswith(b"%PDF"))

    def test_generate_report_in_memory_respects_period(self):
        from datetime import date
        from recurrence_report import generate_report
        with TemporaryDirectory() as folder:
            path = Path(folder) / "calls.parquet"
            pd.DataFrame({
                "Phone": ["5512345678"] * 12 + ["5598765432"],
                "Call end": [f"2026-10-01 0{h % 10}:00:00" for h in range(12)] + ["2026-11-05 09:00:00"],
                "Campaign Name": ["CAMP"] * 13,
                "Call Outcome name": ["No Answer"] * 12 + ["1.Convenio"],
            }).to_parquet(path)
            mapping = Path(folder) / "Mae_contacto.xlsx"
            pd.DataFrame({"Call Outcome name": ["1.Convenio", "No Answer"],
                          "Contacto / No Contacto": ["Contacto", "No Contacto"]}).to_excel(mapping, index=False)
            steps = []
            pdf, csv = generate_report([path], mapping, date(2026, 10, 1), date(2026, 10, 31), log=lambda *_: None,
                                       progress=lambda fraction, text: steps.append((fraction, text)))
            fractions = [fraction for fraction, _ in steps]
            self.assertEqual(fractions, sorted(fractions))
            self.assertEqual((fractions[0], fractions[-1]), (0.0, 1.0))
            self.assertTrue(any(text.startswith("Leyendo archivo 1 de 1") for _, text in steps))
            self.assertTrue(pdf.startswith(b"%PDF"))
            lines = csv.decode("utf-8-sig").strip().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertTrue(lines[1].startswith("5512345678;12;"))
            self.assertEqual(list(Path(folder).glob("*.pdf")), [])


if __name__ == "__main__":
    unittest.main()
