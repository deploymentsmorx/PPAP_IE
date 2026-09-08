# Validation workflow tests.

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from backend.config import Settings
from backend.anthropic_client import DEFAULT_ANTHROPIC_MODEL, AnthropicJsonClient
from backend.validation import ValidationRunner
from backend.validation.checkpoints import CheckpointCatalog


class ValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.settings = replace(Settings(project_root=self.root))
        self.settings.ensure_dirs()
        self.case_id = "case123"
        (self.settings.tagging_case_dir(self.case_id) / "json").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_checkpoint_catalog_counts_match_prompt_ready_rules(self) -> None:
        catalog = CheckpointCatalog()

        self.assertEqual(catalog.payload["counts"]["elements"], 18)
        self.assertEqual(catalog.payload["counts"]["rules"], 267)
        self.assertEqual(catalog.payload["counts"]["watch_outs"], 16)
        self.assertEqual(len(catalog.element(7)["rules"]), 18)
        self.assertEqual(catalog.element(7)["rules"][0]["rule_id"], "1")
        self.assertIn(18, catalog.related_element_numbers(7))

    def test_validation_client_default_timeout_is_1000_seconds(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(AnthropicJsonClient(timeout_seconds=1000).timeout_seconds, 1000)
            self.assertEqual(AnthropicJsonClient().model, DEFAULT_ANTHROPIC_MODEL)
            self.assertEqual(AnthropicJsonClient().max_output_tokens, 4096)

    def test_validation_dry_run_saves_prompt_evidence_and_report(self) -> None:
        self._write_tagged_json(
            "control_plan.tagging.json",
            {
                "document": {
                    "file_name": "02_Control_Plan.xlsx",
                    "file_type": "excel",
                },
                "tables": [
                    {
                        "sheet": "Control Plan",
                        "header": ["Operation", "Characteristic", "Reaction Plan"],
                        "rows": [["10", "Diameter", "Stop and contain"]],
                    }
                ],
                "worksheets": [{"sheet_name": "Control Plan"}],
                "element_tagging": {
                    "unit_predictions": [
                        {
                            "unit_id": "sheet:Control Plan",
                            "unit_type": "worksheet",
                            "label": "Control Plan",
                            "predicted_element": "Control Plan",
                            "element_number": 7,
                            "source": "sheet_name",
                            "confidence": 96,
                            "shared_context": False,
                        }
                    ]
                },
            },
        )
        self._write_tagged_json(
            "psw.tagging.json",
            {
                "document": {
                    "file_name": "18_PSW.pdf",
                    "file_type": "pdf",
                },
                "text": {
                    "digital": [
                        {
                            "page": 1,
                            "text": "Part Submission Warrant part number BR-100 revision A",
                        }
                    ],
                    "ocr": [],
                },
                "element_tagging": {
                    "unit_predictions": [
                        {
                            "unit_id": "page:1",
                            "unit_type": "page",
                            "label": "Part Submission Warrant",
                            "predicted_element": "Part Submission Warrant",
                            "element_number": 18,
                            "source": "page_title",
                            "confidence": 96,
                            "shared_context": False,
                        }
                    ]
                },
            },
        )

        report = ValidationRunner(self.settings).validate_case(
            self.case_id,
            dry_run=True,
            elements=[7],
        )

        self.assertEqual(report["summary"]["sequence"], [7])
        self.assertEqual(report["summary"]["checkpoint_count"], 18)
        self.assertTrue(report["summary"]["dry_run"])

        run_dir = self.settings.validation_case_dir(self.case_id)
        self.assertTrue((run_dir / "validation_report.json").exists())
        prompt_files = list((run_dir / "prompts").glob("*.prompt.txt"))
        evidence_files = list((run_dir / "evidence").glob("*.evidence.json"))
        self.assertEqual(len(prompt_files), 1)
        self.assertEqual(len(evidence_files), 1)

        prompt = prompt_files[0].read_text(encoding="utf-8")
        self.assertIn("02_Control_Plan.xlsx", prompt)
        self.assertIn("sheet:Control Plan", prompt)
        self.assertIn("[REFERENCED E18: PRESENT]", prompt)
        self.assertIn("OPTIONAL_NOT_SUBMITTED", prompt)

        evidence = json.loads(evidence_files[0].read_text(encoding="utf-8"))
        self.assertEqual(evidence["counts"]["primary_chunks"], 1)
        self.assertGreater(evidence["counts"]["related_elements"], 0)
        related = {item["element_number"]: item for item in evidence["related_elements"]}
        self.assertEqual(related[18]["presence_status"], "PRESENT")
        self.assertEqual(related[1]["presence_status"], "OPTIONAL_NOT_SUBMITTED")

    def test_validation_defaults_to_present_plus_level_required_elements(self) -> None:
        self._write_tagged_json(
            "control_plan.tagging.json",
            {
                "document": {
                    "file_name": "02_Control_Plan.xlsx",
                    "file_type": "excel",
                },
                "tables": [],
                "worksheets": [{"sheet_name": "Control Plan"}],
                "element_tagging": {
                    "unit_predictions": [
                        {
                            "unit_id": "sheet:Control Plan",
                            "unit_type": "worksheet",
                            "label": "Control Plan",
                            "predicted_element": "Control Plan",
                            "element_number": 7,
                            "source": "sheet_name",
                            "confidence": 96,
                            "shared_context": False,
                        }
                    ]
                },
            },
        )

        report = ValidationRunner(self.settings).validate_case(
            self.case_id,
            dry_run=True,
            submission_level=1,
        )

        self.assertEqual(report["summary"]["sequence"], [7, 18])
        self.assertEqual(report["summary"]["selection_policy"], "present_plus_level_required")
        self.assertEqual(report["summary"]["required_missing_elements"], [18])
        self.assertEqual(report["summary"]["level_policy"]["compulsory_elements"], [18])
        self.assertIn(7, report["summary"]["level_policy"]["present_optional_elements"])
        self.assertNotIn(7, report["summary"]["required_missing_elements"])

        run_dir = self.settings.validation_case_dir(self.case_id)
        psw_evidence = json.loads(
            next((run_dir / "evidence").glob("*E18*.evidence.json")).read_text(encoding="utf-8")
        )
        self.assertEqual(psw_evidence["counts"]["primary_chunks"], 0)
        self.assertIn("No tagged", psw_evidence["primary_absence_notice"])

    def test_level_two_compulsory_and_optional_elements_are_separated(self) -> None:
        self._write_tagged_json(
            "psw.tagging.json",
            {
                "document": {
                    "file_name": "18_PSW.pdf",
                    "file_type": "pdf",
                },
                "text": {
                    "digital": [{"page": 1, "text": "Part Submission Warrant"}],
                    "ocr": [],
                },
                "element_tagging": {
                    "unit_predictions": [
                        {
                            "unit_id": "page:1",
                            "unit_type": "page",
                            "label": "Part Submission Warrant",
                            "predicted_element": "Part Submission Warrant",
                            "element_number": 18,
                            "source": "page_title",
                            "confidence": 96,
                            "shared_context": False,
                        }
                    ]
                },
            },
        )
        self._write_tagged_json(
            "control_plan.tagging.json",
            {
                "document": {
                    "file_name": "07_Control_Plan.xlsx",
                    "file_type": "excel",
                },
                "tables": [],
                "worksheets": [{"sheet_name": "Control Plan"}],
                "element_tagging": {
                    "unit_predictions": [
                        {
                            "unit_id": "sheet:Control Plan",
                            "unit_type": "worksheet",
                            "label": "Control Plan",
                            "predicted_element": "Control Plan",
                            "element_number": 7,
                            "source": "sheet_name",
                            "confidence": 96,
                            "shared_context": False,
                        }
                    ]
                },
            },
        )

        report = ValidationRunner(self.settings).validate_case(
            self.case_id,
            dry_run=True,
            submission_level=2,
        )

        policy = report["summary"]["level_policy"]
        self.assertEqual(policy["compulsory_elements"], [1, 9, 14, 18])
        self.assertIn(7, policy["optional_elements"])
        self.assertIn(7, policy["present_optional_elements"])
        self.assertNotIn(7, report["summary"]["required_missing_elements"])
        self.assertEqual(report["summary"]["required_missing_elements"], [1, 9, 14])
        self.assertEqual(report["summary"]["sequence"], [1, 7, 9, 14, 18])

    def test_missing_evidence_result_does_not_call_model(self) -> None:
        class FailingClient:
            model = "test-model"

            def validate(self, prompt: str) -> dict:
                raise AssertionError("Model should not be called when primary evidence is absent.")

        report = ValidationRunner(self.settings, llm_client=FailingClient()).validate_case(
            self.case_id,
            dry_run=False,
            elements=[18],
            submission_level=1,
        )

        self.assertEqual(report["summary"]["sequence"], [18])
        self.assertEqual(report["summary"]["overall_status"], "NOT_FOUND")
        self.assertEqual(report["summary"]["required_missing_elements"], [18])
        self.assertIsNone(report["element_results"][0]["model_error"])
        self.assertEqual(report["element_results"][0]["evidence_counts"]["primary_chunks"], 0)

    def test_unsubmitted_optional_element_is_not_found_as_a_failure(self) -> None:
        class FailingClient:
            model = "test-model"

            def validate(self, prompt: str) -> dict:
                raise AssertionError("Model should not be called when primary evidence is absent.")

        report = ValidationRunner(self.settings, llm_client=FailingClient()).validate_case(
            self.case_id,
            dry_run=False,
            elements=[7],
            submission_level=2,
        )

        result = report["element_results"][0]
        self.assertEqual(result["element_status"], "N/A")
        self.assertEqual(report["summary"]["overall_status"], "N/A")
        self.assertEqual(result["missing_evidence"], [])
        self.assertTrue(all(item["status"] == "PASS" for item in result["checkpoint_results"]))

    def test_model_error_marks_element_for_review_with_diagnostics(self) -> None:
        class TimeoutClient:
            model = "test-model"

            def validate(self, prompt: str) -> dict:
                raise RuntimeError("Anthropic API request failed: timed out")

        self._write_tagged_json(
            "psw.tagging.json",
            {
                "document": {
                    "file_name": "E18_part_submission_warrant_PSW.pdf",
                    "file_type": "pdf",
                },
                "text": {
                    "digital": [
                        {
                            "page": 1,
                            "blocks": [
                                {
                                    "block": 1,
                                    "bbox": [10.0, 20.0, 30.0, 40.0],
                                    "lines": [
                                        "Element 18 - Part Submission Warrant",
                                        "Submission level Level 3",
                                        "Submission result requirements met",
                                    ],
                                }
                            ],
                        }
                    ],
                    "ocr": [],
                },
                "element_tagging": {
                    "unit_predictions": [
                        {
                            "unit_id": "page:1",
                            "unit_type": "page",
                            "label": "Page 1",
                            "predicted_element": "Part Submission Warrant",
                            "element_number": 18,
                            "source": "table_signature",
                            "confidence": 92,
                            "shared_context": False,
                        }
                    ]
                },
            },
        )

        report = ValidationRunner(self.settings, llm_client=TimeoutClient()).validate_case(
            self.case_id,
            dry_run=False,
            elements=[18],
            submission_level=1,
        )

        result = report["element_results"][0]
        self.assertEqual(result["element_status"], "REVIEW")
        self.assertEqual(report["summary"]["overall_status"], "REVIEW")
        self.assertEqual(report["summary"]["errored_elements"], [18])
        self.assertEqual(report["summary"]["review_elements"], [18])
        self.assertEqual(report["summary"]["missing_or_unresolved_elements"], [18])
        self.assertEqual(report["summary"]["required_missing_elements"], [])
        self.assertIn("timed out", result["model_error"])
        self.assertTrue(all(item["status"] == "NOT_FOUND" for item in result["checkpoint_results"]))

    def test_incomplete_model_response_marks_element_for_review_without_model_error(self) -> None:
        class PartialClient:
            model = "test-model"

            def __init__(self) -> None:
                self.calls = []

            def validate(self, prompt: str) -> dict:
                self.calls.append(prompt)
                return {
                    "element_number": 18,
                    "element_name": "Part Submission Warrant",
                    "element_status": "PASS",
                    "checkpoint_results": [
                        {
                            "rule_id": "1",
                            "status": "PASS",
                            "confidence": 90,
                            "evidence": [],
                            "reason": "present",
                            "recommended_action": "",
                        }
                    ],
                    "element_summary": "Partial response.",
                    "missing_evidence": [],
                }

        self._write_tagged_json(
            "psw.tagging.json",
            {
                "document": {
                    "file_name": "E18_part_submission_warrant_PSW.pdf",
                    "file_type": "pdf",
                },
                "text": {
                    "digital": [{"page": 1, "text": "Element 18 - Part Submission Warrant"}],
                    "ocr": [],
                },
                "element_tagging": {
                    "unit_predictions": [
                        {
                            "unit_id": "page:1",
                            "unit_type": "page",
                            "label": "Page 1",
                            "predicted_element": "Part Submission Warrant",
                            "element_number": 18,
                            "source": "table_signature",
                            "confidence": 92,
                            "shared_context": False,
                        }
                    ]
                },
            },
        )

        client = PartialClient()
        report = ValidationRunner(self.settings, llm_client=client).validate_case(
            self.case_id,
            dry_run=False,
            elements=[18],
            submission_level=1,
        )

        result = report["element_results"][0]
        run_dir = self.settings.validation_case_dir(self.case_id)
        self.assertEqual(result["element_status"], "REVIEW")
        self.assertEqual(report["summary"]["overall_status"], "REVIEW")
        self.assertEqual(report["summary"]["errored_elements"], [])
        self.assertEqual(report["summary"]["review_elements"], [18])
        self.assertEqual(report["summary"]["missing_or_unresolved_elements"], [18])
        self.assertIsNone(result["model_error"])
        self.assertIn("omitted", result["model_warning"])
        self.assertEqual(
            len([item for item in result["checkpoint_results"] if item["status"] == "NOT_FOUND"]),
            22,
        )
        self.assertEqual(len(client.calls), 3)
        self.assertFalse(list((run_dir / "responses").glob("*retry*.response.json")))

    def test_missing_checkpoint_schema_retries_in_rule_batches(self) -> None:
        class SchemaRetryClient:
            model = "test-model"

            def __init__(self) -> None:
                self.calls = []

            def validate(self, prompt: str) -> dict:
                self.calls.append(prompt)
                if len(self.calls) == 1:
                    return {
                        "element_number": 1,
                        "element_name": "Design Record",
                        "element_status": "PASS",
                        "confidence": 100,
                        "evidence": [{"quote": "Design record present"}],
                        "reason": "Top-level response only.",
                        "recommended_action": "",
                        "element_summary": "Design record appears complete.",
                        "missing_evidence": [],
                    }

                rule_ids = self._required_rule_ids(prompt)
                return {
                    "element_number": 1,
                    "element_name": "Design Record",
                    "element_status": "PASS",
                    "checkpoint_results": [
                        {
                            "rule_id": rule_id,
                            "status": "PASS",
                            "confidence": 95,
                            "evidence": [
                                {
                                    "file_name": "E01_design_record.pdf",
                                    "unit_id": "page:1",
                                    "quote": "Design Record Rev C",
                                }
                            ],
                            "reason": f"{rule_id} passed.",
                            "recommended_action": "",
                        }
                        for rule_id in rule_ids
                    ],
                    "element_summary": "Batch response complete.",
                    "missing_evidence": [],
                }

            def _required_rule_ids(self, prompt: str) -> list[str]:
                section = prompt.split("REQUIRED RULE IDS", 1)[1].split("\n\n", 1)[0]
                return [item.strip() for item in section.split(",") if item.strip()]

        self._write_tagged_json(
            "design_record.tagging.json",
            {
                "document": {
                    "file_name": "E01_design_record.pdf",
                    "file_type": "pdf",
                },
                "text": {
                    "digital": [{"page": 1, "text": "Element 1 Design Record Rev C"}],
                    "ocr": [],
                },
                "element_tagging": {
                    "unit_predictions": [
                        {
                            "unit_id": "page:1",
                            "unit_type": "page",
                            "label": "Design Record",
                            "predicted_element": "Design Record",
                            "element_number": 1,
                            "source": "page_title",
                            "confidence": 96,
                            "shared_context": False,
                        }
                    ]
                },
            },
        )

        client = SchemaRetryClient()
        with patch.dict(os.environ, {"PPAP_VALIDATION_RULE_BATCH_SIZE": "999"}, clear=False):
            report = ValidationRunner(self.settings, llm_client=client).validate_case(
                self.case_id,
                dry_run=False,
                elements=[1],
                submission_level=2,
            )

        result = report["element_results"][0]
        run_dir = self.settings.validation_case_dir(self.case_id)
        self.assertEqual(result["element_status"], "PASS")
        self.assertIsNone(result["model_error"])
        self.assertGreater(len(client.calls), 1)
        self.assertTrue(all(item["status"] == "PASS" for item in result["checkpoint_results"]))
        self.assertTrue(list((run_dir / "responses").glob("*E01*.initial.response.json")))
        self.assertTrue(list((run_dir / "responses").glob("*E01*.batch*.response.json")))

    def test_large_rule_sets_are_batched_into_nine_rule_default_model_calls(self) -> None:
        class BatchClient:
            model = "test-model"

            def __init__(self) -> None:
                self.calls = []

            def validate(self, prompt: str) -> dict:
                self.calls.append(prompt)
                rule_ids = self._required_rule_ids(prompt)
                return {
                    "element_number": 7,
                    "element_name": "Control Plan",
                    "element_status": "PASS",
                    "checkpoint_results": [
                        {
                            "rule_id": rule_id,
                            "status": "PASS",
                            "confidence": 95,
                            "evidence": [
                                {
                                    "file_name": "control_plan.xlsx",
                                    "unit_id": "sheet:Control Plan",
                                    "quote": "Control Plan",
                                }
                            ],
                            "reason": f"{rule_id} passed.",
                            "recommended_action": "",
                        }
                        for rule_id in rule_ids
                    ],
                    "element_summary": "Batch response complete.",
                    "missing_evidence": [],
                }

            def _required_rule_ids(self, prompt: str) -> list[str]:
                section = prompt.split("REQUIRED RULE IDS", 1)[1].split("\n\n", 1)[0]
                return [item.strip() for item in section.split(",") if item.strip()]

        self._write_tagged_json(
            "control_plan.tagging.json",
            {
                "document": {
                    "file_name": "control_plan.xlsx",
                    "file_type": "excel",
                },
                "tables": [
                    {
                        "sheet": "Control Plan",
                        "header": ["Op No", "Product Characteristic", "Reaction Plan"],
                        "rows": [["10", "Diameter", "Stop and contain"]],
                    }
                ],
                "worksheets": [{"sheet_name": "Control Plan"}],
                "element_tagging": {
                    "unit_predictions": [
                        {
                            "unit_id": "sheet:Control Plan",
                            "unit_type": "worksheet",
                            "label": "Control Plan",
                            "predicted_element": "Control Plan",
                            "element_number": 7,
                            "source": "sheet_name",
                            "confidence": 96,
                            "shared_context": False,
                        }
                    ]
                },
            },
        )

        client = BatchClient()
        with patch.dict(os.environ, {"PPAP_VALIDATION_RULE_BATCH_SIZE": "9"}, clear=False):
            report = ValidationRunner(self.settings, llm_client=client).validate_case(
                self.case_id,
                dry_run=False,
                elements=[7],
            )

        result = report["element_results"][0]
        run_dir = self.settings.validation_case_dir(self.case_id)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(result["element_status"], "PASS")
        self.assertEqual(len(result["checkpoint_results"]), 18)
        self.assertTrue(all(item["status"] == "PASS" for item in result["checkpoint_results"]))
        self.assertEqual(len(list((run_dir / "responses").glob("*E07*.batch*.response.json"))), 2)

    def _write_tagged_json(self, filename: str, payload: dict) -> None:
        path = self.settings.tagging_case_dir(self.case_id) / "json" / filename
        path.write_text(json.dumps(payload), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
