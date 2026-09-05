# SQLite schema and small persistence helpers.

import sqlite3
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    standard_id TEXT NOT NULL DEFAULT 'aiag_ppap',
    source_type TEXT NOT NULL,
    source_names TEXT NOT NULL,
    submission_level INTEGER NOT NULL DEFAULT 3,
    layout_type TEXT NOT NULL DEFAULT 'unknown',
    layout_confidence REAL NOT NULL DEFAULT 0.0,
    raw_dir TEXT NOT NULL,
    work_dir TEXT NOT NULL,
    status TEXT NOT NULL,
    ppap_id TEXT,
    customer_name TEXT NOT NULL DEFAULT '',
    part_number TEXT NOT NULL DEFAULT '',
    part_name TEXT NOT NULL DEFAULT '',
    part_revision TEXT NOT NULL DEFAULT '',
    supplier_name TEXT NOT NULL DEFAULT '',
    program_name TEXT NOT NULL DEFAULT '',
    submission_date TEXT NOT NULL DEFAULT '',
    due_date TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS files (
    file_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    filename TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    stored_path TEXT,
    source_container TEXT,
    extension TEXT NOT NULL,
    mime_type TEXT,
    detected_type TEXT NOT NULL DEFAULT 'unknown',
    type_confidence REAL NOT NULL DEFAULT 0.0,
    routing_lane TEXT NOT NULL DEFAULT 'unknown',
    digital_status TEXT NOT NULL DEFAULT 'not_applicable',
    unit_count INTEGER,
    unit_label TEXT,
    archive_depth INTEGER NOT NULL DEFAULT 0,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT,
    is_duplicate INTEGER NOT NULL DEFAULT 0,
    duplicate_of TEXT,
    ignored INTEGER NOT NULL DEFAULT 0,
    ignore_reason TEXT,
    processing_status TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES cases(case_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    event_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES cases(case_id)
);

CREATE TABLE IF NOT EXISTS validation_rules (
    rule_key TEXT PRIMARY KEY,
    standard_id TEXT NOT NULL DEFAULT 'aiag_ppap',
    element_number INTEGER NOT NULL,
    rule_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    description TEXT NOT NULL,
    related_elements_json TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1,
    is_custom INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (standard_id, element_number, rule_id)
);

CREATE TABLE IF NOT EXISTS case_rule_reviews (
    case_id TEXT NOT NULL,
    standard_id TEXT NOT NULL DEFAULT 'aiag_ppap',
    rule_key TEXT NOT NULL,
    element_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    remark TEXT NOT NULL,
    evidence_location TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (case_id, standard_id, rule_key),
    FOREIGN KEY (case_id) REFERENCES cases(case_id),
    FOREIGN KEY (rule_key) REFERENCES validation_rules(rule_key)
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        run_migrations(conn)
        conn.commit()
    finally:
        conn.close()


def run_migrations(conn: sqlite3.Connection) -> None:
    ensure_column(conn, "cases", "standard_id", "TEXT NOT NULL DEFAULT 'aiag_ppap'")
    ensure_column(conn, "cases", "submission_level", "INTEGER NOT NULL DEFAULT 3")
    ensure_column(conn, "cases", "layout_type", "TEXT NOT NULL DEFAULT 'unknown'")
    ensure_column(conn, "cases", "layout_confidence", "REAL NOT NULL DEFAULT 0.0")
    ensure_column(conn, "cases", "ppap_id", "TEXT")
    ensure_column(conn, "cases", "customer_name", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "cases", "part_number", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "cases", "part_name", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "cases", "part_revision", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "cases", "supplier_name", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "cases", "program_name", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "cases", "submission_date", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "cases", "due_date", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "files", "detected_type", "TEXT NOT NULL DEFAULT 'unknown'")
    ensure_column(conn, "files", "type_confidence", "REAL NOT NULL DEFAULT 0.0")
    ensure_column(conn, "files", "routing_lane", "TEXT NOT NULL DEFAULT 'unknown'")
    ensure_column(conn, "files", "digital_status", "TEXT NOT NULL DEFAULT 'not_applicable'")
    ensure_column(conn, "files", "unit_count", "INTEGER")
    ensure_column(conn, "files", "unit_label", "TEXT")
    ensure_column(conn, "files", "archive_depth", "INTEGER NOT NULL DEFAULT 0")
    rebuild_validation_rules_if_needed(conn)
    ensure_column(conn, "case_rule_reviews", "standard_id", "TEXT NOT NULL DEFAULT 'aiag_ppap'")
    rebuild_case_rule_reviews_if_needed(conn)
    backfill_ppap_ids(conn)
    normalize_legacy_statuses(conn)


def backfill_ppap_ids(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT case_id, created_at, ppap_id FROM cases ORDER BY created_at, case_id"
    ).fetchall()
    used: set[str] = set()
    for row in rows:
        existing = row["ppap_id"]
        if existing:
            used.add(str(existing))
            continue
        year = 2026
        created = str(row["created_at"] or "")
        if len(created) >= 4 and created[:4].isdigit():
            year = int(created[:4])
        seq = 1
        while True:
            candidate = f"PPAP-{year}-{seq:05d}"
            if candidate not in used:
                break
            seq += 1
        used.add(candidate)
        conn.execute(
            "UPDATE cases SET ppap_id = ? WHERE case_id = ?",
            (candidate, row["case_id"]),
        )


def normalize_legacy_statuses(conn: sqlite3.Connection) -> None:
    mapping = {
        "registered": "draft",
        "processing": "in_review",
        "processed": "in_review",
        "validated": "in_review",
        "complete": "approved",
        "completed": "approved",
        "ready": "in_review",
        "error": "failed",
    }
    for old, new in mapping.items():
        conn.execute("UPDATE cases SET status = ? WHERE LOWER(status) = ?", (new, old))


def ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
    columns = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    if any(column["name"] == column_name for column in columns):
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def rebuild_validation_rules_if_needed(conn: sqlite3.Connection) -> None:
    columns = conn.execute("PRAGMA table_info(validation_rules)").fetchall()
    if not columns:
        return
    if not any(column["name"] == "standard_id" for column in columns):
        conn.execute("ALTER TABLE validation_rules ADD COLUMN standard_id TEXT NOT NULL DEFAULT 'aiag_ppap'")

    indexes = conn.execute("PRAGMA index_list(validation_rules)").fetchall()
    has_standard_unique = False
    for index in indexes:
        if not index["unique"]:
            continue
        fields = [
            row["name"]
            for row in conn.execute(f"PRAGMA index_info({index['name']})").fetchall()
        ]
        if fields == ["standard_id", "element_number", "rule_id"]:
            has_standard_unique = True
            break
    if has_standard_unique:
        return

    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("ALTER TABLE validation_rules RENAME TO validation_rules_old")
        conn.execute(
            """
            CREATE TABLE validation_rules (
                rule_key TEXT PRIMARY KEY,
                standard_id TEXT NOT NULL DEFAULT 'aiag_ppap',
                element_number INTEGER NOT NULL,
                rule_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                description TEXT NOT NULL,
                related_elements_json TEXT NOT NULL DEFAULT '[]',
                enabled INTEGER NOT NULL DEFAULT 1,
                is_custom INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (standard_id, element_number, rule_id)
            )
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO validation_rules (
                rule_key, standard_id, element_number, rule_id, position, description,
                related_elements_json, enabled, is_custom, created_at, updated_at
            )
            SELECT
                rule_key, COALESCE(standard_id, 'aiag_ppap'), element_number, rule_id,
                position, description, related_elements_json, enabled, is_custom,
                created_at, updated_at
            FROM validation_rules_old
            """
        )
        conn.execute("DROP TABLE validation_rules_old")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def rebuild_case_rule_reviews_if_needed(conn: sqlite3.Connection) -> None:
    columns = conn.execute("PRAGMA table_info(case_rule_reviews)").fetchall()
    if not columns:
        return

    pk_columns = [
        column["name"]
        for column in sorted(
            (column for column in columns if column["pk"]),
            key=lambda column: column["pk"],
        )
    ]
    foreign_keys = conn.execute("PRAGMA foreign_key_list(case_rule_reviews)").fetchall()
    rule_fk_tables = [
        row["table"]
        for row in foreign_keys
        if row["from"] == "rule_key"
    ]
    has_current_rule_fk = rule_fk_tables == ["validation_rules"]
    has_standard_primary_key = pk_columns == ["case_id", "standard_id", "rule_key"]
    if has_current_rule_fk and has_standard_primary_key:
        return

    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("ALTER TABLE case_rule_reviews RENAME TO case_rule_reviews_old")
        conn.execute(
            """
            CREATE TABLE case_rule_reviews (
                case_id TEXT NOT NULL,
                standard_id TEXT NOT NULL DEFAULT 'aiag_ppap',
                rule_key TEXT NOT NULL,
                element_number INTEGER NOT NULL,
                status TEXT NOT NULL,
                remark TEXT NOT NULL,
                evidence_location TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (case_id, standard_id, rule_key),
                FOREIGN KEY (case_id) REFERENCES cases(case_id),
                FOREIGN KEY (rule_key) REFERENCES validation_rules(rule_key)
            )
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO case_rule_reviews (
                case_id, standard_id, rule_key, element_number, status, remark, evidence_location, updated_at
            )
            SELECT
                old.case_id,
                COALESCE(old.standard_id, rules.standard_id, 'aiag_ppap'),
                old.rule_key,
                old.element_number,
                old.status,
                old.remark,
                old.evidence_location,
                old.updated_at
            FROM case_rule_reviews_old AS old
            JOIN cases ON cases.case_id = old.case_id
            JOIN validation_rules AS rules ON rules.rule_key = old.rule_key
            """
        )
        conn.execute("DROP TABLE case_rule_reviews_old")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def insert_case(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    record = {
        "standard_id": "aiag_ppap",
        "ppap_id": None,
        "customer_name": "",
        "part_number": "",
        "part_name": "",
        "part_revision": "",
        "supplier_name": "",
        "program_name": "",
        "submission_date": "",
        "due_date": "",
        **record,
    }
    conn.execute(
        """
        INSERT INTO cases (
            case_id, created_at, standard_id, source_type, source_names, submission_level,
            layout_type, layout_confidence, raw_dir, work_dir, status,
            ppap_id, customer_name, part_number, part_name, part_revision, supplier_name,
            program_name, submission_date, due_date
        )
        VALUES (
            :case_id, :created_at, :standard_id, :source_type, :source_names, :submission_level,
            :layout_type, :layout_confidence, :raw_dir, :work_dir, :status,
            :ppap_id, :customer_name, :part_number, :part_name, :part_revision, :supplier_name,
            :program_name, :submission_date, :due_date
        )
        """,
        record,
    )


def insert_file(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO files (
            file_id, case_id, created_at, filename, relative_path, stored_path,
            source_container, extension, mime_type, detected_type, type_confidence,
            routing_lane, digital_status, unit_count, unit_label, archive_depth,
            size_bytes, sha256,
            is_duplicate, duplicate_of, ignored, ignore_reason, processing_status
        )
        VALUES (
            :file_id, :case_id, :created_at, :filename, :relative_path, :stored_path,
            :source_container, :extension, :mime_type, :detected_type, :type_confidence,
            :routing_lane, :digital_status, :unit_count, :unit_label, :archive_depth,
            :size_bytes, :sha256,
            :is_duplicate, :duplicate_of, :ignored, :ignore_reason, :processing_status
        )
        """,
        record,
    )


def update_case_layout(conn: sqlite3.Connection, case_id: str, layout_type: str, layout_confidence: float) -> None:
    conn.execute(
        """
        UPDATE cases
        SET layout_type = ?, layout_confidence = ?
        WHERE case_id = ?
        """,
        (layout_type, layout_confidence, case_id),
    )


def insert_audit_event(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO audit_log (
            event_id, case_id, created_at, event_type, message, details_json
        )
        VALUES (
            :event_id, :case_id, :created_at, :event_type, :message, :details_json
        )
        """,
        record,
    )


def next_audit_id(conn: sqlite3.Connection, case_id: str) -> str:
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM audit_log WHERE case_id = ?",
        (case_id,),
    ).fetchone()
    return f"{case_id}-event-{int(row['count']) + 1:03d}"


def get_case_summary(conn: sqlite3.Connection, case_id: str) -> dict[str, Any] | None:
    case_row = conn.execute(
        "SELECT * FROM cases WHERE case_id = ?",
        (case_id,),
    ).fetchone()
    if case_row is None:
        return None

    file_rows = conn.execute(
        """
        SELECT * FROM files
        WHERE case_id = ?
        ORDER BY created_at, filename
        """,
        (case_id,),
    ).fetchall()
    files = [dict(row) for row in file_rows]
    audit_rows = conn.execute(
        """
        SELECT * FROM audit_log
        WHERE case_id = ?
        ORDER BY created_at
        """,
        (case_id,),
    ).fetchall()

    return {
        "case": dict(case_row),
        "files": files,
        "audit_log": [dict(row) for row in audit_rows],
        "counts": {
            "total": len(files),
            "registered": sum(1 for file in files if file["processing_status"] == "registered"),
            "ignored": sum(1 for file in files if file["ignored"]),
            "duplicates": sum(1 for file in files if file["is_duplicate"]),
            "nested_archives": sum(1 for file in files if file["archive_depth"] > 0),
        },
    }
