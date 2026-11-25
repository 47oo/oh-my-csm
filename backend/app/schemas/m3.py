"""Pydantic schemas for M3: Credentials, Global Search, Batch Operations, Recycle Bin, and Dashboard Alerts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.system import AuditLogOut

# ---------------- Credentials ----------------

CredentialTargetType = Literal["cluster", "device", "nic", "service", "url"]


class CredentialCreate(BaseModel):
    target_type: CredentialTargetType
    target_id: int
    purpose: str = Field(..., min_length=1, max_length=128)
    username: str | None = Field(default=None, max_length=128)
    secret: str = Field(..., min_length=1, max_length=4096)
    notes: str | None = None


class CredentialUpdate(BaseModel):
    purpose: str | None = Field(default=None, min_length=1, max_length=128)
    username: str | None = Field(default=None, max_length=128)
    secret: str | None = Field(default=None, min_length=1, max_length=4096)
    notes: str | None = None


class CredentialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    target_type: str
    target_id: int
    group_id: int | None
    purpose: str
    username: str | None
    key_version: str
    notes: str | None
    has_secret: bool = True
    created_at: datetime
    updated_at: datetime


class CredentialRevealOut(BaseModel):
    id: int
    purpose: str
    username: str | None
    secret: str


# ---------------- Global Search ----------------


class SearchHit(BaseModel):
    entity_type: Literal["cluster", "device", "nic", "subnet", "ip", "service", "url"]
    id: int
    title: str
    subtitle: str | None = None
    badge: str | None = None
    status: str | None = None
    group_name: str | None = None


class SearchGroup(BaseModel):
    entity_type: str
    label: str
    items: list[SearchHit]


class GlobalSearchOut(BaseModel):
    query: str
    total: int
    groups: list[SearchGroup]


# ---------------- Batch Operations ----------------

BatchEntity = Literal["clusters", "devices", "nics", "subnets", "ips", "services", "urls"]
BatchAction = Literal["tag_add", "tag_remove", "move_group", "update_status", "delete"]


class BatchOperationRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=500)
    action: BatchAction
    tag_ids: list[int] | None = None
    group_id: int | None = None
    status: str | None = None
    env: str | None = None


class BatchFailureItem(BaseModel):
    id: int
    label: str
    reason: str


class BatchOperationResponse(BaseModel):
    succeeded_ids: list[int]
    failed: list[BatchFailureItem]


# ---------------- Recycle Bin ----------------


class RecycleBinItemOut(BaseModel):
    entity_type: str
    id: int
    label: str
    subtitle: str | None = None
    group_id: int | None = None
    group_name: str | None = None
    deleted_at: datetime


# ---------------- Enhanced Dashboard Alerts ----------------


class TopSubnetAlert(BaseModel):
    id: int
    name: str
    cidr: str
    allocated_count: int
    total_capacity: int
    utilization_pct: float


class ExpiringUrlAlert(BaseModel):
    id: int
    name: str
    domain: str
    full_url: str
    ssl_expires_at: datetime
    days_remaining: int


class DuplicateHostnameAlert(BaseModel):
    hostname: str
    count: int


class DashboardEnhancedOut(BaseModel):
    counts: dict[str, int]
    top_subnets: list[TopSubnetAlert]
    expiring_urls: list[ExpiringUrlAlert]
    incomplete_counts: dict[str, int]
    duplicate_hostnames: list[DuplicateHostnameAlert]
    recent_audits: list[AuditLogOut]
