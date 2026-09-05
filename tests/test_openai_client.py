# OpenAI client behavior tests.

import json
import unittest
import urllib.error
from unittest.mock import patch

from backend.openai_client import (
    DEFAULT_OPENAI_MODEL,
    DEFAULT_OPENAI_REQUESTS_PER_MINUTE,
    OpenAIJsonClient,
)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class OpenAIClientTests(unittest.TestCase):
    def test_default_url_uses_chat_completions(self) -> None:
        client = OpenAIJsonClient(api_key="test-key", model=DEFAULT_OPENAI_MODEL)
        self.assertEqual(client.url, "https://api.openai.com/v1/chat/completions")

    def test_chat_completion_content_is_parsed(self) -> None:
        client = OpenAIJsonClient(api_key="test-key")
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({"element_status": "PASS"}),
                    }
                }
            ]
        }
        with patch("backend.openai_client.urllib.request.urlopen", return_value=_FakeResponse(response)):
            payload = client.validate("return json")
        self.assertEqual(payload["element_status"], "PASS")

    def test_requests_per_minute_default(self) -> None:
        client = OpenAIJsonClient(api_key="test-key")
        self.assertEqual(client.requests_per_minute, DEFAULT_OPENAI_REQUESTS_PER_MINUTE)

    def test_http_error_surfaces_message(self) -> None:
        client = OpenAIJsonClient(api_key="test-key")

        class FakeHTTPError(urllib.error.HTTPError):
            def __init__(self):
                super().__init__(
                    url="https://api.openai.com/v1/chat/completions",
                    code=401,
                    msg="Unauthorized",
                    hdrs=None,
                    fp=None,
                )

            def read(self):
                return json.dumps({"error": {"message": "bad key"}}).encode("utf-8")

        with patch("backend.openai_client.urllib.request.urlopen", side_effect=FakeHTTPError()):
            with self.assertRaises(RuntimeError) as ctx:
                client.ask("hello")
        self.assertIn("401", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
