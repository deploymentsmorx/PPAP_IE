# Rule library and human review tests.

import tempfile
import unittest
import sqlite3
from dataclasses import replace
from pathlib import Path

from backend.config import Settings
from backend.database import connect, init_db, insert_case
from backend.review import apply_rule_reviews, save_rule_review
from backend.rule_store import (
    add_rule,
    build_rule_library,
    configured_catalog,
    update_rule,
)


class RuleAndReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.settings = replace(Settings(project_root=self.root))
        self.settings.ensure_dirs()
        init_db(self.settings.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_rules_can_be_disabled_edited_and_added(self) -> None:
        conn = connect(self.settings.db_path)
        try:
            library = build_rule_library(conn)
            first = library["elements"][0]["rules"][0]
            self.assertEqual(library["counts"]["elements"], 18)
            self.assertIn("Locate a design record", first["description"])
            self.assertNotIn("Verdict logic", first["description"])
            self.assertNotIn("PASS:", first["description"])
            update_rule(conn, first["rule_key"], "Confirm the design record is present and readable.", [], False)
            custom = add_rule(conn, "aiag_ppap", 1, "Confirm the customer drawing reference is recorded.", [18])
        finally:
            conn.close()

        catalog = configured_catalog(self.settings.db_path)
        keys = {rule["rule_key"] for rule in catalog.element(1)["rules"]}
        self.assertNotIn(first["rule_key"], keys)
        self.assertIn(custom["rule_key"], keys)
        self.assertEqual(custom["referenced_elements"], [18])

    def test_vda_rule_library_uses_v_artifacts(self) -> None:
        conn = connect(self.settings.db_path)
        try:
            library = build_rule_library(conn, standard_id="vda_ppf")
            custom = add_rule(conn, "vda_ppf", 19, "Confirm the VDA warrant decision is signed.", [1])
        finally:
            conn.close()

        self.assertEqual(library["standard_id"], "vda_ppf")
        self.assertEqual(library["artifact_prefix"], "V")
        self.assertEqual(library["counts"]["elements"], 19)
        self.assertEqual(library["counts"]["enabled_rules"], 152)
        self.assertTrue(custom["rule_key"].startswith("VDA_PPF:V19:CUSTOM:"))

    def test_human_review_overrides_ai_and_allows_optional_location(self) -> None:
        conn = connect(self.settings.db_path)
        try:
            build_rule_library(conn)
            insert_case(
                conn,
                {
                    "case_id": "case-review",
                    "created_at": "2026-07-10T00:00:00+00:00",
                    "source_type": "loose_files",
                    "source_names": "[]",
                    "submission_level": 1,
                    "layout_type": "loose_files",
                    "layout_confidence": 1.0,
                    "raw_dir": str(self.root),
                    "work_dir": str(self.root),
                    "status": "registered",
                },
            )
            conn.commit()
            save_rule_review(
                conn,
                "case-review",
                "E18:BASE:1",
                "FLAG",
                "Part number differs from the drawing.",
                "",
            )
        finally:
            conn.close()

        validation = {
            "summary": {"overall_status": "PASS"},
            "element_results": [
                {
                    "element_number": 18,
                    "element_status": "PASS",
                    "required_for_submission_level": True,
                    "evidence_counts": {"primary_chunks": 1},
                    "checkpoint_results": [
                        {
                            "rule_key": "E18:BASE:1",
                            "rule_id": "1",
                            "status": "PASS",
                            "reason": "AI found the PSW.",
                            "evidence": [],
                        }
                    ],
                }
            ],
        }
        merged = apply_rule_reviews(self.settings.db_path, "case-review", validation)
        checkpoint = merged["element_results"][0]["checkpoint_results"][0]
        self.assertEqual(checkpoint["status"], "FLAG")
        self.assertEqual(checkpoint["final_decision_source"], "HUMAN")
        self.assertEqual(merged["summary"]["overall_status"], "REVIEW")

    def test_legacy_review_table_is_rebuilt_after_rule_table_migration(self) -> None:
        db_path = self.root / "legacy_reviews.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(
                """
                PRAGMA foreign_keys = OFF;
                CREATE TABLE cases (
                    case_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_names TEXT NOT NULL,
                    submission_level INTEGER NOT NULL DEFAULT 3,
                    layout_type TEXT NOT NULL DEFAULT 'unknown',
                    layout_confidence REAL NOT NULL DEFAULT 0.0,
                    raw_dir TEXT NOT NULL,
                    work_dir TEXT NOT NULL,
                    status TEXT NOT NULL
                );
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
                );
                CREATE TABLE case_rule_reviews (
                    case_id TEXT NOT NULL,
                    rule_key TEXT NOT NULL,
                    element_number INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    remark TEXT NOT NULL,
                    evidence_location TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (case_id, rule_key),
                    FOREIGN KEY (case_id) REFERENCES cases(case_id),
                    FOREIGN KEY (rule_key) REFERENCES "validation_rules_old"(rule_key)
                );
                INSERT INTO cases (
                    case_id, created_at, source_type, source_names, submission_level,
                    layout_type, layout_confidence, raw_dir, work_dir, status
                ) VALUES (
                    'legacy-case', '2026-07-10T00:00:00+00:00', 'loose_files', '[]', 1,
                    'loose_files', 1.0, '.', '.', 'registered'
                );
                INSERT INTO validation_rules (
                    rule_key, standard_id, element_number, rule_id, position, description,
                    related_elements_json, enabled, is_custom, created_at, updated_at
                ) VALUES (
                    'E18:BASE:1', 'aiag_ppap', 18, '1', 1, 'Confirm the warrant is present.',
                    '[]', 1, 0, '2026-07-10T00:00:00+00:00', '2026-07-10T00:00:00+00:00'
                );
                INSERT INTO case_rule_reviews (
                    case_id, rule_key, element_number, status, remark, evidence_location, updated_at
                ) VALUES (
                    'legacy-case', 'E18:BASE:1', 18, 'PASS', 'Old review.', '', '2026-07-10T00:00:00+00:00'
                );
                """
            )
            conn.commit()
        finally:
            conn.close()

        init_db(db_path)
        conn = connect(db_path)
        try:
            foreign_keys = conn.execute("PRAGMA foreign_key_list(case_rule_reviews)").fetchall()
            self.assertEqual(
                [row["table"] for row in foreign_keys if row["from"] == "rule_key"],
                ["validation_rules"],
            )
            saved = save_rule_review(
                conn,
                "legacy-case",
                "E18:BASE:1",
                "FLAG",
                "Updated review after migration.",
                "",
            )
        finally:
            conn.close()

        self.assertEqual(saved["status"], "FLAG")


if __name__ == "__main__":
    unittest.main()
