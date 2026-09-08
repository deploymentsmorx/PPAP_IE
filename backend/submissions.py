# Submission metadata helpers and dashboard aggregates.

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .database import connect, get_case_summary, insert_audit_event, insert_case, next_audit_id
from .standards.profiles import DEFAULT_STANDARD_ID, normalize_standard_id, standard_display_name
from .upload.intake import next_case_id, utc_now, validate_standard_id, validate_submission_level


SUBMISSION_STATUSES = ("draft", "in_review", "failed", "approved", "submitted")
STATUS_ALIASES = {
    "registered": "draft",
    "processing": "in_review",
    "processed": "in_review",
    "validated": "in_review",
    "complete": "approved",
    "completed": "approved",
    "ready": "in_review",
    "error": "failed",
}


def normalize_status(status: str | None) -> str:
    value = str(status or "draft").strip().lower().replace(" ", "_")
    value = STATUS_ALIASES.get(value, value)
    if value not in SUBMISSION_STATUSES:
        return "draft"
    return value


def next_ppap_id(conn, year: int | None = None) -> str:
    year = year or datetime.now(timezone.utc).year
    prefix = f"PPAP-{year}-"
    rows = conn.execute(
        "SELECT ppap_id FROM cases WHERE ppap_id LIKE ?",
        (f"{prefix}%",),
    ).fetchall()
    numbers = []
    for row in rows:
        ppap_id = str(row["ppap_id"] or "")
        suffix = ppap_id.rsplit("-", 1)[-1]
        if suffix.isdigit():
            numbers.append(int(suffix))
    return f"{prefix}{max(numbers, default=0) + 1:05d}"


def create_submission(conn, app_settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    standard_id = validate_standard_id(payload.get("standard_id") or DEFAULT_STANDARD_ID)
    submission_level = int(payload.get("submission_level") or 3)
    validate_submission_level(submission_level)

    required = ("customer_name", "part_number", "part_name", "part_revision", "supplier_name")
    for field in required:
        value = str(payload.get(field) or "").strip()
        if len(value) < 2:
            raise ValueError(f"{field.replace('_', ' ').title()} is required.")
        payload[field] = value

    case_id = next_case_id(conn, app_settings)
    ppap_id = next_ppap_id(conn)
    created_at = utc_now()
    raw_case_dir = app_settings.raw_case_dir(case_id)
    work_case_dir = app_settings.work_case_dir(case_id)
    raw_case_dir.mkdir(parents=True, exist_ok=False)
    work_case_dir.mkdir(parents=True, exist_ok=False)

    record = {
        "case_id": case_id,
        "created_at": created_at,
        "standard_id": standard_id,
        "source_type": "draft",
        "source_names": "[]",
        "submission_level": submission_level,
        "layout_type": "unknown",
        "layout_confidence": 0.0,
        "raw_dir": str(raw_case_dir),
        "work_dir": str(work_case_dir),
        "status": "draft",
        "ppap_id": ppap_id,
        "s3_prefix": app_settings.s3_case_prefix(case_id),
        "customer_name": payload["customer_name"],
        "part_number": payload["part_number"],
        "part_name": payload["part_name"],
        "part_revision": payload["part_revision"],
        "supplier_name": payload["supplier_name"],
        "program_name": str(payload.get("program_name") or "").strip(),
        "submission_date": str(payload.get("submission_date") or "").strip(),
        "due_date": str(payload.get("due_date") or "").strip(),
    }
    insert_case(conn, record)
    insert_audit_event(
        conn,
        {
            "event_id": next_audit_id(conn, case_id),
            "case_id": case_id,
            "created_at": created_at,
            "event_type": "submission_created",
            "message": f"Draft submission {ppap_id} created.",
            "details_json": json.dumps({"ppap_id": ppap_id, "standard_id": standard_id}),
        },
    )
    conn.commit()
    return serialize_submission(get_case_summary(conn, case_id) or {"case": record, "files": [], "audit_log": [], "counts": {}})


def list_submissions(conn, status: str | None = None, search: str | None = None) -> dict[str, Any]:
    clauses = []
    params: list[Any] = []
    if status and status != "all":
        clauses.append("LOWER(status) = ?")
        params.append(normalize_status(status))
    if search:
        like = f"%{search.strip()}%"
        clauses.append(
            "("
            "ppap_id LIKE ? OR customer_name LIKE ? OR part_number LIKE ? OR part_name LIKE ? "
            "OR supplier_name LIKE ? OR case_id LIKE ?"
            ")"
        )
        params.extend([like, like, like, like, like, like])

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""
        SELECT *
        FROM cases
        {where}
        ORDER BY created_at DESC
        """,
        params,
    ).fetchall()
    items = [serialize_case_row(dict(row)) for row in rows]
    return {"items": items, "count": len(items)}


def get_submission(conn, identifier: str) -> dict[str, Any] | None:
    case_id = resolve_case_id(conn, identifier)
    if not case_id:
        return None
    summary = get_case_summary(conn, case_id)
    if summary is None:
        return None
    return serialize_submission(summary)


def update_submission(conn, identifier: str, payload: dict[str, Any]) -> dict[str, Any]:
    case_id = resolve_case_id(conn, identifier)
    if not case_id:
        raise KeyError(f"Unknown submission: {identifier}")

    fields = []
    params: list[Any] = []
    allowed = {
        "customer_name",
        "part_number",
        "part_name",
        "part_revision",
        "supplier_name",
        "program_name",
        "submission_date",
        "due_date",
        "submission_level",
        "standard_id",
        "status",
    }
    for key, value in payload.items():
        if key not in allowed or value is None:
            continue
        if key == "standard_id":
            value = validate_standard_id(value)
        elif key == "submission_level":
            value = int(value)
            validate_submission_level(value)
        elif key == "status":
            value = normalize_status(str(value))
        else:
            value = str(value).strip()
        fields.append(f"{key} = ?")
        params.append(value)

    if not fields:
        raise ValueError("No updatable fields provided.")

    params.append(case_id)
    conn.execute(f"UPDATE cases SET {', '.join(fields)} WHERE case_id = ?", params)
    insert_audit_event(
        conn,
        {
            "event_id": next_audit_id(conn, case_id),
            "case_id": case_id,
            "created_at": utc_now(),
            "event_type": "submission_updated",
            "message": "Submission metadata updated.",
            "details_json": json.dumps({k: payload.get(k) for k in allowed if k in payload}),
        },
    )
    conn.commit()
    summary = get_case_summary(conn, case_id)
    if summary is None:
        raise KeyError(f"Unknown submission: {identifier}")
    return serialize_submission(summary)


def resolve_case_id(conn, identifier: str) -> str | None:
    value = str(identifier or "").strip()
    if not value:
        return None
    row = conn.execute("SELECT case_id FROM cases WHERE case_id = ?", (value,)).fetchone()
    if row:
        return str(row["case_id"])
    row = conn.execute("SELECT case_id FROM cases WHERE ppap_id = ?", (value,)).fetchone()
    return str(row["case_id"]) if row else None


def serialize_case_row(case: dict[str, Any]) -> dict[str, Any]:
    status = normalize_status(case.get("status"))
    standard_id = normalize_standard_id(case.get("standard_id") or DEFAULT_STANDARD_ID)
    ppap_id = case.get("ppap_id") or f"CASE-{case.get('case_id')}"
    return {
        "case_id": str(case.get("case_id")),
        "ppap_id": ppap_id,
        "created_at": case.get("created_at"),
        "standard_id": standard_id,
        "standard_name": standard_display_name(standard_id),
        "submission_level": case.get("submission_level") or 3,
        "status": status,
        "customer_name": case.get("customer_name") or "",
        "part_number": case.get("part_number") or "",
        "part_name": case.get("part_name") or "",
        "part_revision": case.get("part_revision") or "",
        "supplier_name": case.get("supplier_name") or "",
        "program_name": case.get("program_name") or "",
        "submission_date": case.get("submission_date") or "",
        "due_date": case.get("due_date") or "",
        "source_type": case.get("source_type") or "",
        "source_names": _parse_json_list(case.get("source_names")),
    }


def serialize_submission(summary: dict[str, Any]) -> dict[str, Any]:
    case = serialize_case_row(summary.get("case") or {})
    return {
        **case,
        "files": summary.get("files") or [],
        "audit_log": summary.get("audit_log") or [],
        "counts": summary.get("counts") or {},
        "case": summary.get("case") or {},
    }


def build_dashboard(conn, app_settings: Settings) -> dict[str, Any]:
    rows = [dict(row) for row in conn.execute("SELECT * FROM cases ORDER BY created_at DESC").fetchall()]
    items = [serialize_case_row(row) for row in rows]
    counts = {status: 0 for status in SUBMISSION_STATUSES}
    counts["all"] = len(items)
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1

    health = {
        "documents": 0,
        "requirements": 0,
        "quality": 0,
        "critical": 0,
        "warnings": 0,
        "missing_documents": 0,
        "samples": 0,
    }
    for item in items[:25]:
        sample = _validation_health(app_settings, item["case_id"])
        if not sample:
            continue
        health["samples"] += 1
        for key in ("documents", "requirements", "quality"):
            health[key] += sample[key]
        health["critical"] += sample["critical"]
        health["warnings"] += sample["warnings"]
        health["missing_documents"] += sample["missing_documents"]

    if health["samples"]:
        for key in ("documents", "requirements", "quality"):
            health[key] = round(health[key] / health["samples"])

    return {
        "greeting": _greeting(),
        "counts": counts,
        "recent": items[:8],
        "health": health,
    }


def document_matrix(conn, app_settings: Settings, identifier: str) -> dict[str, Any]:
    submission = get_submission(conn, identifier)
    if submission is None:
        raise KeyError(f"Unknown submission: {identifier}")

    case_id = submission["case_id"]
    standard_id = submission["standard_id"]
    level = int(submission["submission_level"] or 3)

    from .validation.runner import ValidationRunner

    runner = ValidationRunner(app_settings, standard_id=standard_id)
    required = set(runner._required_elements_for_level(level))
    catalog = runner.catalog
    tagged = _load_tagged_map(app_settings, case_id)
    validation = _load_validation(app_settings, case_id)

    rows = []
    validation_by_number: dict[int, dict[str, Any]] = {}
    if validation:
        for element in validation.get("element_results") or []:
            try:
                validation_by_number[int(element.get("element_number"))] = element
            except (TypeError, ValueError):
                continue

    for number in catalog.sequence():
        element = catalog.element(number)
        name = element.get("element_name") or element.get("name") or f"Element {number}"
        mapped = tagged.get(number, [])
        element_result = validation_by_number.get(number) or {}
        status = element_result.get("element_status") or ("mapped" if mapped else "missing")
        rows.append(
            {
                "element_number": number,
                "element_name": name,
                "required": number in required,
                "documents": mapped,
                "status": status,
                "severity": _severity_for_element(element_result, number in required),
            }
        )

    return {
        "ppap_id": submission["ppap_id"],
        "case_id": case_id,
        "standard_id": standard_id,
        "submission_level": level,
        "rows": rows,
    }


def severity_summary(validation_report: dict[str, Any]) -> dict[str, Any]:
    buckets = {"critical": [], "major": [], "warning": [], "passed": []}
    elements = validation_report.get("element_results") or validation_report.get("elements") or []
    for element in elements:
        required = bool(element.get("required_for_submission_level"))
        rules = element.get("checkpoint_results") or element.get("rules") or []
        for rule in rules:
            item = {
                "rule_key": rule.get("rule_key") or rule.get("id"),
                "rule_id": rule.get("rule_id") or rule.get("id"),
                "element_number": element.get("element_number"),
                "element_name": element.get("element_name") or element.get("name"),
                "status": rule.get("status"),
                "issue": rule.get("reason") or rule.get("finding") or rule.get("remark") or rule.get("summary") or "",
                "expected": rule.get("expected") or rule.get("rule_description") or "",
                "found": _evidence_text(rule.get("evidence")),
                "recommended_action": rule.get("recommended_action") or "",
                "required": required,
            }
            severity = _severity_for_rule(rule.get("status"), required)
            item["severity"] = severity
            buckets[severity].append(item)

    return {
        "counts": {key: len(value) for key, value in buckets.items()},
        "items": buckets,
    }


def _evidence_text(evidence: Any) -> str:
    if isinstance(evidence, list) and evidence:
        parts = []
        for item in evidence[:3]:
            if isinstance(item, dict):
                parts.append(str(item.get("location") or item.get("text") or item.get("source") or item))
            else:
                parts.append(str(item))
        return "; ".join(parts)
    if evidence:
        return str(evidence)
    return ""


def _severity_for_rule(status: str | None, required: bool) -> str:
    value = str(status or "").upper()
    if value in {"PASS", "PASSED", "OK", "N/A"}:
        return "passed"
    if value in {"NOT_FOUND", "FAIL", "FAILED", "ERROR"}:
        return "critical" if required else "major"
    if value in {"FLAG", "WARNING", "WARN", "REVIEW"}:
        return "warning"
    return "major" if required else "warning"


def _severity_for_element(element_result: dict[str, Any], required: bool) -> str:
    status = str(element_result.get("element_status") or element_result.get("status") or "").upper()
    if not status:
        return "warning" if required else "passed"
    return _severity_for_rule(status, required)


def _load_tagged_map(app_settings: Settings, case_id: str) -> dict[int, list[str]]:
    folder = app_settings.tagging_case_dir(case_id) / "json"
    mapped: dict[int, list[str]] = {}
    if not folder.exists():
        return mapped
    for path in sorted(folder.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        filename = (
            (payload.get("document") or {}).get("file_name")
            or payload.get("source_file")
            or payload.get("filename")
            or path.stem
        )
        numbers: list[int] = []
        tagging = payload.get("element_tagging") or {}
        if tagging.get("element_number") is not None:
            try:
                numbers.append(int(tagging["element_number"]))
            except (TypeError, ValueError):
                pass
        for group in tagging.get("element_groups") or []:
            try:
                numbers.append(int(group.get("element_number")))
            except (TypeError, ValueError):
                continue
        for unit in tagging.get("unit_predictions") or payload.get("units") or []:
            prediction = unit.get("prediction") or unit
            try:
                numbers.append(int(prediction.get("element_number")))
            except (TypeError, ValueError):
                continue
        for number in sorted(set(numbers)):
            mapped.setdefault(number, [])
            if filename not in mapped[number]:
                mapped[number].append(str(filename))
    return mapped


def _load_validation(app_settings: Settings, case_id: str) -> dict[str, Any] | None:
    path = app_settings.validation_case_dir(case_id) / "validation_report.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _validation_health(app_settings: Settings, case_id: str) -> dict[str, int] | None:
    report = _load_validation(app_settings, case_id)
    if not report:
        return None
    summary = severity_summary(report)
    elements = report.get("element_results") or []
    level = report.get("summary", {}).get("level_policy") or {}
    required_numbers = set(level.get("compulsory_elements") or [])
    required = [
        el for el in elements
        if el.get("required_for_submission_level") or int(el.get("element_number") or 0) in required_numbers
    ]
    present = [
        el for el in required
        if str(el.get("element_status") or "").upper() not in {"NOT_FOUND", "ERROR", "N/A"}
    ]
    passed_rules = summary["counts"]["passed"]
    total_rules = max(sum(summary["counts"].values()), 1)
    docs = round(100 * len(present) / max(len(required), 1)) if required else 100
    return {
        "documents": docs,
        "requirements": round(100 * passed_rules / total_rules),
        "quality": round((docs + round(100 * passed_rules / total_rules)) / 2),
        "critical": summary["counts"]["critical"],
        "warnings": summary["counts"]["warning"] + summary["counts"]["major"],
        "missing_documents": sum(
            1 for el in required if str(el.get("element_status") or "").upper() == "NOT_FOUND"
        ),
    }


def _greeting() -> str:
    hour = datetime.now().astimezone().hour
    if hour < 12:
        prefix = "Good morning"
    elif hour < 18:
        prefix = "Good afternoon"
    else:
        prefix = "Good evening"
    return f"{prefix}, Team"


def _parse_json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except (TypeError, json.JSONDecodeError):
        pass
    return [str(value)]
