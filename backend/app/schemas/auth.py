"""Authentication, current user profile, and Personal Access Token schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    must_change_password: bool


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


class UserAssignmentOut(BaseModel):
    id: int
    group_id: int | None
    group_name: str | None = None
    role_id: int
    role_code: str
    role_name: str


class ClusterAssignmentOut(BaseModel):
    id: int
    cluster_id: int
    cluster_name: str | None = None
    role_id: int
    role_code: str
    role_name: str


class CurrentUserOut(BaseModel):
    id: int
    username: str
    display_name: str
    email: str | None
    auth_source: str
    is_active: bool
    allow_public_assets: bool = False
    must_change_password: bool
    last_login_at: datetime | None
    is_superadmin: bool
    global_permissions: list[str]
    group_permissions: dict[int, list[str]]
    cluster_permissions: dict[int, list[str]] = Field(default_factory=dict)
    assignments: list[UserAssignmentOut]
    cluster_assignments: list[ClusterAssignmentOut] = Field(default_factory=list)


class ApiTokenCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    scope: Literal["read_only", "inherit"] = "read_only"
    expires_in_days: int | None = Field(default=90, ge=1, le=3650)


class ApiTokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    token_prefix: str
    scope: str
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiTokenCreatedOut(ApiTokenOut):
    token: str
