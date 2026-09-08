# S3 helpers for PPAP case file storage under s3://bucket/PPAP/{case_id}/...

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from .config import Settings, settings

logger = logging.getLogger(__name__)


def _client(app_settings: Settings | None = None):
    cfg = app_settings or settings
    return boto3.client(
        "s3",
        region_name=cfg.aws_region,
    )


def s3_uri(key: str, app_settings: Settings | None = None) -> str:
    cfg = app_settings or settings
    return f"s3://{cfg.s3_bucket}/{key.lstrip('/')}"


def case_object_key(case_id: str, relative: str, app_settings: Settings | None = None) -> str:
    cfg = app_settings or settings
    rel = str(relative).replace("\\", "/").lstrip("/")
    return f"{cfg.s3_prefix}{case_id}/{rel}"


def upload_file(local_path: Path, key: str, app_settings: Settings | None = None) -> str:
    cfg = app_settings or settings
    if not cfg.s3_enabled:
        return str(local_path)
    content_type, _ = mimetypes.guess_type(str(local_path))
    extra = {"ContentType": content_type} if content_type else {}
    try:
        _client(cfg).upload_file(str(local_path), cfg.s3_bucket, key, ExtraArgs=extra or None)
    except TypeError:
        _client(cfg).upload_file(str(local_path), cfg.s3_bucket, key)
    except (BotoCoreError, ClientError) as exc:
        logger.warning("S3 upload failed for %s: %s", key, exc)
        raise
    return s3_uri(key, cfg)


def upload_bytes(data: bytes, key: str, content_type: str | None = None, app_settings: Settings | None = None) -> str:
    cfg = app_settings or settings
    if not cfg.s3_enabled:
        return key
    extra: dict = {"Bucket": cfg.s3_bucket, "Key": key, "Body": data}
    if content_type:
        extra["ContentType"] = content_type
    _client(cfg).put_object(**extra)
    return s3_uri(key, cfg)


def download_to_path(key: str, target: Path, app_settings: Settings | None = None) -> Path:
    cfg = app_settings or settings
    target.parent.mkdir(parents=True, exist_ok=True)
    _client(cfg).download_file(cfg.s3_bucket, key, str(target))
    return target


def upload_directory(local_dir: Path, case_id: str, app_settings: Settings | None = None) -> list[str]:
    """Upload all files under a local case directory to S3. Returns list of keys."""
    cfg = app_settings or settings
    if not cfg.s3_enabled or not local_dir.exists():
        return []
    keys: list[str] = []
    root = local_dir.resolve()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        # local_dir is often cases/{case_id}; keys should be PPAP/{case_id}/...
        if root.name == str(case_id):
            key = case_object_key(case_id, rel, cfg)
        else:
            key = case_object_key(case_id, f"{root.name}/{rel}", cfg)
        upload_file(path, key, cfg)
        keys.append(key)
    return keys


def sync_case_to_s3(case_id: str, app_settings: Settings | None = None) -> str:
    cfg = app_settings or settings
    case_dir = cfg.case_dir(case_id)
    if not case_dir.exists():
        return ""
    if not cfg.s3_enabled:
        return str(case_dir)
    upload_directory(case_dir, case_id, cfg)
    return s3_uri(cfg.s3_case_prefix(case_id), cfg)


def ensure_case_local(case_id: str, app_settings: Settings | None = None) -> Path:
    """Ensure case files exist locally (download from S3 when missing)."""
    cfg = app_settings or settings
    case_dir = cfg.case_dir(case_id)
    if case_dir.exists() and any(case_dir.rglob("*")):
        return case_dir
    if not cfg.s3_enabled:
        case_dir.mkdir(parents=True, exist_ok=True)
        return case_dir
    prefix = cfg.s3_case_prefix(case_id)
    try:
        client = _client(cfg)
        paginator = client.get_paginator("list_objects_v2")
        found = False
        for page in paginator.paginate(Bucket=cfg.s3_bucket, Prefix=prefix):
            for obj in page.get("Contents") or []:
                key = obj["Key"]
                rel = key[len(prefix) :] if key.startswith(prefix) else key
                if not rel or key.endswith("/"):
                    continue
                target = case_dir / rel
                download_to_path(key, target, cfg)
                found = True
        if not found:
            case_dir.mkdir(parents=True, exist_ok=True)
    except (BotoCoreError, ClientError) as exc:
        logger.warning("S3 download for case %s failed: %s", case_id, exc)
        case_dir.mkdir(parents=True, exist_ok=True)
    return case_dir
