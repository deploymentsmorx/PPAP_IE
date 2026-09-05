# Writes extraction and tagging JSON artifacts.

import json
from pathlib import Path


def save_json(file_path, data, data_dir=None, case_id=None, file_id=None):
    output_folder = _case_output_folder(data_dir, "extraction", case_id) / "json"
    output_folder.mkdir(parents=True, exist_ok=True)

    json_file = output_folder / _json_name(file_path, file_id)
    _write_json(json_file, data)
    return json_file


def save_tagging_json(
    file_path,
    extracted_content,
    tagging_result,
    data_dir=None,
    case_id=None,
    file_id=None,
    extraction_json_path=None,
):
    output_folder = _case_output_folder(data_dir, "tagging", case_id) / "json"
    output_folder.mkdir(parents=True, exist_ok=True)

    payload = {
        "document": (extracted_content or {}).get("document", {}),
        "extraction_json_path": str(extraction_json_path) if extraction_json_path else "",
        "element_tagging": tagging_result,
    }
    json_file = output_folder / _json_name(file_path, file_id, suffix=".tagging")
    _write_json(json_file, payload)
    return json_file


def _case_output_folder(data_dir, category, case_id):
    root = Path(data_dir) if data_dir else Path("data")
    if case_id:
        return root / "cases" / str(case_id) / category
    return root / category


def _json_name(file_path, file_id=None, suffix=""):
    stem = Path(file_path).stem
    if file_id:
        stem = f"{file_id}_{stem}"
    return f"{stem}{suffix}.json"


def _write_json(path, data):
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
