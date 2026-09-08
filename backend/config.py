# Project paths, database, and object-storage settings.

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .env import load_project_env


def _normalize_database_url(url: str) -> str:
    """Neon sometimes adds channel_binding=require which older clients reject."""
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        return url.strip()
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.pop("channel_binding", None)
    if "sslmode" not in query:
        query["sslmode"] = "require"
    return urlunparse(parsed._replace(query=urlencode(query)))


@dataclass(frozen=True)
class Settings:
    project_root: Path = Path(__file__).resolve().parents[1]
    max_upload_size_bytes: int = 500 * 1024 * 1024

    @property
    def data_dir(self) -> Path:
        return self.project_root / "data"

    @property
    def cases_dir(self) -> Path:
        return self.data_dir / "cases"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ppap_registry.db"

    @property
    def frontend_dir(self) -> Path:
        return self.project_root / "frontend"

    @property
    def database_url(self) -> str | None:
        load_project_env(self.project_root)
        raw = (os.getenv("DATABASE_URL") or "").strip()
        return _normalize_database_url(raw) if raw else None

    @property
    def use_postgres(self) -> bool:
        return bool(self.database_url)

    @property
    def s3_bucket(self) -> str:
        load_project_env(self.project_root)
        return (os.getenv("S3_BUCKET") or "ieppcostsheeterp").strip()

    @property
    def s3_prefix(self) -> str:
        load_project_env(self.project_root)
        prefix = (os.getenv("S3_PREFIX") or "PPAP/").strip()
        return prefix if prefix.endswith("/") else f"{prefix}/"

    @property
    def aws_region(self) -> str:
        load_project_env(self.project_root)
        return (os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-2").strip()

    @property
    def s3_enabled(self) -> bool:
        load_project_env(self.project_root)
        flag = (os.getenv("S3_ENABLED") or "").strip().lower()
        if flag in {"0", "false", "no", "off"}:
            return False
        if flag in {"1", "true", "yes", "on"}:
            return True
        return bool(
            (os.getenv("AWS_ACCESS_KEY_ID") or "").strip()
            and (os.getenv("AWS_SECRET_ACCESS_KEY") or "").strip()
        )

    def case_dir(self, case_id: str) -> Path:
        return self.cases_dir / str(case_id)

    def raw_case_dir(self, case_id: str) -> Path:
        return self.case_dir(case_id) / "raw"

    def work_case_dir(self, case_id: str) -> Path:
        return self.case_dir(case_id) / "work"

    def extracted_case_dir(self, case_id: str) -> Path:
        return self.case_dir(case_id) / "extraction"

    def tagging_case_dir(self, case_id: str) -> Path:
        return self.case_dir(case_id) / "tagging"

    def validation_case_dir(self, case_id: str) -> Path:
        return self.case_dir(case_id) / "validation"

    def reports_case_dir(self, case_id: str) -> Path:
        return self.case_dir(case_id) / "reports"

    def s3_case_prefix(self, case_id: str) -> str:
        return f"{self.s3_prefix}{case_id}/"

    def ensure_dirs(self) -> None:
        # Case folders and upload temp folders are created only when needed.
        self.data_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
