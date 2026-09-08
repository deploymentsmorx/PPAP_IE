# FastAPI routes for the SmorX PPAP Control Center.

import csv
import io
import shutil
import uuid
from contextlib import closing
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import (
    authenticate,
    create_platform_user,
    current_user,
    delete_platform_user,
    extract_bearer_token,
    list_platform_users,
    logout,
    require_super_admin,
    require_user,
    update_platform_user,
)
from .config import settings
from .customers import (
    activate_license,
    add_engineer,
    change_engineer_password,
    create_customer,
    list_customers,
    login_engineer,
    serialize_customer,
)
from .database import connect, get_case_summary, init_db
from .reporting import ReportGenerator
from .review import clear_rule_review, review_payload, save_rule_review
from .rule_store import add_rule, build_rule_library, update_rule
from .standards.profiles import DEFAULT_STANDARD_ID, report_filename
from .submissions import (
    build_dashboard,
    create_submission,
    document_matrix,
    get_submission,
    list_submissions,
    resolve_case_id,
    serialize_submission,
    severity_summary,
    update_submission,
)
from .upload.intake import (
    CHUNK_SIZE,
    IntakeError,
    UploadItem,
    preview_uploads,
    process_uploads,
    safe_filename,
    unique_path,
)
from .validation import ValidationRunner


settings.ensure_dirs()
try:
    init_db()
except Exception as exc:  # noqa: BLE001 — allow boot when Neon is briefly unreachable
    import logging

    logging.getLogger(__name__).warning("Database init deferred: %s", exc)

app = FastAPI(title="SmorX.ai PPAP Flow")
app.mount("/static", StaticFiles(directory=settings.frontend_dir), name="static")


class RuleCreate(BaseModel):
    standard_id: str = DEFAULT_STANDARD_ID
    element_number: int = Field(ge=1, le=99)
    description: str = Field(min_length=12, max_length=2000)
    referenced_elements: list[int] = Field(default_factory=list)
    enabled: bool = True


class RuleUpdate(BaseModel):
    description: str = Field(min_length=12, max_length=2000)
    referenced_elements: list[int] = Field(default_factory=list)
    enabled: bool = True


class RuleReviewRequest(BaseModel):
    status: str
    remark: str = Field(min_length=3, max_length=1000)
    evidence_location: str = Field(default="", max_length=1000)


class SubmissionCreate(BaseModel):
    customer_name: str = Field(min_length=2, max_length=200)
    part_number: str = Field(min_length=1, max_length=120)
    part_name: str = Field(min_length=2, max_length=200)
    part_revision: str = Field(min_length=1, max_length=80)
    supplier_name: str = Field(min_length=2, max_length=200)
    program_name: str = Field(default="", max_length=200)
    submission_date: str = Field(default="", max_length=40)
    due_date: str = Field(default="", max_length=40)
    standard_id: str = DEFAULT_STANDARD_ID
    submission_level: int = Field(default=3, ge=1, le=5)


class SubmissionUpdate(BaseModel):
    customer_name: str | None = Field(default=None, min_length=2, max_length=200)
    part_number: str | None = Field(default=None, min_length=1, max_length=120)
    part_name: str | None = Field(default=None, min_length=2, max_length=200)
    part_revision: str | None = Field(default=None, min_length=1, max_length=80)
    supplier_name: str | None = Field(default=None, min_length=2, max_length=200)
    program_name: str | None = Field(default=None, max_length=200)
    submission_date: str | None = Field(default=None, max_length=40)
    due_date: str | None = Field(default=None, max_length=40)
    standard_id: str | None = None
    submission_level: int | None = Field(default=None, ge=1, le=5)
    status: str | None = None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=200)


class CustomerCreate(BaseModel):
    company_name: str = Field(min_length=2, max_length=200)
    license_key: str = Field(default="", max_length=80)
    install_password: str = Field(min_length=6, max_length=200)
    device_id: str = Field(default="", max_length=120)
    host_name: str = Field(default="", max_length=200)
    engineer_full_name: str = Field(min_length=2, max_length=200)
    engineer_email: str = Field(min_length=3, max_length=200)
    temporary_password: str = Field(min_length=6, max_length=200)
    require_device: bool = True
    grant_full_access: bool = True
    role: str = Field(default="quality", max_length=40)


class EngineerCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    email: str = Field(min_length=3, max_length=200)
    temporary_password: str = Field(min_length=6, max_length=200)
    device_id: str = Field(default="", max_length=120)
    host_name: str = Field(default="", max_length=200)
    grant_full_access: bool = True
    role: str = Field(default="quality", max_length=40)


class PlatformUserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=120)
    display_name: str = Field(default="", max_length=200)
    role: str = Field(min_length=2, max_length=40)
    password: str = Field(min_length=6, max_length=200)
    must_change_password: bool = False


class PlatformUserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, max_length=40)
    password: str | None = Field(default=None, min_length=6, max_length=200)


class LicenseActivateRequest(BaseModel):
    license_key: str = Field(min_length=4, max_length=80)
    install_password: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=200)
    temporary_password: str = Field(min_length=1, max_length=200)
    device_id: str = Field(default="", max_length=120)
    host_name: str = Field(default="", max_length=200)


class LicenseLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)
    license_key: str = Field(default="", max_length=80)
    device_id: str = Field(default="", max_length=120)
    host_name: str = Field(default="", max_length=200)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(settings.frontend_dir / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/auth/login")
def auth_login(payload: LoginRequest) -> dict:
    try:
        with closing(connect()) as conn:
            return authenticate(conn, payload.username, payload.password)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/auth/logout")
def auth_logout(authorization: str | None = Header(default=None)) -> dict[str, str]:
    with closing(connect()) as conn:
        logout(conn, extract_bearer_token(authorization))
    return {"status": "ok"}


@app.get("/api/auth/me")
def auth_me(authorization: str | None = Header(default=None)) -> dict:
    with closing(connect()) as conn:
        user = current_user(conn, extract_bearer_token(authorization))
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return {"user": user}


@app.post("/api/auth/change-password")
def auth_change_password(
    payload: ChangePasswordRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    try:
        with closing(connect()) as conn:
            user = require_user(conn, extract_bearer_token(authorization))
            return change_engineer_password(
                conn,
                user,
                payload.current_password,
                payload.new_password,
            )
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/admin/customers")
def admin_customers_list(authorization: str | None = Header(default=None)) -> dict:
    require_super_admin_or_403(authorization)
    with closing(connect()) as conn:
        return list_customers(conn)


@app.post("/api/admin/customers")
def admin_customers_create(
    payload: CustomerCreate,
    authorization: str | None = Header(default=None),
) -> dict:
    require_super_admin_or_403(authorization)
    try:
        with closing(connect()) as conn:
            return create_customer(conn, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/admin/customers/{customer_id}")
def admin_customers_get(customer_id: str, authorization: str | None = Header(default=None)) -> dict:
    require_super_admin_or_403(authorization)
    try:
        with closing(connect()) as conn:
            return serialize_customer(conn, customer_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/admin/customers/{customer_id}/engineers")
def admin_customers_add_engineer(
    customer_id: str,
    payload: EngineerCreate,
    authorization: str | None = Header(default=None),
) -> dict:
    require_super_admin_or_403(authorization)
    try:
        with closing(connect()) as conn:
            return add_engineer(conn, customer_id, payload.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/admin/users")
def admin_users_list(authorization: str | None = Header(default=None)) -> dict:
    require_super_admin_or_403(authorization)
    with closing(connect()) as conn:
        return list_platform_users(conn)


@app.post("/api/admin/users")
def admin_users_create(
    payload: PlatformUserCreate,
    authorization: str | None = Header(default=None),
) -> dict:
    require_super_admin_or_403(authorization)
    try:
        with closing(connect()) as conn:
            return create_platform_user(conn, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/admin/users/{user_id}")
def admin_users_update(
    user_id: str,
    payload: PlatformUserUpdate,
    authorization: str | None = Header(default=None),
) -> dict:
    require_super_admin_or_403(authorization)
    try:
        with closing(connect()) as conn:
            return update_platform_user(
                conn,
                user_id,
                {k: v for k, v in payload.model_dump().items() if v is not None},
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/admin/users/{user_id}", status_code=204)
def admin_users_delete(user_id: str, authorization: str | None = Header(default=None)) -> None:
    require_super_admin_or_403(authorization)
    try:
        with closing(connect()) as conn:
            delete_platform_user(conn, user_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/license/activate")
def license_activate(payload: LicenseActivateRequest) -> dict:
    try:
        with closing(connect()) as conn:
            return activate_license(conn, payload.model_dump())
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/license/login")
def license_login(payload: LicenseLoginRequest) -> dict:
    try:
        with closing(connect()) as conn:
            return login_engineer(conn, payload.model_dump())
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/dashboard")
def dashboard() -> dict:
    with closing(connect()) as conn:
        return build_dashboard(conn, settings)


@app.get("/api/submissions")
def submissions_list(status: str | None = None, search: str | None = None) -> dict:
    with closing(connect()) as conn:
        return list_submissions(conn, status=status, search=search)


@app.post("/api/submissions")
def submissions_create(payload: SubmissionCreate) -> dict:
    try:
        with closing(connect()) as conn:
            return create_submission(conn, settings, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/submissions/{identifier}")
def submissions_get(identifier: str) -> dict:
    with closing(connect()) as conn:
        summary = get_submission(conn, identifier)
    if summary is None:
        raise HTTPException(status_code=404, detail="Submission not found.")
    return summary


@app.patch("/api/submissions/{identifier}")
def submissions_patch(identifier: str, payload: SubmissionUpdate) -> dict:
    try:
        with closing(connect()) as conn:
            return update_submission(
                conn,
                identifier,
                {key: value for key, value in payload.model_dump().items() if value is not None},
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/submissions/{identifier}/matrix")
def submissions_matrix(identifier: str) -> dict:
    try:
        with closing(connect()) as conn:
            return document_matrix(conn, settings, identifier)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/submissions/{identifier}/severity")
def submissions_severity(identifier: str) -> dict:
    case_id = case_id_or_404(identifier)
    try:
        standard_id = case_or_404(case_id)["case"].get("standard_id") or DEFAULT_STANDARD_ID
        validation = ValidationRunner(settings, standard_id=standard_id).latest_report(case_id)
        return severity_summary(validation)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/cases/preview")
async def preview_case_upload(
    submission_level: int = Form(...),
    standard_id: str = Form(DEFAULT_STANDARD_ID),
    files: list[UploadFile] = File(...),
) -> dict:
    request_dir: Path | None = None
    try:
        request_dir, upload_items = await stage_uploads(files)
        return preview_uploads(upload_items, settings, submission_level, standard_id)
    except IntakeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if request_dir:
            shutil.rmtree(request_dir, ignore_errors=True)


@app.get("/api/rules")
def get_rules(
    standard_id: str = DEFAULT_STANDARD_ID,
    authorization: str | None = Header(default=None),
) -> dict:
    require_super_admin_or_403(authorization)
    with closing(connect()) as conn:
        return build_rule_library(conn, standard_id=standard_id)


@app.post("/api/rules")
def create_rule(payload: RuleCreate, authorization: str | None = Header(default=None)) -> dict:
    require_super_admin_or_403(authorization)
    try:
        with closing(connect()) as conn:
            return add_rule(
                conn,
                payload.standard_id,
                payload.element_number,
                payload.description,
                payload.referenced_elements,
                payload.enabled,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/rules/{rule_key}")
def edit_rule(
    rule_key: str,
    payload: RuleUpdate,
    authorization: str | None = Header(default=None),
) -> dict:
    require_super_admin_or_403(authorization)
    try:
        with closing(connect()) as conn:
            return update_rule(
                conn,
                rule_key,
                payload.description,
                payload.referenced_elements,
                payload.enabled,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/cases/upload")
async def upload_case(
    submission_level: int = Form(...),
    standard_id: str = Form(DEFAULT_STANDARD_ID),
    case_id: str | None = Form(default=None),
    files: list[UploadFile] = File(...),
) -> dict:
    request_dir: Path | None = None
    try:
        request_dir, upload_items = await stage_uploads(files)
        resolved = None
        if case_id:
            resolved = case_id_or_404(case_id)
        with closing(connect()) as conn:
            summary = process_uploads(
                upload_items,
                settings,
                conn,
                submission_level,
                standard_id,
                case_id=resolved,
            )
            return serialize_submission(summary)
    except IntakeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if request_dir:
            shutil.rmtree(request_dir, ignore_errors=True)


@app.get("/api/cases/{case_id}")
def get_case(case_id: str) -> dict:
    return serialize_submission(case_or_404(case_id))


@app.post("/api/cases/{case_id}/validate")
def validate_case(case_id: str, dry_run: bool = False, elements: str | None = None) -> dict:
    case_id = case_id_or_404(case_id)
    summary = case_or_404(case_id)

    try:
        standard_id = summary["case"].get("standard_id") or DEFAULT_STANDARD_ID
        report = ValidationRunner(settings).validate_case(
            case_id,
            dry_run=dry_run,
            elements=parse_elements(elements),
            submission_level=summary["case"].get("submission_level"),
            standard_id=standard_id,
        )
        with closing(connect()) as conn:
            status = "in_review"
            overall = str((report.get("summary") or {}).get("overall_status") or "").upper()
            missing = (report.get("summary") or {}).get("required_missing_elements") or []
            if overall in {"NOT_FOUND", "REVIEW"} or missing:
                status = "failed"
            conn.execute("UPDATE cases SET status = ? WHERE case_id = ?", (status, case_id))
            conn.commit()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    payload = report["summary"]
    payload["severity"] = severity_summary(report)
    return payload


@app.get("/api/cases/{case_id}/validation")
def get_validation(case_id: str) -> dict:
    case_id = case_id_or_404(case_id)
    case_or_404(case_id)

    try:
        standard_id = case_or_404(case_id)["case"].get("standard_id") or DEFAULT_STANDARD_ID
        report = ValidationRunner(settings, standard_id=standard_id).latest_report(case_id)
        report["severity"] = severity_summary(report)
        return report
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/cases/{case_id}/review")
def get_case_review(case_id: str) -> dict:
    case_id = case_id_or_404(case_id)
    case_or_404(case_id)
    try:
        standard_id = case_or_404(case_id)["case"].get("standard_id") or DEFAULT_STANDARD_ID
        validation = ValidationRunner(settings, standard_id=standard_id).latest_report(case_id)
        payload = review_payload(case_id, validation)
        payload["severity"] = severity_summary(validation)
        return payload
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/cases/{case_id}/review/{rule_key}")
def review_rule(case_id: str, rule_key: str, payload: RuleReviewRequest) -> dict:
    case_id = case_id_or_404(case_id)
    try:
        with closing(connect()) as conn:
            return save_rule_review(
                conn,
                case_id,
                rule_key,
                payload.status,
                payload.remark,
                payload.evidence_location,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/cases/{case_id}/review/{rule_key}", status_code=204)
def reset_rule_review(case_id: str, rule_key: str) -> None:
    case_id = case_id_or_404(case_id)
    with closing(connect()) as conn:
        clear_rule_review(conn, case_id, rule_key)


@app.post("/api/cases/{case_id}/report")
def generate_report(case_id: str, use_llm: bool = True) -> dict:
    case_id = case_id_or_404(case_id)
    case_or_404(case_id)

    try:
        report = ReportGenerator(settings).generate_from_latest_validation(case_id, use_llm=use_llm)
        with closing(connect()) as conn:
            conn.execute("UPDATE cases SET status = ? WHERE case_id = ?", ("in_review", case_id))
            conn.commit()
        return report
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/cases/{case_id}/report")
def get_report(case_id: str) -> dict:
    case_id = case_id_or_404(case_id)
    case_or_404(case_id)

    try:
        return ReportGenerator(settings).latest_report(case_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/cases/{case_id}/report.pdf")
def get_report_pdf(case_id: str) -> FileResponse:
    case_id = case_id_or_404(case_id)
    case_or_404(case_id)

    try:
        path = ReportGenerator(settings).latest_pdf_path(case_id)
        summary = case_or_404(case_id)
        standard_id = summary["case"].get("standard_id") or DEFAULT_STANDARD_ID
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=f"{Path(report_filename(standard_id)).stem}_{case_id}.pdf",
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/cases/{case_id}/report.csv")
def get_report_csv(case_id: str) -> StreamingResponse:
    case_id = case_id_or_404(case_id)
    case_or_404(case_id)
    try:
        report = ReportGenerator(settings).latest_report(case_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Element", "Status", "Finding", "Recommended Action"])
    for element in report.get("elements") or []:
        writer.writerow(
            [
                f"{element.get('element_number')} {element.get('element_name')}",
                element.get("status"),
                element.get("summary") or element.get("finding") or "",
                element.get("recommended_action") or "",
            ]
        )
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="ppap_report_{case_id}.csv"'},
    )


async def stream_upload(upload: UploadFile, target_path: Path) -> None:
    total_bytes = 0
    with target_path.open("wb") as target:
        while True:
            chunk = await upload.read(CHUNK_SIZE)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > settings.max_upload_size_bytes:
                raise IntakeError("Upload rejected: file is too large.")
            target.write(chunk)


async def stage_uploads(files: list[UploadFile]) -> tuple[Path, list[UploadItem]]:
    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required.")

    request_dir = settings.tmp_dir / uuid.uuid4().hex
    request_dir.mkdir(parents=True, exist_ok=False)
    upload_items = []
    try:
        for upload in files:
            filename = safe_filename(upload.filename or "upload.bin")
            target_path = unique_path(request_dir, filename)
            await stream_upload(upload, target_path)
            upload_items.append(UploadItem(target_path, upload.filename or filename))
        return request_dir, upload_items
    except Exception:
        shutil.rmtree(request_dir, ignore_errors=True)
        raise


def case_id_or_404(identifier: str) -> str:
    with closing(connect()) as conn:
        case_id = resolve_case_id(conn, identifier)
    if not case_id:
        raise HTTPException(status_code=404, detail="Case not found.")
    return case_id


def case_or_404(case_id: str) -> dict:
    with closing(connect()) as conn:
        resolved = resolve_case_id(conn, case_id) or str(case_id)
        summary = get_case_summary(conn, resolved)
    if summary is None:
        raise HTTPException(status_code=404, detail="Case not found.")
    return summary


def require_super_admin_or_403(authorization: str | None) -> dict:
    try:
        with closing(connect()) as conn:
            return require_super_admin(conn, extract_bearer_token(authorization))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def parse_elements(value: str | None) -> list[int] | None:
    if not value:
        return None
    try:
        return [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="elements must be a comma-separated list of element numbers.",
        ) from exc

