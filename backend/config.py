# Project paths and runtime limits.

from dataclasses import dataclass
from pathlib import Path


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

    def ensure_dirs(self) -> None:
        # Case folders and upload temp folders are created only when needed.
        self.data_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
