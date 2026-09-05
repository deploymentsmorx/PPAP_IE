# Text normalization helpers for tagging.

import re
from difflib import SequenceMatcher


class TextHelperMixin:
    TEXT_FIRST_KEYS = (
        "text",
        "title",
        "heading",
        "name",
        "value",
        "notes",
        "lines",
        "blocks",
        "header",
        "rows",
        "data",
        "cells",
    )
    LAYOUT_KEYS = {
        "bbox",
        "block",
        "page",
        "page_number",
        "left",
        "top",
        "right",
        "bottom",
        "width",
        "height",
        "x",
        "y",
        "x0",
        "x1",
        "y0",
        "y1",
        "xmin",
        "xmax",
        "ymin",
        "ymax",
        "zmin",
        "zmax",
        "confidence",
    }

    def _normalize(self, text):
        text = str(text or "").lower()
        text = text.replace("&", " and ")
        text = re.sub(r"[_\-\\/]+", " ", text)
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _append_value(self, parts, value):
        if value is None:
            return

        if isinstance(value, (str, int, float, bool)):
            text = str(value).strip()
            if text:
                parts.append(text)
            return

        if isinstance(value, dict):
            text_keys = [
                key
                for key in self.TEXT_FIRST_KEYS
                if key in value and value.get(key) not in (None, "", [])
            ]
            if text_keys:
                for key in text_keys:
                    self._append_value(parts, value.get(key))
                return

            for key, item in value.items():
                if str(key).lower() in self.LAYOUT_KEYS:
                    continue
                self._append_value(parts, item)
            return

        if isinstance(value, (list, tuple)):
            for item in value:
                self._append_value(parts, item)
            return

        text = str(value).strip()
        if text:
            parts.append(text)

    def _table_text(self, table):
        parts = []
        self._append_value(parts, table.get("header"))

        rows = table.get("rows")
        if not isinstance(rows, list):
            rows = table.get("data")
        if isinstance(rows, list):
            for row in rows[:self.MAX_ROWS_PER_TABLE]:
                self._append_value(parts, row)

        return " ".join(parts)

    def _extract_text(self, doc):
        return self._normalize(
            " ".join(unit["text"] for unit in self._build_units(doc))
        )

    def _contains_phrase(self, text, phrase):
        text = self._normalize(text)
        phrase = self._normalize(phrase)
        if not text or not phrase:
            return False

        if len(phrase) <= 2:
            return bool(re.search(rf"(^| ){re.escape(phrase)}( |$)", text))

        if f" {phrase} " in f" {text} ":
            return True

        return phrase.replace(" ", "") in text.replace(" ", "")

    def _close_label_match(self, label, phrase):
        label = self._normalize(label)
        phrase = self._normalize(phrase)
        if not label or not phrase:
            return False

        if self._contains_phrase(label, phrase):
            return True

        label_tokens = set(label.split())
        phrase_tokens = set(phrase.split())
        if len(phrase_tokens) > 1 and phrase_tokens.issubset(label_tokens):
            return True

        if len(label) >= 5 and len(phrase) >= 5:
            return SequenceMatcher(None, label, phrase).ratio() >= 0.86

        return False

    def _join_limited(self, parts):
        text = " ".join(part for part in parts if str(part).strip())
        return text[:self.MAX_TEXT_PER_UNIT]

    def _document_label(self, doc):
        sources = [
            ("headings", ["text", "title"]),
            ("titles", ["title", "text"]),
            ("paragraphs", ["text"]),
            ("text", ["text", "title"]),
        ]

        for key, fields in sources:
            value = doc.get(key)
            items = value if isinstance(value, list) else [value]
            for item in items:
                if isinstance(item, dict):
                    candidates = [item.get(field) for field in fields]
                else:
                    candidates = [item]
                for candidate in candidates:
                    text = str(candidate or "").strip()
                    if self._is_good_label_text(text):
                        return text
        return None

    def _first_short_text(self, parts):
        for part in parts:
            text = str(part or "").strip()
            if self._is_good_label_text(text):
                return text
        return None

    def _is_good_label_text(self, text):
        normalized = self._normalize(text)
        return bool(
            normalized
            and len(normalized) <= 120
            and re.search(r"[a-z]", normalized)
            and normalized not in {"normal", "plain text"}
        )
