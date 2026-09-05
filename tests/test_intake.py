# Upload intake tests.

import sqlite3
import tempfile
import unittest
import zipfile
import json
from dataclasses import replace
from pathlib import Path

from backend.config import Settings
from backend.database import get_case_summary, init_db
from backend.upload.intake import IntakeError, UploadItem, preview_uploads, process_uploads


class IntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.settings = replace(
            Settings(project_root=self.root),
            max_upload_size_bytes=10 * 1024 * 1024,
        )
        self.settings.ensure_dirs()
        init_db(self.settings.db_path)
        self.conn = sqlite3.connect(self.settings.db_path)
        self.conn.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.conn.close()
        self.temp_dir.cleanup()

    def test_zip_upload_registers_real_files_and_marks_junk(self) -> None:
        upload_path = self.root / "supplier_ppap.zip"
        with zipfile.ZipFile(upload_path, "w") as archive:
            archive.writestr("dimensional.xlsx", b"dimensional")
            archive.writestr("nested/cp.xlsx", b"control-plan")
            archive.writestr("thumbs.db", b"junk")
            archive.writestr("readme.txt", b"notes")

        summary = process_uploads(
            [UploadItem(upload_path, "supplier_ppap.zip")],
            self.settings,
            self.conn,
        )

        self.assertEqual(summary["counts"]["total"], 4)
        self.assertEqual(summary["counts"]["registered"], 2)
        self.assertEqual(summary["counts"]["ignored"], 2)

        files = {file["relative_path"]: file for file in summary["files"]}
        self.assertEqual(files["dimensional.xlsx"]["processing_status"], "registered")
        self.assertEqual(files["nested/cp.xlsx"]["processing_status"], "registered")
        self.assertEqual(files["thumbs.db"]["processing_status"], "ignored")
        self.assertIsNone(files["readme.txt"]["stored_path"])
        self.assertEqual(summary["case"]["submission_level"], 3)
        self.assertEqual(summary["case"]["layout_type"], "mixed_flat_and_nested")
        self.assertEqual(summary["case"]["case_id"], "1")

        case_id = summary["case"]["case_id"]
        extracted_files = list((self.settings.extracted_case_dir(case_id) / "json").glob("*.json"))
        tagged_files = list((self.settings.tagging_case_dir(case_id) / "json").glob("*.json"))
        self.assertEqual(len(extracted_files), 2)
        self.assertEqual(len(tagged_files), 2)

        extracted_payload = json.loads(extracted_files[0].read_text(encoding="utf-8"))
        tagged_payload = json.loads(tagged_files[0].read_text(encoding="utf-8"))
        self.assertNotIn("element_tagging", extracted_payload)
        self.assertEqual(set(tagged_payload.keys()), {"document", "extraction_json_path", "element_tagging"})
        self.assertTrue(Path(tagged_payload["extraction_json_path"]).exists())
        self.assertIn("element_tagging", tagged_payload)

    def test_upload_skips_orphan_numeric_case_folder(self) -> None:
        (self.settings.raw_case_dir("1")).mkdir(parents=True)
        upload_path = self.root / "ppf_warrant.txt"
        upload_path.write_text("Submission level Reason for submission Customer disposition", encoding="ascii")

        summary = process_uploads(
            [UploadItem(upload_path, "ppf_warrant.txt")],
            self.settings,
            self.conn,
        )

        self.assertEqual(summary["case"]["case_id"], "2")
        self.assertEqual(summary["counts"]["registered"], 1)

    def test_zip_preview_lists_members_without_processing_case(self) -> None:
        upload_path = self.root / "supplier_ppap.zip"
        with zipfile.ZipFile(upload_path, "w") as archive:
            archive.writestr("dimensional.xlsx", b"dimensional")
            archive.writestr("nested/cp.xlsx", b"control-plan")
            archive.writestr("readme.txt", b"notes")

        preview = preview_uploads(
            [UploadItem(upload_path, "supplier_ppap.zip")],
            self.settings,
            submission_level=3,
        )

        self.assertEqual(preview["source_type"], "zip")
        self.assertEqual(preview["counts"]["total"], 3)
        self.assertEqual(preview["counts"]["processable"], 2)
        self.assertEqual(preview["files"][1]["relative_path"], "nested/cp.xlsx")
        self.assertEqual(preview["files"][1]["source_container"], "supplier_ppap.zip")
        self.assertFalse((self.settings.cases_dir).exists())

    def test_zip_upload_normalizes_parent_directory_members(self) -> None:
        upload_path = self.root / "unsafe.zip"
        with zipfile.ZipFile(upload_path, "w") as archive:
            archive.writestr("../evil.txt", b"outside")

        summary = process_uploads([UploadItem(upload_path, "unsafe.zip")], self.settings, self.conn)

        self.assertEqual(summary["counts"]["registered"], 1)
        self.assertEqual(summary["files"][0]["relative_path"], "evil.txt")
        self.assertFalse((self.root / "evil.txt").exists())

    def test_fake_zip_extension_is_rejected(self) -> None:
        upload_path = self.root / "supplier_ppap.zip"
        upload_path.write_bytes(b"MZ fake executable")

        with self.assertRaises(IntakeError):
            process_uploads([UploadItem(upload_path, "supplier_ppap.zip")], self.settings, self.conn)

    def test_loose_file_duplicate_checksum_tracking_is_not_used(self) -> None:
        first = self.root / "dimensional.xlsx"
        second = self.root / "dimensional_copy.xlsx"
        first.write_bytes(b"same content")
        second.write_bytes(b"same content")

        summary = process_uploads(
            [
                UploadItem(first, "dimensional.xlsx"),
                UploadItem(second, "dimensional_copy.xlsx"),
            ],
            self.settings,
            self.conn,
        )

        self.assertEqual(summary["counts"]["total"], 2)
        self.assertEqual(summary["counts"]["duplicates"], 0)
        self.assertTrue(all(not file["is_duplicate"] for file in summary["files"]))
        self.assertTrue(all(file["sha256"] is None for file in summary["files"]))

    def test_submission_level_is_stored_and_validated(self) -> None:
        upload_path = self.root / "dimensional.xlsx"
        upload_path.write_bytes(b"dimensional")

        summary = process_uploads(
            [UploadItem(upload_path, "dimensional.xlsx")],
            self.settings,
            self.conn,
            submission_level=5,
        )

        self.assertEqual(summary["case"]["submission_level"], 5)
        self.assertEqual(summary["case"]["layout_type"], "loose_files")

        with self.assertRaises(IntakeError):
            process_uploads(
                [UploadItem(upload_path, "dimensional.xlsx")],
                self.settings,
                self.conn,
                submission_level=6,
            )

    def test_vda_upload_extracts_and_tags_warrant_file(self) -> None:
        upload_path = self.root / "ppf_warrant.txt"
        upload_path.write_text(
            "Submission level Reason for submission Customer disposition",
            encoding="ascii",
        )

        summary = process_uploads(
            [UploadItem(upload_path, "ppf_warrant.txt")],
            self.settings,
            self.conn,
            standard_id="vda_ppf",
        )

        case_id = summary["case"]["case_id"]
        tagged_files = list((self.settings.tagging_case_dir(case_id) / "json").glob("*.json"))
        tagged_payload = json.loads(tagged_files[0].read_text(encoding="utf-8"))
        tagging = tagged_payload["element_tagging"]

        self.assertEqual(summary["case"]["standard_id"], "vda_ppf")
        self.assertEqual(summary["counts"]["registered"], 1)
        self.assertEqual(tagging["standard_id"], "vda_ppf")
        self.assertEqual(tagging["predicted_element"], "PPF Submission Warrant")
        self.assertEqual(tagging["element_number"], 19)

    def test_uploaded_case_is_persisted_after_connection_closes(self) -> None:
        upload_path = self.root / "dimensional.xlsx"
        upload_path.write_bytes(b"dimensional")

        summary = process_uploads(
            [UploadItem(upload_path, "dimensional.xlsx")],
            self.settings,
            self.conn,
        )
        case_id = summary["case"]["case_id"]

        self.conn.close()
        self.conn = sqlite3.connect(self.settings.db_path)
        self.conn.row_factory = sqlite3.Row

        persisted = get_case_summary(self.conn, case_id)
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted["counts"]["registered"], 1)

    def test_nested_zip_is_extracted_and_registered(self) -> None:
        nested_zip = self.root / "nested.zip"
        with zipfile.ZipFile(nested_zip, "w") as archive:
            archive.writestr("inner/dimensional.xlsx", b"dimensional")

        upload_path = self.root / "supplier_ppap.zip"
        with zipfile.ZipFile(upload_path, "w") as archive:
            archive.write(nested_zip, "nested.zip")

        summary = process_uploads(
            [UploadItem(upload_path, "supplier_ppap.zip")],
            self.settings,
            self.conn,
        )

        files = {file["relative_path"]: file for file in summary["files"]}
        self.assertIn("nested.zip", files)
        self.assertIn("nested.zip::inner/dimensional.xlsx", files)
        self.assertEqual(files["nested.zip"]["detected_type"], "zip_archive")
        self.assertEqual(files["nested.zip::inner/dimensional.xlsx"]["archive_depth"], 1)
        self.assertEqual(summary["counts"]["nested_archives"], 1)

    def test_phase_two_classifies_file_types_and_routes(self) -> None:
        xlsx_path = self.root / "dimensional.xlsx"
        make_minimal_xlsx(xlsx_path)
        pdf_path = self.root / "drawing.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n1 0 obj<</Type /Page /Font <<>>>>stream\nBT\nET\nendstream")
        text_path = self.root / "notes.txt"
        text_path.write_text("plain text package note", encoding="ascii")

        summary = process_uploads(
            [
                UploadItem(xlsx_path, "dimensional.xlsx"),
                UploadItem(pdf_path, "drawing.pdf"),
                UploadItem(text_path, "notes.txt"),
            ],
            self.settings,
            self.conn,
        )

        files = {file["relative_path"]: file for file in summary["files"]}
        self.assertEqual(files["dimensional.xlsx"]["detected_type"], "excel_workbook")
        self.assertEqual(files["dimensional.xlsx"]["routing_lane"], "excel_lane")
        self.assertEqual(files["dimensional.xlsx"]["unit_count"], 2)
        self.assertEqual(files["drawing.pdf"]["detected_type"], "pdf")
        self.assertEqual(files["drawing.pdf"]["digital_status"], "likely_digital")
        self.assertEqual(files["notes.txt"]["detected_type"], "text")
        self.assertEqual(files["notes.txt"]["routing_lane"], "text_lane")


def make_minimal_xlsx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types></Types>")
        archive.writestr(
            "xl/workbook.xml",
            """
            <workbook>
              <sheets>
                <sheet name="Dimensional" sheetId="1"/>
                <sheet name="Summary" sheetId="2"/>
              </sheets>
            </workbook>
            """,
        )


if __name__ == "__main__":
    unittest.main()
