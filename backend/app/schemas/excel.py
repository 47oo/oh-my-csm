"""Pydantic schemas for Excel template, upload inspection, Dry-run preview, commit, and export."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ImportMode = Literal["multi_sheet", "wide_table"]
ConflictStrategy = Literal["skip", "merge_non_empty", "overwrite"]
ErrorMode = Literal["all_or_nothing", "skip_errors"]


class UploadedSheetInfo(BaseModel):
    name: str
    headers: list[str]
    row_count: int
    sample_rows: list[dict[str, Any]]


class WideFieldMeta(BaseModel):
    key: str
    label: str
    required_hint: str | None = None


class ExcelUploadOut(BaseModel):
    upload_id: str
    filename: str
    detected_mode: ImportMode
    sheets: list[UploadedSheetInfo]
    wide_fields: list[WideFieldMeta]
    suggested_mapping: dict[str, str]


class ExcelPreviewRequest(BaseModel):
    upload_id: str
    mode: ImportMode
    conflict_strategy: ConflictStrategy = "merge_non_empty"
    error_mode: ErrorMode = "all_or_nothing"
    default_group_id: int | None = None
    # Wide-table options
    sheet_name: str | None = None
    column_mapping: dict[str, str] = Field(default_factory=dict)  # target_field -> source_header
    store_unmapped_to_extra: bool = True


class ExcelRowResult(BaseModel):
    sheet: str
    row_num: int
    entity_type: str
    natural_key: str
    operation: Literal["create", "update", "skip", "error"]
    reason: str | None = None


class ExcelPreviewOut(BaseModel):
    upload_id: str
    mode: ImportMode
    total_rows: int
    create_count: int
    update_count: int
    skip_count: int
    error_count: int
    can_commit: bool
    rows: list[ExcelRowResult]
    errors: list[ExcelRowResult]


class ExcelCommitOut(BaseModel):
    batch_id: int
    status: str
    total_rows: int
    created_count: int
    updated_count: int
    skipped_count: int
    failed_count: int
    errors: list[ExcelRowResult]


class ImportBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int | None
    filename: str
    mode: str
    conflict_strategy: str
    error_mode: str
    total_rows: int
    created_count: int
    updated_count: int
    skipped_count: int
    failed_count: int
    status: str
    created_at: datetime


class ExcelExportRequest(BaseModel):
    mode: ImportMode = "multi_sheet"
    group_id: int | None = None
    env: str | None = None
