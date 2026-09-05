# Tag cleanup and confidence scoring.

class PredictionPostProcessorMixin:
    def _fill_continuity_and_fallbacks(self, predictions, units, filename, filename_elements, extraction_failed):
        known = [
            (index, prediction["predicted_element"])
            for index, prediction in enumerate(predictions)
            if prediction.get("predicted_element") and not prediction.get("shared_context")
        ]

        for index, prediction in enumerate(predictions):
            if prediction.get("shared_context") or prediction.get("predicted_element"):
                continue

            previous_element = next((element for pos, element in reversed(known) if pos < index), None)
            next_element = next((element for pos, element in known if pos > index), None)

            if previous_element and next_element and previous_element != next_element:
                nearby_result = self._nearby_content_result(prediction, {previous_element, next_element})
                if nearby_result:
                    self._apply_result(prediction, nearby_result)
                else:
                    self._mark_shared_context(prediction, "Shared context between different element groups.")
                continue

            inherited = previous_element or next_element
            if inherited:
                self._apply_inherited_prediction(prediction, inherited, "inherited_context", 66)

        for prediction, unit in zip(predictions, units):
            if prediction.get("shared_context"):
                continue
            if prediction["predicted_element"]:
                continue

            backup = self._file_backup_prediction(filename_elements, prediction["content_prediction"])
            if backup:
                self._apply_result(prediction, backup)
                continue

            if extraction_failed or not self._normalize(unit["text"]):
                prediction["llm_fallback"]["error"] = "Skipped: extraction failed or chunk has no text."
                continue

            if prediction["content_score"] <= 0 and not filename_elements:
                prediction["llm_fallback"]["error"] = "Skipped: no meaningful rule evidence."
                continue

            if not self.llm_enabled:
                prediction["llm_fallback"]["error"] = "Skipped: OpenAI tagging fallback disabled."
                continue

            fallback = self._llm_fallback(
                filename,
                unit["text"],
                filename_elements[0] if filename_elements else None,
                prediction["content_prediction"],
                prediction["all_scores"],
            )
            prediction["llm_fallback"] = fallback
            if fallback.get("predicted_element"):
                result = {
                    "element": fallback["predicted_element"],
                    "source": "llm_fallback",
                    "confidence": fallback["confidence"],
                    "evidence": fallback.get("reason", ""),
                    "review_required": False,
                }
                self._apply_result(prediction, result)

    def _nearby_content_result(self, prediction, allowed_elements):
        element = prediction.get("content_prediction")
        if (
            element not in allowed_elements
            or prediction.get("content_score", 0) < 12
            or prediction.get("score_margin", 0) < 4
        ):
            return None
        return {
            "element": element,
            "source": "content_nearby",
            "confidence": 72,
            "evidence": prediction.get("matched_keywords", {}),
            "review_required": False,
        }

    def _mark_shared_context(self, prediction, evidence):
        prediction["shared_context"] = True
        prediction["source"] = "shared_context"
        prediction["confidence"] = 70
        prediction["evidence"] = evidence
        prediction["review_required"] = False

    def _apply_inherited_prediction(self, prediction, element, source, confidence):
        result = {
            "element": element,
            "source": source,
            "confidence": confidence,
            "evidence": "No new heading found; inherited nearby element.",
            "review_required": False,
        }
        self._apply_result(prediction, result)

    def _apply_result(self, prediction, result):
        element = result["element"]
        prediction["predicted_element"] = element
        prediction["element_number"] = self.elements[element]["element_number"]
        prediction["source"] = result["source"]
        prediction["confidence"] = result["confidence"]
        prediction["evidence"] = result["evidence"]
        prediction["review_required"] = result["review_required"]

    def _group_predictions(self, predictions):
        groups = []
        shared_context = [
            {
                "unit_id": prediction["unit_id"],
                "label": prediction["label"],
                "source": prediction["source"],
            }
            for prediction in predictions
            if prediction.get("shared_context")
        ]

        for prediction in predictions:
            element = prediction.get("predicted_element")
            if not element:
                continue

            if groups and groups[-1]["element"] == element:
                group = groups[-1]
                group["units"].append(prediction["unit_id"])
                group["labels"].append(prediction["label"])
                group["confidence"] = min(group["confidence"], prediction["confidence"])
                group["sources"].append(prediction["source"])
                continue

            groups.append(
                {
                    "element": element,
                    "element_number": self.elements[element]["element_number"],
                    "units": [prediction["unit_id"]],
                    "labels": [prediction["label"]],
                    "confidence": prediction["confidence"],
                    "sources": [prediction["source"]],
                }
            )

        for group in groups:
            group["sources"] = sorted(set(group["sources"]))
            group["shared_units"] = [item["unit_id"] for item in shared_context]
            group["shared_labels"] = [item["label"] for item in shared_context]

        return groups

    def _summary_status(self, groups, predictions):
        if not groups:
            return "Needs Review", True

        unresolved = any(
            not item.get("predicted_element")
            and not item.get("shared_context")
            for item in predictions
        )
        llm_failed = any(
            item["llm_fallback"].get("used")
            and not item["llm_fallback"].get("predicted_element")
            for item in predictions
        )
        gamma_required = unresolved or llm_failed

        if gamma_required:
            return "Pending Gamma Verification", True

        distinct = {group["element"] for group in groups}
        if len(distinct) > 1:
            return "Multi-element Verified", False
        return "Verified", False
