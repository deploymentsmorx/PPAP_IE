# Editable validation rule library.

import copy
import json
import re
import uuid
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from .database import connect, init_db
from .db import DbConnection
from .standards.profiles import DEFAULT_STANDARD_ID, artifact_prefix, normalize_standard_id, standard_display_name

if TYPE_CHECKING:
    from .validation.checkpoints import CheckpointCatalog


ELEMENT_SHORT_CODES = {
    1: "DR",
    2: "ECD",
    3: "CEA",
    4: "DFMEA",
    5: "PFD",
    6: "PFMEA",
    7: "CP",
    8: "MSA",
    9: "DIM",
    10: "MPT",
    11: "IPS",
    12: "LAB",
    13: "AAR",
    14: "SPP",
    15: "MS",
    16: "CA",
    17: "CSR",
    18: "PSW",
}


def configured_catalog(
    db_path=None,
    source: "CheckpointCatalog | None" = None,
    standard_id: str = DEFAULT_STANDARD_ID,
) -> "CheckpointCatalog":
    standard_id = normalize_standard_id(standard_id)
    init_db(db_path)
    catalog = source or _default_catalog(standard_id)
    with closing(connect(db_path)) as conn:
        sync_rule_catalog(conn, catalog, standard_id)
        rows = list_rule_rows(conn, include_disabled=False, standard_id=standard_id)
    return _catalog_from_rows(catalog, rows)


def build_rule_library(
    conn: DbConnection,
    source: "CheckpointCatalog | None" = None,
    standard_id: str = DEFAULT_STANDARD_ID,
) -> dict[str, Any]:
    standard_id = normalize_standard_id(standard_id)
    catalog = source or _default_catalog(standard_id)
    sync_rule_catalog(conn, catalog, standard_id)
    rows = list_rule_rows(conn, include_disabled=True, standard_id=standard_id)
    by_element: dict[int, list[dict[str, Any]]] = {number: [] for number in catalog.elements}
    for row in rows:
        by_element[int(row["element_number"])].append(_public_rule(row))

    elements = []
    for number in catalog.sequence():
        source_element = catalog.element(number)
        rules = by_element[number]
        elements.append(
            {
                "element_number": number,
                "element_name": source_element.get("element_name", ""),
                "short_code": _short_code(standard_id, number),
                "applicability_rule": source_element.get("applicability_rule", ""),
                "rule_count": len(rules),
                "enabled_rule_count": sum(1 for rule in rules if rule["enabled"]),
                "rules": rules,
            }
        )
    return {
        "schema_version": "4.0",
        "standard_id": standard_id,
        "standard_name": standard_display_name(standard_id),
        "artifact_prefix": artifact_prefix(standard_id),
        "counts": {
            "elements": len(catalog.elements),
            "rules": len(rows),
            "enabled_rules": sum(1 for row in rows if bool(row["enabled"])),
            "disabled_rules": sum(1 for row in rows if not bool(row["enabled"])),
        },
        "elements": elements,
    }


def sync_rule_catalog(
    conn: DbConnection,
    catalog: "CheckpointCatalog | None" = None,
    standard_id: str = DEFAULT_STANDARD_ID,
) -> None:
    standard_id = normalize_standard_id(standard_id)
    catalog = catalog or _default_catalog(standard_id)
    now = _utc_now()
    for element in catalog.payload.get("elements", []):
        number = int(element["element_number"])
        for position, rule in enumerate(element.get("rules", []), start=1):
            rule_id = str(rule.get("rule_id") or position)
            conn.execute(
                """
                INSERT OR IGNORE INTO validation_rules (
                    rule_key, standard_id, element_number, rule_id, position, description,
                    related_elements_json, enabled, is_custom, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)
                """,
                (
                    _base_rule_key(standard_id, number, rule_id),
                    standard_id,
                    number,
                    rule_id,
                    position,
                    _clean_instruction(rule.get("agent_instruction", "")),
                    json.dumps(rule.get("related_element_numbers", [])),
                    now,
                    now,
                ),
            )
    conn.commit()


def list_rule_rows(conn: DbConnection, include_disabled: bool, standard_id: str) -> list[dict[str, Any]]:
    standard_id = normalize_standard_id(standard_id)
    extra = "" if include_disabled else "AND enabled = 1"
    return conn.execute(
        f"""
        SELECT * FROM validation_rules
        WHERE standard_id = ?
        {extra}
        ORDER BY element_number, position, rule_id
        """,
        (standard_id,),
    ).fetchall()


def add_rule(
    conn: DbConnection,
    standard_id: str,
    element_number: int,
    description: str,
    related_elements: list[int],
    enabled: bool = True,
) -> dict[str, Any]:
    standard_id = normalize_standard_id(standard_id)
    number = _validate_element_number(standard_id, element_number)
    clean_description = _validate_description(description)
    related = _validate_related_elements(standard_id, related_elements, number)
    row = conn.execute(
        "SELECT COALESCE(MAX(position), 0) AS max_position FROM validation_rules WHERE standard_id = ? AND element_number = ?",
        (standard_id, number),
    ).fetchone()
    position = int(row["max_position"]) + 1
    existing_ids = {
        int(item["rule_id"])
        for item in conn.execute(
            "SELECT rule_id FROM validation_rules WHERE standard_id = ? AND element_number = ?",
            (standard_id, number),
        ).fetchall()
        if str(item["rule_id"]).isdigit()
    }
    rule_id = str(max(existing_ids, default=0) + 1)
    rule_key = f"{_rule_key_prefix(standard_id, number)}:CUSTOM:{uuid.uuid4().hex}"
    now = _utc_now()
    conn.execute(
        """
        INSERT INTO validation_rules (
            rule_key, standard_id, element_number, rule_id, position, description,
            related_elements_json, enabled, is_custom, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            rule_key,
            standard_id,
            number,
            rule_id,
            position,
            clean_description,
            json.dumps(related),
            1 if enabled else 0,
            now,
            now,
        ),
    )
    conn.commit()
    return _public_rule(_get_rule_row(conn, rule_key))


def update_rule(
    conn: DbConnection,
    rule_key: str,
    description: str,
    related_elements: list[int],
    enabled: bool,
) -> dict[str, Any]:
    existing = _get_rule_row(conn, rule_key)
    standard_id = existing["standard_id"]
    number = int(existing["element_number"])
    conn.execute(
        """
        UPDATE validation_rules
        SET description = ?, related_elements_json = ?, enabled = ?, updated_at = ?
        WHERE rule_key = ?
        """,
        (
            _validate_description(description),
            json.dumps(_validate_related_elements(standard_id, related_elements, number)),
            1 if enabled else 0,
            _utc_now(),
            rule_key,
        ),
    )
    conn.commit()
    return _public_rule(_get_rule_row(conn, rule_key))


def _catalog_from_rows(catalog: "CheckpointCatalog", rows: list[dict[str, Any]]) -> "CheckpointCatalog":
    configured = copy.deepcopy(catalog)
    by_element: dict[int, list[dict[str, Any]]] = {number: [] for number in configured.elements}
    for row in rows:
        related = json.loads(row["related_elements_json"] or "[]")
        by_element[int(row["element_number"])].append(
            {
                "rule_key": row["rule_key"],
                "rule_id": str(row["rule_id"]),
                "agent_instruction": row["description"],
                "related_element_numbers": related,
                "must_return_evidence": True,
            }
        )
    for number, element in configured.elements.items():
        element["rules"] = by_element[number]
    configured.payload["counts"]["rules"] = len(rows)
    return configured


def _public_rule(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule_key": row["rule_key"],
        "standard_id": row["standard_id"],
        "rule_id": str(row["rule_id"]),
        "element_number": int(row["element_number"]),
        "description": _clean_instruction(row["description"]),
        "referenced_elements": json.loads(row["related_elements_json"] or "[]"),
        "enabled": bool(row["enabled"]),
        "is_custom": bool(row["is_custom"]),
    }


def _default_catalog(standard_id: str = DEFAULT_STANDARD_ID) -> "CheckpointCatalog":
    from .validation.checkpoints import CheckpointCatalog

    return CheckpointCatalog(standard_id=standard_id)


def _get_rule_row(conn: DbConnection, rule_key: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM validation_rules WHERE rule_key = ?",
        (rule_key,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Unknown rule: {rule_key}")
    return row


def _base_rule_key(standard_id: str, element_number: int, rule_id: str) -> str:
    return f"{_rule_key_prefix(standard_id, element_number)}:BASE:{rule_id}"


def _validate_element_number(standard_id: str, value: int) -> int:
    number = int(value)
    catalog = _default_catalog(standard_id)
    if number not in catalog.elements:
        raise ValueError(f"element_number must be valid for {standard_display_name(standard_id)}.")
    return number


def _validate_description(value: str) -> str:
    text = " ".join(str(value or "").split())
    if len(text) < 12:
        raise ValueError("Rule description must contain at least 12 characters.")
    if len(text) > 2000:
        raise ValueError("Rule description must not exceed 2000 characters.")
    return text


def _validate_related_elements(standard_id: str, values: list[int], current: int) -> list[int]:
    related = sorted({int(value) for value in values if int(value) != current})
    catalog = _default_catalog(standard_id)
    if any(value not in catalog.elements for value in related):
        raise ValueError(f"Referenced artifacts must be valid for {standard_display_name(standard_id)}.")
    return related


def _short_code(standard_id: str, number: int) -> str:
    if standard_id == DEFAULT_STANDARD_ID:
        return ELEMENT_SHORT_CODES.get(number, f"E{number:02d}")
    return f"{artifact_prefix(standard_id)}{number:02d}"


def _rule_key_prefix(standard_id: str, number: int) -> str:
    code = f"E{number:02d}" if standard_id == DEFAULT_STANDARD_ID else _short_code(standard_id, number)
    if standard_id == DEFAULT_STANDARD_ID:
        return code
    return f"{standard_id.upper()}:{code}"


def _clean_instruction(value: str) -> str:
    text = str(value or "").strip()
    if text.lower().startswith("task:"):
        text = text[5:].strip()
    text = re.split(r"\bVerdict logic\s*:", text, maxsplit=1, flags=re.IGNORECASE)[0]
    text = re.sub(r"\b(PASS|FLAG|NOT_FOUND)\s*:\s*[^.;]+[.;]?", "", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
