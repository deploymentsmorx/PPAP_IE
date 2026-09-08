# Runs the validation sequence for a case.

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..env import load_project_env
from ..anthropic_client import AnthropicJsonClient
from ..rule_store import _clean_instruction, configured_catalog
from ..standards.profiles import DEFAULT_STANDARD_ID, artifact_prefix, normalize_standard_id, standard_display_name, standard_profile
from .checkpoints import CheckpointCatalog
from .evidence import EvidenceBuilder
from .prompts import ValidationPromptBuilder


VALIDATION_SEQUENCE = [1, 2, 3, 17, 4, 5, 6, 7, 8, 16, 9, 10, 12, 11, 14, 15, 13, 18]
ALLOWED_STATUSES = {"PASS", "FLAG", "NOT_FOUND"}
ERROR_STATUS = "ERROR"
LEVEL_POLICIES = {
    1: {
        "compulsory_elements": [18],
        "optional_elements": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17],
    },
    2: {
        "compulsory_elements": [1, 9, 14, 18],
        "optional_elements": [2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 15, 16, 17],
    },
    3: {
        "compulsory_elements": [1, 5, 6, 7, 8, 9, 10, 11, 12, 14, 15, 17, 18],
        "optional_elements": [2, 3, 4, 13, 16],
    },
    4: {
        "compulsory_elements": [18],
        "optional_elements": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17],
        "note": "Other compulsory elements must be read from customer-specific Level 4 requirement.",
    },
    5: {
        "compulsory_elements": [1, 5, 6, 7, 8, 9, 10, 11, 12, 14, 15, 17, 18],
        "optional_elements": [2, 3, 4, 13, 16],
    },
}


class ValidationRunner:
    def __init__(
        self,
        settings,
        llm_client: AnthropicJsonClient | None = None,
        catalog: CheckpointCatalog | None = None,
        prompt_builder: ValidationPromptBuilder | None = None,
        standard_id: str = DEFAULT_STANDARD_ID,
    ):
        load_project_env()
        self.standard_id = normalize_standard_id(standard_id)
        self.profile = standard_profile(self.standard_id)
        self.settings = settings
        self.catalog = catalog or configured_catalog(standard_id=self.standard_id)
        self.prompt_builder = prompt_builder or ValidationPromptBuilder()
        self.llm_client = llm_client or AnthropicJsonClient()
        self.evidence_builder = EvidenceBuilder(settings.cases_dir)
        self.rule_batch_size = int(os.getenv("PPAP_VALIDATION_RULE_BATCH_SIZE", "9"))
        self.schema_retry_rule_batch_size = int(os.getenv("PPAP_VALIDATION_SCHEMA_RETRY_RULE_BATCH_SIZE", "9"))
        self.retry_missing_rules = os.getenv("PPAP_VALIDATION_RETRY_MISSING_RULES", "0").lower() in {
            "1",
            "true",
            "yes",
        }

    def validate_case(
        self,
        case_id: str,
        dry_run: bool = False,
        elements: list[int] | None = None,
        submission_level: int | None = None,
        standard_id: str | None = None,
    ) -> dict[str, Any]:
        if standard_id and normalize_standard_id(standard_id) != self.standard_id:
            return ValidationRunner(
                self.settings,
                llm_client=self.llm_client,
                prompt_builder=self.prompt_builder,
                standard_id=standard_id,
            ).validate_case(case_id, dry_run, elements, submission_level)

        from ..storage import ensure_case_local, sync_case_to_s3

        ensure_case_local(case_id, self.settings)

        run_id = uuid.uuid4().hex
        run_dir = self._run_dir(case_id)
        self._ensure_run_dirs(run_dir)

        documents = self.evidence_builder.load_tagged_documents(case_id)
        case_index = self.evidence_builder.build_case_index(documents)

        selection_policy = "explicit_elements" if elements else "present_plus_level_required"
        sequence = self._selected_sequence(elements, case_index, submission_level)
        required_elements = set(self._required_elements_for_level(submission_level))
        element_results = []

        for position, element_number in enumerate(sequence, start=1):
            result = self._validate_element(
                case_id,
                run_id,
                run_dir,
                position,
                element_number,
                case_index,
                dry_run,
                element_number in required_elements,
                required_elements,
            )
            element_results.append(result)

        report = self._build_report(
            case_id,
            run_id,
            dry_run,
            sequence,
            element_results,
            submission_level,
            selection_policy,
            self._present_elements(case_index),
        )
        self._write_json(run_dir / "validation_report.json", report)
        self._write_json(run_dir / "summary.json", report["summary"])
        try:
            sync_case_to_s3(case_id, self.settings)
        except Exception:
            pass
        return report

    def latest_report(self, case_id: str) -> dict[str, Any]:
        from ..storage import ensure_case_local

        ensure_case_local(case_id, self.settings)
        path = self._run_dir(case_id) / "validation_report.json"
        if not path.exists():
            raise FileNotFoundError(f"Validation report not found for case: {case_id}")
        return self._read_json(path)

    def _validate_element(
        self,
        case_id: str,
        run_id: str,
        run_dir: Path,
        position: int,
        element_number: int,
        case_index: dict[str, Any],
        dry_run: bool,
        required_for_level: bool,
        required_element_numbers: set[int],
    ) -> dict[str, Any]:
        element_rules = self.catalog.element(element_number)
        evidence = self.evidence_builder.element_evidence(
            case_index,
            element_number,
            related_element_numbers=self.catalog.related_element_numbers(element_number),
            required_element_numbers=required_element_numbers,
        )
        evidence["required_for_submission_level"] = required_for_level

        file_stem = f"{position:02d}_{artifact_prefix(self.standard_id)}{element_number:02d}_{self._slug(element_rules['element_name'])}"
        paths = {
            "prompt_path": str(run_dir / "prompts" / f"{file_stem}.prompt.txt"),
            "evidence_path": str(run_dir / "evidence" / f"{file_stem}.evidence.json"),
            "response_path": str(run_dir / "responses" / f"{file_stem}.response.json"),
            "element_result_path": str(run_dir / "elements" / f"{file_stem}.result.json"),
        }

        prompt_parts = self.prompt_builder.build_prompt(
            self.catalog.global_prompt_contract(),
            element_rules,
            evidence,
            paths,
            artifact_prefix(self.standard_id),
        )

        self._write_text(Path(paths["prompt_path"]), prompt_parts["combined"])
        self._write_json(Path(paths["evidence_path"]), evidence)

        if dry_run:
            parsed_response = self._dry_run_response(element_rules)
        elif evidence.get("counts", {}).get("primary_chunks", 0) == 0:
            parsed_response = self._missing_evidence_response(element_rules, required_for_level)
        else:
            parsed_response = self._validate_with_model(element_rules, evidence, paths)

        self._write_json(Path(paths["response_path"]), parsed_response)
        normalized = self._normalize_element_result(element_rules, parsed_response)
        normalized.update(
            {
                "case_id": case_id,
                "validation_run_id": run_id,
                "sequence_position": position,
                "dry_run": dry_run,
                "required_for_submission_level": required_for_level,
                "paths": paths,
                "evidence_counts": evidence.get("counts", {}),
            }
        )
        self._write_json(Path(paths["element_result_path"]), normalized)
        return normalized

    def _normalize_element_result(
        self,
        element_rules: dict[str, Any],
        parsed_response: dict[str, Any],
    ) -> dict[str, Any]:
        rules = element_rules.get("rules", [])
        missing_rule_ids = []
        returned = {
            item.get("rule_id"): item
            for item in parsed_response.get("checkpoint_results", [])
            if isinstance(item, dict) and item.get("rule_id")
        }

        checkpoint_results = []
        for rule in rules:
            rule_id = rule["rule_id"]
            item = returned.get(rule_id)
            if not item:
                missing_rule_ids.append(rule_id)
                item = {
                    "rule_id": rule_id,
                    "status": "NOT_FOUND",
                    "confidence": 0,
                    "evidence": [],
                    "reason": "Model response skipped this checkpoint.",
                    "recommended_action": "Review this checkpoint manually or re-run validation.",
                }
            item = self._normalize_checkpoint_result(rule, item)
            checkpoint_results.append(item)

        model_error = parsed_response.get("model_error")
        model_warning = parsed_response.get("model_warning")
        if missing_rule_ids:
            model_warning = (
                "Model response omitted "
                f"{len(missing_rule_ids)} required rule result(s): "
                f"{', '.join(missing_rule_ids)}"
            )

        if parsed_response.get("dry_run"):
            element_status = "N/A"
        elif parsed_response.get("absence_status"):
            element_status = parsed_response["absence_status"]
        elif model_error:
            element_status = "REVIEW"
        else:
            element_status = self._element_status(checkpoint_results)
        return {
            "element_number": element_rules["element_number"],
            "element_name": element_rules["element_name"],
            "element_status": element_status,
            "checkpoint_results": checkpoint_results,
            "element_summary": parsed_response.get("element_summary", ""),
            "missing_evidence": parsed_response.get("missing_evidence", []),
            "model_error": model_error,
            "model_warning": model_warning,
        }

    def _validate_with_model(
        self,
        element_rules: dict[str, Any],
        evidence: dict[str, Any],
        paths: dict[str, str],
    ) -> dict[str, Any]:
        rules = element_rules.get("rules", [])
        batch_size = self._effective_rule_batch_size(len(rules))
        if batch_size > 0 and len(rules) > batch_size:
            return self._validate_rule_batches(
                element_rules,
                evidence,
                paths,
                batch_size,
            )

        try:
            response = self.llm_client.validate(
                self.prompt_builder.build_prompt(
                    self.catalog.global_prompt_contract(),
                    element_rules,
                    evidence,
                    paths,
                    artifact_prefix(self.standard_id),
                )["combined"]
            )
        except Exception as exc:
            return self._model_error_response(element_rules, str(exc))

        missing_rule_ids = self._missing_response_rule_ids(element_rules, response)
        if (
            missing_rule_ids
            and not response.get("model_error")
            and self.schema_retry_rule_batch_size > 0
            and len(rules) > self.schema_retry_rule_batch_size
        ):
            self._write_json(self._initial_response_path(paths), response)
            retry_response = self._validate_rule_batches(
                element_rules,
                evidence,
                paths,
                self.schema_retry_rule_batch_size,
            )
            retry_response["schema_retry"] = {
                "reason": "Initial model response omitted required checkpoint_results.",
                "missing_rule_count": len(missing_rule_ids),
                "initial_response_path": str(self._initial_response_path(paths)),
            }
            return retry_response

        return response

    def _validate_rule_batches(
        self,
        element_rules: dict[str, Any],
        evidence: dict[str, Any],
        paths: dict[str, str],
        batch_size: int,
    ) -> dict[str, Any]:
        rules = element_rules.get("rules", [])
        if batch_size <= 0 or len(rules) <= batch_size:
            try:
                return self.llm_client.validate(
                    self.prompt_builder.build_prompt(
                        self.catalog.global_prompt_contract(),
                        element_rules,
                        evidence,
                        paths,
                        artifact_prefix(self.standard_id),
                    )["combined"]
                )
            except Exception as exc:
                return self._model_error_response(element_rules, str(exc))

        checkpoint_results = []
        missing_evidence = []
        summaries = []
        model_errors = []
        batch_count = 0

        for batch_count, batch_rules in enumerate(self._rule_batches(rules, batch_size), start=1):
            batch_element_rules = dict(element_rules)
            batch_element_rules["rules"] = batch_rules
            batch_evidence = self._evidence_for_rules(evidence, batch_rules)
            batch_paths = self._batch_paths(paths, batch_count)
            prompt_parts = self.prompt_builder.build_prompt(
                self.catalog.global_prompt_contract(),
                batch_element_rules,
                batch_evidence,
                batch_paths,
                artifact_prefix(self.standard_id),
            )
            self._write_text(Path(batch_paths["prompt_path"]), prompt_parts["combined"])

            try:
                batch_response = self.llm_client.validate(prompt_parts["combined"])
            except Exception as exc:
                batch_response = self._model_error_response(batch_element_rules, str(exc))
                model_errors.append(str(exc))

            batch_response = self._filter_response_rule_ids(
                batch_response,
                [rule["rule_id"] for rule in batch_rules if rule.get("rule_id")],
            )
            self._write_json(Path(batch_paths["response_path"]), batch_response)
            missing_rule_ids = self._missing_response_rule_ids(batch_element_rules, batch_response)
            if (
                self.retry_missing_rules
                and missing_rule_ids
                and not batch_response.get("model_error")
                and len(batch_rules) > 1
            ):
                self._write_json(self._batch_initial_response_path(batch_paths), batch_response)
                retry_response = self._retry_missing_rules(
                    element_rules,
                    evidence,
                    paths,
                    batch_count,
                    batch_rules,
                    missing_rule_ids,
                )
                batch_response = self._merge_batch_responses(batch_response, retry_response)
                self._write_json(Path(batch_paths["response_path"]), batch_response)

            checkpoint_results.extend(batch_response.get("checkpoint_results", []))
            missing_evidence.extend(batch_response.get("missing_evidence", []) or [])
            if batch_response.get("element_summary"):
                summaries.append(str(batch_response.get("element_summary")))
            if batch_response.get("model_error"):
                model_errors.append(str(batch_response.get("model_error")))

        return {
            "element_number": element_rules["element_number"],
            "element_name": element_rules["element_name"],
            "element_status": ERROR_STATUS if model_errors else "",
            "checkpoint_results": checkpoint_results,
            "element_summary": " ".join(summaries),
            "missing_evidence": self._dedupe_strings(missing_evidence),
            "model_error": "; ".join(self._dedupe_strings(model_errors)) or None,
            "batch_count": batch_count,
        }

    def _missing_response_rule_ids(
        self,
        element_rules: dict[str, Any],
        parsed_response: dict[str, Any],
    ) -> list[str]:
        required = [
            rule["rule_id"]
            for rule in element_rules.get("rules", [])
            if rule.get("rule_id")
        ]
        returned = {
            item.get("rule_id")
            for item in parsed_response.get("checkpoint_results", [])
            if isinstance(item, dict) and item.get("rule_id")
        }
        return [rule_id for rule_id in required if rule_id not in returned]

    def _rule_batches(self, rules: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
        return [
            rules[index : index + batch_size]
            for index in range(0, len(rules), batch_size)
        ]

    def _effective_rule_batch_size(self, rule_count: int) -> int:
        if self.rule_batch_size > 0:
            return self.rule_batch_size
        return 0

    def _batch_paths(self, paths: dict[str, str], batch_number: int) -> dict[str, str]:
        batch_paths = dict(paths)
        for key in ("prompt_path", "response_path"):
            path = Path(paths[key])
            suffix = ".prompt.txt" if key == "prompt_path" else ".response.json"
            batch_paths[key] = str(path.with_name(f"{path.stem}.batch{batch_number:02d}{suffix}"))
        return batch_paths

    def _initial_response_path(self, paths: dict[str, str]) -> Path:
        path = Path(paths["response_path"])
        return path.with_name(path.name.replace(".response.json", ".initial.response.json"))

    def _batch_initial_response_path(self, paths: dict[str, str]) -> Path:
        path = Path(paths["response_path"])
        return path.with_name(path.name.replace(".response.json", ".initial.response.json"))

    def _batch_retry_paths(
        self,
        paths: dict[str, str],
        batch_number: int,
        retry_number: int,
    ) -> dict[str, str]:
        retry_paths = dict(paths)
        for key in ("prompt_path", "response_path"):
            path = Path(paths[key])
            suffix = ".prompt.txt" if key == "prompt_path" else ".response.json"
            retry_paths[key] = str(
                path.with_name(f"{path.stem}.batch{batch_number:02d}.retry{retry_number:02d}{suffix}")
            )
        return retry_paths

    def _retry_missing_rules(
        self,
        element_rules: dict[str, Any],
        evidence: dict[str, Any],
        paths: dict[str, str],
        batch_number: int,
        batch_rules: list[dict[str, Any]],
        missing_rule_ids: list[str],
    ) -> dict[str, Any]:
        missing = set(missing_rule_ids)
        rule_lookup = {
            rule["rule_id"]: rule
            for rule in batch_rules
            if rule.get("rule_id") in missing
        }
        checkpoint_results = []
        missing_evidence = []
        summaries = []
        model_errors = []

        for retry_number, rule_id in enumerate(missing_rule_ids, start=1):
            retry_rule = rule_lookup.get(rule_id)
            if not retry_rule:
                continue
            retry_element_rules = dict(element_rules)
            retry_element_rules["rules"] = [retry_rule]
            retry_evidence = self._evidence_for_rules(evidence, [retry_rule])
            retry_paths = self._batch_retry_paths(paths, batch_number, retry_number)
            prompt_parts = self.prompt_builder.build_prompt(
                self.catalog.global_prompt_contract(),
                retry_element_rules,
                retry_evidence,
                retry_paths,
                artifact_prefix(self.standard_id),
            )
            self._write_text(Path(retry_paths["prompt_path"]), prompt_parts["combined"])
            try:
                retry_response = self.llm_client.validate(prompt_parts["combined"])
            except Exception as exc:
                retry_response = self._model_error_response(retry_element_rules, str(exc))
                model_errors.append(str(exc))

            retry_response = self._filter_response_rule_ids(retry_response, [rule_id])
            self._write_json(Path(retry_paths["response_path"]), retry_response)
            checkpoint_results.extend(retry_response.get("checkpoint_results", []))
            missing_evidence.extend(retry_response.get("missing_evidence", []) or [])
            if retry_response.get("element_summary"):
                summaries.append(str(retry_response.get("element_summary")))
            if retry_response.get("model_error"):
                model_errors.append(str(retry_response.get("model_error")))

        return {
            "checkpoint_results": checkpoint_results,
            "missing_evidence": missing_evidence,
            "element_summary": " ".join(summaries),
            "model_error": "; ".join(self._dedupe_strings(model_errors)) or None,
        }

    def _merge_batch_responses(
        self,
        batch_response: dict[str, Any],
        retry_response: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(batch_response)
        results = [
            item
            for item in batch_response.get("checkpoint_results", [])
            if isinstance(item, dict) and item.get("rule_id")
        ]
        seen = {item["rule_id"] for item in results}
        for item in retry_response.get("checkpoint_results", []):
            if not isinstance(item, dict) or not item.get("rule_id"):
                continue
            if item["rule_id"] in seen:
                continue
            results.append(item)
            seen.add(item["rule_id"])
        merged["checkpoint_results"] = results
        merged["missing_evidence"] = self._dedupe_strings(
            (batch_response.get("missing_evidence", []) or [])
            + (retry_response.get("missing_evidence", []) or [])
        )
        summaries = [
            str(value).strip()
            for value in (batch_response.get("element_summary"), retry_response.get("element_summary"))
            if str(value or "").strip()
        ]
        merged["element_summary"] = " ".join(summaries)
        errors = self._dedupe_strings(
            [
                batch_response.get("model_error"),
                retry_response.get("model_error"),
            ]
        )
        merged["model_error"] = "; ".join(errors) or None
        return merged

    def _filter_response_rule_ids(
        self,
        response: dict[str, Any],
        allowed_rule_ids: list[str],
    ) -> dict[str, Any]:
        allowed = set(allowed_rule_ids)
        filtered = dict(response)
        filtered["checkpoint_results"] = [
            item
            for item in response.get("checkpoint_results", [])
            if isinstance(item, dict) and item.get("rule_id") in allowed
        ]
        return filtered

    def _evidence_for_rules(
        self,
        evidence: dict[str, Any],
        rules: list[dict[str, Any]],
    ) -> dict[str, Any]:
        related_numbers = {
            int(number)
            for rule in rules
            for number in rule.get("related_element_numbers", [])
        }
        filtered = dict(evidence)
        filtered["related_elements"] = [
            item
            for item in evidence.get("related_elements", [])
            if int(item.get("element_number", 0)) in related_numbers
        ]
        counts = dict(evidence.get("counts", {}))
        counts["related_elements"] = len(filtered["related_elements"])
        counts["related_chunks"] = sum(
            len(item.get("chunks", []) or [])
            for item in filtered["related_elements"]
        )
        filtered["counts"] = counts
        return filtered

    def _dedupe_strings(self, values: list[Any]) -> list[str]:
        deduped = []
        seen = set()
        for value in values:
            if value is None:
                continue
            text = str(value).strip()
            if text and text.lower() != "none" and text not in seen:
                deduped.append(text)
                seen.add(text)
        return deduped

    def _normalize_checkpoint_result(self, rule: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        rule_id = rule["rule_id"]
        status = str(item.get("status", "NOT_FOUND")).upper()
        status = {
            "CONDITIONAL": "FLAG",
            "MISSED": "NOT_FOUND",
            "N/A": "PASS",
        }.get(status, status)
        if status not in ALLOWED_STATUSES:
            status = "NOT_FOUND"

        try:
            confidence = int(item.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0

        evidence = item.get("evidence")
        if not isinstance(evidence, list):
            evidence = []

        return {
            "rule_key": rule.get("rule_key", f"E00:BASE:{rule_id}"),
            "rule_id": rule_id,
            "rule_description": _clean_instruction(rule.get("agent_instruction", "")),
            "status": status,
            "confidence": max(0, min(confidence, 100)),
            "evidence": evidence,
            "reason": str(item.get("reason", "")).strip(),
            "recommended_action": str(item.get("recommended_action", "")).strip(),
        }

    def _dry_run_response(self, element_rules: dict[str, Any]) -> dict[str, Any]:
        return {
            "element_number": element_rules["element_number"],
            "element_name": element_rules["element_name"],
            "element_status": "N/A",
            "checkpoint_results": [
                {
                    "rule_id": rule["rule_id"],
                    "status": "PASS",
                    "confidence": 0,
                    "evidence": [],
                    "reason": "Dry run only: prompt and evidence were generated, model was not called.",
                    "recommended_action": "",
                }
                for rule in element_rules.get("rules", [])
            ],
            "element_summary": "Dry run only.",
            "missing_evidence": [],
            "dry_run": True,
        }

    def _missing_evidence_response(
        self,
        element_rules: dict[str, Any],
        required_for_level: bool,
    ) -> dict[str, Any]:
        status = "NOT_FOUND" if required_for_level else "N/A"
        checkpoint_status = "NOT_FOUND" if required_for_level else "PASS"
        reason = (
            f"No tagged file, page, sheet, or chunk was found for this required {self.profile.get('artifact_label', 'artifact')}."
            if required_for_level
            else f"This optional {self.profile.get('artifact_label', 'artifact')} was not submitted."
        )
        return {
            "element_number": element_rules["element_number"],
            "element_name": element_rules["element_name"],
            "element_status": status,
            "checkpoint_results": [
                {
                    "rule_id": rule["rule_id"],
                    "status": checkpoint_status,
                    "confidence": 100,
                    "evidence": [],
                    "reason": reason,
                    "recommended_action": (
                        f"Upload or correctly tag the required evidence for this {self.profile.get('artifact_label', 'artifact')}."
                        if required_for_level
                        else ""
                    ),
                }
                for rule in element_rules.get("rules", [])
            ],
            "element_summary": reason,
            "missing_evidence": [reason] if required_for_level else [],
            "absence_status": status,
            "model_error": None,
        }

    def _model_error_response(self, element_rules: dict[str, Any], error: str) -> dict[str, Any]:
        return {
            "element_number": element_rules["element_number"],
            "element_name": element_rules["element_name"],
            "element_status": "REVIEW",
            "checkpoint_results": [
                {
                    "rule_id": rule["rule_id"],
                    "status": "NOT_FOUND",
                    "confidence": 0,
                    "evidence": [],
                    "reason": "Validation model call failed; checkpoint was not evaluated.",
                    "recommended_action": "Confirm the Anthropic API key and model access are available, then re-run validation.",
                }
                for rule in element_rules.get("rules", [])
            ],
            "element_summary": "Validation model call failed.",
            "missing_evidence": [],
            "model_error": error,
        }

    def _build_report(
        self,
        case_id: str,
        run_id: str,
        dry_run: bool,
        sequence: list[int],
        element_results: list[dict[str, Any]],
        submission_level: int | None,
        selection_policy: str,
        present_elements: list[int],
    ) -> dict[str, Any]:
        level_policy = self._level_policy_summary(submission_level, present_elements)
        summary = {
            "case_id": case_id,
            "validation_run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "dry_run": dry_run,
            "model": getattr(self.llm_client, "model", None),
            "standard_id": self.standard_id,
            "standard_name": standard_display_name(self.standard_id),
            "artifact_prefix": artifact_prefix(self.standard_id),
            "submission_level": submission_level,
            "level_policy": level_policy,
            "sequence": sequence,
            "selection_policy": selection_policy,
            "overall_status": self._package_status(element_results),
            "element_count": len(element_results),
            "checkpoint_count": sum(len(item.get("checkpoint_results", [])) for item in element_results),
            "review_elements": [
                item["element_number"]
                for item in element_results
                if item.get("element_status") == "REVIEW"
            ],
            "errored_elements": [
                item["element_number"]
                for item in element_results
                if item.get("model_error")
            ],
            "missing_or_unresolved_elements": [
                item["element_number"]
                for item in element_results
                if item.get("element_status") in {"NOT_FOUND", "REVIEW"}
            ],
            "required_missing_elements": [
                element
                for element in level_policy["missing_compulsory_elements"]
            ],
        }
        return {
            "summary": summary,
            "element_results": element_results,
        }

    def _element_status(self, checkpoints: list[dict[str, Any]]) -> str:
        statuses = [item.get("status") for item in checkpoints]
        if any(status in {"FLAG", "NOT_FOUND"} for status in statuses):
            return "REVIEW"
        return "PASS"

    def _package_status(self, elements: list[dict[str, Any]]) -> str:
        statuses = [item.get("element_status") for item in elements]
        if any(status == "NOT_FOUND" for status in statuses):
            return "NOT_FOUND"
        if any(status == "REVIEW" for status in statuses):
            return "REVIEW"
        if statuses and all(status == "N/A" for status in statuses):
            return "N/A"
        return "PASS"

    def _selected_sequence(
        self,
        elements: list[int] | None,
        case_index: dict[str, Any],
        submission_level: int | None,
    ) -> list[int]:
        if not elements:
            selected = set(self._present_elements(case_index))
            selected.update(self._required_elements_for_level(submission_level))
            return [element for element in self._validation_sequence() if element in selected]
        requested = {int(element) for element in elements}
        return [element for element in self._validation_sequence() if element in requested]

    def _present_elements(self, case_index: dict[str, Any]) -> list[int]:
        present = set()
        for chunk in case_index.get("chunks", []):
            number = chunk.get("element_number")
            if number:
                present.add(int(number))
        return sorted(present)

    def _required_elements_for_level(self, submission_level: int | None) -> list[int]:
        if submission_level is None:
            return []
        return self._level_policy(submission_level).get("compulsory_elements", [])

    def _optional_elements_for_level(self, submission_level: int | None) -> list[int]:
        if submission_level is None:
            return []
        return self._level_policy(submission_level).get("optional_elements", [])

    def _level_policy(self, submission_level: int | None) -> dict[str, Any]:
        if self.standard_id != DEFAULT_STANDARD_ID:
            required = [int(number) for number in self.profile.get("required_elements", [])]
            if not required:
                required = [1, self.catalog.sequence()[-1]]
            optional = [number for number in self._validation_sequence() if number not in required]
            return {
                "compulsory_elements": required,
                "optional_elements": optional,
                "note": "VDA PPF/PPA uses the customer-agreed submission scope; this policy is a configurable default.",
            }
        try:
            level = int(submission_level) if submission_level is not None else None
        except (TypeError, ValueError):
            level = None
        return LEVEL_POLICIES.get(level, {"compulsory_elements": [], "optional_elements": []})

    def _validation_sequence(self) -> list[int]:
        return self.catalog.sequence()

    def _level_policy_summary(
        self,
        submission_level: int | None,
        present_elements: list[int],
    ) -> dict[str, Any]:
        policy = self._level_policy(submission_level)
        compulsory = policy.get("compulsory_elements", [])
        optional = policy.get("optional_elements", [])
        present = {int(element) for element in present_elements}

        summary = {
            "compulsory_elements": compulsory,
            "optional_elements": optional,
            "present_compulsory_elements": [element for element in compulsory if element in present],
            "missing_compulsory_elements": [element for element in compulsory if element not in present],
            "present_optional_elements": [element for element in optional if element in present],
            "absent_optional_elements": [element for element in optional if element not in present],
        }
        if policy.get("note"):
            summary["note"] = policy["note"]
        return summary

    def _run_dir(self, case_id: str) -> Path:
        return self.settings.validation_case_dir(case_id)

    def _ensure_run_dirs(self, run_dir: Path) -> None:
        for child in ("prompts", "evidence", "responses", "elements"):
            (run_dir / child).mkdir(parents=True, exist_ok=True)

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)

    def _read_json(self, path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _write_text(self, path: Path, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    def _slug(self, value: str) -> str:
        return "".join(char.lower() if char.isalnum() else "_" for char in value).strip("_")
