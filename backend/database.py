# Schema and persistence helpers (Postgres / SQLite via db.connect).

from __future__ import annotations

from pathlib import Path
from typing import Any

from .db import DbConnection, connect

SCHEMA_SQLITE = """
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
    due_date TEXT NOT NULL DEFAULT '',
    s3_prefix TEXT NOT NULL DEFAULT ''
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
    s3_key TEXT,
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

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    customer_id TEXT,
    user_kind TEXT NOT NULL DEFAULT 'platform'
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    license_key TEXT NOT NULL UNIQUE,
    install_password_salt TEXT NOT NULL,
    install_password_hash TEXT NOT NULL,
    device_id TEXT NOT NULL DEFAULT '',
    host_name TEXT NOT NULL DEFAULT '',
    require_device INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customer_engineers (
    engineer_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    device_id TEXT NOT NULL DEFAULT '',
    host_name TEXT NOT NULL DEFAULT '',
    grant_full_access INTEGER NOT NULL DEFAULT 1,
    role TEXT NOT NULL DEFAULT 'quality',
    must_change_password INTEGER NOT NULL DEFAULT 1,
    activated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (customer_id, email),
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);
"""

SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    standard_id TEXT NOT NULL DEFAULT 'aiag_ppap',
    source_type TEXT NOT NULL,
    source_names TEXT NOT NULL,
    submission_level INTEGER NOT NULL DEFAULT 3,
    layout_type TEXT NOT NULL DEFAULT 'unknown',
    layout_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
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
    due_date TEXT NOT NULL DEFAULT '',
    s3_prefix TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS files (
    file_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    created_at TEXT NOT NULL,
    filename TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    stored_path TEXT,
    source_container TEXT,
    extension TEXT NOT NULL,
    mime_type TEXT,
    detected_type TEXT NOT NULL DEFAULT 'unknown',
    type_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
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
    s3_key TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    event_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    created_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT NOT NULL
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
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    standard_id TEXT NOT NULL DEFAULT 'aiag_ppap',
    rule_key TEXT NOT NULL REFERENCES validation_rules(rule_key),
    element_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    remark TEXT NOT NULL,
    evidence_location TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (case_id, standard_id, rule_key)
);

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    customer_id TEXT,
    user_kind TEXT NOT NULL DEFAULT 'platform'
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    license_key TEXT NOT NULL UNIQUE,
    install_password_salt TEXT NOT NULL,
    install_password_hash TEXT NOT NULL,
    device_id TEXT NOT NULL DEFAULT '',
    host_name TEXT NOT NULL DEFAULT '',
    require_device INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customer_engineers (
    engineer_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    full_name TEXT NOT NULL,
    email TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    device_id TEXT NOT NULL DEFAULT '',
    host_name TEXT NOT NULL DEFAULT '',
    grant_full_access INTEGER NOT NULL DEFAULT 1,
    role TEXT NOT NULL DEFAULT 'quality',
    must_change_password INTEGER NOT NULL DEFAULT 1,
    activated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (customer_id, email)
);
"""


def init_db(db_path: Path | None = None) -> None:
    from .auth import ensure_auth_tables

    conn = connect(db_path)
    try:
        if conn.backend == "postgres":
            conn.executescript(SCHEMA_POSTGRES)
        else:
            conn.executescript(SCHEMA_SQLITE)
        run_migrations(conn)
        ensure_auth_tables(conn)
        conn.commit()
    finally:
        conn.close()


def run_migrations(conn: DbConnection) -> None:
    ensure_column(conn, "cases", "standard_id", "TEXT NOT NULL DEFAULT 'aiag_ppap'")
    ensure_column(conn, "cases", "submission_level", "INTEGER NOT NULL DEFAULT 3")
    ensure_column(conn, "cases", "layout_type", "TEXT NOT NULL DEFAULT 'unknown'")
    ensure_column(conn, "cases", "layout_confidence", "DOUBLE PRECISION NOT NULL DEFAULT 0.0" if conn.backend == "postgres" else "REAL NOT NULL DEFAULT 0.0")
    ensure_column(conn, "cases", "ppap_id", "TEXT")
    ensure_column(conn, "cases", "customer_name", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "cases", "part_number", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "cases", "part_name", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "cases", "part_revision", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "cases", "supplier_name", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "cases", "program_name", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "cases", "submission_date", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "cases", "due_date", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "cases", "s3_prefix", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "files", "detected_type", "TEXT NOT NULL DEFAULT 'unknown'")
    ensure_column(conn, "files", "type_confidence", "DOUBLE PRECISION NOT NULL DEFAULT 0.0" if conn.backend == "postgres" else "REAL NOT NULL DEFAULT 0.0")
    ensure_column(conn, "files", "routing_lane", "TEXT NOT NULL DEFAULT 'unknown'")
    ensure_column(conn, "files", "digital_status", "TEXT NOT NULL DEFAULT 'not_applicable'")
    ensure_column(conn, "files", "unit_count", "INTEGER")
    ensure_column(conn, "files", "unit_label", "TEXT")
    ensure_column(conn, "files", "archive_depth", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "files", "s3_key", "TEXT")
    ensure_column(conn, "users", "must_change_password", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "users", "customer_id", "TEXT")
    ensure_column(conn, "users", "user_kind", "TEXT NOT NULL DEFAULT 'platform'")
    ensure_column(conn, "case_rule_reviews", "standard_id", "TEXT NOT NULL DEFAULT 'aiag_ppap'")
    ensure_column(conn, "customer_engineers", "role", "TEXT NOT NULL DEFAULT 'quality'")
    backfill_ppap_ids(conn)
    normalize_legacy_statuses(conn)
    backfill_engineer_roles(conn)


def backfill_ppap_ids(conn: DbConnection) -> None:
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


def backfill_engineer_roles(conn: DbConnection) -> None:
    """Pre-migration rows only carried grant_full_access; map that onto the new role column."""
    conn.execute(
        "UPDATE customer_engineers SET role = 'full_access' WHERE role = 'quality' AND grant_full_access = 1"
    )


def normalize_legacy_statuses(conn: DbConnection) -> None:
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


def ensure_column(conn: DbConnection, table_name: str, column_name: str, definition: str) -> None:
    if conn.backend == "postgres":
        row = conn.execute(
            """
            SELECT 1 AS present
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ? AND column_name = ?
            """,
            (table_name, column_name),
        ).fetchone()
        if row:
            return
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
        return
    columns = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    if any(column["name"] == column_name for column in columns):
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def insert_case(conn: DbConnection, record: dict[str, Any]) -> None:
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
        "s3_prefix": "",
        **record,
    }
    conn.execute(
        """
        INSERT INTO cases (
            case_id, created_at, standard_id, source_type, source_names, submission_level,
            layout_type, layout_confidence, raw_dir, work_dir, status,
            ppap_id, customer_name, part_number, part_name, part_revision, supplier_name,
            program_name, submission_date, due_date, s3_prefix
        )
        VALUES (
            :case_id, :created_at, :standard_id, :source_type, :source_names, :submission_level,
            :layout_type, :layout_confidence, :raw_dir, :work_dir, :status,
            :ppap_id, :customer_name, :part_number, :part_name, :part_revision, :supplier_name,
            :program_name, :submission_date, :due_date, :s3_prefix
        )
        """,
        record,
    )


def insert_file(conn: DbConnection, record: dict[str, Any]) -> None:
    record = {**record}
    record.setdefault("s3_key", None)
    conn.execute(
        """
        INSERT INTO files (
            file_id, case_id, created_at, filename, relative_path, stored_path,
            source_container, extension, mime_type, detected_type, type_confidence,
            routing_lane, digital_status, unit_count, unit_label, archive_depth,
            size_bytes, sha256,
            is_duplicate, duplicate_of, ignored, ignore_reason, processing_status, s3_key
        )
        VALUES (
            :file_id, :case_id, :created_at, :filename, :relative_path, :stored_path,
            :source_container, :extension, :mime_type, :detected_type, :type_confidence,
            :routing_lane, :digital_status, :unit_count, :unit_label, :archive_depth,
            :size_bytes, :sha256,
            :is_duplicate, :duplicate_of, :ignored, :ignore_reason, :processing_status, :s3_key
        )
        """,
        record,
    )


def update_case_layout(conn: DbConnection, case_id: str, layout_type: str, layout_confidence: float) -> None:
    conn.execute(
        """
        UPDATE cases
        SET layout_type = ?, layout_confidence = ?
        WHERE case_id = ?
        """,
        (layout_type, layout_confidence, case_id),
    )


def insert_audit_event(conn: DbConnection, record: dict[str, Any]) -> None:
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


def next_audit_id(conn: DbConnection, case_id: str) -> str:
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM audit_log WHERE case_id = ?",
        (case_id,),
    ).fetchone()
    return f"{case_id}-event-{int(row['count']) + 1:03d}"


def get_case_summary(conn: DbConnection, case_id: str) -> dict[str, Any] | None:
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
