# Builds stable output paths for extracted assets.

from contextvars import ContextVar
from pathlib import Path


_output_context = ContextVar("extractor_output_context", default={})


def set_output_context(data_dir=None, case_id=None):
    return _output_context.set({
        "data_dir": Path(data_dir) if data_dir else Path("data"),
        "case_id": case_id,
    })


def reset_output_context(token):
    _output_context.reset(token)


def extracted_dir(category, file_path=None):
    context = _output_context.get()
    data_dir = context.get("data_dir", Path("data"))
    case_id = context.get("case_id")

    folder = data_dir / "cases" / str(case_id) / "extraction" / "assets" if case_id else data_dir / "extraction" / "assets"
    folder = folder / category

    if file_path:
        folder = folder / Path(file_path).stem

    folder.mkdir(parents=True, exist_ok=True)
    return folder
