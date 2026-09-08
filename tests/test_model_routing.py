# Model routing configuration tests.

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from backend.config import Settings
from backend.anthropic_client import AnthropicJsonClient
from backend.reporting import ReportGenerator
from backend.tagging.element_tagger import ElementTagger


class ModelRoutingTests(unittest.TestCase):
    def test_report_and_tagging_fallback_use_anthropic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = replace(Settings(project_root=Path(temp_dir)))

            with (
                patch("backend.reporting.generator.load_project_env", lambda: None),
                patch("backend.tagging.element_tagger.load_project_env", lambda: None),
                patch.dict(os.environ, {"PPAP_ANTHROPIC_MODEL": "claude-opus-5"}, clear=True),
            ):
                report_generator = ReportGenerator(settings)
                tagger = ElementTagger()

        self.assertIsInstance(report_generator.narrative_client, AnthropicJsonClient)
        self.assertEqual(report_generator.narrative_client.model, "claude-opus-5")
        self.assertIsInstance(tagger.llm.client, AnthropicJsonClient)
        self.assertEqual(tagger.llm.model, "claude-opus-5")

    def test_tagging_fallback_auto_enables_when_anthropic_key_exists(self) -> None:
        with (
            patch("backend.tagging.element_tagger.load_project_env", lambda: None),
            patch.dict(os.environ, {"PPAP_TAGGING_ANTHROPIC_API_KEY": "tag-key"}, clear=True),
        ):
            tagger = ElementTagger()

        self.assertTrue(tagger.llm_enabled)
        self.assertEqual(tagger.llm.client.api_key, "tag-key")

    def test_tagging_fallback_can_be_explicitly_disabled(self) -> None:
        with (
            patch("backend.tagging.element_tagger.load_project_env", lambda: None),
            patch.dict(
                os.environ,
                {
                    "PPAP_TAGGING_ANTHROPIC_API_KEY": "tag-key",
                    "PPAP_TAGGING_ANTHROPIC_FALLBACK": "0",
                },
                clear=True,
            ),
        ):
            tagger = ElementTagger()

        self.assertFalse(tagger.llm_enabled)
        self.assertEqual(tagger.llm.client.api_key, "tag-key")


if __name__ == "__main__":
    unittest.main()
