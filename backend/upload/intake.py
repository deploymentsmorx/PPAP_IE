# Upload intake, ZIP expansion, and case registration.

import json
import logging
import mimetypes
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Settings
from ..database import get_case_summary, insert_audit_event, insert_case, insert_file, next_audit_id, update_case_layout
from ..db import DbConnection
from ..extraction.extract import extract_document
from ..extraction.json_writer import save_json, save_tagging_json
from ..storage import case_object_key, sync_case_to_s3
from ..tagging.element_tagger import ElementTagger
from ..standards.profiles import DEFAULT_STANDARD_ID, normalize_standard_id, standard_display_name
from .classification import classify_file

JUNK_FILENAMES = {"thumbs.db", ".ds_store", "readme.txt"}
CHUNK_SIZE = 1024 * 1024
LOGGER = logging.getLogger(__name__)

TAGGERS: dict[str, ElementTagger] = {}

class IntakeError(ValueError):
    pass


@dataclass(frozen=True)
class UploadItem:
    source_path: Path
    original_filename: str


def process_uploads(
    uploads: list[UploadItem],
    app_settings: Settings,
    conn: DbConnection,
    submission_level: int = 3,
    standard_id: str = DEFAULT_STANDARD_ID,
    case_id: str | None = None,
) -> dict[str, Any]:
    if not uploads:
        raise IntakeError("At least one file is required.")
    standard_id = validate_standard_id(standard_id)
    validate_submission_level(submission_level)

    app_settings.ensure_dirs()

    created_at = utc_now()
    if case_id:
        existing = get_case_summary(conn, str(case_id))
        if existing is None:
            raise IntakeError(f"Unknown submission case: {case_id}")
        case_id = str(case_id)
        raw_case_dir = Path(existing["case"]["raw_dir"])
        work_case_dir = Path(existing["case"]["work_dir"])
        raw_case_dir.mkdir(parents=True, exist_ok=True)
        work_case_dir.mkdir(parents=True, exist_ok=True)
        source_names = [item.original_filename for item in uploads]
        source_type = upload_source_type(uploads)
        conn.execute(
            """
            UPDATE cases
            SET source_type = ?, source_names = ?, submission_level = ?, standard_id = ?, status = ?
            WHERE case_id = ?
            """,
            (
                source_type,
                json.dumps(source_names),
                submission_level,
                standard_id,
                "in_review",
                case_id,
            ),
        )
        write_audit(
            conn,
            case_id,
            "files_attached",
            "Files attached to existing submission.",
            {"source_type": source_type, "source_names": source_names, "standard_id": standard_id},
        )
    else:
        from ..submissions import next_ppap_id

        case_id = next_case_id(conn, app_settings)
        raw_case_dir = app_settings.raw_case_dir(case_id)
        work_case_dir = app_settings.work_case_dir(case_id)
        raw_case_dir.mkdir(parents=True, exist_ok=False)
        work_case_dir.mkdir(parents=True, exist_ok=False)

        source_names = [item.original_filename for item in uploads]
        source_type = upload_source_type(uploads)
        insert_case(
            conn,
            {
                "case_id": case_id,
                "created_at": created_at,
                "standard_id": standard_id,
                "source_type": source_type,
                "source_names": json.dumps(source_names),
                "submission_level": submission_level,
                "layout_type": "unknown",
                "layout_confidence": 0.0,
                "raw_dir": str(raw_case_dir),
                "work_dir": str(work_case_dir),
                "status": "in_review",
                "ppap_id": next_ppap_id(conn),
            },
        )
        write_audit(
            conn,
            case_id,
            "case_created",
            f"Case created for {standard_display_name(standard_id)}.",
            {"source_type": source_type, "source_names": source_names, "standard_id": standard_id},
        )

    registered_paths: list[str] = []
    for upload in uploads:
        validate_upload_size(upload.source_path, app_settings.max_upload_size_bytes)
        raw_path = copy_to_unique_path(upload.source_path, raw_case_dir, safe_filename(upload.original_filename))
        if is_zip_name(upload.original_filename):
            register_zip_contents(
                raw_path,
                upload.original_filename,
                case_id,
                work_case_dir,
                app_settings,
                conn,
                registered_paths,
                standard_id=standard_id,
            )
        else:
            register_file(
                raw_path,
                upload.original_filename,
                case_id,
                upload.original_filename,
                None,
                app_settings,
                conn,
                registered_paths,
                standard_id=standard_id,
            )

    layout_type, layout_confidence = detect_layout(registered_paths, source_type)
    update_case_layout(conn, case_id, layout_type, layout_confidence)
    write_audit(
        conn,
        case_id,
        "layout_detected",
        f"Upload layout detected as {layout_type}.",
        {"layout_type": layout_type, "layout_confidence": layout_confidence},
    )

    summary = get_case_summary(conn, case_id) or {}
    try:
        s3_uri = sync_case_to_s3(case_id, app_settings)
        if s3_uri:
            prefix = app_settings.s3_case_prefix(case_id)
            conn.execute(
                "UPDATE cases SET s3_prefix = ? WHERE case_id = ?",
                (prefix, case_id),
            )
            for file_row in summary.get("files") or []:
                stored = file_row.get("stored_path") or ""
                case_root = str(app_settings.case_dir(case_id))
                if stored.startswith(case_root):
                    rel = Path(stored).relative_to(app_settings.case_dir(case_id)).as_posix()
                    key = case_object_key(case_id, rel, app_settings)
                    conn.execute(
                        "UPDATE files SET s3_key = ?, stored_path = ? WHERE file_id = ?",
                        (key, f"s3://{app_settings.s3_bucket}/{key}" if app_settings.s3_enabled else stored, file_row["file_id"]),
                    )
            summary = get_case_summary(conn, case_id) or summary
    except Exception as exc:  # noqa: BLE001 â€” keep intake success if S3 fails temporarily
        LOGGER.warning("S3 sync after upload failed for %s: %s", case_id, exc)
    conn.commit()
    return summary


def preview_uploads(
    uploads: list[UploadItem],
    app_settings: Settings,
    submission_level: int = 3,
    standard_id: str = DEFAULT_STANDARD_ID,
) -> dict[str, Any]:
    if not uploads:
        raise IntakeError("At least one file is required.")
    standard_id = validate_standard_id(standard_id)
    validate_submission_level(submission_level)

    source_names = [item.original_filename for item in uploads]
    source_type = upload_source_type(uploads)
    files: list[dict[str, Any]] = []

    for upload in uploads:
        validate_upload_size(upload.source_path, app_settings.max_upload_size_bytes)
        if is_zip_name(upload.original_filename):
            files.extend(preview_zip_members(upload.source_path, upload.original_filename, app_settings))
        else:
            files.append(preview_loose_file(upload.source_path, upload.original_filename))

    registered_paths = [file["relative_path"] for file in files if not file["ignored"]]
    layout_type, layout_confidence = detect_layout(registered_paths, source_type)
    return {
        "source_type": source_type,
        "source_names": source_names,
        "standard_id": standard_id,
        "standard_name": standard_display_name(standard_id),
        "submission_level": submission_level,
        "layout_type": layout_type,
        "layout_confidence": layout_confidence,
        "files": files,
        "counts": {
            "total": len(files),
            "processable": sum(1 for file in files if not file["ignored"]),
            "ignored": sum(1 for file in files if file["ignored"]),
        },
    }


def preview_zip_members(zip_path: Path, original_filename: str, app_settings: Settings) -> list[dict[str, Any]]:
    files = []
    try:
        with zipfile.ZipFile(zip_path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                relative_path = zip_member_path(info.filename)
                ignored = is_junk_file(relative_path.name)
                files.append(
                    {
                        "filename": relative_path.name,
                        "relative_path": relative_path.as_posix(),
                        "source_container": original_filename,
                        "extension": Path(relative_path.name).suffix.lower(),
                        "size_bytes": info.file_size,
                        "ignored": ignored,
                        "ignore_reason": "junk_file" if ignored else "",
                    }
                )
    except zipfile.BadZipFile as exc:
        raise IntakeError("ZIP upload could not be read.") from exc
    return files


def preview_loose_file(path: Path, original_filename: str) -> dict[str, Any]:
    filename = safe_filename(original_filename)
    ignored = is_junk_file(filename)
    return {
        "filename": filename,
        "relative_path": filename,
        "source_container": "",
        "extension": Path(filename).suffix.lower(),
        "size_bytes": path.stat().st_size,
        "ignored": ignored,
        "ignore_reason": "junk_file" if ignored else "",
    }


def register_zip_contents(
    zip_path: Path,
    original_filename: str,
    case_id: str,
    work_case_dir: Path,
    app_settings: Settings,
    conn: DbConnection,
    registered_paths: list[str],
    archive_depth: int = 0,
    path_prefix: str = "",
    standard_id: str = DEFAULT_STANDARD_ID,
) -> None:
    extract_root = unique_directory(work_case_dir, safe_stem(original_filename))
    extract_root.mkdir(parents=True, exist_ok=False)
    write_audit(
        conn,
        case_id,
        "zip_extracted",
        f"ZIP extracted: {original_filename}.",
        {"archive_depth": archive_depth, "extract_root": str(extract_root), "path_prefix": path_prefix},
    )

    try:
        with zipfile.ZipFile(zip_path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue

                relative_path = zip_member_path(info.filename)
                display_path = combine_archive_path(path_prefix, relative_path.as_posix())
                if is_junk_file(relative_path.name):
                    register_ignored_file(
                        case_id,
                        relative_path.name,
                        display_path,
                        original_filename,
                        info.file_size,
                        conn,
                        "junk_file",
                        archive_depth,
                    )
                    continue

                target_path = resolve_child_path(extract_root, relative_path)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target_path.open("wb") as target:
                    shutil.copyfileobj(source, target, CHUNK_SIZE)

                register_file(
                    target_path,
                    relative_path.name,
                    case_id,
                    display_path,
                    original_filename,
                    app_settings,
                    conn,
                    registered_paths,
                    archive_depth,
                    standard_id,
                )
                if is_zip_name(relative_path.name):
                    register_zip_contents(
                        target_path,
                        display_path,
                        case_id,
                        work_case_dir,
                        app_settings,
                        conn,
                        registered_paths,
                        archive_depth + 1,
                        display_path,
                        standard_id,
                    )
    except zipfile.BadZipFile as exc:
        raise IntakeError("ZIP upload could not be read.") from exc


def register_file(
    stored_path: Path,
    filename: str,
    case_id: str,
    relative_path: str,
    source_container: str | None,
    app_settings: Settings,
    conn: DbConnection,
    registered_paths: list[str],
    archive_depth: int = 0,
    standard_id: str = DEFAULT_STANDARD_ID,
) -> str:
    file_id = next_file_id(conn, case_id)
    ignored = is_junk_file(filename)
    classification = classify_file(stored_path, filename)
    try:
        extracted_content = extract_document(
            stored_path,
            data_dir=app_settings.data_dir,
            case_id=case_id,
        )
    except Exception as exc:
        extracted_content = {
            "document": {
                "file_name": filename,
                "file_type": "extraction_error",
                "extension": Path(filename).suffix.lower(),
                "file_size": stored_path.stat().st_size,
            },
            "errors": [
                {
                    "stage": "extraction",
                    "error": str(exc),
                }
            ],
        }
    tagging_result = tagger_for(standard_id).tag_document(extracted_content)
    json_path = save_json(
        stored_path,
        extracted_content,
        data_dir=app_settings.data_dir,
        case_id=case_id,
        file_id=file_id,
    )
    tagged_json_path = save_tagging_json(
        stored_path,
        extracted_content,
        tagging_result,
        data_dir=app_settings.data_dir,
        case_id=case_id,
        file_id=file_id,
        extraction_json_path=json_path,
    )

    LOGGER.debug(
        "Processed %s: status=%s, element=%s, extracted_json=%s, tagged_json=%s",
        relative_path,
        tagging_result.get("status"),
        tagging_result.get("primary_element") or tagging_result.get("predicted_element") or "Unresolved",
        json_path,
        tagged_json_path,
    )
    insert_file(
        conn,
        {
            "file_id": file_id,
            "case_id": case_id,
            "created_at": utc_now(),
            "filename": filename,
            "relative_path": relative_path,
            "stored_path": str(stored_path),
            "source_container": source_container,
            "extension": Path(filename).suffix.lower(),
            "mime_type": guess_mime_type(filename),
            "detected_type": classification.detected_type,
            "type_confidence": classification.type_confidence,
            "routing_lane": classification.routing_lane,
            "digital_status": classification.digital_status,
            "unit_count": classification.unit_count,
            "unit_label": classification.unit_label,
            "archive_depth": archive_depth,
            "size_bytes": stored_path.stat().st_size,
            "sha256": None,
            "is_duplicate": 0,
            "duplicate_of": None,
            "ignored": 1 if ignored else 0,
            "ignore_reason": "junk_file" if ignored else None,
            "processing_status": "ignored" if ignored else "registered",
        },
    )
    if not ignored:
        registered_paths.append(relative_path)
    write_audit(
        conn,
        case_id,
        "file_registered" if not ignored else "file_ignored",
        f"File {relative_path} marked as {'ignored' if ignored else 'registered'}.",
        {
            "file_id": file_id,
            "relative_path": relative_path,
            "detected_type": classification.detected_type,
            "routing_lane": classification.routing_lane,
            "archive_depth": archive_depth,
        },
    )
    return file_id


def register_ignored_file(
    case_id: str,
    filename: str,
    relative_path: str,
    source_container: str | None,
    size_bytes: int,
    conn: DbConnection,
    reason: str,
    archive_depth: int = 0,
) -> None:
    insert_file(
        conn,
        {
            "file_id": next_file_id(conn, case_id),
            "case_id": case_id,
            "created_at": utc_now(),
            "filename": filename,
            "relative_path": relative_path,
            "stored_path": None,
            "source_container": source_container,
            "extension": Path(filename).suffix.lower(),
            "mime_type": guess_mime_type(filename),
            "detected_type": "ignored",
            "type_confidence": 1.0,
            "routing_lane": "ignored",
            "digital_status": "not_applicable",
            "unit_count": None,
            "unit_label": None,
            "archive_depth": archive_depth,
            "size_bytes": size_bytes,
            "sha256": None,
            "is_duplicate": 0,
            "duplicate_of": None,
            "ignored": 1,
            "ignore_reason": reason,
            "processing_status": "ignored",
        },
    )
    write_audit(
        conn,
        case_id,
        "file_ignored",
        f"File {relative_path} ignored.",
        {"relative_path": relative_path, "reason": reason, "archive_depth": archive_depth},
    )


def zip_member_path(member_name: str) -> Path:
    normalized = member_name.replace("\\", "/")
    parts = [safe_filename(part) for part in normalized.split("/") if part not in ("", ".", "..")]
    if not parts:
        return Path("upload.bin")
    return Path(*parts)


def resolve_child_path(root: Path, child: Path) -> Path:
    return root / child


def is_zip_name(filename: str) -> bool:
    return Path(filename).suffix.lower() == ".zip"


def upload_source_type(uploads: list[UploadItem]) -> str:
    return "zip" if len(uploads) == 1 and is_zip_name(uploads[0].original_filename) else "loose_files"


def validate_upload_size(path: Path, max_size_bytes: int) -> None:
    if path.stat().st_size > max_size_bytes:
        raise IntakeError("Upload rejected: file is too large.")


def validate_submission_level(submission_level: int) -> None:
    if submission_level not in {1, 2, 3, 4, 5}:
        raise IntakeError("Submission level must be between 1 and 5.")


def validate_standard_id(standard_id: str) -> str:
    try:
        return normalize_standard_id(standard_id)
    except ValueError as exc:
        raise IntakeError(str(exc)) from exc


def tagger_for(standard_id: str) -> ElementTagger:
    standard_id = validate_standard_id(standard_id)
    if standard_id not in TAGGERS:
        TAGGERS[standard_id] = ElementTagger(standard_id)
    return TAGGERS[standard_id]


def is_junk_file(filename: str) -> bool:
    return filename.lower() in JUNK_FILENAMES


def safe_filename(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name.strip()
    if not name:
        name = "upload.bin"
    return re.sub(r"[^A-Za-z0-9._ -]", "_", name)


def safe_stem(filename: str) -> str:
    stem = Path(safe_filename(filename)).stem.strip()
    return stem or "upload"


def copy_to_unique_path(source: Path, target_dir: Path, filename: str) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    target = unique_path(target_dir, filename)
    shutil.copy2(source, target)
    return target


def unique_directory(parent: Path, name: str) -> Path:
    candidate = parent / name
    if not candidate.exists():
        return candidate

    counter = 2
    while True:
        next_candidate = parent / f"{name}_{counter}"
        if not next_candidate.exists():
            return next_candidate
        counter += 1


def unique_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while True:
        next_candidate = directory / f"{stem}_{counter}{suffix}"
        if not next_candidate.exists():
            return next_candidate
        counter += 1


def guess_mime_type(filename: str) -> str | None:
    return mimetypes.guess_type(filename)[0]


def detect_layout(relative_paths: list[str], source_type: str) -> tuple[str, float]:
    if source_type == "loose_files":
        return "loose_files", 0.95
    if not relative_paths:
        return "empty_or_ignored", 0.7

    normal_paths = [path.replace("\\", "/") for path in relative_paths]
    top_folders = [path.split("/", 1)[0] for path in normal_paths if "/" in path]
    if not top_folders:
        return "flat", 0.95

    folder_ratio = len(top_folders) / len(normal_paths)
    element_folder_count = sum(1 for folder in top_folders if looks_like_element_folder(folder))
    if folder_ratio >= 0.7 and element_folder_count / len(top_folders) >= 0.6:
        return "folders_per_element", 0.85
    if folder_ratio >= 0.7:
        return "random_nested", 0.7
    return "mixed_flat_and_nested", 0.65


def looks_like_element_folder(folder: str) -> bool:
    normalized = folder.lower().replace("_", " ").replace("-", " ")
    if re.search(r"\belement\s*0?([1-9]|1[0-8])\b", normalized):
        return True
    if re.match(r"^0?([1-9]|1[0-8])\b", normalized):
        return True
    element_terms = (
        "design",
        "fmea",
        "control plan",
        "flow",
        "msa",
        "dimensional",
        "material",
        "psw",
        "warrant",
    )
    return any(term in normalized for term in element_terms)


def combine_archive_path(path_prefix: str, relative_path: str) -> str:
    if not path_prefix:
        return relative_path
    return f"{path_prefix}::{relative_path}"


def write_audit(
    conn: DbConnection,
    case_id: str,
    event_type: str,
    message: str,
    details: dict[str, Any],
) -> None:
    insert_audit_event(
        conn,
        {
            "event_id": next_audit_id(conn, case_id),
            "case_id": case_id,
            "created_at": utc_now(),
            "event_type": event_type,
            "message": message,
            "details_json": json.dumps(details),
        },
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def next_case_id(conn: DbConnection, app_settings: Settings | None = None) -> str:
    rows = conn.execute("SELECT case_id FROM cases").fetchall()
    numbers = {int(row["case_id"]) for row in rows if str(row["case_id"]).isdigit()}
    if app_settings and app_settings.cases_dir.exists():
        numbers.update(
            int(path.name)
            for path in app_settings.cases_dir.iterdir()
            if path.is_dir() and path.name.isdigit()
        )
    return str(max(numbers, default=0) + 1)


def next_file_id(conn: DbConnection, case_id: str) -> str:
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM files WHERE case_id = ?",
        (case_id,),
    ).fetchone()
    return f"{case_id}-{int(row['count']) + 1:03d}"
