import tempfile
import unittest
from pathlib import Path

import pandas as pd

from mora import UNMAPPED_MORA, map_campaigns, read_mora_master, set_order, sort_by_mora, sorted_moras
from recurrence_report import group_by_mora

EXCLUDED = r"test|prueba"


class MoraTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "Mae_Mora.xlsx"
        pd.DataFrame({
            "Campaña": ["CP_A", " cp_b ", "CP_C", "CP_D"],
            "ALTURA DE MORA": ["MORA 8+", "MORA 1", "MANUAL ", "MORA 1"],
            "Posicion": [8, 1, 9, 1],
        }).to_excel(self.path, index=False)
        self.addCleanup(set_order, {})

    def test_master_maps_campaigns_case_insensitive_and_orders_by_position(self):
        mapping, order = read_mora_master(self.path)
        self.assertEqual(mapping["cp_b"], "MORA 1")
        self.assertEqual(mapping["cp_c"], "MANUAL")
        set_order(order)
        self.assertEqual(sorted_moras(["MANUAL", UNMAPPED_MORA, "MORA 8+", "MORA 1"]),
                         ["MORA 1", "MORA 8+", "MANUAL", UNMAPPED_MORA])

    def test_unmapped_names_keep_test_exclusion(self):
        mapping, _ = read_mora_master(self.path)
        mapped = map_campaigns(pd.Series(["CP_A", "cp_b", "CP_PRUEBA_1", "Nueva"]), mapping, EXCLUDED)
        self.assertEqual(mapped.tolist(), ["MORA 8+", "MORA 1", "CP_PRUEBA_1", UNMAPPED_MORA])

    def test_conflicting_or_incomplete_master_raises(self):
        pd.DataFrame({"Campaña": ["CP_A", "cp_a"], "ALTURA DE MORA": ["MORA 1", "MORA 2"], "Posicion": [1, 2]}).to_excel(self.path, index=False)
        with self.assertRaisesRegex(ValueError, "distintas"):
            read_mora_master(self.path)
        pd.DataFrame({"Campaña": ["CP_A"], "ALTURA DE MORA": ["MORA 1"], "Posicion": ["x"]}).to_excel(self.path, index=False)
        with self.assertRaisesRegex(ValueError, "Posicion"):
            read_mora_master(self.path)

    def test_sort_and_pdf_grouping_keep_totals(self):
        campaigns = pd.DataFrame({"campaña": ["CP_A", "CP_B", "CP_D"], "llamadas": [10, 20, 30], "llamadas_cronicos": [1, 2, 3]})
        grouped = sort_by_mora(group_by_mora(campaigns, self.path), "campaña")
        self.assertEqual(grouped["campaña"].tolist(), ["MORA 1", "MORA 8+"])
        self.assertEqual(grouped["llamadas"].tolist(), [50, 10])
        self.assertEqual(grouped["llamadas_cronicos"].sum(), 6)
        self.assertAlmostEqual(grouped["pct_cronicos"].iloc[0], 5 / 50)


if __name__ == "__main__":
    unittest.main()
