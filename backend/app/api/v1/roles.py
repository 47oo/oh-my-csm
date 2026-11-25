"""Role and permission catalog endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import (
    ALL_PERMISSION_CODES,
    PERMISSION_CATALOG,
    AuthContext,
    get_current_auth,
)
from app.models.auth import ClusterACL, Role, UserGroupRole
from app.schemas.common import MessageResponse, PermissionItem
from app.schemas.system import RoleCreate, RoleOut, RoleUpdate
from app.services.audit import record_audit

router = APIRouter(prefix="/roles", tags=["角色与权限"])


def _validate_permissions(perms: list[str]) -> list[str]:
    invalid = set(perms) - ALL_PERMISSION_CODES
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"包含无效权限码: {', '.join(sorted(invalid))}",
        )
    return sorted(set(perms))


@router.get("/permissions", response_model=list[PermissionItem])
async def list_permissions(
    _: AuthContext = Depends(get_current_auth),
) -> list[PermissionItem]:
    return [PermissionItem(**item) for item in PERMISSION_CATALOG]


@router.get("", response_model=list[RoleOut])
async def list_roles(
    _: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[RoleOut]:
    rows = list((await db.execute(select(Role).order_by(Role.id.asc()))).scalars().all())
    return [RoleOut.model_validate(r) for r in rows]


@router.post("", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
async def create_role(
    body: RoleCreate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> RoleOut:
    auth.require_global("system:manage")

    exists = (await db.execute(select(Role.id).where(Role.code == body.code))).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"角色编码 '{body.code}' 已存在",
        )

    perms = _validate_permissions(body.permissions)
    role = Role(
        code=body.code,
        name=body.name.strip(),
        description=body.description,
        is_builtin=False,
        permissions=perms,
    )
    db.add(role)
    await db.flush()

    await record_audit(
        db,
        action="create",
        target_type="role",
        target_id=role.id,
        target_label=role.name,
        after={"code": role.code, "name": role.name, "permissions": role.permissions},
        auth=auth,
    )
    return RoleOut.model_validate(role)


@router.put("/{role_id}", response_model=RoleOut)
async def update_role(
    role_id: int,
    body: RoleUpdate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> RoleOut:
    auth.require_global("system:manage")

    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="角色不存在")

    before = {"name": role.name, "description": role.description, "permissions": role.permissions}
    if body.name is not None:
        role.name = body.name.strip()
    if body.description is not None:
        role.description = body.description
    if body.permissions is not None:
        if role.code == "super_admin":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="内置超级管理员角色的权限不可修改",
            )
        role.permissions = _validate_permissions(body.permissions)
    await db.flush()

    await record_audit(
        db,
        action="update",
        target_type="role",
        target_id=role.id,
        target_label=role.name,
        before=before,
        after={"name": role.name, "description": role.description, "permissions": role.permissions},
        auth=auth,
    )
    return RoleOut.model_validate(role)


@router.delete("/{role_id}", response_model=MessageResponse)
async def delete_role(
    role_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    auth.require_global("system:manage")

    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="角色不存在")
    if role.is_builtin:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="内置角色不可删除")

    used_group_count = int(
        (
            await db.execute(
                select(func.count()).select_from(UserGroupRole).where(UserGroupRole.role_id == role.id)
            )
        ).scalar_one()
    )
    used_cluster_count = int(
        (
            await db.execute(
                select(func.count()).select_from(ClusterACL).where(ClusterACL.role_id == role.id)
            )
        ).scalar_one()
    )
    used_count = used_group_count + used_cluster_count
    if used_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"该角色仍被 {used_count} 条用户授权引用（分组 {used_group_count} / 集群 {used_cluster_count}），请先解除绑定",
        )

    await db.delete(role)
    await db.flush()
    await record_audit(
        db,
        action="delete",
        target_type="role",
        target_id=role_id,
        target_label=role.name,
        before={"code": role.code, "name": role.name},
        auth=auth,
    )
    return MessageResponse(message=f"角色 '{role.name}' 已删除")
