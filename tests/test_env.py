# Environment loading tests.

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import env


class EnvTests(unittest.TestCase):
    def test_project_env_values_refresh_after_initial_load(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_file = root / ".env"
            env_file.write_text("PPAP_OPENAI_MODEL=old-model\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                env._PROJECT_ENV_VALUES.clear()
                env.load_project_env(root)
                self.assertEqual(os.environ["PPAP_OPENAI_MODEL"], "old-model")

                env_file.write_text("PPAP_OPENAI_MODEL=new-model\n", encoding="utf-8")
                env.load_project_env(root)
                self.assertEqual(os.environ["PPAP_OPENAI_MODEL"], "new-model")

    def test_external_environment_value_is_not_overridden(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text("PPAP_OPENAI_MODEL=file-model\n", encoding="utf-8")

            with patch.dict(os.environ, {"PPAP_OPENAI_MODEL": "external-model"}, clear=True):
                env._PROJECT_ENV_VALUES.clear()
                env.load_project_env(root)
                self.assertEqual(os.environ["PPAP_OPENAI_MODEL"], "external-model")

    def test_temporary_environment_change_is_not_overridden_by_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_file = root / ".env"
            env_file.write_text("PPAP_VALIDATION_RULE_BATCH_SIZE=9\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                env._PROJECT_ENV_VALUES.clear()
                env.load_project_env(root)
                self.assertEqual(os.environ["PPAP_VALIDATION_RULE_BATCH_SIZE"], "9")

                os.environ["PPAP_VALIDATION_RULE_BATCH_SIZE"] = "999"
                env_file.write_text("PPAP_VALIDATION_RULE_BATCH_SIZE=8\n", encoding="utf-8")
                env.load_project_env(root)
                self.assertEqual(os.environ["PPAP_VALIDATION_RULE_BATCH_SIZE"], "999")


if __name__ == "__main__":
    unittest.main()
