# Human review overrides for validation results.

import copy
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .database import connect, init_db, insert_audit_event, next_audit_id
from .standards.profiles import DEFAULT_STANDARD_ID


REVIEW_STATUSES = {"PASS", "FLAG", "NOT_FOUND"}


def save_rule_review(
    conn: sqlite3.Connection,
    case_id: str,
    rule_key: str,
    status: str,
    remark: str,
    evidence_location: str = "",
) -> dict[str, Any]:
    normalized_status = str(status or "").upper()
    if normalized_status not in REVIEW_STATUSES:
        raise ValueError("Review status must be PASS, FLAG, or NOT_FOUND.")
    clean_remark = " ".join(str(remark or "").split())
    if len(clean_remark) < 3:
        raise ValueError("A review remark is required.")
    clean_location = " ".join(str(evidence_location or "").split())
    rule = conn.execute(
        "SELECT standard_id, element_number FROM validation_rules WHERE rule_key = ?",
        (rule_key,),
    ).fetchone()
    if rule is None:
        raise KeyError(f"Unknown rule: {rule_key}")
    if conn.execute("SELECT 1 FROM cases WHERE case_id = ?", (case_id,)).fetchone() is None:
        raise KeyError(f"Unknown case: {case_id}")

    now = _utc_now()
    updated = conn.execute(
        """
        UPDATE case_rule_reviews
        SET status = ?, remark = ?, evidence_location = ?, updated_at = ?
        WHERE case_id = ? AND rule_key = ?
        """,
        (normalized_status, clean_remark, clean_location, now, case_id, rule_key),
    )
    if updated.rowcount == 0:
        conn.execute(
            """
            INSERT INTO case_rule_reviews (
                case_id, standard_id, rule_key, element_number, status, remark, evidence_location, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                case_id,
                rule["standard_id"],
                rule_key,
                int(rule["element_number"]),
                normalized_status,
                clean_remark,
                clean_location,
                now,
            ),
        )
    insert_audit_event(
        conn,
        {
            "event_id": next_audit_id(conn, case_id),
            "case_id": case_id,
            "created_at": now,
            "event_type": "rule_reviewed",
            "message": f"Human review set {rule_key} to {normalized_status}.",
            "details_json": json.dumps(
                {
                    "rule_key": rule_key,
                    "status": normalized_status,
                    "remark": clean_remark,
                    "evidence_location": clean_location,
                }
            ),
        },
    )
    conn.commit()
    return {
        "rule_key": rule_key,
        "status": normalized_status,
        "remark": clean_remark,
        "evidence_location": clean_location,
        "updated_at": now,
    }


def clear_rule_review(conn: sqlite3.Connection, case_id: str, rule_key: str) -> None:
    conn.execute(
        "DELETE FROM case_rule_reviews WHERE case_id = ? AND rule_key = ?",
        (case_id, rule_key),
    )
    conn.commit()


def review_payload(db_path: Path, case_id: str, validation_report: dict[str, Any]) -> dict[str, Any]:
    final_report = apply_rule_reviews(db_path, case_id, validation_report)
    elements = []
    for element in final_report.get("element_results", []):
        if (
            not element.get("required_for_submission_level")
            and int(element.get("evidence_counts", {}).get("primary_chunks", 0) or 0) == 0
        ):
            continue
        checkpoints = []
        for checkpoint in element.get("checkpoint_results", []):
            checkpoints.append(
                {
                    **checkpoint,
                    "locations": _evidence_locations(checkpoint.get("evidence", [])),
                }
            )
        elements.append(
            {
                **element,
                "checkpoint_results": checkpoints,
                "status_summary": _status_counts(checkpoints),
            }
        )
    return {
        "case_id": case_id,
        "summary": final_report.get("summary", {}),
        "elements": elements,
    }


def apply_rule_reviews(
    db_path: Path,
    case_id: str,
    validation_report: dict[str, Any],
) -> dict[str, Any]:
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM case_rule_reviews WHERE case_id = ?",
            (case_id,),
        ).fetchall()
    overrides = {row["rule_key"]: dict(row) for row in rows}
    merged = copy.deepcopy(validation_report)
    standard_id = merged.get("summary", {}).get("standard_id") or DEFAULT_STANDARD_ID

    for element in merged.get("element_results", []):
        for index, checkpoint in enumerate(element.get("checkpoint_results", []), start=1):
            checkpoint.setdefault(
                "rule_key",
                _fallback_rule_key(standard_id, int(element.get("element_number", 0)), index),
            )
            ai_status = checkpoint.get("ai_status") or checkpoint.get("status", "NOT_FOUND")
            ai_reason = checkpoint.get("ai_reason") or checkpoint.get("reason", "")
            checkpoint["ai_status"] = ai_status
            checkpoint["ai_reason"] = ai_reason
            override = overrides.get(checkpoint.get("rule_key"))
            if override:
                checkpoint["human_status"] = override["status"]
                checkpoint["human_remark"] = override["remark"]
                checkpoint["human_evidence_location"] = override["evidence_location"]
                checkpoint["reviewed_at"] = override["updated_at"]
                checkpoint["status"] = override["status"]
                checkpoint["reason"] = override["remark"]
                checkpoint["final_decision_source"] = "HUMAN"
                checkpoint["final_evidence_location"] = override["evidence_location"]
            else:
                checkpoint["human_status"] = None
                checkpoint["human_remark"] = ""
                checkpoint["human_evidence_location"] = ""
                checkpoint["final_decision_source"] = "AI"
                checkpoint["final_evidence_location"] = _first_location(checkpoint.get("evidence", []))

        primary_count = int(element.get("evidence_counts", {}).get("primary_chunks", 0) or 0)
        if primary_count == 0:
            element["element_status"] = (
                "NOT_FOUND" if element.get("required_for_submission_level") else "N/A"
            )
        else:
            statuses = {
                checkpoint.get("status")
                for checkpoint in element.get("checkpoint_results", [])
            }
            element["element_status"] = (
                "REVIEW" if statuses.intersection({"FLAG", "NOT_FOUND"}) else "PASS"
            )

    summary = merged.setdefault("summary", {})
    statuses = [item.get("element_status") for item in merged.get("element_results", [])]
    if "NOT_FOUND" in statuses:
        summary["overall_status"] = "NOT_FOUND"
    elif "REVIEW" in statuses:
        summary["overall_status"] = "REVIEW"
    elif statuses and all(status == "N/A" for status in statuses):
        summary["overall_status"] = "N/A"
    else:
        summary["overall_status"] = "PASS"
    summary["human_override_count"] = len(overrides)
    summary["review_elements"] = [
        item["element_number"]
        for item in merged.get("element_results", [])
        if item.get("element_status") == "REVIEW"
    ]
    summary["missing_or_unresolved_elements"] = [
        item["element_number"]
        for item in merged.get("element_results", [])
        if item.get("element_status") in {"NOT_FOUND", "REVIEW"}
    ]
    return merged


def _status_counts(checkpoints: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(checkpoints),
        "pass": sum(1 for item in checkpoints if item.get("status") == "PASS"),
        "flag": sum(1 for item in checkpoints if item.get("status") == "FLAG"),
        "not_found": sum(1 for item in checkpoints if item.get("status") == "NOT_FOUND"),
        "human_overrides": sum(1 for item in checkpoints if item.get("final_decision_source") == "HUMAN"),
    }


def _evidence_locations(evidence: list[dict[str, Any]]) -> list[dict[str, str]]:
    locations = []
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        locations.append(
            {
                "file_name": str(item.get("file_name", "")),
                "unit_id": str(item.get("unit_id", "")),
                "quote": str(item.get("quote", "")),
            }
        )
    return locations


def _first_location(evidence: list[dict[str, Any]]) -> str:
    locations = _evidence_locations(evidence)
    if not locations:
        return ""
    first = locations[0]
    source = " / ".join(value for value in (first["file_name"], first["unit_id"]) if value)
    return f"{source}: {first['quote']}".strip(": ")


def _fallback_rule_key(standard_id: str, element_number: int, index: int) -> str:
    if standard_id == DEFAULT_STANDARD_ID:
        return f"E{element_number:02d}:BASE:{index}"
    return f"{standard_id.upper()}:V{element_number:02d}:BASE:{index}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
