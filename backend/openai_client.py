# OpenAI JSON helpers used by tagging, validation, and reporting.

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from typing import Any

from .env import load_project_env


DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_REQUESTS_PER_MINUTE = 30
DEFAULT_OPENAI_429_RETRY_SECONDS = 10


class _OpenAIRateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._request_times = deque()

    def wait(self, requests_per_minute: int) -> None:
        if requests_per_minute <= 0:
            return

        window_seconds = 60.0
        while True:
            with self._lock:
                now = time.monotonic()
                while self._request_times and now - self._request_times[0] >= window_seconds:
                    self._request_times.popleft()

                if len(self._request_times) < requests_per_minute:
                    self._request_times.append(now)
                    return

                wait_seconds = window_seconds - (now - self._request_times[0])

            time.sleep(max(wait_seconds, 0.1))


_OPENAI_RATE_LIMITER = _OpenAIRateLimiter()


class OpenAIJsonClient:
    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        url: str | None = None,
        timeout_seconds: int | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ):
        load_project_env()
        self.model = (
            model
            or os.getenv("PPAP_OPENAI_MODEL")
            or os.getenv("PPAP_VALIDATION_MODEL")
            or DEFAULT_OPENAI_MODEL
        )
        self.api_key = (
            api_key
            or os.getenv("PPAP_TAGGING_OPENAI_API_KEY")
            or os.getenv("PPAP_OPENAI_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        )
        self.url = self._resolve_url(url or os.getenv("PPAP_OPENAI_URL"))
        self.timeout_seconds = timeout_seconds or int(os.getenv("PPAP_OPENAI_TIMEOUT", "120"))
        self.max_output_tokens = max_output_tokens or int(os.getenv("PPAP_OPENAI_MAX_OUTPUT_TOKENS", "4096"))
        self.temperature = 0 if temperature is None else temperature
        self.requests_per_minute = int(
            os.getenv("PPAP_OPENAI_REQUESTS_PER_MINUTE", str(DEFAULT_OPENAI_REQUESTS_PER_MINUTE))
        )
        self.retry_after_429_seconds = int(
            os.getenv("PPAP_OPENAI_429_RETRY_SECONDS", str(DEFAULT_OPENAI_429_RETRY_SECONDS))
        )

    def validate(self, prompt: str) -> dict[str, Any]:
        response_text = self.ask(prompt)
        return self.parse_json_response(response_text)

    def ask(self, prompt: str) -> str:
        if not self.api_key:
            raise RuntimeError(
                "OpenAI API key is missing. Set OPENAI_API_KEY or PPAP_OPENAI_API_KEY in the project .env file."
            )

        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "You are a careful PPAP quality assistant. Always respond with valid JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
        }
        data = json.dumps(payload).encode("utf-8")
        body = self._send_with_retry(data)

        try:
            result = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("OpenAI API returned a non-JSON response.") from exc

        return self._response_text(result)

    def _send_with_retry(self, data: bytes) -> str:
        for attempt in range(2):
            _OPENAI_RATE_LIMITER.wait(self.requests_per_minute)
            request = urllib.request.Request(
                self.url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
            )

            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    return response.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                if exc.code == 429 and attempt == 0:
                    time.sleep(max(self.retry_after_429_seconds, 0))
                    continue
                message = self._error_message(body) or str(exc)
                raise RuntimeError(f"OpenAI API request failed ({exc.code}): {message}") from exc
            except (OSError, TimeoutError, urllib.error.URLError) as exc:
                raise RuntimeError(f"OpenAI API request failed: {exc}") from exc

        raise RuntimeError("OpenAI API request failed after retry.")

    @staticmethod
    def parse_json_response(response_text: str) -> dict[str, Any]:
        text = str(response_text or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?", "", text).strip()
            text = re.sub(r"```$", "", text).strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                raise ValueError("Model response did not contain a JSON object.")
            return json.loads(match.group(0))

    @staticmethod
    def _error_message(body: str) -> str:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return body.strip()
        error = payload.get("error", {})
        if isinstance(error, dict):
            return str(error.get("message") or error)
        return str(error or payload)

    def _resolve_url(self, configured_url: str | None) -> str:
        if configured_url:
            return configured_url.format(model=self.model)
        return f"{DEFAULT_OPENAI_BASE_URL}/chat/completions"

    def _response_text(self, result: dict[str, Any]) -> str:
        if result.get("error"):
            raise RuntimeError(f"OpenAI API request failed: {result['error']}")

        choices = result.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(str(item.get("text") or ""))
                    elif isinstance(item, str):
                        parts.append(item)
                joined = "\n".join(part for part in parts if part.strip())
                if joined:
                    return joined

        raise RuntimeError("OpenAI API response did not include text output.")
