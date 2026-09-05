"""Small helpers for standard-specific assets."""

import json
from pathlib import Path
from typing import Any


DEFAULT_STANDARD_ID = "aiag_ppap"
SUPPORTED_STANDARD_IDS = {"aiag_ppap", "vda_ppf"}
STANDARDS_DIR = Path(__file__).resolve().parent


def normalize_standard_id(value: str | None) -> str:
    standard_id = str(value or DEFAULT_STANDARD_ID).strip().lower()
    if standard_id not in SUPPORTED_STANDARD_IDS:
        raise ValueError(f"Unsupported standard_id: {value}")
    return standard_id


def standard_root(standard_id: str | None = None) -> Path:
    return STANDARDS_DIR / normalize_standard_id(standard_id)


def standard_profile(standard_id: str | None = None) -> dict[str, Any]:
    root = standard_root(standard_id)
    with (root / "profile.json").open("r", encoding="utf-8") as handle:
        profile = json.load(handle)
    profile["standard_id"] = normalize_standard_id(profile.get("standard_id"))
    return profile


def standard_display_name(standard_id: str | None = None) -> str:
    return standard_profile(standard_id).get("display_name", normalize_standard_id(standard_id))


def checkpoint_catalog_path(standard_id: str | None = None) -> Path:
    profile = standard_profile(standard_id)
    return standard_root(standard_id) / profile["validation"]["checkpoint_catalog_path"]


def tagging_paths(standard_id: str | None = None) -> tuple[Path, Path]:
    profile = standard_profile(standard_id)
    root = standard_root(standard_id)
    return root / profile["tagging"]["elements_path"], root / profile["tagging"]["keywords_path"]


def artifact_prefix(standard_id: str | None = None) -> str:
    return standard_profile(standard_id).get("artifact_prefix", "E")


def report_filename(standard_id: str | None = None) -> str:
    return standard_profile(standard_id).get("reporting", {}).get("default_filename", "Report.pdf")
