# Loads validation checkpoint definitions for the selected standard.

import json
from pathlib import Path
from typing import Any

from ..standards.profiles import DEFAULT_STANDARD_ID, checkpoint_catalog_path, normalize_standard_id, standard_profile

CHECKPOINTS_PATH = checkpoint_catalog_path(DEFAULT_STANDARD_ID)


class CheckpointCatalog:
    def __init__(self, path: Path | None = None, standard_id: str = DEFAULT_STANDARD_ID):
        self.standard_id = normalize_standard_id(standard_id)
        self.profile = standard_profile(self.standard_id)
        path = path or checkpoint_catalog_path(self.standard_id)
        self.path = path
        self.payload = self._load(path)
        self.payload.setdefault("standard_id", self.standard_id)
        self.elements = {
            int(element["element_number"]): element
            for element in self.payload.get("elements", [])
        }

    def global_prompt_contract(self) -> dict[str, Any]:
        return self.payload["global_prompt_contract"]

    def element(self, element_number: int) -> dict[str, Any]:
        try:
            return self.elements[int(element_number)]
        except KeyError as exc:
            raise ValueError(f"Unknown {self.profile.get('display_name')} element number: {element_number}") from exc

    def sequence(self) -> list[int]:
        configured = self.profile.get("validation_sequence")
        if configured:
            return [int(number) for number in configured]
        return sorted(self.elements)

    def element_count(self) -> int:
        return len(self.elements)

    def all_rule_ids(self, element_number: int) -> list[str]:
        return [rule["rule_id"] for rule in self.element(element_number).get("rules", [])]

    def related_element_numbers(self, element_number: int) -> list[int]:
        related = {
            int(related_number)
            for rule in self.element(element_number).get("rules", [])
            for related_number in rule.get("related_element_numbers", [])
            if int(related_number) != int(element_number)
        }
        return sorted(related)

    @staticmethod
    def _load(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
