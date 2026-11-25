"""Schemas for Users, Roles, OpsGroups, NetworkZones, Dicts, Tags, Audit, and Dashboard."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.auth import ClusterAssignmentOut, UserAssignmentOut


class UserAssignmentInput(BaseModel):
    group_id: int | None = None
    role_id: int


class ClusterAssignmentInput(BaseModel):
    cluster_id: int
    role_id: int


class UserCreate(BaseModel):
    username: str = Field(..., min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(..., min_length=1, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)
    is_active: bool = True
    allow_public_assets: bool = False
    must_change_password: bool = True
    assignments: list[UserAssignmentInput] = Field(default_factory=list)
    cluster_assignments: list[ClusterAssignmentInput] = Field(default_factory=list)


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    allow_public_assets: bool | None = None
    must_change_password: bool | None = None
    assignments: list[UserAssignmentInput] | None = None
    cluster_assignments: list[ClusterAssignmentInput] | None = None


class UserResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=8, max_length=128)
    must_change_password: bool = True


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    email: str | None
    auth_source: str
    is_active: bool
    allow_public_assets: bool = False
    must_change_password: bool
    failed_login_attempts: int
    locked_until: datetime | None
    last_login_at: datetime | None
    created_at: datetime
    assignments: list[UserAssignmentOut] = Field(default_factory=list)
    cluster_assignments: list[ClusterAssignmentOut] = Field(default_factory=list)


class RoleCreate(BaseModel):
    code: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-z0-9_]+$")
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = None
    permissions: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    permissions: list[str] | None = None


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None
    is_builtin: bool
    permissions: list[str]
    created_at: datetime
    updated_at: datetime


class OpsGroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    code: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    parent_id: int | None = None
    description: str | None = None
    extra_attrs: dict[str, Any] = Field(default_factory=dict)


class OpsGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    parent_id: int | None = None
    description: str | None = None
    extra_attrs: dict[str, Any] | None = None


class OpsGroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    parent_id: int | None
    description: str | None
    extra_attrs: dict[str, Any]
    member_count: int = 0
    created_at: datetime
    updated_at: datetime


class GroupMemberAssignRequest(BaseModel):
    user_id: int
    role_id: int


class GroupMemberOut(BaseModel):
    assignment_id: int
    user_id: int
    username: str
    display_name: str
    email: str | None
    is_active: bool
    role_id: int
    role_code: str
    role_name: str


class NetworkZoneCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    code: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    location: str | None = Field(default=None, max_length=128)
    is_default: bool = False
    description: str | None = None
    extra_attrs: dict[str, Any] = Field(default_factory=dict)


class NetworkZoneUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    location: str | None = Field(default=None, max_length=128)
    is_default: bool | None = None
    description: str | None = None
    extra_attrs: dict[str, Any] | None = None


class NetworkZoneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    location: str | None
    is_default: bool
    description: str | None
    extra_attrs: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class DictItemCreate(BaseModel):
    category: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-z0-9_]+$")
    code: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    label: str = Field(..., min_length=1, max_length=128)
    color: str | None = Field(default="#2080f0", max_length=32)
    sort_order: int = 0
    is_enabled: bool = True
    description: str | None = None


class DictItemUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=128)
    color: str | None = Field(default=None, max_length=32)
    sort_order: int | None = None
    is_enabled: bool | None = None
    description: str | None = None


class DictItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category: str
    code: str
    label: str
    color: str | None
    sort_order: int
    is_builtin: bool
    is_enabled: bool
    description: str | None
    created_at: datetime
    updated_at: datetime


class TagCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    color: str | None = Field(default="#2080f0", max_length=32)
    description: str | None = None


class TagUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    color: str | None = Field(default=None, max_length=32)
    description: str | None = None


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    color: str | None
    description: str | None
    created_at: datetime
    updated_at: datetime


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_id: int | None
    actor_username: str
    auth_method: str
    client_ip: str
    action: str
    target_type: str
    target_id: int | None
    target_label: str | None
    group_id: int | None
    import_batch_id: int | None
    diff_before: dict[str, Any] | None
    diff_after: dict[str, Any] | None
    created_at: datetime


class DashboardSummaryOut(BaseModel):
    counts: dict[str, int]
    recent_audits: list[AuditLogOut]
