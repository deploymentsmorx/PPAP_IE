# PDF text and image extraction.

import pymupdf as fitz
from .base_extractor import create_document_template
import pdfplumber
from pdf2image import convert_from_path
import easyocr
from .output_paths import extracted_dir




def extract_metadata(doc):
    """
    Extract PDF metadata.
    """

    metadata = doc.metadata

    return {
        "page_count": len(doc),
        "title": metadata.get("title", ""),
        "author": metadata.get("author", ""),
        "subject": metadata.get("subject", ""),
        "keywords": metadata.get("keywords", ""),
        "creator": metadata.get("creator", ""),
        "producer": metadata.get("producer", ""),
        "creation_date": metadata.get("creationDate", ""),
        "modification_date": metadata.get("modDate", "")
    }


def extract_text(doc):
    """
    Extract text page-wise, block-wise and line-wise.
    """

    pages = []

    for page_number, page in enumerate(doc, start=1):

        page_data = {
            "page": page_number,
            "blocks": []
        }

        blocks = page.get_text("dict")["blocks"]

        block_number = 1

        for block in blocks:

            # Ignore image blocks
            if block["type"] != 0:
                continue

            block_data = {
                "block": block_number,
                "bbox": block["bbox"],
                "lines": []
            }

            for line in block["lines"]:

                line_text = ""

                for span in line["spans"]:
                    line_text += span["text"]

                line_text = line_text.strip()

                if line_text:
                    block_data["lines"].append(line_text)

            if block_data["lines"]:
                page_data["blocks"].append(block_data)
                block_number += 1

        pages.append(page_data)

    return pages

def extract_images(doc, file_path):

    images = []

    output_folder = extracted_dir("images", file_path)

    image_counter = 1

    for page_number, page in enumerate(doc, start=1):

        image_list = page.get_images(full=True)

        for img in image_list:

            xref = img[0]

            pix = fitz.Pixmap(doc, xref)

            if pix.alpha:
                pix = fitz.Pixmap(fitz.csRGB, pix)

            extension = "png"

            image_name = f"page_{page_number}_image_{image_counter}.{extension}"

            image_path = output_folder / image_name

            pix.save(str(image_path))

            images.append({

                "page": page_number,

                "image_number": image_counter,

                "image_name": image_name,

                "image_path": str(image_path),

                "width": pix.width,

                "height": pix.height,

                "colorspace": str(pix.colorspace)

            })

            pix = None

            image_counter += 1

    return images



def extract_tables(file_path):
    """
    Extract all tables from the PDF.
    Stores header separately from table rows.
    """

    extracted_tables = []

    with pdfplumber.open(file_path) as pdf:

        table_number = 1

        for page_number, page in enumerate(pdf.pages, start=1):

            tables = page.extract_tables()

            for table in tables:

                if not table or len(table) == 0:
                    continue

                # Clean table
                cleaned_table = []

                for row in table:

                    if row is None:
                        continue

                    cleaned_row = []

                    for cell in row:

                        if cell is None:
                            cleaned_row.append("")
                        else:
                            cleaned_row.append(str(cell).strip())

                    cleaned_table.append(cleaned_row)

                if not cleaned_table:
                    continue

                header = cleaned_table[0]
                rows = cleaned_table[1:]

                extracted_tables.append({
                    "page": page_number,
                    "table_number": table_number,
                    "header": header,
                    "row_count": len(rows),
                    "column_count": len(header),
                    "rows": rows
                })

                table_number += 1

    return extracted_tables

def extract_links(doc):
    """
    Extract hyperlinks from the PDF.
    """

    links = []

    for page_number, page in enumerate(doc, start=1):

        page_links = page.get_links()

        for index, link in enumerate(page_links, start=1):

            links.append({
                "page": page_number,
                "link_number": index,
                "uri": link.get("uri", ""),
                "kind": link.get("kind", ""),
                "from": list(link.get("from", ()))
            })

    return links

def extract_annotations(doc):
    """
    Extract annotations (comments, highlights, notes, etc.) from the PDF.
    """

    annotations = []

    for page_number, page in enumerate(doc, start=1):

        annot = page.first_annot

        annotation_number = 1

        while annot:

            annotation = {
                "page": page_number,
                "annotation_number": annotation_number,
                "type": annot.type[1],          # e.g., Highlight, Text, Square
                "content": annot.info.get("content", ""),
                "title": annot.info.get("title", ""),
                "subject": annot.info.get("subject", ""),
                "author": annot.info.get("id", ""),
                "creation_date": annot.info.get("creationDate", ""),
                "modification_date": annot.info.get("modDate", "")
            }

            annotations.append(annotation)

            annotation_number += 1

            annot = annot.next

    return annotations

def extract_form_fields(doc):
    """
    Extract fillable form fields (AcroForms) from the PDF.
    """

    form_fields = []

    try:
        widgets = []

        for page_number, page in enumerate(doc, start=1):

            for widget in page.widgets():

                widgets.append({
                    "page": page_number,
                    "field_name": widget.field_name,
                    "field_label": widget.field_label,
                    "field_type": widget.field_type_string,
                    "field_value": widget.field_value,
                    "field_flags": widget.field_flags
                })

        form_fields = widgets

    except Exception as e:
        form_fields.append({
            "error": str(e)
        })

    return form_fields

def extract_embedded_files(doc):
    """
    Extract information about embedded files in the PDF.
    """

    embedded_files = []

    try:
        embedded_count = doc.embfile_count()

        for i in range(embedded_count):

            info = doc.embfile_info(i)

            embedded_files.append({
                "file_number": i + 1,
                "name": info.get("name", ""),
                "filename": info.get("filename", ""),
                "description": info.get("desc", ""),
                "size": info.get("length", 0),
                "creation_date": info.get("creationDate", ""),
                "modification_date": info.get("modDate", "")
            })

    except Exception as e:

        embedded_files.append({
            "error": str(e)
        })

    return embedded_files

def perform_ocr(file_path):
    """
    Perform OCR on scanned PDF pages.
    Returns page-wise extracted text.
    """

    reader = easyocr.Reader(['en'])

    ocr_pages = []

    images = convert_from_path(file_path)

    for page_number, image in enumerate(images, start=1):

        results = reader.readtext(image)

        lines = []

        for result in results:
            lines.append(result[1])

        ocr_pages.append({
            "page": page_number,
            "lines": lines
        })

    return ocr_pages


def extract(file_path):
    """
    Main PDF extractor.
    """

    doc = fitz.open(file_path)

    result = create_document_template(file_path)
    result["document"]["file_type"] = "pdf"
    result["metadata"] = extract_metadata(doc)
    # -------------------------
    # Digital Text Extraction
    # -------------------------

    digital_text = extract_text(doc)

    result["text"] = {
        "source": "digital",
        "digital": digital_text,
        "ocr": []
    }

    # Check if digital text exists
    plain_text = ""

    for page in digital_text:

        for block in page["blocks"]:

            for line in block["lines"]:

                plain_text += line.strip()

    # -------------------------
    # OCR (Only if needed)
    # -------------------------

    if len(plain_text.strip()) < 20:
        ocr_text = perform_ocr(file_path)

        result["text"]["source"] = "ocr"

        result["text"]["ocr"] = ocr_text
    
    result["images"] = extract_images(doc, file_path)
    result["tables"] = extract_tables(file_path)
    result["links"] = extract_links(doc)
    result["annotations"] = extract_annotations(doc)
    result["form_fields"] = extract_form_fields(doc)
    result["embedded_files"] = extract_embedded_files(doc)
    
    

    doc.close()

    return result
