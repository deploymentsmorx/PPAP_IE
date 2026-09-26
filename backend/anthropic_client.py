# OpenAI JSON helpers used by tagging, validation, and reporting.
# Class name AnthropicJsonClient kept for call-site compatibility.

import json
import os
import re
import threading
import time
from collections import deque
from typing import Any

from openai import OpenAI
from openai import APIConnectionError, APIStatusError, RateLimitError

from .env import load_project_env


DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_OPENAI_REQUESTS_PER_MINUTE = 60
DEFAULT_OPENAI_429_RETRY_SECONDS = 10

SYSTEM_PROMPT = "You are a careful PPAP quality assistant. Always respond with valid JSON only, with no markdown formatting or commentary."


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

# Back-compat aliases expected by older imports/tests
DEFAULT_ANTHROPIC_MODEL = DEFAULT_OPENAI_MODEL
DEFAULT_ANTHROPIC_REQUESTS_PER_MINUTE = DEFAULT_OPENAI_REQUESTS_PER_MINUTE
DEFAULT_ANTHROPIC_429_RETRY_SECONDS = DEFAULT_OPENAI_429_RETRY_SECONDS


class AnthropicJsonClient:
    """OpenAI-backed JSON client (legacy class name preserved)."""

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
            or os.getenv("OPENAI_MODEL")
            or os.getenv("PPAP_ANTHROPIC_MODEL")
            or os.getenv("PPAP_VALIDATION_MODEL")
            or DEFAULT_OPENAI_MODEL
        )
        self.api_key = (
            api_key
            or os.getenv("PPAP_OPENAI_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("PPAP_TAGGING_ANTHROPIC_API_KEY")
            or os.getenv("PPAP_ANTHROPIC_API_KEY")
            or os.getenv("ANTHROPIC_API_KEY")
        )
        self.base_url = url or os.getenv("PPAP_OPENAI_URL") or os.getenv("OPENAI_BASE_URL") or None
        self.timeout_seconds = timeout_seconds or int(
            os.getenv("PPAP_OPENAI_TIMEOUT", os.getenv("PPAP_ANTHROPIC_TIMEOUT", "120"))
        )
        self.max_output_tokens = max_output_tokens or int(
            os.getenv("PPAP_OPENAI_MAX_OUTPUT_TOKENS", os.getenv("PPAP_ANTHROPIC_MAX_OUTPUT_TOKENS", "4096"))
        )
        self.temperature = temperature
        self.requests_per_minute = int(
            os.getenv(
                "PPAP_OPENAI_REQUESTS_PER_MINUTE",
                os.getenv("PPAP_ANTHROPIC_REQUESTS_PER_MINUTE", str(DEFAULT_OPENAI_REQUESTS_PER_MINUTE)),
            )
        )
        self.retry_after_429_seconds = int(
            os.getenv(
                "PPAP_OPENAI_429_RETRY_SECONDS",
                os.getenv("PPAP_ANTHROPIC_429_RETRY_SECONDS", str(DEFAULT_OPENAI_429_RETRY_SECONDS)),
            )
        )
        self._client: OpenAI | None = None

    def validate(self, prompt: str) -> dict[str, Any]:
        response_text = self.ask(prompt)
        return self.parse_json_response(response_text)

    def _get_client(self) -> OpenAI:
        if self._client is None:
            if not self.api_key:
                raise RuntimeError(
                    "OpenAI API key is missing. Set OPENAI_API_KEY or PPAP_OPENAI_API_KEY in the project .env file."
                )
            kwargs: dict[str, Any] = {"api_key": self.api_key, "max_retries": 0}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def ask(self, prompt: str) -> str:
        client = self._get_client().with_options(timeout=float(self.timeout_seconds))

        for attempt in range(2):
            _OPENAI_RATE_LIMITER.wait(self.requests_per_minute)
            try:
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "max_tokens": self.max_output_tokens,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                }
                if self.temperature is not None:
                    kwargs["temperature"] = self.temperature
                response = client.chat.completions.create(**kwargs)
            except RateLimitError as exc:
                if attempt == 0:
                    time.sleep(max(self.retry_after_429_seconds, 0))
                    continue
                raise RuntimeError("OpenAI API request failed: rate limited.") from exc
            except APIStatusError as exc:
                message = self._error_message(exc)
                raise RuntimeError(f"OpenAI API request failed ({exc.status_code}): {message}") from exc
            except APIConnectionError as exc:
                raise RuntimeError(f"OpenAI API request failed: {exc}") from exc
            else:
                return self._response_text(response)

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
    def _error_message(exc: APIStatusError) -> str:
        message = getattr(exc, "message", None)
        if message:
            return str(message)
        return str(exc)

    @staticmethod
    def _response_text(response: Any) -> str:
        text = response.choices[0].message.content if response.choices else ""
        joined = str(text or "").strip()
        if not joined:
            raise RuntimeError("OpenAI API response did not include text output.")
        return joined
