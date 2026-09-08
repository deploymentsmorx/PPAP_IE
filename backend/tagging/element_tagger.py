# Element tagging orchestration.

import json
import os

from ..env import load_project_env
from ..anthropic_client import DEFAULT_ANTHROPIC_MODEL
from ..standards.profiles import DEFAULT_STANDARD_ID, normalize_standard_id, tagging_paths
from .llm_fallback import LlmFallbackClient
from .postprocess import PredictionPostProcessorMixin
from .prediction_rules import PredictionRuleMixin
from .text_utils import TextHelperMixin
from .unit_builder import UnitBuilderMixin


class ElementTagger(TextHelperMixin, PredictionRuleMixin, UnitBuilderMixin, PredictionPostProcessorMixin):
    ANTHROPIC_API_KEY_ENV_NAMES = (
        "PPAP_TAGGING_ANTHROPIC_API_KEY",
        "PPAP_ANTHROPIC_API_KEY",
        "ANTHROPIC_API_KEY",
    )

    HIGH_WEIGHT = 10
    MEDIUM_WEIGHT = 5
    LOW_WEIGHT = 2

    LLM_TEXT_LIMIT = 6000
    LLM_TIMEOUT_SECONDS = 60
    LLM_NUM_PREDICT = 512

    MAX_ROWS_PER_TABLE = 30
    MAX_TEXT_PER_UNIT = 12000
    STRONG_SINGLE_WORD_ALIASES = {
        "aai",
        "aar",
        "ablaufplan",
        "bemusterungsumfang",
        "cp",
        "csr",
        "dfmea",
        "ecn",
        "eco",
        "ecr",
        "empb",
        "erstmusterteile",
        "flowchart",
        "grenzmuster",
        "controlplan",
        "isir",
        "lehre",
        "lenkungsplan",
        "massbericht",
        "messmittelfaehigkeit",
        "messsystemanalyse",
        "msa",
        "musterteile",
        "oberflaechenfreigabe",
        "pfd",
        "pfmea",
        "prozessablaufplan",
        "prozessfaehigkeit",
        "pruefmittel",
        "pruefplan",
        "psw",
        "referenzmuster",
        "sonderfreigabe",
        "teilevorlagebestaetigung",
        "zeichnung",
    }

    def __init__(self, standard_id: str = DEFAULT_STANDARD_ID):
        load_project_env()
        self.standard_id = normalize_standard_id(standard_id)
        elements_path, keywords_path = tagging_paths(self.standard_id)
        self.llm_model = os.getenv("PPAP_TAGGING_ANTHROPIC_MODEL") or os.getenv("PPAP_ANTHROPIC_MODEL") or DEFAULT_ANTHROPIC_MODEL
        self.llm_api_key = self._anthropic_api_key()
        self.llm_enabled = self._tagging_fallback_enabled()
        llm_url = os.getenv("PPAP_TAGGING_ANTHROPIC_URL") or os.getenv("PPAP_ANTHROPIC_URL")
        llm_timeout = int(os.getenv("PPAP_TAGGING_TIMEOUT", str(self.LLM_TIMEOUT_SECONDS)))
        llm_max_output_tokens = int(
            os.getenv(
                "PPAP_TAGGING_MAX_OUTPUT_TOKENS",
                os.getenv("PPAP_TAGGING_NUM_PREDICT", str(self.LLM_NUM_PREDICT)),
            )
        )

        with elements_path.open("r", encoding="utf-8") as f:
            self.elements = json.load(f)

        with keywords_path.open("r", encoding="utf-8") as f:
            raw_keywords = json.load(f)

        self.keywords = {
            element: raw_keywords.get(element, {})
            for element in self.elements
        }
        self._add_runtime_keyword_gaps()

        self.element_by_number = {
            int(cfg["element_number"]): element
            for element, cfg in self.elements.items()
        }
        self.llm = LlmFallbackClient(
            self.elements,
            self._match_element_name,
            self.llm_model,
            llm_url,
            self.LLM_TEXT_LIMIT,
            llm_timeout,
            llm_max_output_tokens,
            api_key=self.llm_api_key,
        )

    def _anthropic_api_key(self):
        for name in self.ANTHROPIC_API_KEY_ENV_NAMES:
            value = os.getenv(name)
            if value and value.strip():
                return value.strip()
        return None

    def _tagging_fallback_enabled(self):
        configured = os.getenv("PPAP_TAGGING_ANTHROPIC_FALLBACK")
        if configured is None:
            return bool(self.llm_api_key)
        return configured.strip().lower() in {"1", "true", "yes", "on"}

    def _add_runtime_keyword_gaps(self):
        if "Appearance Approval Report" in self.elements and not self.keywords.get("Appearance Approval Report"):
            self.keywords["Appearance Approval Report"] = {
                "high_priority": [
                    "appearance approval report",
                    "aar",
                    "appearance approval",
                    "color approval",
                    "grain approval",
                    "gloss approval",
                    "appearance requirement",
                    "appearance waiver",
                ],
                "medium_priority": [
                    "appearance",
                    "color",
                    "grain",
                    "gloss",
                    "surface appearance",
                    "visible surface",
                    "appearance applicable",
                ],
                "low_priority": [
                    "customer",
                    "supplier",
                    "approval",
                    "approved by",
                    "date",
                    "signature",
                ],
            }

    def tag_document(self, document_json):
        filename = document_json.get("document", {}).get("file_name", "")
        extraction_failed = (
            document_json.get("document", {}).get("file_type") == "extraction_error"
            or bool(document_json.get("errors"))
        )

        filename_elements = self._predict_filenames(filename)
        filename_prediction = filename_elements[0] if filename_elements else None

        units = self._build_units(document_json)
        predictions = [
            self._classify_unit(unit, filename, filename_elements)
            for unit in units
        ]
        self._fill_continuity_and_fallbacks(
            predictions,
            units,
            filename,
            filename_elements,
            extraction_failed,
        )

        groups = self._group_predictions(predictions)
        status, gamma_required = self._summary_status(groups, predictions)

        distinct_elements = []
        for group in groups:
            if group["element"] not in distinct_elements:
                distinct_elements.append(group["element"])

        primary_element = distinct_elements[0] if len(distinct_elements) == 1 else None
        predicted_element = primary_element or (groups[0]["element"] if groups else None)
        element_number = (
            self.elements[predicted_element]["element_number"]
            if predicted_element
            else None
        )

        whole_text = self._extract_text(document_json)
        content_prediction, score, matches, all_scores, score_margin = self._predict_content(whole_text)

        confidence = 0
        if predictions and groups:
            confidence = min(
                item["confidence"]
                for item in predictions
                if item.get("predicted_element")
            )

        remarks = [
            "Tagged by document units: headings/sheets/slides/pages first, content second, filename as backup."
        ]
        if len(distinct_elements) > 1:
            remarks.append("Multiple submission artifacts were found in this file.")
        if gamma_required:
            remarks.append("One or more chunks still need Gamma/LLM verification.")
        shared_context_units = [
            item["unit_id"]
            for item in predictions
            if item.get("shared_context")
        ]
        if shared_context_units:
            remarks.append("Shared metadata/context units are attached to each element group.")

        llm_fallback = self._empty_llm_result()
        used_fallbacks = [item["llm_fallback"] for item in predictions if item["llm_fallback"].get("used")]
        if used_fallbacks:
            llm_fallback = {
                "used": True,
                "model": self.llm_model,
                "chunks_checked": len(used_fallbacks),
                "successful_chunks": sum(1 for item in used_fallbacks if item.get("predicted_element")),
                "errors": [item.get("error") for item in used_fallbacks if item.get("error")],
            }

        return {
            "predicted_element": predicted_element,
            "primary_element": primary_element,
            "element_number": element_number,
            "standard_id": self.standard_id,
            "is_multi_element": len(distinct_elements) > 1,
            "element_groups": groups,
            "unit_predictions": predictions,
            "filename_prediction": filename_prediction,
            "filename_predictions": filename_elements,
            "content_prediction": content_prediction,
            "content_score": score,
            "score_margin": score_margin,
            "all_scores": all_scores,
            "matched_keywords": matches,
            "confidence": confidence,
            "gamma_required": gamma_required,
            "status": status,
            "remarks": remarks,
            "llm_fallback": llm_fallback,
            "analysis": {
                "matched_keywords": matches,
                "all_scores": all_scores,
                "score_margin": score_margin,
                "text_length": len(whole_text),
                "unit_count": len(units),
                "distinct_elements": distinct_elements,
                "shared_context_units": shared_context_units,
            },
        }
