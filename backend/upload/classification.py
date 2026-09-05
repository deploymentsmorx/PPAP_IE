# Lightweight file classification for uploaded PPAP files.

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path


READ_BYTES = 512 * 1024


@dataclass(frozen=True)
class FileClassification:
    detected_type: str
    type_confidence: float
    routing_lane: str
    digital_status: str = "not_applicable"
    unit_count: int | None = None
    unit_label: str | None = None


def classify_file(path: Path, filename: str) -> FileClassification:
    extension = Path(filename).suffix.lower()
    header = read_header(path)

    if header.startswith(b"%PDF"):
        return classify_pdf(path)
    if header.startswith(b"\xff\xd8\xff"):
        return FileClassification("jpeg_image", 0.99, "image_lane", unit_count=1, unit_label="image")
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return FileClassification("png_image", 0.99, "image_lane", unit_count=1, unit_label="image")
    if header[:4] in (b"II*\x00", b"MM\x00*"):
        return FileClassification("tiff_image", 0.99, "image_lane", unit_count=1, unit_label="image")
    if header.startswith(b"PK\x03\x04") and zipfile.is_zipfile(path):
        return classify_zip_container(path, extension)
    if is_probably_text(header):
        return FileClassification("text", 0.7, "text_lane", unit_count=1, unit_label="file")

    return FileClassification("unknown", 0.0, "manual_review")


def classify_pdf(path: Path) -> FileClassification:
    data = read_header(path)
    page_count = estimate_pdf_page_count(path)
    has_text_layer_hint = b"/Font" in data or b"BT" in data or b"/ToUnicode" in data
    digital_status = "likely_digital" if has_text_layer_hint else "likely_scanned"
    routing_lane = "pdf_text_lane" if has_text_layer_hint else "ocr_lane"
    return FileClassification("pdf", 0.99, routing_lane, digital_status, page_count, "page")


def classify_zip_container(path: Path, extension: str) -> FileClassification:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())

        if "[Content_Types].xml" in names:
            if any(name.startswith("xl/") for name in names):
                return FileClassification("excel_workbook", 0.98, "excel_lane", unit_count=count_excel_sheets(archive), unit_label="sheet")
            if any(name.startswith("word/") for name in names):
                return FileClassification("word_document", 0.98, "word_lane", unit_count=count_word_sections(archive), unit_label="section")
            if any(name.startswith("ppt/") for name in names):
                return FileClassification("powerpoint_deck", 0.98, "powerpoint_lane", unit_count=count_powerpoint_slides(names), unit_label="slide")

    if extension == ".zip":
        return FileClassification("zip_archive", 0.99, "archive_lane")
    return FileClassification("zip_container_unknown", 0.65, "manual_review")


def count_excel_sheets(archive: zipfile.ZipFile) -> int | None:
    try:
        xml = archive.read("xl/workbook.xml").decode("utf-8", errors="ignore")
    except KeyError:
        return None
    return max(1, len(re.findall(r"<sheet\b", xml)))


def count_word_sections(archive: zipfile.ZipFile) -> int | None:
    try:
        xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    except KeyError:
        return None
    return max(1, len(re.findall(r"<w:sectPr\b", xml)) or 1)


def count_powerpoint_slides(names: set[str]) -> int | None:
    count = sum(1 for name in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", name))
    return count or None


def estimate_pdf_page_count(path: Path) -> int | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    count = len(re.findall(rb"/Type\s*/Page\b", data))
    return count or None


def is_probably_text(data: bytes) -> bool:
    if not data:
        return True
    if b"\x00" in data:
        return False
    sample = data[:4096]
    printable = sum(1 for byte in sample if byte in b"\r\n\t" or 32 <= byte <= 126)
    return printable / len(sample) > 0.85


def read_header(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(READ_BYTES)
