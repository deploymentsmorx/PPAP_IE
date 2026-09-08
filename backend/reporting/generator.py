# Builds final report JSON and PDF outputs.

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..env import load_project_env
from ..anthropic_client import AnthropicJsonClient
from ..review import apply_rule_reviews
from ..standards.profiles import DEFAULT_STANDARD_ID, artifact_prefix, normalize_standard_id, report_filename, standard_display_name, standard_profile
from ..validation.checkpoints import CheckpointCatalog
from .pdf_generator import generate_report_pdf


ISSUE_STATUSES = {"FLAG", "NOT_FOUND", "ERROR"}
KEY_FIELD_PATTERNS = [
    (
        "customer_part_number",
        "Customer Part Number",
        [r"\bCustomer\s+Part\s+(?:No\.?|Number)\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]+)"],
    ),
    (
        "supplier_part_number",
        "Supplier Part Number",
        [r"\bSupplier\s+Part\s+(?:No\.?|Number)\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]+)"],
    ),
    (
        "part_number",
        "Part Number",
        [r"(?<!Customer\s)(?<!Supplier\s)\bPart\s+(?:No\.?|Number)\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]+)"],
    ),
    (
        "part_name",
        "Part Name",
        [
            r"\bPart\s+Name\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9 ._/\-xX]+?)(?=\s+(?:Supplier|Customer|Part\s+(?:No|Number|Name)|Drawing|Revision|Rev|Submission|Reason|Results|$))"
        ],
    ),
    (
        "customer_name",
        "Customer",
        [
            r"\bCustomer\s+Name\s*[:#-]?\s*([A-Za-z0-9 .,&/-]{2,80}?)(?=\s+(?:Supplier|Part\s+(?:No|Number|Name)|Drawing|Release|Submission|Reason|$))",
            r"\bCustomer\s+([A-Z][A-Za-z0-9 .,&/-]{2,80}?(?:Ltd\.?|Limited|Inc\.?|LLC|Corp\.?|Corporation|Motors|Systems|Pvt\.?\s*Ltd\.?))(?=\s+(?:Supplier|Part\s+(?:No|Number|Name)|Drawing|Release|Submission|Reason|$))",
        ],
    ),
    (
        "supplier_name",
        "Supplier",
        [
            r"\bSupplier\s+Name\s*[:#-]?\s*([A-Za-z0-9 .,&/-]{2,80}?)(?=\s+(?:Customer|Part\s+(?:No|Number|Name)|Drawing|Release|Submission|Reason|$))",
            r"\bSupplier(?!\s+Part)\s+([A-Z][A-Za-z0-9 .,&/-]{2,80}?(?:Ltd\.?|Limited|Inc\.?|LLC|Corp\.?|Corporation|Motors|Systems|Pvt\.?\s*Ltd\.?))(?=\s+(?:Customer|Part\s+(?:No|Number|Name)|Drawing|Release|Submission|Reason|$))",
        ],
    ),
    (
        "drawing_number",
        "Drawing Number",
        [r"\bDrawing\s+(?:No\.?|Number)\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]+)"],
    ),
    (
        "revision",
        "Revision / Change Level",
        [
            r"\b(?:Revision\s*/\s*Change\s+Level|Change\s+Level|Revision)\s*[:#-]?\s*([A-Z0-9._/-]+)",
            r"\bRev(?:\.|\b)\s*[:#-]?\s*([A-Z0-9._/-]+)",
        ],
    ),
    (
        "submission_level",
        "Submission Level",
        [r"\bSubmission\s+Level\s*[:#-]?\s*(?:Level\s*)?([1-5])"],
    ),
    (
        "reason_for_submission",
        "Reason for Submission",
        [
            r"\bReason\s+for\s+Submission\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9 .,&/-]{2,100}?)(?=\s+(?:Submission|Results|Customer|Supplier|Authorized|Signature|$))"
        ],
    ),
    (
        "supplier_representative",
        "Supplier Representative",
        [
            r"\bAuthorized\s+supplier\s+(?:representative|signature)\s*[:#-]?\s*([A-Za-z][A-Za-z .,'-]{2,80}?)(?=\s+(?:Signature|Date|$))"
        ],
    ),
    (
        "signature_date",
        "Signature Date",
        [r"\bSignature\s+date\s*[:#-]?\s*(\d{4}-\d{2}-\d{2})"],
    ),
    (
        "material_specification",
        "Material Specification",
        [
            r"\bMaterial(?:\s*/\s*hardness)?\s*[:#-]?\s*([A-Z0-9][A-Za-z0-9 .,+/-]{2,100}?)(?=\s+(?:Hardness|Customer|Supplier|Part|Drawing|$))"
        ],
    ),
    ("cpk", "Cpk", [r"\bCpk\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)"]),
    ("ppk", "Ppk / Pk", [r"\b(?:Ppk|Pk)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)"]),
    ("cp", "Cp", [r"\bCp\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)"]),
    ("pp", "Pp", [r"\bPp\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)"]),
    ("ndc", "NDC", [r"\bNDC\s*[:=]?\s*([0-9]+)"]),
    ("gage_rr", "Gage R&R", [r"\b(?:Gage|Gauge)\s+R&R\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?\s*%?)"]),
]


class ReportGenerator:
    def __init__(
        self,
        settings,
        narrative_client: Any | None = None,
        catalog: CheckpointCatalog | None = None,
        standard_id: str = DEFAULT_STANDARD_ID,
    ):
        self.settings = settings
        self.standard_id = normalize_standard_id(standard_id)
        self.catalog = catalog or CheckpointCatalog(standard_id=self.standard_id)
        self.profile = standard_profile(self.standard_id)
        self.narrative_client = narrative_client or self._default_narrative_client()

    def generate_from_latest_validation(self, case_id: str, use_llm: bool = True) -> dict[str, Any]:
        validation_path = self.settings.validation_case_dir(case_id) / "validation_report.json"
        if not validation_path.exists():
            raise FileNotFoundError(f"Validation report not found for case: {case_id}")
        raw_report = self._read_json(validation_path)
        self._use_standard(raw_report.get("summary", {}).get("standard_id"))
        validation_report = apply_rule_reviews(
            case_id,
            raw_report,
            db_path=self.settings.db_path,
        )
        return self.generate(case_id, validation_report, use_llm=use_llm)

    def generate(
        self,
        case_id: str,
        validation_report: dict[str, Any],
        use_llm: bool = True,
    ) -> dict[str, Any]:
        report = self._build_structured_report(case_id, validation_report)
        self._add_narratives(report, use_llm=use_llm)
        self._save_report(case_id, report)
        return report

    def latest_report(self, case_id: str) -> dict[str, Any]:
        path = self._case_report_dir(case_id) / "overall_report.json"
        if not path.exists():
            raise FileNotFoundError(f"Report not found for case: {case_id}")
        return self._read_json(path)

    def latest_pdf_path(self, case_id: str) -> Path:
        report = self.latest_report(case_id)
        path = self._case_report_dir(case_id) / report["summary"].get("report_filename", report_filename())
        if not path.exists():
            raise FileNotFoundError(f"PDF report not found for case: {case_id}")
        return path

    def _build_structured_report(self, case_id: str, validation_report: dict[str, Any]) -> dict[str, Any]:
        validation_summary = validation_report.get("summary", {})
        self._use_standard(validation_summary.get("standard_id"))
        level_policy = validation_summary.get("level_policy", {})
        source_results = {
            int(item["element_number"]): item
            for item in validation_report.get("element_results", [])
            if item.get("element_number")
        }
        element_reports = [
            self._build_element_report(number, source_results.get(number), level_policy)
            for number in self.catalog.sequence()
        ]
        critical_issues = self._critical_issues(element_reports)
        missing_evidence = self._missing_evidence_rows(element_reports)
        recommended_actions = self._recommended_action_rows(critical_issues)
        checkpoint_summary = self._aggregate_checkpoint_summary(element_reports)
        key_fields = self._aggregate_key_fields(element_reports)
        key_field_summary = self._key_field_summary(key_fields)

        summary = {
            "case_id": case_id,
            "validation_run_id": validation_summary.get("validation_run_id"),
            "source_validation_created_at": validation_summary.get("created_at"),
            "report_generated_at": datetime.now(timezone.utc).isoformat(),
            "standard_id": self.standard_id,
            "standard_name": standard_display_name(self.standard_id),
            "artifact_prefix": artifact_prefix(self.standard_id),
            "report_title": self.profile.get("reporting", {}).get("title", "Validation Report"),
            "report_kicker": self.profile.get("reporting", {}).get("kicker", standard_display_name(self.standard_id)),
            "report_filename": report_filename(self.standard_id),
            "submission_level": validation_summary.get("submission_level"),
            "overall_status": validation_summary.get("overall_status", "N/A"),
            "element_count": len(element_reports),
            "validated_element_count": sum(
                1 for item in element_reports if item["presence_status"] != "OPTIONAL_NOT_SUBMITTED"
            ),
            "checkpoint_summary": checkpoint_summary,
            "critical_issue_count": len(critical_issues),
            "required_missing_elements": validation_summary.get("required_missing_elements", []),
            "human_override_count": validation_summary.get("human_override_count", 0),
            "key_field_summary": key_field_summary,
            "narrative_source": "pending",
            "warnings": [],
        }

        return {
            "summary": summary,
            "level_policy": level_policy,
            "narrative": self._fallback_overall_narrative(summary, critical_issues, missing_evidence),
            "tables": {
                "element_status_summary": [
                    self._element_summary_row(item) for item in element_reports
                ],
                "critical_issues": critical_issues,
                "missing_evidence": missing_evidence,
                "recommended_actions": recommended_actions,
                "key_fields": key_fields,
            },
            "element_reports": element_reports,
        }

    def _build_element_report(
        self,
        number: int,
        source: dict[str, Any] | None,
        level_policy: dict[str, Any],
    ) -> dict[str, Any]:
        element_name = self.catalog.element(number)["element_name"]
        requirement = self._submission_requirement(number, level_policy)

        if source:
            checkpoints = source.get("checkpoint_results", [])
            status = source.get("element_status", "N/A")
            evidence_counts = source.get("evidence_counts", {})
            presence_status = (
                "PRESENT"
                if int(evidence_counts.get("primary_chunks", 0) or 0) > 0
                else "NO_TAGGED_EVIDENCE"
            )
            missing_evidence = self._string_list(source.get("missing_evidence", []))
            model_error = source.get("model_error")
            model_warning = source.get("model_warning")
            element_summary = source.get("element_summary", "")
            paths = source.get("paths", {})
            required = bool(source.get("required_for_submission_level"))
            key_fields = self._extract_element_key_fields(
                number,
                element_name,
                checkpoints,
                paths,
            )
        else:
            checkpoints = []
            status = "NOT_FOUND" if requirement == "COMPULSORY" else "N/A"
            evidence_counts = {"primary_chunks": 0}
            presence_status = (
                "REQUIRED_MISSING" if requirement == "COMPULSORY" else "OPTIONAL_NOT_SUBMITTED"
            )
            code = f"{artifact_prefix(self.standard_id)}{number:02d}"
            missing_evidence = (
                [f"No submitted evidence was found for required artifact {code}."]
                if requirement == "COMPULSORY"
                else []
            )
            model_error = None
            model_warning = None
            element_summary = ""
            paths = {}
            required = requirement == "COMPULSORY"
            key_fields = []

        checkpoint_summary = self._checkpoint_summary(checkpoints)
        top_issues = self._top_issues(number, element_name, requirement, status, checkpoints, model_error)
        recommended_actions = self._recommended_actions(top_issues)

        report = {
            "element_number": number,
            "element_name": element_name,
            "artifact_prefix": artifact_prefix(self.standard_id),
            "submission_requirement": requirement,
            "required_for_submission_level": required,
            "presence_status": presence_status,
            "element_status": status,
            "checkpoint_summary": checkpoint_summary,
            "element_summary": element_summary,
            "narrative": {},
            "top_issues": top_issues,
            "missing_evidence": missing_evidence,
            "recommended_actions": recommended_actions,
            "key_fields": key_fields,
            "checkpoint_results": checkpoints,
            "evidence_counts": evidence_counts,
            "model_error": model_error,
            "model_warning": model_warning,
            "paths": paths,
        }
        report["narrative"] = self._fallback_element_narrative(report)
        return report

    def _add_narratives(self, report: dict[str, Any], use_llm: bool) -> None:
        errors = []
        used_llm = False

        if use_llm:
            for element in report["element_reports"]:
                if element["presence_status"] == "OPTIONAL_NOT_SUBMITTED":
                    continue
                try:
                    element["narrative"] = self._model_element_narrative(element)
                    used_llm = True
                except Exception as exc:
                    code = f"{artifact_prefix(self.standard_id)}{element['element_number']:02d}"
                    errors.append(f"{code}: {exc}")
                    element["narrative"] = self._fallback_element_narrative(element)

            try:
                report["narrative"] = self._model_overall_narrative(report)
                used_llm = True
            except Exception as exc:
                errors.append(f"overall: {exc}")
                report["narrative"] = self._fallback_overall_narrative(
                    report["summary"],
                    report["tables"]["critical_issues"],
                    report["tables"]["missing_evidence"],
                )

        if not use_llm:
            report["summary"]["narrative_source"] = "fallback"
        elif errors and used_llm:
            report["summary"]["narrative_source"] = "mixed"
        elif errors:
            report["summary"]["narrative_source"] = "fallback"
        else:
            report["summary"]["narrative_source"] = "llm"

        if errors:
            report["summary"]["warnings"].extend(
                f"Report narrative fallback used for {error}" for error in errors
            )

    def _model_element_narrative(self, element: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "element_number": element["element_number"],
            "element_name": element["element_name"],
            "submission_requirement": element["submission_requirement"],
            "presence_status": element["presence_status"],
            "element_status": element["element_status"],
            "checkpoint_summary": element["checkpoint_summary"],
            "key_fields": element["key_fields"][:12],
            "missing_evidence": element["missing_evidence"][:6],
            "top_issues": element["top_issues"][:8],
            "recommended_actions": element["recommended_actions"][:8],
        }
        response = self.narrative_client.validate(self._element_prompt(payload))
        return self._coerce_element_narrative(response, element)

    def _model_overall_narrative(self, report: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "summary": report["summary"],
            "level_policy": report["level_policy"],
            "key_field_summary": report["summary"].get("key_field_summary", {}),
            "key_fields": report["tables"]["key_fields"][:20],
            "element_status_summary": report["tables"]["element_status_summary"],
            "critical_issues": report["tables"]["critical_issues"][:12],
            "missing_evidence": report["tables"]["missing_evidence"][:12],
            "recommended_actions": report["tables"]["recommended_actions"][:12],
        }
        response = self.narrative_client.validate(self._overall_prompt(payload))
        return self._coerce_overall_narrative(response, report)

    def _element_prompt(self, payload: dict[str, Any]) -> str:
        return (
            f"You are a {standard_display_name(self.standard_id)} report writing assistant. Write concise, professional report text "
            "from the supplied validation facts only. Do not change statuses, counts, rule ids, "
            "or required/optional decisions. Do not invent documents or evidence. Return strict JSON only.\n\n"
            "Return this JSON shape:\n"
            "{\"summary\":\"2-4 sentences\",\"risk_statement\":\"1-2 sentences\","
            "\"recommended_actions\":[\"short action\"],\"review_notes\":[\"short note\"]}\n\n"
            f"VALIDATION_FACTS:\n{json.dumps(payload, indent=2, ensure_ascii=False)}"
        )

    def _overall_prompt(self, payload: dict[str, Any]) -> str:
        return (
            f"You are a {standard_display_name(self.standard_id)} package report writing assistant. Create readable executive narrative "
            "from the supplied validation facts only. Python has already decided every status. "
            "Do not change statuses, counts, required elements, optional elements, or issue priority. "
            "Cross-document findings in the supplied facts are valid validation results. Return strict JSON only.\n\n"
            "Return this JSON shape:\n"
            "{\"executive_summary\":\"3-5 sentences\",\"submission_level_summary\":\"1-2 sentences\","
            "\"risk_summary\":\"2-4 sentences\",\"recommended_action_plan\":[\"short action\"],"
            "\"limitations\":[\"short limitation\"]}\n\n"
            f"VALIDATION_FACTS:\n{json.dumps(payload, indent=2, ensure_ascii=False)}"
        )

    def _coerce_element_narrative(
        self,
        response: dict[str, Any],
        element: dict[str, Any],
    ) -> dict[str, Any]:
        fallback = self._fallback_element_narrative(element)
        return {
            "summary": self._clean_text(response.get("summary")) or fallback["summary"],
            "risk_statement": self._clean_text(response.get("risk_statement"))
            or fallback["risk_statement"],
            "recommended_actions": self._clean_list(response.get("recommended_actions"))
            or fallback["recommended_actions"],
            "review_notes": self._clean_list(response.get("review_notes")) or fallback["review_notes"],
        }

    def _coerce_overall_narrative(
        self,
        response: dict[str, Any],
        report: dict[str, Any],
    ) -> dict[str, Any]:
        fallback = self._fallback_overall_narrative(
            report["summary"],
            report["tables"]["critical_issues"],
            report["tables"]["missing_evidence"],
        )
        return {
            "executive_summary": self._clean_text(response.get("executive_summary"))
            or fallback["executive_summary"],
            "submission_level_summary": self._clean_text(response.get("submission_level_summary"))
            or fallback["submission_level_summary"],
            "risk_summary": self._clean_text(response.get("risk_summary")) or fallback["risk_summary"],
            "recommended_action_plan": self._clean_list(response.get("recommended_action_plan"))
            or fallback["recommended_action_plan"],
            "limitations": self._clean_list(response.get("limitations")) or fallback["limitations"],
        }

    def _fallback_element_narrative(self, element: dict[str, Any]) -> dict[str, Any]:
        number = element["element_number"]
        code = f"{artifact_prefix(self.standard_id)}{number:02d}"
        status = element["element_status"]
        requirement = element["submission_requirement"].lower()
        counts = element["checkpoint_summary"]

        if element["presence_status"] == "OPTIONAL_NOT_SUBMITTED":
            summary = f"{code} {element['element_name']} was optional for this submission scope and was not submitted."
            risk = "No issue is raised for this artifact because it is not compulsory for the selected scope."
            actions = []
        else:
            summary = (
                f"{code} {element['element_name']} is {requirement} and finished with status {status}. "
                f"The validation reviewed {counts['total_rules']} checkpoints: {counts['pass']} pass, "
                f"{counts['flag']} flag, {counts['not_found']} not found, "
                f"and {counts['na']} not applicable."
            )
            risk = self._fallback_risk_sentence(status, element["required_for_submission_level"])
            actions = element["recommended_actions"][:5]

        return {
            "summary": summary,
            "risk_statement": risk,
            "recommended_actions": actions,
            "review_notes": element["missing_evidence"][:5],
        }

    def _fallback_overall_narrative(
        self,
        summary: dict[str, Any],
        critical_issues: list[dict[str, Any]],
        missing_evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        status = summary.get("overall_status", "N/A")
        level = summary.get("submission_level", "unknown")
        issue_count = len(critical_issues)
        missing_count = len(missing_evidence)
        scope_text = (
            f"submission level {level}"
            if self.standard_id == DEFAULT_STANDARD_ID
            else "the configured VDA customer-agreed scope"
        )
        executive = (
            f"The {standard_display_name(self.standard_id)} package was validated for {scope_text} and currently has overall status {status}. "
            f"The report found {issue_count} issue rows and {missing_count} missing-evidence rows based on the saved validation results."
        )
        return {
            "executive_summary": executive,
            "submission_level_summary": (
                "Required and optional artifacts were evaluated using the configured standard policy."
            ),
            "risk_summary": self._overall_risk_sentence(status, issue_count, missing_count),
            "recommended_action_plan": [
                item["recommended_action"]
                for item in critical_issues
                if item.get("recommended_action")
            ][:8],
            "limitations": [
                "Narrative text is generated from validation outputs only.",
                "Cross-document checks depend on the evidence available for each referenced element.",
            ],
        }

    def _checkpoint_summary(self, checkpoints: list[dict[str, Any]]) -> dict[str, int]:
        counts = {
            "total_rules": len(checkpoints),
            "pass": 0,
            "flag": 0,
            "not_found": 0,
            "na": 0,
        }
        for checkpoint in checkpoints:
            status = str(checkpoint.get("status", "N/A")).upper()
            status = {"CONDITIONAL": "FLAG", "MISSED": "NOT_FOUND"}.get(status, status)
            if status == "PASS":
                counts["pass"] += 1
            elif status == "FLAG":
                counts["flag"] += 1
            elif status == "NOT_FOUND":
                counts["not_found"] += 1
            else:
                counts["na"] += 1
        return counts

    def _aggregate_checkpoint_summary(self, element_reports: list[dict[str, Any]]) -> dict[str, int]:
        aggregate = {
            "total_rules": 0,
            "pass": 0,
            "flag": 0,
            "not_found": 0,
            "na": 0,
        }
        for element in element_reports:
            for key in aggregate:
                aggregate[key] += int(element["checkpoint_summary"].get(key, 0))
        return aggregate

    def _top_issues(
        self,
        element_number: int,
        element_name: str,
        requirement: str,
        element_status: str,
        checkpoints: list[dict[str, Any]],
        model_error: str | None,
    ) -> list[dict[str, Any]]:
        issues = []
        required = requirement == "COMPULSORY"
        if model_error:
            issues.append(
                {
                    "priority": "High" if required else "Medium",
                    "element_number": element_number,
                    "element_name": element_name,
                    "rule_id": "MODEL_ERROR",
                    "status": "ERROR",
                    "issue": self._short_text(model_error, 220),
                    "evidence": "",
                    "recommended_action": "Re-run validation after confirming Anthropic API access is available.",
                    "required": required,
                }
            )

        for checkpoint in checkpoints:
            status = str(checkpoint.get("status", "")).upper()
            if status not in ISSUE_STATUSES:
                continue
            issues.append(
                {
                    "priority": self._priority(status, required, element_status),
                    "element_number": element_number,
                    "element_name": element_name,
                    "rule_id": checkpoint.get("rule_id", ""),
                    "status": status,
                    "issue": self._short_text(
                        checkpoint.get("reason") or f"Checkpoint returned {status}.",
                        220,
                    ),
                    "evidence": checkpoint.get("final_evidence_location")
                    or self._evidence_text(checkpoint.get("evidence")),
                    "recommended_action": self._short_text(checkpoint.get("recommended_action"), 220),
                    "required": required,
                }
            )
        return issues

    def _critical_issues(self, element_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
        issues = []
        for element in element_reports:
            issues.extend(element["top_issues"])
            if element["presence_status"] == "REQUIRED_MISSING":
                issues.append(
                    {
                        "priority": "High",
                        "element_number": element["element_number"],
                        "element_name": element["element_name"],
                        "rule_id": "ELEMENT_MISSING",
                        "status": "NOT_FOUND",
                        "issue": "Required element was not submitted or not tagged.",
                        "evidence": "",
                        "recommended_action": f"Upload and tag evidence for {artifact_prefix(self.standard_id)}{element['element_number']:02d}.",
                        "required": True,
                    }
                )
        priority_order = {"High": 0, "Medium": 1, "Low": 2}
        return sorted(
            issues,
            key=lambda item: (
                priority_order.get(item.get("priority"), 9),
                item.get("element_number", 99),
                str(item.get("rule_id", "")),
            ),
        )

    def _missing_evidence_rows(self, element_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = []
        for element in element_reports:
            for item in element["missing_evidence"]:
                rows.append(
                    {
                        "element_number": element["element_number"],
                        "element_name": element["element_name"],
                        "missing_evidence": item,
                        "required": element["required_for_submission_level"],
                        "impact": (
                            "Required evidence gap can block customer acceptance."
                            if element["required_for_submission_level"]
                            else "Optional evidence gap; review only if customer requires this element."
                        ),
                    }
                )
        return rows

    def _recommended_action_rows(self, critical_issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = []
        seen = set()
        for issue in critical_issues:
            action = issue.get("recommended_action", "").strip()
            if not action or action in seen:
                continue
            rows.append(
                {
                    "priority": issue.get("priority", "Medium"),
                    "element_number": issue.get("element_number"),
                    "element_name": issue.get("element_name"),
                    "rule_id": issue.get("rule_id"),
                    "recommended_action": action,
                }
            )
            seen.add(action)
        return rows

    def _recommended_actions(self, issues: list[dict[str, Any]]) -> list[str]:
        actions = []
        seen = set()
        for issue in issues:
            action = issue.get("recommended_action", "").strip()
            if action and action not in seen:
                actions.append(action)
                seen.add(action)
        return actions

    def _extract_element_key_fields(
        self,
        element_number: int,
        element_name: str,
        checkpoints: list[dict[str, Any]],
        paths: dict[str, str],
    ) -> list[dict[str, Any]]:
        rows = []
        seen = set()
        for source in self._key_field_sources(checkpoints, paths):
            text = source["text"]
            for field_key, label, patterns in KEY_FIELD_PATTERNS:
                for pattern in patterns:
                    for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                        value = self._normalize_field_value(match.group(1))
                        if not value or not self._is_plausible_field_value(field_key, value):
                            continue
                        dedupe_key = (field_key, value.lower(), source.get("file_name"), source.get("unit_id"))
                        if dedupe_key in seen:
                            continue
                        rows.append(
                            {
                                "field_key": field_key,
                                "label": label,
                                "value": value,
                                "element_number": element_number,
                                "element_name": element_name,
                                "file_name": source.get("file_name", ""),
                                "unit_id": source.get("unit_id", ""),
                                "quote": self._short_text(source.get("quote") or text, 240),
                            }
                        )
                        seen.add(dedupe_key)
                        if len([item for item in rows if item["field_key"] == field_key]) >= 4:
                            break
        return rows

    def _key_field_sources(
        self,
        checkpoints: list[dict[str, Any]],
        paths: dict[str, str],
    ) -> list[dict[str, str]]:
        sources = []
        evidence_path = paths.get("evidence_path")
        if evidence_path and Path(evidence_path).exists():
            try:
                evidence = self._read_json(Path(evidence_path))
            except (OSError, json.JSONDecodeError):
                evidence = {}
            for chunk in evidence.get("primary_chunks", []) or []:
                text = self._short_text(chunk.get("text"), 5000)
                if text:
                    sources.append(
                        {
                            "text": text,
                            "quote": text,
                            "file_name": str(chunk.get("file_name", "")),
                            "unit_id": str(chunk.get("unit_id", "")),
                        }
                    )

        for checkpoint in checkpoints:
            for evidence in checkpoint.get("evidence", []) or []:
                if not isinstance(evidence, dict):
                    continue
                quote = self._short_text(
                    evidence.get("quote") or evidence.get("text") or evidence.get("value"),
                    1000,
                )
                if quote:
                    sources.append(
                        {
                            "text": quote,
                            "quote": quote,
                            "file_name": str(evidence.get("file_name", "")),
                            "unit_id": str(evidence.get("unit_id", "")),
                        }
                    )
        return sources

    def _aggregate_key_fields(self, element_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = []
        seen = set()
        for element in element_reports:
            for item in element.get("key_fields", []):
                key = (
                    item.get("field_key"),
                    str(item.get("value", "")).lower(),
                    item.get("element_number"),
                )
                if key in seen:
                    continue
                rows.append(item)
                seen.add(key)
        priority = {field_key: index for index, (field_key, _label, _patterns) in enumerate(KEY_FIELD_PATTERNS)}
        return sorted(
            rows,
            key=lambda item: (
                priority.get(item.get("field_key"), 999),
                item.get("element_number", 99),
                str(item.get("value", "")),
            ),
        )

    def _key_field_summary(self, key_fields: list[dict[str, Any]]) -> dict[str, str]:
        summary = {}
        for item in key_fields:
            key = item.get("field_key")
            value = item.get("value")
            if key and value and key not in summary:
                summary[key] = value
        return summary

    def _normalize_field_value(self, value: Any) -> str:
        text = self._short_text(value, 140)
        text = re.sub(r"^[\s:;,#-]+", "", text)
        text = re.sub(r"[\s,.;:-]+$", "", text)
        return text

    def _is_plausible_field_value(self, field_key: str, value: str) -> bool:
        if value in {"", "/", "-", "."}:
            return False
        lowered = value.lower()
        if field_key in {"customer_name", "supplier_name"}:
            noisy_starts = (
                "disposition",
                "requirements",
                "part number",
                "part name",
                "signature",
                "submission",
                "reason",
            )
            if lowered.startswith(noisy_starts):
                return False
        if field_key == "revision" and not re.search(r"[A-Z0-9]", value, flags=re.IGNORECASE):
            return False
        return True

    def _element_summary_row(self, element: dict[str, Any]) -> dict[str, Any]:
        counts = element["checkpoint_summary"]
        return {
            "element_number": element["element_number"],
            "element_name": element["element_name"],
            "submission_requirement": element["submission_requirement"],
            "presence_status": element["presence_status"],
            "element_status": element["element_status"],
            "total_rules": counts["total_rules"],
            "pass": counts["pass"],
            "flag": counts["flag"],
            "not_found": counts["not_found"],
            "na": counts["na"],
        }

    def _submission_requirement(self, number: int, level_policy: dict[str, Any]) -> str:
        if number in set(level_policy.get("compulsory_elements", [])):
            return "COMPULSORY"
        if number in set(level_policy.get("optional_elements", [])):
            return "OPTIONAL"
        return "UNSPECIFIED"

    def _priority(self, status: str, required: bool, element_status: str) -> str:
        if required and status in {"ERROR", "FLAG", "NOT_FOUND"}:
            return "High"
        if status in {"FLAG", "NOT_FOUND"} or element_status in {"ERROR", "REVIEW"}:
            return "Medium"
        return "Low"

    def _fallback_risk_sentence(self, status: str, required: bool) -> str:
        if status in {"PASS", "N/A"}:
            return "No material validation risk was identified for this element."
        if required:
            return "Because this element is required, unresolved findings should be corrected before submission."
        return "Findings are advisory unless the customer requires this optional element."

    def _overall_risk_sentence(self, status: str, issue_count: int, missing_count: int) -> str:
        if status == "PASS" and issue_count == 0 and missing_count == 0:
            return "No blocking package-level risk was identified from the available validation results."
        return (
            "The package contains unresolved findings that should be reviewed before customer submission. "
            "Focus first on required elements, flagged checkpoints, missing evidence, and model errors."
        )

    def _save_report(self, case_id: str, report: dict[str, Any]) -> None:
        report_dir = self._case_report_dir(case_id)
        element_dir = report_dir / "element_reports"
        element_dir.mkdir(parents=True, exist_ok=True)

        for element in report["element_reports"]:
            code = f"{artifact_prefix(self.standard_id)}{element['element_number']:02d}"
            self._write_json(
                element_dir / f"{code}_report.json",
                element,
            )
        self._write_json(report_dir / "overall_report.json", report)
        generate_report_pdf(report, report_dir / report["summary"].get("report_filename", report_filename(self.standard_id)))
        try:
            from ..storage import sync_case_to_s3

            sync_case_to_s3(case_id, self.settings)
        except Exception:
            pass

    def _use_standard(self, standard_id: str | None) -> None:
        standard_id = normalize_standard_id(standard_id or self.standard_id)
        if standard_id == self.standard_id:
            return
        self.standard_id = standard_id
        self.catalog = CheckpointCatalog(standard_id=standard_id)
        self.profile = standard_profile(standard_id)

    def _default_narrative_client(self) -> AnthropicJsonClient:
        load_project_env()
        return AnthropicJsonClient(
            model=os.getenv("PPAP_REPORT_ANTHROPIC_MODEL") or os.getenv("PPAP_ANTHROPIC_MODEL"),
            timeout_seconds=int(
                os.getenv("PPAP_REPORT_TIMEOUT", os.getenv("PPAP_ANTHROPIC_TIMEOUT", "120"))
            ),
            max_output_tokens=int(
                os.getenv("PPAP_REPORT_MAX_OUTPUT_TOKENS", os.getenv("PPAP_ANTHROPIC_MAX_OUTPUT_TOKENS", "4096"))
            ),
        )

    def _case_report_dir(self, case_id: str) -> Path:
        return self.settings.reports_case_dir(case_id)

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)

    def _read_json(self, path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _clean_text(self, value: Any) -> str:
        return self._short_text(value, 900)

    def _clean_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [self._short_text(item, 300) for item in value if self._short_text(item, 300)]

    def _string_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [self._short_text(item, 300) for item in value if self._short_text(item, 300)]
        text = self._short_text(value, 300)
        return [text] if text else []

    def _short_text(self, value: Any, limit: int = 160) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False)
        else:
            text = str(value)
        text = " ".join(text.split())
        if len(text) <= limit:
            return text
        return f"{text[: limit - 3].rstrip()}..."

    def _evidence_text(self, evidence: Any) -> str:
        if not evidence:
            return ""
        if isinstance(evidence, list):
            return "; ".join(self._evidence_text(item) for item in evidence[:2]).strip("; ")
        if isinstance(evidence, dict):
            for key in ("quote", "text", "value", "snippet", "file_name", "source_file"):
                if evidence.get(key):
                    return self._short_text(evidence.get(key), 180)
            return self._short_text(evidence, 180)
        return self._short_text(evidence, 180)
