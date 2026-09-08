# Element tagging tests.

import os
import unittest
from unittest.mock import patch

from backend.tagging.element_tagger import ElementTagger


class ElementTaggerTests(unittest.TestCase):
    def test_control_plan_workbook_is_tagged_without_external_files(self) -> None:
        document = {
            "document": {
                "file_name": "E07_Control_Plan.xlsx",
                "file_type": "excel",
                "extension": ".xlsx",
            },
            "worksheets": [{"sheet_name": "Control_Plan"}],
            "tables": [
                {
                    "sheet": "Control_Plan",
                    "header": ["Control Plan", "Part Number", "Revision"],
                    "rows": [["Process Step", "Product Characteristic", "Reaction Plan"]],
                }
            ],
            "text": [],
        }

        result = ElementTagger().tag_document(document)

        self.assertEqual(result["primary_element"], "Control Plan")
        self.assertEqual(result["element_number"], 7)
        self.assertFalse(result["gamma_required"])

    def test_pdf_units_do_not_include_layout_coordinates(self) -> None:
        document = {
            "document": {
                "file_name": "E18_part_submission_warrant_PSW.pdf",
                "file_type": "pdf",
                "extension": ".pdf",
            },
            "text": {
                "digital": [
                    {
                        "page": 1,
                        "blocks": [
                            {
                                "block": 1,
                                "bbox": [34.015750885, 811.687561035],
                                "lines": [
                                    "Element 18 - Part Submission Warrant",
                                    "Submission level Level 3",
                                ],
                            }
                        ],
                    }
                ],
                "ocr": [],
            },
            "tables": [],
        }

        units = ElementTagger()._build_units(document)

        self.assertEqual(units[0]["unit_id"], "page:1")
        self.assertIn("Element 18 - Part Submission Warrant", units[0]["text"])
        self.assertIn("Submission level Level 3", units[0]["text"])
        self.assertNotIn("34.015750885", units[0]["text"])
        self.assertNotIn("811.687561035", units[0]["text"])

    def test_weak_generic_text_does_not_call_anthropic_by_default(self) -> None:
        document = {
            "document": {"file_name": "supplier_form.txt", "file_type": "text", "extension": ".txt"},
            "text": [{"text": "Customer Supplier Date Signature Part Number Revision"}],
        }

        with patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "",
                "PPAP_ANTHROPIC_API_KEY": "",
                "PPAP_TAGGING_ANTHROPIC_API_KEY": "",
                "PPAP_TAGGING_ANTHROPIC_FALLBACK": "0",
            },
        ):
            result = ElementTagger().tag_document(document)

        unit = result["unit_predictions"][0]
        self.assertIsNone(result["predicted_element"])
        self.assertEqual(result["status"], "Needs Review")
        self.assertTrue(unit["shared_context"])
        self.assertEqual(unit["source"], "shared_context")
        self.assertFalse(unit["llm_fallback"]["used"])

    def test_middle_metadata_page_is_not_inherited_from_previous_element(self) -> None:
        document = {
            "document": {"file_name": "merged_ppap.pdf", "file_type": "pdf", "extension": ".pdf"},
            "text": {
                "digital": [
                    {"page": 1, "text": "Part Submission Warrant Submission Level 3 Reason for Submission Initial"},
                    {"page": 2, "text": "Customer Supplier Date Signature Part Number Revision"},
                    {"page": 3, "text": "Control Plan Control Method Sample Size Frequency Reaction Plan"},
                ],
                "ocr": [],
            },
            "tables": [],
        }

        result = ElementTagger().tag_document(document)
        units = result["unit_predictions"]

        self.assertEqual(units[0]["predicted_element"], "Part Submission Warrant")
        self.assertIsNone(units[1]["predicted_element"])
        self.assertTrue(units[1]["shared_context"])
        self.assertEqual(units[2]["predicted_element"], "Control Plan")
        self.assertEqual(result["element_groups"][0]["shared_units"], ["page:2"])

    def test_clear_filename_context_blocks_weak_wrong_subsection_tag(self) -> None:
        document = {
            "document": {
                "file_name": "CEA-BR280_Customer_Engineering_Approval.docx",
                "file_type": "word",
                "extension": ".docx",
            },
            "headings": [{"text": "1. Approval Decision and Conditions"}],
            "paragraphs": [
                {"text": "1. Approval Decision and Conditions"},
                {"text": "Customer approval is granted for the submitted part submission warrant package."},
                {"text": "Submission level, customer disposition, representative signature, and approval date are recorded."},
            ],
            "tables": [],
        }

        result = ElementTagger().tag_document(document)
        unit = result["unit_predictions"][0]

        self.assertEqual(result["predicted_element"], "Customer Engineering Approval")
        self.assertEqual(unit["source"], "filename_context")

    def test_strong_heading_can_override_misleading_filename(self) -> None:
        document = {
            "document": {"file_name": "PSW_filename_but_control_plan.txt", "file_type": "text", "extension": ".txt"},
            "text": [{"text": "Control Plan\nReaction Plan Control Method Sample Size Frequency Process Step Product Characteristic"}],
        }

        result = ElementTagger().tag_document(document)

        self.assertEqual(result["predicted_element"], "Control Plan")
        self.assertEqual(result["unit_predictions"][0]["source"], "document")

    def test_package_index_readme_is_shared_context_not_psw(self) -> None:
        document = {
            "document": {"file_name": "00_README_Package_Index.txt", "file_type": "text", "extension": ".txt"},
            "text": [
                {
                    "text": (
                        "PPAP package index for a Level 3 submission. Customer, supplier, part number, "
                        "submission level, and reason for submission are listed for navigation only."
                    )
                }
            ],
        }

        result = ElementTagger().tag_document(document)
        unit = result["unit_predictions"][0]

        self.assertIsNone(result["predicted_element"])
        self.assertTrue(unit["shared_context"])
        self.assertEqual(unit["source"], "shared_context")

    def test_vda_signature_classifier_returns_vda_warrant_name(self) -> None:
        document = {
            "document": {"file_name": "warrant.txt", "file_type": "text", "extension": ".txt"},
            "text": [{"text": "Submission level Reason for submission Customer disposition"}],
        }

        result = ElementTagger("vda_ppf").tag_document(document)

        self.assertEqual(result["predicted_element"], "PPF Submission Warrant")
        self.assertEqual(result["element_number"], 19)

    def test_vda_short_alias_filename_is_tagged(self) -> None:
        document = {
            "document": {"file_name": "ISIR.xlsx", "file_type": "excel", "extension": ".xlsx"},
            "worksheets": [{"sheet_name": "ISIR"}],
            "tables": [],
            "text": [],
        }

        result = ElementTagger("vda_ppf").tag_document(document)

        self.assertEqual(result["predicted_element"], "Initial Sample Inspection Report")
        self.assertEqual(result["element_number"], 11)

    def test_customer_specific_requirements_signature_maps_to_each_standard(self) -> None:
        document = {
            "document": {"file_name": "customer_scope.txt", "file_type": "text", "extension": ".txt"},
            "text": [
                {
                    "text": (
                        "Customer specific requirements define the submission scope, customer form, "
                        "scope agreement, thresholds, and approval requirements."
                    )
                }
            ],
        }

        aiag_result = ElementTagger("aiag_ppap").tag_document(document)
        vda_result = ElementTagger("vda_ppf").tag_document(document)

        self.assertEqual(aiag_result["predicted_element"], "Customer Specific Requirements")
        self.assertEqual(aiag_result["element_number"], 17)
        self.assertEqual(vda_result["predicted_element"], "Customer Specific Requirements")
        self.assertEqual(vda_result["element_number"], 18)


if __name__ == "__main__":
    unittest.main()
