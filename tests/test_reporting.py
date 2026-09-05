# Report generation tests.

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from backend.config import Settings
from backend.reporting import ReportGenerator


class ReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.settings = replace(Settings(project_root=self.root))
        self.settings.ensure_dirs()
        self.case_id = "case-report"
        validation_dir = self.settings.validation_case_dir(self.case_id)
        validation_dir.mkdir(parents=True)
        (validation_dir / "validation_report.json").write_text(
            json.dumps(self._validation_report()),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_report_generation_keeps_optional_absent_elements_out_of_missing(self) -> None:
        report = ReportGenerator(self.settings).generate_from_latest_validation(
            self.case_id,
            use_llm=False,
        )

        self.assertEqual(report["summary"]["overall_status"], "PASS")
        self.assertEqual(report["summary"]["narrative_source"], "fallback")
        self.assertEqual(report["summary"]["required_missing_elements"], [])

        e1 = report["element_reports"][0]
        e18 = report["element_reports"][17]
        self.assertEqual(e1["submission_requirement"], "OPTIONAL")
        self.assertEqual(e1["presence_status"], "OPTIONAL_NOT_SUBMITTED")
        self.assertEqual(e1["element_status"], "N/A")
        self.assertEqual(e18["submission_requirement"], "COMPULSORY")
        self.assertEqual(e18["presence_status"], "PRESENT")
        self.assertEqual(e18["checkpoint_summary"], {
            "total_rules": 1,
            "pass": 1,
            "flag": 0,
            "not_found": 0,
            "na": 0,
        })
        self.assertEqual(report["summary"]["key_field_summary"]["customer_part_number"], "NVR-FR-BR-ROT-280")
        self.assertEqual(report["summary"]["key_field_summary"]["supplier_part_number"], "ABS-BR-280V-01")
        self.assertEqual(report["summary"]["key_field_summary"]["customer_name"], "Nivara Motors Pvt Ltd")
        self.assertEqual(report["summary"]["key_field_summary"]["supplier_name"], "Asha Brake Systems Ltd")
        self.assertEqual(report["summary"]["key_field_summary"]["cpk"], "1.67")
        self.assertTrue(e18["key_fields"])

        report_dir = self.settings.reports_case_dir(self.case_id)
        self.assertTrue((report_dir / "overall_report.json").exists())
        self.assertTrue((report_dir / "PPAP_Report.pdf").exists())
        self.assertTrue((report_dir / "element_reports" / "E18_report.json").exists())
        self.assertGreater((report_dir / "PPAP_Report.pdf").stat().st_size, 1000)
        self.assertFalse((report_dir / "overall_report.html").exists())

    def test_llm_narrative_uses_compact_report_facts(self) -> None:
        client = StaticNarrativeClient()
        report = ReportGenerator(self.settings, narrative_client=client).generate_from_latest_validation(
            self.case_id,
            use_llm=True,
        )

        self.assertEqual(report["summary"]["narrative_source"], "llm")
        self.assertEqual(report["element_reports"][17]["narrative"]["summary"], "Element narrative.")
        self.assertEqual(report["narrative"]["executive_summary"], "Overall narrative.")
        self.assertEqual(len(client.prompts), 2)
        self.assertTrue(all("VALIDATION_FACTS" in prompt for prompt in client.prompts))
        self.assertTrue(all("checkpoint_results" not in prompt for prompt in client.prompts))

    def _validation_report(self) -> dict:
        return {
            "summary": {
                "case_id": self.case_id,
                "validation_run_id": "run123",
                "created_at": "2026-07-08T18:32:50+00:00",
                "dry_run": False,
                "model": "gpt-4.1-mini",
                "submission_level": 1,
                "level_policy": {
                    "compulsory_elements": [18],
                    "optional_elements": list(range(1, 18)),
                    "present_compulsory_elements": [18],
                    "missing_compulsory_elements": [],
                    "present_optional_elements": [],
                    "absent_optional_elements": list(range(1, 18)),
                },
                "overall_status": "PASS",
                "required_missing_elements": [],
            },
            "element_results": [
                {
                    "element_number": 18,
                    "element_name": "Part Submission Warrant (PSW)",
                    "element_status": "PASS",
                    "checkpoint_results": [
                        {
                            "rule_id": "E18-E-001",
                            "status": "PASS",
                            "confidence": 98,
                            "evidence": [
                                {
                                    "quote": (
                                        "Customer part number NVR-FR-BR-ROT-280 "
                                        "Part name Front Ventilated Brake Rotor 280x24 "
                                        "Supplier part number ABS-BR-280V-01 "
                                        "Supplier Asha Brake Systems Ltd "
                                        "Customer Nivara Motors Pvt Ltd "
                                        "Submission level 1 Cpk 1.67 Ppk 1.52"
                                    )
                                }
                            ],
                            "reason": "PSW was found.",
                            "recommended_action": "",
                        }
                    ],
                    "element_summary": "PSW appears complete.",
                    "missing_evidence": [],
                    "model_error": None,
                    "required_for_submission_level": True,
                    "evidence_counts": {"primary_chunks": 1},
                    "paths": {},
                }
            ],
        }


class StaticNarrativeClient:
    model = "static-report-model"

    def __init__(self) -> None:
        self.prompts = []

    def validate(self, prompt: str) -> dict:
        self.prompts.append(prompt)
        if "executive_summary" in prompt:
            return {
                "executive_summary": "Overall narrative.",
                "submission_level_summary": "Level narrative.",
                "risk_summary": "Risk narrative.",
                "recommended_action_plan": ["Review final package."],
                "limitations": ["No cross-element comparison."],
            }
        return {
            "summary": "Element narrative.",
            "risk_statement": "Element risk.",
            "recommended_actions": ["Element action."],
            "review_notes": ["Element note."],
        }


if __name__ == "__main__":
    unittest.main()
