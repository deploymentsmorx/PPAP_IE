# Keyword and rule based PPAP element prediction.

import re


class PredictionRuleMixin:
    def _element_aliases(self, element):
        cfg = self.elements[element]
        aliases = [element]
        aliases.extend(cfg.get("aliases", []))
        aliases.extend(cfg.get("filenames", []))
        return sorted(set(aliases), key=len, reverse=True)

    def _find_numbered_elements(self, label):
        normalized = self._normalize(label)
        matches = []

        prefixes = ["e", "element", "artifact", "item"]
        if getattr(self, "standard_id", "") == "vda_ppf":
            prefixes.extend(["v", "vda", "vda artifact", "vda element"])
        else:
            prefixes.extend(["ppap", "ppap artifact", "ppap element"])

        prefix_pattern = "|".join(re.escape(prefix) for prefix in prefixes)
        for match in re.finditer(rf"\b(?:{prefix_pattern})\s*0?(\d{{1,2}})\b", normalized):
            element = self.element_by_number.get(int(match.group(1)))
            if element:
                matches.append(element)

        ordered = []
        for element in matches:
            if element not in ordered:
                ordered.append(element)
        return ordered

    def _find_label_elements(self, label):
        normalized = self._normalize(label)
        numbered_matches = self._find_numbered_elements(label)
        alias_matches = []

        for element in self.elements:
            for alias in self._element_aliases(element):
                alias_norm = self._normalize(alias)
                if len(alias_norm) <= 2 and alias_norm not in {"cp", "psw"}:
                    continue
                if (
                    len(alias_norm.split()) == 1
                    and alias_norm not in self.STRONG_SINGLE_WORD_ALIASES
                ):
                    continue
                if self._close_label_match(normalized, alias_norm):
                    alias_matches.append(element)
                    break

        matches = numbered_matches + alias_matches
        non_number_aliases = [
            element
            for element in alias_matches
            if element not in numbered_matches
        ]
        if (
            len(numbered_matches) == 1
            and len(non_number_aliases) == 1
            and not normalized.startswith("element ")
        ):
            matches = non_number_aliases + numbered_matches + alias_matches

        ordered = []
        for element in matches:
            if element not in ordered:
                ordered.append(element)
        return ordered

    def _predict_from_label(self, label, source):
        elements = self._find_label_elements(label)
        if not elements:
            return None

        return {
            "element": elements[0],
            "source": source,
            "confidence": 96,
            "evidence": str(label or "").strip(),
            "all_matches": elements,
            "review_required": False,
        }

    def _predict_filename(self, filename):
        elements = self._find_label_elements(filename)
        return elements[0] if elements else None

    def _predict_filenames(self, filename):
        return self._find_label_elements(filename)

    def _predict_content(self, text):
        text = self._normalize(text)
        best_element = None
        best_score = -1
        best_matches = {"high": [], "medium": [], "low": []}
        all_scores = {}

        for element in self.elements:
            cfg = self.keywords.get(element, {})
            score = 0
            matches = {"high": [], "medium": [], "low": []}

            for kw in cfg.get("high_priority", []):
                if self._contains_phrase(text, kw):
                    score += self.HIGH_WEIGHT
                    matches["high"].append(kw)

            for kw in cfg.get("medium_priority", []):
                if self._contains_phrase(text, kw):
                    score += self.MEDIUM_WEIGHT
                    matches["medium"].append(kw)

            for kw in cfg.get("low_priority", []):
                if self._contains_phrase(text, kw):
                    score += self.LOW_WEIGHT
                    matches["low"].append(kw)

            all_scores[element] = score
            if score > best_score:
                best_element = element
                best_score = score
                best_matches = matches

        if best_score <= 0:
            return None, 0, {"high": [], "medium": [], "low": []}, all_scores, 0

        sorted_scores = sorted(all_scores.items(), key=lambda item: item[1], reverse=True)
        second_best_score = sorted_scores[1][1] if len(sorted_scores) > 1 else 0
        score_margin = best_score - second_best_score
        return best_element, best_score, best_matches, all_scores, score_margin

    def _predict_by_signature(self, text):
        signatures = [
            (
                ("Authorized Engineering Change Documents", "Engineering Change Documentation"),
                [
                    "engineering change document",
                    "engineering change notice",
                    "engineering change request",
                    "engineering change order",
                    "technical change",
                    "change approval",
                    "approved change",
                    "ecn",
                    "eco",
                    "ecr",
                ],
                2,
            ),
            (
                ("Customer Engineering Approval", "Customer Technical Approval"),
                [
                    "customer engineering approval",
                    "customer technical approval",
                    "deviation approval",
                    "customer release",
                    "customer sign off",
                    "approved by customer",
                    "customer authorization",
                    "approval decision",
                ],
                2,
            ),
            (
                ("Design FMEA",),
                [
                    "design fmea",
                    "dfmea",
                    "design risk analysis",
                    "design controls",
                    "design verification",
                    "design validation",
                    "design responsibility",
                    "failure mode",
                    "severity",
                    "occurrence",
                    "detection",
                ],
                4,
            ),
            (
                ("Process Flow Diagram",),
                [
                    "process flow",
                    "process sequence",
                    "operation sequence",
                    "process step",
                    "incoming inspection",
                    "outgoing inspection",
                    "material flow",
                    "routing",
                    "workstation",
                    "rework",
                ],
                3,
            ),
            (
                ("Process FMEA",),
                [
                    "process fmea",
                    "pfmea",
                    "process risk analysis",
                    "failure mode",
                    "effect",
                    "severity",
                    "occurrence",
                    "detection",
                    "rpn",
                    "process controls",
                ],
                4,
            ),
            (
                ("Control Plan",),
                [
                    "control plan",
                    "reaction plan",
                    "control method",
                    "sample size",
                    "frequency",
                    "measurement method",
                ],
                4,
            ),
            (
                ("Measurement System Analysis",),
                [
                    "gage r and r",
                    "gauge r and r",
                    "repeatability",
                    "reproducibility",
                    "ndc",
                ],
                3,
            ),
            (
                ("Dimensional Results",),
                ["nominal dimension", "actual dimension", "measured dimension", "tolerance", "inspection result"],
                3,
            ),
            (
                ("Material / Performance Test Results", "Material and Functional Test Results"),
                ["material test", "performance test", "chemical composition", "mechanical properties", "hardness"],
                3,
            ),
            (
                ("Initial Process Studies", "Initial Process Capability"),
                ["cpk", "ppk", "capability index", "process capability", "control chart"],
                2,
            ),
            (
                ("Initial Sample Inspection Report",),
                [
                    "initial sample inspection report",
                    "initial sample report",
                    "first article inspection",
                    "isir",
                    "empb",
                    "sample disposition",
                    "measured result",
                ],
                2,
            ),
            (
                ("Qualified Laboratory Documentation",),
                ["iso 17025", "iso17025", "laboratory accreditation", "certificate of accreditation", "lab certificate"],
                2,
            ),
            (
                ("Appearance Approval Report", "Appearance Approval"),
                [
                    "appearance approval",
                    "appearance report",
                    "aai",
                    "surface approval",
                    "color approval",
                    "grain approval",
                    "gloss approval",
                    "visible surface",
                ],
                2,
            ),
            (
                ("Sample Production Parts",),
                [
                    "sample production parts",
                    "production sample",
                    "sample parts",
                    "initial samples",
                    "sample quantity",
                    "sample lot",
                    "serial sample",
                    "musterteile",
                ],
                2,
            ),
            (
                ("Master Sample", "Reference Sample"),
                [
                    "master sample",
                    "reference sample",
                    "retained sample",
                    "sample retention",
                    "golden sample",
                    "approved sample",
                    "reference part",
                    "grenzmuster",
                ],
                2,
            ),
            (
                ("Checking Aids",),
                [
                    "checking aid",
                    "checking aids",
                    "checking fixture",
                    "inspection fixture",
                    "special gauge",
                    "go no go gauge",
                    "pruefmittel",
                    "lehre",
                ],
                2,
            ),
            (
                ("Customer Specific Requirements",),
                [
                    "customer specific requirements",
                    "customer requirements",
                    "customer requirement",
                    "csr",
                    "scope agreement",
                    "submission scope",
                    "customer form",
                    "customer manual",
                    "special customer requirement",
                    "kundenspezifische anforderungen",
                    "bemusterungsumfang",
                ],
                2,
            ),
            (
                ("Part Submission Warrant", "PPF Submission Warrant"),
                ["part submission warrant", "submission level", "reason for submission", "customer disposition"],
                2,
            ),
            (
                ("Design Record",),
                ["drawing number", "drawing no", "drawing revision", "engineering drawing"],
                2,
            ),
        ]

        best = None
        best_hits = []
        for candidates, phrases, required_hits in signatures:
            element = self._resolve_signature_element(candidates)
            if not element:
                continue
            hits = [phrase for phrase in phrases if self._contains_phrase(text, phrase)]
            if len(hits) >= required_hits and len(hits) > len(best_hits):
                best = element
                best_hits = hits

        if not best:
            return None

        return {
            "element": best,
            "source": "table_signature",
            "confidence": 92,
            "evidence": ", ".join(best_hits),
            "review_required": False,
        }

    def _resolve_signature_element(self, candidates):
        for candidate in candidates:
            if candidate in self.elements:
                return candidate

            normalized_candidate = self._normalize(candidate).replace("/", " ")
            for element, cfg in self.elements.items():
                aliases = [element]
                aliases.extend(cfg.get("aliases", []))
                aliases.extend(cfg.get("filenames", []))
                for alias in aliases:
                    if normalized_candidate == self._normalize(alias).replace("/", " "):
                        return element
        return None

    def _confidence(self, score, filename_match, score_margin):
        if filename_match:
            score += 20
        score += min(score_margin, 20)
        if score >= 80:
            return 99
        if score >= 60:
            return 95
        if score >= 45:
            return 90
        if score >= 30:
            return 80
        if score >= 15:
            return 70
        return 50

    def _content_result_to_prediction(self, content_prediction, score, matches, score_margin):
        if not content_prediction:
            return None

        strong_score = score >= 24 and score_margin >= 8
        medium_score = score >= 16 and score_margin >= 12
        if not (strong_score or medium_score):
            return None

        return {
            "element": content_prediction,
            "source": "content",
            "confidence": self._confidence(score, False, score_margin),
            "evidence": matches,
            "review_required": False,
        }

    def _file_backup_prediction(self, filename_elements, content_prediction=None):
        if not filename_elements:
            return None

        element = filename_elements[0]
        source = "filename"
        confidence = 74

        if content_prediction and content_prediction in filename_elements:
            element = content_prediction
            source = "filename_content_agree"
            confidence = 84

        return {
            "element": element,
            "source": source,
            "confidence": confidence,
            "evidence": filename_elements,
            "review_required": False,
        }

    def _classify_unit(self, unit, filename, filename_elements):
        label_result = self._predict_from_label(unit["label"], unit["label_source"])
        text = self._normalize(unit["text"])
        content_prediction, score, matches, all_scores, score_margin = self._predict_content(text)
        metadata_unit = self._is_metadata_unit(unit, filename) or self._is_shared_context_unit(unit, score, score_margin)

        result = label_result
        if not result and not metadata_unit:
            result = self._predict_by_signature(text)
        if not result and not metadata_unit:
            result = self._content_result_to_prediction(
                content_prediction,
                score,
                matches,
                score_margin,
            )
        if self._should_use_filename_context(result, filename_elements, score, score_margin):
            result = {
                "element": filename_elements[0],
                "source": "filename_context",
                "confidence": 82,
                "evidence": f"Document filename points to {filename_elements[0]}; weak subsection match ignored.",
                "review_required": False,
            }
        shared_context = metadata_unit and not result

        return {
            "unit_id": unit["unit_id"],
            "unit_type": unit["unit_type"],
            "label": unit["label"],
            "predicted_element": result["element"] if result else None,
            "element_number": self.elements[result["element"]]["element_number"] if result else None,
            "source": result["source"] if result else ("shared_context" if shared_context else None),
            "confidence": result["confidence"] if result else (70 if shared_context else 0),
            "evidence": result["evidence"] if result else (
                "Shared metadata/context for nearby element groups."
                if shared_context
                else None
            ),
            "content_prediction": content_prediction,
            "content_score": score,
            "score_margin": score_margin,
            "matched_keywords": matches,
            "all_scores": all_scores,
            "review_required": False if shared_context else (True if not result else result["review_required"]),
            "shared_context": shared_context,
            "llm_fallback": self._empty_llm_result(),
            "text_length": len(text),
        }

    def _is_metadata_unit(self, unit, filename=""):
        label = self._normalize(unit.get("label", ""))
        filename_text = self._normalize(filename)
        text = f"{label} {filename_text}"
        return any(
            marker in text
            for marker in ["metadata", "package index", "readme", "cover sheet", "manifest"]
        )

    def _should_use_filename_context(self, result, filename_elements, score, score_margin):
        if not result or len(filename_elements) != 1 or result["element"] == filename_elements[0]:
            return False
        if result["source"] in {"document", "heading", "page_title", "sheet_name"} and result["confidence"] >= 96:
            return False
        return not (score >= 60 and score_margin >= 20)

    def _is_shared_context_unit(self, unit, score, score_margin):
        text = self._normalize(unit.get("text", ""))
        if not text or score >= 30 or score_margin >= 10:
            return False
        generic_terms = [
            "customer",
            "supplier",
            "part number",
            "revision",
            "date",
            "signature",
            "document number",
            "page",
        ]
        return sum(1 for term in generic_terms if self._contains_phrase(text, term)) >= 3

    def _empty_llm_result(self):
        return self.llm.empty_result()

    def _match_element_name(self, value):
        if value is None:
            return None
        if value in self.elements:
            return value

        label_matches = self._find_label_elements(value)
        if label_matches:
            return label_matches[0]

        normalized_value = self._normalize(value).replace("/", " ")
        number_match = re.search(r"\b0?(\d{1,2})\b", normalized_value)
        if number_match:
            element = self.element_by_number.get(int(number_match.group(1)))
            if element:
                return element

        for element in self.elements:
            for alias in self._element_aliases(element):
                normalized_alias = self._normalize(alias).replace("/", " ")
                if normalized_value == normalized_alias:
                    return element
        return None

    def _llm_fallback(
        self,
        filename,
        text,
        filename_prediction,
        content_prediction,
        all_scores,
    ):
        return self.llm.classify(
            filename,
            text,
            filename_prediction,
            content_prediction,
            all_scores,
        )
