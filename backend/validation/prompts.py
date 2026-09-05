# Prompt builders for validation checks.

import json
from typing import Any


class ValidationPromptBuilder:
    def build_prompt(
        self,
        global_contract: dict[str, Any],
        element_rules: dict[str, Any],
        evidence_bundle: dict[str, Any],
        output_paths: dict[str, str],
        artifact_prefix: str = "E",
    ) -> dict[str, str]:
        system_prompt = self._system_prompt(global_contract)
        user_prompt = self._user_prompt(
            global_contract,
            element_rules,
            evidence_bundle,
            output_paths,
            artifact_prefix,
        )
        return {
            "system": system_prompt,
            "user": user_prompt,
            "combined": f"{system_prompt}\n\n{user_prompt}",
        }

    def _system_prompt(self, global_contract: dict[str, Any]) -> str:
        hard_rules = "\n".join(
            f"- {rule}"
            for rule in global_contract.get("hard_rules", [])
        )
        verdicts = json.dumps(global_contract.get("allowed_verdicts", {}), indent=2)

        return f"""
{global_contract.get("system_message", "You are a validation agent.")}

You must validate exactly one submission artifact per request.
You must apply every supplied rule and return strict JSON only.
You must cite evidence using file_name and unit_id.
Be brief: no paragraphs, no markdown, no extra keys.
Each checkpoint reason must be one short factual sentence that states the exact value, date, identifier, count, or mismatch used for the decision when evidence contains it.
Before finalizing, verify that every required rule_id appears exactly once in checkpoint_results.

Allowed verdict meanings:
{verdicts}

Hard rules:
{hard_rules}
""".strip()

    def _user_prompt(
        self,
        global_contract: dict[str, Any],
        element_rules: dict[str, Any],
        evidence_bundle: dict[str, Any],
        output_paths: dict[str, str],
        artifact_prefix: str,
    ) -> str:
        element_number = element_rules["element_number"]
        element_name = element_rules["element_name"]
        required_rule_ids = [
            rule.get("rule_id")
            for rule in element_rules.get("rules", [])
            if rule.get("rule_id")
        ]
        watch_outs = "\n".join(
            f"- {self._rule_text(item)}"
            for item in element_rules.get("watch_outs", [])
        )
        rules_text = "\n\n".join(
            self._compact_rule_text(rule, artifact_prefix)
            for rule in element_rules.get("rules", [])
        )
        evidence_text = "\n\n".join(
            self._compact_chunk_text(index, chunk)
            for index, chunk in enumerate(evidence_bundle.get("primary_chunks", []), start=1)
        )
        if not evidence_text:
            evidence_text = evidence_bundle.get("primary_absence_notice") or "No evidence chunks supplied."
        related_evidence_text = self._related_evidence_text(evidence_bundle, artifact_prefix)

        checkpoint_skeleton = [
            {
                "rule_id": rule_id,
                "status": "PASS|FLAG|NOT_FOUND",
                "confidence": 0,
                "evidence": [
                    {
                        "file_name": "source file or empty",
                        "unit_id": "source unit_id or empty",
                        "quote": "max 28 words or empty",
                    }
                ],
                "reason": "max 32 words; include exact value or mismatch when present",
                "recommended_action": "max 20 words; empty unless FLAG/NOT_FOUND",
            }
            for rule_id in required_rule_ids
        ]

        output_schema = {
            "element_number": element_number,
            "element_name": element_name,
            "checkpoint_results": checkpoint_skeleton,
            "element_summary": "max 20 words",
            "missing_evidence": [],
        }
        rule_id_lines = "\n".join(f"- {rule_id}" for rule_id in required_rule_ids)

        return f"""
TASK
Validate artifact {artifact_prefix}{element_number:02d}: {element_name}.
Use primary evidence for this artifact and referenced evidence only when the rule names that artifact.
Return JSON only. The application saves files; do not claim that you saved anything.
Keep the response small and direct.

ELEMENT APPLICABILITY
{self._rule_text(element_rules.get("applicability_rule", ""))}

WATCH OUTS
{watch_outs or "- None."}

REQUIRED RULE IDS
{", ".join(required_rule_ids)}

REQUIRED RULE ID CHECKLIST
{rule_id_lines}

RULES
{rules_text}

EVIDENCE COUNTS
{json.dumps(evidence_bundle.get("counts", {}), ensure_ascii=False)}
required_for_submission_level={evidence_bundle.get("required_for_submission_level", False)}

PRIMARY EVIDENCE
{evidence_text}

REFERENCED ELEMENT EVIDENCE
{related_evidence_text}

OUTPUT JSON SHAPE
{json.dumps(output_schema, indent=2, ensure_ascii=False)}

FINAL INSTRUCTIONS
- Return one JSON object only.
- Fill the OUTPUT JSON SHAPE above; do not remove checkpoint_results entries.
- Include exactly one checkpoint_results item for every required_rule_id.
- checkpoint_results length must be exactly {len(required_rule_ids)}.
- checkpoint_results must be an array, not an object and not grouped text.
- Do not return only top-level evidence/reason/confidence; those fields must be inside each checkpoint_results item.
- The response is invalid if checkpoint_results is missing, empty, or missing any required_rule_id.
- Use the exact rule_id strings listed above.
- Do not rename, merge, reorder, or summarize rule_id values.
- Never skip a rule_id. If evidence is weak, still return that rule_id with NOT_FOUND.
- If a rule cannot be validated from supplied evidence, return NOT_FOUND with a reason.
- For a cross-document rule, return PASS when a needed referenced element is OPTIONAL_NOT_SUBMITTED because no comparison is required.
- For a cross-document rule, return NOT_FOUND when a needed referenced element is REQUIRED_MISSING.
- For a cross-document rule with evidence on both sides, compare exact extracted values, identifiers, revisions, dates, characteristic numbers, specifications, tolerances, limits, sample sizes, and results. PASS only when the specific compared details match or logically satisfy the rule.
- Do not PASS a cross-document rule merely because both documents contain similar field types, numeric values, dates, or part references. If exact values differ, return FLAG and state both values.
- If the evidence has a value needed by the rule, write it in the reason, for example: "Supplier part number is ABS-BR-280V-01 on the PSW." Do not write only that the value is present.
- Do not invent evidence, file names, unit IDs, values, signatures, dates, or approvals.
- Cite at most one evidence item per checkpoint.
- Keep quote, reason, recommended_action, and element_summary concise but specific.
- Final self-check before returning: count checkpoint_results and confirm all required IDs are present exactly once.
""".strip()

    def _compact_rule_text(self, rule: dict[str, Any], artifact_prefix: str = "E") -> str:
        instruction = self._rule_text(rule.get("agent_instruction", ""))
        if instruction.lower().startswith("task:"):
            instruction = instruction[5:].strip()
        parts = [f"Rule {rule.get('rule_id')}: {instruction}"]
        related = [int(number) for number in rule.get("related_element_numbers", [])]
        if related:
            parts.append("Referenced artifacts: " + ", ".join(f"{artifact_prefix}{number:02d}" for number in related))
            parts.append(
                "Cross-check instruction: compare exact field values from the primary element against the referenced element evidence; "
                "state both values when they differ."
            )
        return "\n".join(parts)

    def _compact_chunk_text(self, index: int, chunk: dict[str, Any]) -> str:
        return (
            f"[EVIDENCE {index}]\n"
            f"file_name: {chunk.get('file_name')}\n"
            f"unit_id: {chunk.get('unit_id')}\n"
            f"unit_type: {chunk.get('unit_type')}\n"
            f"label: {chunk.get('label')}\n"
            f"tag_confidence: {chunk.get('confidence')}\n"
            f"content:\n{chunk.get('text', '')}"
        )

    def _related_evidence_text(self, evidence_bundle: dict[str, Any], artifact_prefix: str = "E") -> str:
        blocks = []
        for related in evidence_bundle.get("related_elements", []):
            number = int(related.get("element_number", 0))
            presence = related.get("presence_status", "OPTIONAL_NOT_SUBMITTED")
            chunks = related.get("chunks", []) or []
            header = f"[REFERENCED {artifact_prefix}{number:02d}: {presence}]"
            if not chunks:
                blocks.append(f"{header}\nNo tagged evidence was supplied.")
                continue
            chunk_text = "\n\n".join(
                self._compact_chunk_text(index, chunk)
                for index, chunk in enumerate(chunks, start=1)
            )
            blocks.append(f"{header}\n{chunk_text}")
        return "\n\n".join(blocks) or "No referenced-element evidence is needed for these rules."

    def _rule_text(self, value: Any) -> str:
        return str(value or "").strip().replace(
            "N/A",
            "PASS and state that the requirement is not applicable",
        )
