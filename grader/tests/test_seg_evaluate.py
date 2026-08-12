from __future__ import annotations

import math
import unittest

from xl_seg.evaluate import EPOCH, Evaluator, RangeValues, _irr


class EvaluatorFunctionCoverageTests(unittest.TestCase):
    def setUp(self):
        self.evaluator = Evaluator(graph=None, cg=None)

    def test_finance_and_rounding_functions(self):
        expected = 100 / 1.1 + 100 / 1.1**2
        self.assertTrue(math.isclose(
            self.evaluator._fn_npv([0.1, [100, 100]]),
            expected,
        ))
        multiple_root_flows = [
            -1000, 998.356164383561, -3240.0821917808216,
            -2907.686301369842, -2555.59944172466, -2134.760761973943,
            -1713.603437823287, -1242.8320540504646, -746.1239110597326,
            -163.78517009537063, 9095.005857453772,
        ]
        self.assertTrue(math.isclose(
            _irr(multiple_root_flows),
            -0.08159949366437602,
            abs_tol=1e-12,
        ))
        self.assertEqual(self.evaluator._fn_mround([1193, 5]), 1195)
        self.assertEqual(self.evaluator._fn_mround([-12, -5]), -10)
        self.assertEqual(self.evaluator._fn_roundup([12.341, 2]), 12.35)
        self.assertEqual(
            self.evaluator._fn_forecast([4, [2, 4, 6], [1, 2, 3]]),
            8,
        )
        self.assertTrue(math.isclose(
            self.evaluator._fn_rri([2, 100, 121]),
            0.1,
        ))

    def test_conditional_aggregations(self):
        targets = RangeValues([10, 20, 30, 40], 1, 4)
        regions = RangeValues(["A", "A", "B", "B"], 1, 4)
        years = RangeValues([2024, 2025, 2024, 2025], 1, 4)
        args = [targets, regions, "B", years, ">=2025"]
        self.assertEqual(self.evaluator._fn_sumifs(args), 40)
        self.assertEqual(self.evaluator._fn_averageifs(args), 40)
        self.assertEqual(
            self.evaluator._fn_countifs([regions, "B", years, ">=2024"]),
            2,
        )

    def test_lookup_and_array_functions(self):
        table = RangeValues(["Downside", 3, "Base", 7, "Upside", 11], 3, 2)
        self.assertEqual(
            self.evaluator._fn_vlookup(["Base", table, 2, False]),
            7,
        )
        self.assertEqual(
            self.evaluator._fn_match(["Base", ["Downside", "Base"], 0]),
            2,
        )
        self.assertEqual(
            self.evaluator._fn_sumproduct([[1, 2, 3], [4, 5, 6]]),
            32,
        )
        transposed = self.evaluator._fn_transpose(
            [RangeValues([1, 2, 3, 4, 5, 6], 2, 3)]
        )
        self.assertEqual(transposed, [1, 4, 2, 5, 3, 6])
        self.assertEqual((transposed.rows, transposed.cols), (3, 2))

    def test_date_text_and_logical_functions(self):
        jan_31 = (EPOCH.replace(year=2024, month=1, day=31) - EPOCH).days
        feb_29 = (EPOCH.replace(year=2024, month=2, day=29) - EPOCH).days
        self.assertEqual(self.evaluator._fn_edate([jan_31, 1]), feb_29)
        self.assertEqual(self.evaluator._fn_days([feb_29, jan_31]), 29)
        self.assertEqual(self.evaluator._fn_month([feb_29]), 2)
        self.assertEqual(self.evaluator._fn_text([0.125, "0.0%"]), "12.5%")
        self.assertEqual(self.evaluator._fn_proper(["custom FORMULA"]), "Custom Formula")
        self.assertTrue(self.evaluator._fn_and([[1, True, "TRUE"]]))
        self.assertTrue(self.evaluator._fn_or([[0, False, 2]]))


if __name__ == "__main__":
    unittest.main()
