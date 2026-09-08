# Anthropic client behavior tests.

import json
import unittest
from unittest.mock import patch

import anthropic
import httpx2

from backend.anthropic_client import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_ANTHROPIC_REQUESTS_PER_MINUTE,
    AnthropicJsonClient,
)


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeMessage:
    def __init__(self, text: str, stop_reason: str = "end_turn") -> None:
        self.content = [_FakeTextBlock(text)]
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, result=None, error=None) -> None:
        self._result = result
        self._error = error
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._result


class _FakeAnthropicClient:
    def __init__(self, result=None, error=None, **_kwargs) -> None:
        self.messages = _FakeMessages(result, error)

    def with_options(self, **_kwargs):
        return self


class AnthropicClientTests(unittest.TestCase):
    def test_default_model(self) -> None:
        client = AnthropicJsonClient(api_key="test-key")
        self.assertEqual(client.model, DEFAULT_ANTHROPIC_MODEL)

    def test_response_content_is_parsed(self) -> None:
        client = AnthropicJsonClient(api_key="test-key")
        fake = _FakeAnthropicClient(result=_FakeMessage(json.dumps({"element_status": "PASS"})))
        with patch("backend.anthropic_client.anthropic.Anthropic", return_value=fake):
            payload = client.validate("return json")
        self.assertEqual(payload["element_status"], "PASS")

    def test_requests_per_minute_default(self) -> None:
        client = AnthropicJsonClient(api_key="test-key")
        self.assertEqual(client.requests_per_minute, DEFAULT_ANTHROPIC_REQUESTS_PER_MINUTE)

    def test_missing_api_key_raises(self) -> None:
        client = AnthropicJsonClient(api_key=None)
        with self.assertRaises(RuntimeError):
            client.ask("hello")

    def test_refusal_stop_reason_raises(self) -> None:
        client = AnthropicJsonClient(api_key="test-key")
        fake = _FakeAnthropicClient(result=_FakeMessage("", stop_reason="refusal"))
        with patch("backend.anthropic_client.anthropic.Anthropic", return_value=fake):
            with self.assertRaises(RuntimeError):
                client.ask("hello")

    def test_api_status_error_surfaces_message(self) -> None:
        client = AnthropicJsonClient(api_key="test-key")
        response = httpx2.Response(
            401, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        )
        error = anthropic.APIStatusError(
            "bad key", response=response, body={"error": {"message": "bad key"}}
        )
        fake = _FakeAnthropicClient(error=error)
        with patch("backend.anthropic_client.anthropic.Anthropic", return_value=fake):
            with self.assertRaises(RuntimeError) as ctx:
                client.ask("hello")
        self.assertIn("401", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
