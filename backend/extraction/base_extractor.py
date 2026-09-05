# Shared document extraction helpers.

from pathlib import Path


def create_document_template(file_path):
    path = Path(file_path)

    return {
        "document": {
            "file_name": path.name,
            "file_type": "",
            "document_type": "",
            "extension": path.suffix.lower(),
            "file_size": path.stat().st_size
        },

        "metadata": {},

        "text": [],
        "paragraphs": [],
        "headings": [],

        "tables": [],
        "images": [],

        "worksheets": [],
        "charts": [],
        "merged_cells": [],
        "formulas": [],
        "named_ranges": [],
        "pivot_tables": [],

        "headers": [],
        "footers": [],
        "sections": [],
        "comments": [],
        "bookmarks": [],
        "fields": [],
        "content_controls": [],
        "track_changes": [],

        "links": [],
        "hyperlinks": [],
        "annotations": [],
        "form_fields": [],
        "embedded_files": [],

        "errors": [],
        "slides": [],
        "titles": [],
        "notes": [],
        "shapes": [],
        "smartart": [],
        "embedded_objects": [],
        "videos": [],
        "audio": [],
        "image_properties": {},
        "exif": {},
        "ocr": [],
        "qr_codes": [],
        "barcodes": [],
        "thumbnail": {},
        "image_hash": {},
        "dominant_colors": [],

        "file_properties": {},
        "parts": [],
        "assemblies": [],
        "solids": [],
        "surfaces": [],
        "edges": [],
        "vertices": [],
        "bounding_box": {},
        "volume": {},
        "surface_area": {},
        "center_of_mass": {},
        "materials": [],
        "colors": [],
        "layers": [],
        "pmi": [],
        "preview": {},
        "file_properties": {},
        "parts": [],
        "assemblies": [],
        "solids": [],
        "surfaces": [],
        "edges": [],
        "vertices": [],
        "bounding_box": {},
        "volume": {},
        "surface_area": {},
        "center_of_mass": {},
        "materials": [],
        "colors": [],
        "layers": [],
        "pmi": [],
        "preview": {},
    }
