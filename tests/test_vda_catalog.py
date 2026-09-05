# VDA catalog structure tests.

import json
import unittest
from pathlib import Path


VDA_ROOT = Path(__file__).resolve().parents[1] / "backend" / "standards" / "vda_ppf"


class VdaCatalogTests(unittest.TestCase):
    def test_vda_catalog_counts_and_required_artifacts(self) -> None:
        catalog = self._read_json(VDA_ROOT / "validation" / "checkpoints" / "vda_validation_checkpoints.json")

        self.assertEqual(catalog["standard_id"], "vda_ppf")
        self.assertEqual(catalog["counts"]["elements"], 19)
        self.assertEqual(catalog["counts"]["rules"], 152)
        self.assertEqual(len(catalog["elements"]), 19)
        self.assertEqual(sum(len(element["rules"]) for element in catalog["elements"]), 152)
        self.assertEqual(catalog["elements"][10]["element_name"], "Initial Sample Inspection Report")
        self.assertEqual(catalog["elements"][18]["element_name"], "PPF Submission Warrant")

    def test_vda_rules_keep_prompt_ready_contract(self) -> None:
        catalog = self._read_json(VDA_ROOT / "validation" / "checkpoints" / "vda_validation_checkpoints.json")
        allowed = {"PASS", "FLAG", "NOT_FOUND"}

        hard_rules = catalog["global_prompt_contract"]["hard_rules"]
        self.assertTrue(any("Do not bypass" in rule for rule in hard_rules))
        self.assertTrue(any("Do not skip" in rule for rule in hard_rules))

        for element in catalog["elements"]:
            self.assertIn("element_number", element)
            self.assertIn("element_name", element)
            self.assertIn("applicability_rule", element)
            self.assertTrue(element["prompt_instructions"])
            self.assertTrue(element["watch_outs"])
            self.assertEqual(len(element["rules"]), 8)

            for expected_id, rule in enumerate(element["rules"], start=1):
                self.assertEqual(rule["rule_id"], str(expected_id))
                self.assertIn("Task:", rule["agent_instruction"])
                self.assertIn("Verdict logic:", rule["agent_instruction"])
                self.assertTrue(rule["evidence_instruction"])
                self.assertEqual(set(rule["allowed_verdicts"]), allowed)
                self.assertTrue(rule["must_return_evidence"])

    def test_vda_tagging_assets_match_catalog_names(self) -> None:
        catalog = self._read_json(VDA_ROOT / "validation" / "checkpoints" / "vda_validation_checkpoints.json")
        elements = self._read_json(VDA_ROOT / "tagging" / "elements.json")
        keywords = self._read_json(VDA_ROOT / "tagging" / "keywords.json")

        catalog_names = {element["element_name"] for element in catalog["elements"]}
        self.assertEqual(set(elements), catalog_names)
        self.assertEqual(set(keywords), catalog_names)
        self.assertEqual(
            {config["element_number"] for config in elements.values()},
            set(range(1, 20)),
        )

    @staticmethod
    def _read_json(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
