# Collects tagged evidence for validation prompts.

import json
from pathlib import Path
from typing import Any

from ..tagging.text_utils import TextHelperMixin
from ..tagging.unit_builder import UnitBuilderMixin


class UnitSplitter(TextHelperMixin, UnitBuilderMixin):
    MAX_ROWS_PER_TABLE = 30
    MAX_TEXT_PER_UNIT = 12000


class EvidenceBuilder:
    def __init__(
        self,
        cases_dir: Path,
        max_unit_chars: int = 8000,
        max_primary_chars: int = 60000,
        max_related_element_chars: int = 12000,
    ):
        self.cases_dir = Path(cases_dir)
        self.max_unit_chars = max_unit_chars
        self.max_primary_chars = max_primary_chars
        self.max_related_element_chars = max_related_element_chars
        self.unit_builder = UnitSplitter()

    def load_tagged_documents(self, case_id: str) -> list[dict[str, Any]]:
        folder = self.cases_dir / str(case_id) / "tagging" / "json"
        if not folder.exists():
            raise FileNotFoundError(f"Tagged JSON folder not found: {folder}")

        documents = []
        for path in sorted(folder.glob("*.json")):
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            document = self._hydrate_tagged_payload(payload)
            document["_tagged_json_path"] = str(path)
            documents.append(document)
        return documents

    def _hydrate_tagged_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        extraction_path = payload.get("extraction_json_path")
        if extraction_path:
            path = Path(extraction_path)
            if path.exists():
                with path.open("r", encoding="utf-8") as handle:
                    document = json.load(handle)
                document["element_tagging"] = payload.get("element_tagging", {})
                document["_extraction_json_path"] = str(path)
                return document
        return payload

    def build_case_index(self, documents: list[dict[str, Any]]) -> dict[str, Any]:
        chunks = []
        shared_chunks = []

        for payload in documents:
            file_name = payload.get("document", {}).get("file_name", "")
            units = {
                unit["unit_id"]: unit
                for unit in self.unit_builder._build_units(payload)
            }
            predictions = payload.get("element_tagging", {}).get("unit_predictions", [])

            for prediction in predictions:
                unit_id = prediction.get("unit_id")
                unit = units.get(unit_id, {})
                text = unit.get("text", "") or ""
                chunk = {
                    "file_name": file_name,
                    "tagged_json_path": payload.get("_tagged_json_path"),
                    "unit_id": unit_id,
                    "unit_type": prediction.get("unit_type") or unit.get("unit_type"),
                    "label": prediction.get("label") or unit.get("label"),
                    "predicted_element": prediction.get("predicted_element"),
                    "element_number": prediction.get("element_number"),
                    "source": prediction.get("source"),
                    "confidence": prediction.get("confidence"),
                    "shared_context": bool(prediction.get("shared_context")),
                    "text": text[: self.max_unit_chars],
                    "text_length": len(text),
                }
                if chunk["shared_context"]:
                    shared_chunks.append(chunk)
                else:
                    chunks.append(chunk)

        return {
            "chunks": chunks,
            "shared_chunks": shared_chunks,
            "documents": [
                {
                    "file_name": payload.get("document", {}).get("file_name", ""),
                    "file_type": payload.get("document", {}).get("file_type", ""),
                    "tagged_json_path": payload.get("_tagged_json_path"),
                    "primary_element": payload.get("element_tagging", {}).get("primary_element"),
                    "is_multi_element": payload.get("element_tagging", {}).get("is_multi_element"),
                }
                for payload in documents
            ],
        }

    def element_evidence(
        self,
        case_index: dict[str, Any],
        element_number: int,
        related_element_numbers: list[int] | None = None,
        required_element_numbers: set[int] | None = None,
    ) -> dict[str, Any]:
        primary = [
            chunk
            for chunk in case_index["chunks"]
            if chunk.get("element_number") == element_number
        ]

        primary = self._cap_chunks(primary, self.max_primary_chars)
        required = required_element_numbers or set()
        related_elements = []
        for related_number in sorted(set(related_element_numbers or []) - {element_number}):
            related_chunks = [
                chunk
                for chunk in case_index["chunks"]
                if chunk.get("element_number") == related_number
            ]
            related_chunks = self._cap_chunks(related_chunks, self.max_related_element_chars)
            related_elements.append(
                {
                    "element_number": related_number,
                    "presence_status": (
                        "PRESENT"
                        if related_chunks
                        else "REQUIRED_MISSING"
                        if related_number in required
                        else "OPTIONAL_NOT_SUBMITTED"
                    ),
                    "chunks": related_chunks,
                }
            )

        return {
            "element_number": element_number,
            "primary_chunks": primary,
            "related_elements": related_elements,
            "primary_absence_notice": (
                "No tagged file/page/sheet/chunk was found for this submission artifact."
                if not primary
                else ""
            ),
            "counts": {
                "primary_chunks": len(primary),
                "related_elements": len(related_elements),
                "related_chunks": sum(len(item["chunks"]) for item in related_elements),
            },
        }

    def _cap_chunks(self, chunks: list[dict[str, Any]], max_chars: int) -> list[dict[str, Any]]:
        capped = []
        used = 0
        for chunk in chunks:
            text = chunk.get("text", "")
            if used >= max_chars:
                break
            remaining = max_chars - used
            item = dict(chunk)
            item["text"] = text[:remaining]
            item["truncated_for_prompt"] = len(text) > len(item["text"])
            used += len(item["text"])
            capped.append(item)
        return capped
