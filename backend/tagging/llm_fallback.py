# Anthropic fallback for low-confidence tags.

from typing import Any

from ..anthropic_client import AnthropicJsonClient


class LlmFallbackClient:
    def __init__(
        self,
        elements,
        match_element_name,
        model,
        url,
        text_limit,
        timeout_seconds,
        max_output_tokens,
        api_key=None,
    ):
        self.elements = elements
        self.match_element_name = match_element_name
        self.text_limit = text_limit
        self.timeout_seconds = timeout_seconds
        self.client = AnthropicJsonClient(
            model=model,
            api_key=api_key,
            url=url,
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
            temperature=0,
        )
        self.model = self.client.model

    def empty_result(self):
        return {
            "used": False,
            "model": self.model,
            "predicted_element": None,
            "confidence": 0,
            "reason": "",
            "error": None,
        }

    def classify(self, filename, text, filename_prediction, content_prediction, all_scores):
        fallback = self.empty_result()
        fallback["used"] = True

        prompt = self._build_prompt(
            filename,
            text,
            filename_prediction,
            content_prediction,
            all_scores,
        )

        try:
            parsed = self._parse_response(self.client.validate(prompt))
        except (RuntimeError, ValueError, TypeError) as exc:
            fallback["error"] = str(exc)
            return fallback

        if not parsed:
            fallback["error"] = "LLM response did not contain a valid artifact."
            return fallback

        fallback.update(parsed)
        return fallback

    def _build_prompt(
        self,
        filename,
        text,
        filename_prediction,
        content_prediction,
        all_scores,
    ):
        element_lines = []
        for element, cfg in self.elements.items():
            number = cfg.get("element_number")
            description = cfg.get("description", "")
            element_lines.append(f"{number}. {element}: {description}")

        top_scores = sorted(all_scores.items(), key=lambda item: item[1], reverse=True)[:5]

        return f"""
You are classifying one extracted submission document chunk into exactly one allowed artifact.

Use these rules:
- Prefer the chunk heading, sheet name, slide title, or page title when it clearly names an artifact.
- Ignore cross-reference matrices that merely list all artifacts.
- If the chunk is shared metadata, choose the closest actual artifact only when evidence supports it.
- Use filename only as supporting evidence, not as the main evidence when chunk content is clear.

Allowed artifacts:
{chr(10).join(element_lines)}

Filename:
{filename}

Filename rule result:
{filename_prediction or "None"}

Content rule result:
{content_prediction or "None"}

Top keyword scores:
{top_scores}

Chunk text:
{text[:self.text_limit]}

Return only JSON in this exact shape:
{{"predicted_element": "exact allowed element name or null", "confidence": 80, "reason": "short reason"}}
""".strip()

    def _parse_response(self, data: dict[str, Any]):
        if not isinstance(data, dict):
            return None
        element = self.match_element_name(data.get("predicted_element"))
        if not element:
            return None

        try:
            confidence = int(data.get("confidence", 80))
        except (TypeError, ValueError):
            confidence = 80

        return {
            "predicted_element": element,
            "confidence": max(0, min(confidence, 100)),
            "reason": str(data.get("reason", "")).strip(),
        }
