# Routes uploaded files to the right extractor.

from pathlib import Path

from .base_extractor import create_document_template
from .excel_extractor import extract as extract_excel
from .image_extractor import extract as extract_image
from .output_paths import reset_output_context, set_output_context
from .pdf_extractor import extract as extract_pdf
from .ppt_extractor import extract as extract_ppt
from .word_extractor import extract as extract_word


EXTRACTORS = {
    ".pdf": extract_pdf,
    ".xlsx": extract_excel,
    ".xls": extract_excel,
    ".xlsm": extract_excel,
    ".docx": extract_word,
    ".ppt": extract_ppt,
    ".pptx": extract_ppt,
    ".jpg": extract_image,
    ".jpeg": extract_image,
    ".png": extract_image,
    ".txt": None,
}


def extract_text(file_path):
    document = create_document_template(file_path)
    document["document"]["file_type"] = "text"

    path = Path(file_path)
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = path.read_text(encoding="cp1252", errors="replace")

    document["text"].append({"text": content})
    document["paragraphs"] = [
        {"paragraph_number": index, "text": line.strip(), "style": "Plain Text"}
        for index, line in enumerate(content.splitlines(), start=1)
        if line.strip()
    ]
    return document


def extract_document(file_path, data_dir=None, case_id=None):
    token = set_output_context(data_dir=data_dir, case_id=case_id)
    try:
        return _extract_document(file_path)
    finally:
        reset_output_context(token)


def _extract_document(file_path):
    extension = Path(file_path).suffix.lower()
    extractor = EXTRACTORS.get(extension)

    if extension == ".txt":
        return extract_text(file_path)
    if extractor:
        return extractor(file_path)

    document = create_document_template(file_path)
    document["document"]["file_type"] = "unsupported"
    document["errors"].append({"stage": "routing", "error": f"Unsupported file type: {extension}"})
    return document
