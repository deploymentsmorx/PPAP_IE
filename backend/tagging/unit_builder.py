# Builds taggable units from extracted documents.

class UnitBuilderMixin:
    def _build_units(self, doc):
        file_type = self._normalize(doc.get("document", {}).get("file_type", ""))
        if file_type == "excel" or doc.get("worksheets"):
            return self._build_excel_units(doc)
        if file_type == "word" or doc.get("headings"):
            return self._build_word_units(doc)
        if file_type == "powerpoint" or doc.get("slides"):
            return self._build_powerpoint_units(doc)
        if file_type == "pdf" or isinstance(doc.get("text"), dict):
            return self._build_pdf_units(doc)
        return self._build_whole_document_unit(doc)

    def _build_excel_units(self, doc):
        sheets = []
        for sheet in doc.get("worksheets", []):
            if isinstance(sheet, dict) and sheet.get("sheet_name"):
                sheets.append(sheet["sheet_name"])

        tables_by_sheet = {}
        for table in doc.get("tables", []):
            sheet = table.get("sheet") or "Workbook"
            tables_by_sheet.setdefault(sheet, []).append(table)
            if sheet not in sheets:
                sheets.append(sheet)

        units = []
        for index, sheet in enumerate(sheets, start=1):
            parts = [sheet]
            for table in tables_by_sheet.get(sheet, []):
                parts.append(self._table_text(table))
            units.append(
                {
                    "unit_id": f"sheet:{sheet}",
                    "unit_type": "worksheet",
                    "label": sheet,
                    "label_source": "sheet_name",
                    "text": self._join_limited(parts),
                    "order": index,
                }
            )

        return units or self._build_whole_document_unit(doc)

    def _build_word_units(self, doc):
        headings = [
            item.get("text", "")
            for item in doc.get("headings", [])
            if isinstance(item, dict) and item.get("text")
        ]
        if not headings:
            return self._build_whole_document_unit(doc)

        paragraphs = [
            item
            for item in doc.get("paragraphs", [])
            if isinstance(item, dict) and item.get("text")
        ]
        tables = doc.get("tables", [])
        units = []

        for index, heading in enumerate(headings, start=1):
            parts = [heading]
            in_section = False
            next_heading = headings[index] if index < len(headings) else None

            for paragraph in paragraphs:
                text = paragraph.get("text", "")
                if text == heading:
                    in_section = True
                    parts.append(text)
                    continue
                if next_heading and text == next_heading:
                    in_section = False
                if in_section:
                    parts.append(text)

            if index <= len(tables):
                parts.append(self._table_text(tables[index - 1]))

            units.append(
                {
                    "unit_id": f"heading:{index}",
                    "unit_type": "heading_section",
                    "label": heading,
                    "label_source": "heading",
                    "text": self._join_limited(parts),
                    "order": index,
                }
            )

        return units

    def _build_powerpoint_units(self, doc):
        slide_numbers = []
        for slide in doc.get("slides", []):
            number = slide.get("slide_number") if isinstance(slide, dict) else None
            if number is not None and number not in slide_numbers:
                slide_numbers.append(number)

        text_by_slide = {}
        for key in ["text", "shapes", "notes"]:
            for item in doc.get(key, []):
                if not isinstance(item, dict):
                    continue
                number = item.get("slide_number")
                if number is None:
                    continue
                text = item.get("text") or item.get("notes") or item.get("title") or ""
                if text:
                    text_by_slide.setdefault(number, []).append(text)
                    if number not in slide_numbers:
                        slide_numbers.append(number)

        units = []
        for number in sorted(slide_numbers):
            parts = text_by_slide.get(number, [])
            label = next((part for part in parts if str(part).strip()), f"Slide {number}")
            units.append(
                {
                    "unit_id": f"slide:{number}",
                    "unit_type": "slide",
                    "label": label,
                    "label_source": "slide_title",
                    "text": self._join_limited(parts),
                    "order": number,
                }
            )

        return units or self._build_whole_document_unit(doc)

    def _build_pdf_units(self, doc):
        pages = set()
        text_by_page = {}
        text_obj = doc.get("text", {})

        if isinstance(text_obj, dict):
            for source in ["digital", "ocr"]:
                for page in text_obj.get(source, []) or []:
                    if not isinstance(page, dict):
                        continue
                    number = page.get("page") or page.get("page_number") or 1
                    pages.add(number)
                    parts = []
                    self._append_value(parts, page.get("blocks"))
                    self._append_value(parts, page.get("lines"))
                    self._append_value(parts, page.get("text"))
                    text_by_page.setdefault(number, []).extend(parts)
        else:
            self._append_value(text_by_page.setdefault(1, []), text_obj)
            pages.add(1)

        for table in doc.get("tables", []):
            number = table.get("page") or 1
            pages.add(number)
            text_by_page.setdefault(number, []).append(self._table_text(table))

        units = []
        for number in sorted(pages):
            parts = text_by_page.get(number, [])
            label = f"Page {number}"
            first_text = next((part for part in parts if str(part).strip()), "")
            if first_text and len(self._normalize(first_text)) < 120:
                label = first_text
            units.append(
                {
                    "unit_id": f"page:{number}",
                    "unit_type": "page",
                    "label": label,
                    "label_source": "page_title",
                    "text": self._join_limited(parts),
                    "order": number,
                }
            )

        return units or self._build_whole_document_unit(doc)

    def _build_whole_document_unit(self, doc):
        parts = []
        for key in [
            "headings",
            "titles",
            "text",
            "paragraphs",
            "headers",
            "footers",
            "comments",
            "shapes",
            "ocr",
            "tables",
        ]:
            self._append_value(parts, doc.get(key))

        label = self._document_label(doc) or self._first_short_text(parts) or "Document"
        return [
            {
                "unit_id": "document:1",
                "unit_type": "document",
                "label": label,
                "label_source": "document",
                "text": self._join_limited(parts),
                "order": 1,
            }
        ]
