# Anthropic JSON helpers used by tagging, validation, and reporting.

import json
import os
import re
import threading
import time
from collections import deque
from typing import Any

import anthropic

from .env import load_project_env


DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
DEFAULT_ANTHROPIC_REQUESTS_PER_MINUTE = 30
DEFAULT_ANTHROPIC_429_RETRY_SECONDS = 10

SYSTEM_PROMPT = "You are a careful PPAP quality assistant. Always respond with valid JSON only, with no markdown formatting or commentary."


class _AnthropicRateLimiter:
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


_ANTHROPIC_RATE_LIMITER = _AnthropicRateLimiter()


class AnthropicJsonClient:
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
            or os.getenv("PPAP_ANTHROPIC_MODEL")
            or os.getenv("PPAP_VALIDATION_MODEL")
            or DEFAULT_ANTHROPIC_MODEL
        )
        self.api_key = (
            api_key
            or os.getenv("PPAP_TAGGING_ANTHROPIC_API_KEY")
            or os.getenv("PPAP_ANTHROPIC_API_KEY")
            or os.getenv("ANTHROPIC_API_KEY")
        )
        self.base_url = url or os.getenv("PPAP_ANTHROPIC_URL") or None
        self.timeout_seconds = timeout_seconds or int(os.getenv("PPAP_ANTHROPIC_TIMEOUT", "120"))
        self.max_output_tokens = max_output_tokens or int(os.getenv("PPAP_ANTHROPIC_MAX_OUTPUT_TOKENS", "4096"))
        # Sampling params (temperature/top_p/top_k) are rejected on Claude Opus 5 —
        # accepted for interface parity with older call sites but never sent.
        self.temperature = temperature
        self.requests_per_minute = int(
            os.getenv("PPAP_ANTHROPIC_REQUESTS_PER_MINUTE", str(DEFAULT_ANTHROPIC_REQUESTS_PER_MINUTE))
        )
        self.retry_after_429_seconds = int(
            os.getenv("PPAP_ANTHROPIC_429_RETRY_SECONDS", str(DEFAULT_ANTHROPIC_429_RETRY_SECONDS))
        )
        self._client: anthropic.Anthropic | None = None

    def validate(self, prompt: str) -> dict[str, Any]:
        response_text = self.ask(prompt)
        return self.parse_json_response(response_text)

    def _get_client(self) -> anthropic.Anthropic:
        if self._client is None:
            if not self.api_key:
                raise RuntimeError(
                    "Anthropic API key is missing. Set ANTHROPIC_API_KEY or PPAP_ANTHROPIC_API_KEY in the project .env file."
                )
            kwargs: dict[str, Any] = {"api_key": self.api_key, "max_retries": 0}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = anthropic.Anthropic(**kwargs)
        return self._client

    def ask(self, prompt: str) -> str:
        client = self._get_client().with_options(timeout=float(self.timeout_seconds))

        for attempt in range(2):
            _ANTHROPIC_RATE_LIMITER.wait(self.requests_per_minute)
            try:
                response = client.messages.create(
                    model=self.model,
                    max_tokens=self.max_output_tokens,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
            except anthropic.RateLimitError as exc:
                if attempt == 0:
                    time.sleep(max(self.retry_after_429_seconds, 0))
                    continue
                raise RuntimeError("Anthropic API request failed: rate limited.") from exc
            except anthropic.APIStatusError as exc:
                message = self._error_message(exc)
                raise RuntimeError(f"Anthropic API request failed ({exc.status_code}): {message}") from exc
            except anthropic.APIConnectionError as exc:
                raise RuntimeError(f"Anthropic API request failed: {exc}") from exc
            else:
                return self._response_text(response)

        raise RuntimeError("Anthropic API request failed after retry.")

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
    def _error_message(exc: "anthropic.APIStatusError") -> str:
        message = getattr(exc, "message", None)
        if message:
            return str(message)
        return str(exc)

    @staticmethod
    def _response_text(response: "anthropic.types.Message") -> str:
        if getattr(response, "stop_reason", None) == "refusal":
            raise RuntimeError("Anthropic API declined the request (safety refusal).")

        parts = [
            block.text
            for block in response.content
            if getattr(block, "type", None) == "text" and str(getattr(block, "text", "")).strip()
        ]
        joined = "\n".join(parts)
        if not joined:
            raise RuntimeError("Anthropic API response did not include text output.")
        return joined
