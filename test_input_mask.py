import unittest

from xl_input_mask import _assumptions_sheet_xml


class EmbeddedAssumptionMaskTests(unittest.TestCase):
    def test_external_mask_applies_to_generated_assumption_cells(self):
        rows = [{
            "sheet": "Model",
            "host": "Model!B2",
            "label": "Tax rate",
            "value": 0.25,
            "formula": "=Revenue*0.25",
        }]
        xml = _assumptions_sheet_xml(rows, {(2, 4), (2, 5)}).decode("utf-8")

        self.assertIn('r="A2"', xml)
        self.assertIn('r="B2"', xml)
        self.assertIn('r="C2"', xml)
        self.assertNotIn('r="D2"', xml)
        self.assertNotIn('r="E2"', xml)

    def test_formula_context_is_stored_as_text(self):
        rows = [{
            "sheet": "Model",
            "host": "Model!B2",
            "label": "Tax rate",
            "value": 0.25,
            "formula": "=Revenue*0.25",
        }]
        xml = _assumptions_sheet_xml(rows).decode("utf-8")
        self.assertIn("'=Revenue*0.25", xml)


if __name__ == "__main__":
    unittest.main()
