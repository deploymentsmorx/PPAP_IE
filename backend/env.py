# Environment loading for local runs.

import os
from pathlib import Path


_PROJECT_ENV_VALUES: dict[str, str] = {}


def load_project_env(project_root: Path | None = None) -> None:
    root = project_root or Path(__file__).resolve().parents[1]
    candidates = []

    explicit_path = os.getenv("PPAP_ENV_FILE")
    if explicit_path:
        candidates.append(Path(explicit_path))

    candidates.extend([root / ".env", root / ".env.local"])

    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key.startswith("export "):
                key = key.removeprefix("export ").strip()
            if not key:
                continue

            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            previous_project_value = _PROJECT_ENV_VALUES.get(key)
            if key not in os.environ or os.environ.get(key) == previous_project_value:
                os.environ[key] = value
                _PROJECT_ENV_VALUES[key] = value
