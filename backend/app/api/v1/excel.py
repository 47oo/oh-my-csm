"""Excel import/export REST API endpoints (template, upload, Dry-run preview, commit, error report, export)."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import AuthContext, get_current_auth
from app.models.audit import ImportBatch
from app.schemas.excel import (
    ExcelCommitOut,
    ExcelExportRequest,
    ExcelPreviewOut,
    ExcelPreviewRequest,
    ExcelUploadOut,
    ImportBatchOut,
)
from app.services.excel_engine import (
    export_assets_workbook,
    generate_error_report_bytes,
    generate_standard_template_bytes,
    run_excel_import,
    save_and_inspect_upload,
)

router = APIRouter(prefix="/excel", tags=["Excel 双向批处理"])

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx_response(data: bytes, filename: str) -> Response:
    encoded = quote(filename)
    return Response(
        content=data,
        media_type=XLSX_MIME,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
    )


@router.get("/template")
async def download_template(
    _: AuthContext = Depends(get_current_auth),
) -> Response:
    data = generate_standard_template_bytes()
    return _xlsx_response(data, "oh-my-csm-标准导入模板.xlsx")


@router.post("/upload", response_model=ExcelUploadOut)
async def upload_excel(
    file: UploadFile = File(...),
    auth: AuthContext = Depends(get_current_auth),
) -> ExcelUploadOut:
    if not auth.has_any_permission("excel:import") and not auth.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="缺少 Excel 导入权限")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="上传文件为空")
    return save_and_inspect_upload(file.filename or "upload.xlsx", content)


@router.post("/preview", response_model=ExcelPreviewOut)
async def preview_import(
    body: ExcelPreviewRequest,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> ExcelPreviewOut:
    if not auth.has_any_permission("excel:import") and not auth.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="缺少 Excel 导入权限")
    res = await run_excel_import(db, auth, body, commit_mode=False)
    assert isinstance(res, ExcelPreviewOut)
    return res


@router.post("/commit", response_model=ExcelCommitOut)
async def commit_import(
    body: ExcelPreviewRequest,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> ExcelCommitOut:
    if not auth.has_any_permission("excel:import") and not auth.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="缺少 Excel 导入权限")
    res = await run_excel_import(db, auth, body, commit_mode=True)
    assert isinstance(res, ExcelCommitOut)
    return res


@router.get("/batches", response_model=list[ImportBatchOut])
async def list_import_batches(
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[ImportBatchOut]:
    stmt = select(ImportBatch)
    if not auth.is_superadmin:
        stmt = stmt.where(ImportBatch.user_id == auth.user.id)
    stmt = stmt.order_by(ImportBatch.created_at.desc()).limit(50)
    rows = list((await db.execute(stmt)).scalars().all())
    return [ImportBatchOut.model_validate(r) for r in rows]


@router.get("/batches/{batch_id}/errors")
async def download_batch_errors(
    batch_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    batch = (
        await db.execute(select(ImportBatch).where(ImportBatch.id == batch_id))
    ).scalar_one_or_none()
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="导入批次不存在")
    if not auth.is_superadmin and batch.user_id != auth.user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权下载该批次错误清单")
    errors = batch.error_report_json or []
    data = generate_error_report_bytes(errors)
    return _xlsx_response(data, f"import-batch-{batch.id}-错误清单.xlsx")


@router.post("/export")
async def export_excel(
    body: ExcelExportRequest,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    data = await export_assets_workbook(
        db,
        auth,
        mode=body.mode,
        group_id=body.group_id,
        env=body.env,
    )
    fname = (
        "oh-my-csm-全量台账-6Sheet.xlsx"
        if body.mode == "multi_sheet"
        else "oh-my-csm-台账宽表.xlsx"
    )
    return _xlsx_response(data, fname)
