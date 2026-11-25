"""OpsGroup CRUD and group-level member management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.permissions import AuthContext, get_current_auth
from app.core.security import utc_now
from app.models.auth import Role, User, UserGroupRole
from app.models.system import OpsGroup
from app.schemas.common import MessageResponse
from app.schemas.system import (
    GroupMemberAssignRequest,
    GroupMemberOut,
    OpsGroupCreate,
    OpsGroupOut,
    OpsGroupUpdate,
)
from app.services.audit import record_audit

router = APIRouter(prefix="/groups", tags=["运维分组"])


@router.get("", response_model=list[OpsGroupOut])
async def list_groups(
    q: str | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[OpsGroupOut]:
    stmt = select(OpsGroup).where(OpsGroup.deleted_at.is_(None))
    if not auth.has_global_permission("system:manage") and not auth.has_global_permission("asset:read"):
        visible_ids = set(auth.group_permissions.keys())
        if not visible_ids:
            return []
        stmt = stmt.where(OpsGroup.id.in_(visible_ids))

    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(OpsGroup.name.ilike(pattern), OpsGroup.code.ilike(pattern)))

    stmt = stmt.order_by(OpsGroup.id.asc())
    groups = list((await db.execute(stmt)).scalars().all())

    if not groups:
        return []

    counts_stmt = (
        select(UserGroupRole.group_id, func.count(func.distinct(UserGroupRole.user_id)))
        .where(UserGroupRole.group_id.in_([g.id for g in groups]))
        .group_by(UserGroupRole.group_id)
    )
    count_map = {gid: cnt for gid, cnt in (await db.execute(counts_stmt)).all()}

    results: list[OpsGroupOut] = []
    for g in groups:
        out = OpsGroupOut.model_validate(g)
        out.member_count = int(count_map.get(g.id, 0))
        results.append(out)
    return results


@router.post("", response_model=OpsGroupOut, status_code=status.HTTP_201_CREATED)
async def create_group(
    body: OpsGroupCreate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> OpsGroupOut:
    auth.require_global("system:manage")

    code = body.code.strip()
    exists = (
        await db.execute(
            select(OpsGroup.id).where(OpsGroup.code == code, OpsGroup.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"分组编码 '{code}' 已存在",
        )

    group = OpsGroup(
        name=body.name.strip(),
        code=code,
        parent_id=body.parent_id,
        description=body.description,
        extra_attrs=body.extra_attrs,
        created_by=auth.user.id,
        updated_by=auth.user.id,
    )
    db.add(group)
    await db.flush()

    await record_audit(
        db,
        action="create",
        target_type="ops_group",
        target_id=group.id,
        target_label=group.name,
        group_id=group.id,
        after={"name": group.name, "code": group.code, "description": group.description},
        auth=auth,
    )
    return OpsGroupOut.model_validate(group)


@router.put("/{group_id}", response_model=OpsGroupOut)
async def update_group(
    group_id: int,
    body: OpsGroupUpdate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> OpsGroupOut:
    if not auth.has_global_permission("system:manage"):
        auth.require_group_permission("group:member_manage", group_id)

    group = (
        await db.execute(
            select(OpsGroup).where(OpsGroup.id == group_id, OpsGroup.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分组不存在")

    before = {
        "name": group.name,
        "parent_id": group.parent_id,
        "description": group.description,
        "extra_attrs": group.extra_attrs,
    }
    if body.name is not None:
        group.name = body.name.strip()
    if body.parent_id is not None:
        if body.parent_id == group.id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="父分组不能是自身")
        group.parent_id = body.parent_id
    if body.description is not None:
        group.description = body.description
    if body.extra_attrs is not None:
        group.extra_attrs = body.extra_attrs
    group.updated_by = auth.user.id
    await db.flush()

    await record_audit(
        db,
        action="update",
        target_type="ops_group",
        target_id=group.id,
        target_label=group.name,
        group_id=group.id,
        before=before,
        after={
            "name": group.name,
            "parent_id": group.parent_id,
            "description": group.description,
            "extra_attrs": group.extra_attrs,
        },
        auth=auth,
    )
    return OpsGroupOut.model_validate(group)


@router.delete("/{group_id}", response_model=MessageResponse)
async def delete_group(
    group_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    auth.require_global("system:manage")

    group = (
        await db.execute(
            select(OpsGroup).where(OpsGroup.id == group_id, OpsGroup.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分组不存在")

    child_count = int(
        (
            await db.execute(
                select(func.count())
                .select_from(OpsGroup)
                .where(OpsGroup.parent_id == group.id, OpsGroup.deleted_at.is_(None))
            )
        ).scalar_one()
    )
    if child_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"该分组下仍有 {child_count} 个子分组，无法删除",
        )

    group.deleted_at = utc_now()
    group.updated_by = auth.user.id
    await db.flush()

    await record_audit(
        db,
        action="delete",
        target_type="ops_group",
        target_id=group.id,
        target_label=group.name,
        group_id=group.id,
        before={"name": group.name, "code": group.code},
        auth=auth,
    )
    return MessageResponse(message=f"分组 '{group.name}' 已删除")


@router.get("/{group_id}/members", response_model=list[GroupMemberOut])
async def list_group_members(
    group_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[GroupMemberOut]:
    auth.require_group_permission("asset:read", group_id)

    stmt = (
        select(UserGroupRole)
        .options(selectinload(UserGroupRole.user), selectinload(UserGroupRole.role))
        .where(UserGroupRole.group_id == group_id)
        .order_by(UserGroupRole.id.asc())
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return [
        GroupMemberOut(
            assignment_id=r.id,
            user_id=r.user.id,
            username=r.user.username,
            display_name=r.user.display_name,
            email=r.user.email,
            is_active=r.user.is_active,
            role_id=r.role.id,
            role_code=r.role.code,
            role_name=r.role.name,
        )
        for r in rows
        if r.user is not None and r.user.deleted_at is None and r.role is not None
    ]


@router.post("/{group_id}/members", response_model=GroupMemberOut, status_code=status.HTTP_201_CREATED)
async def add_group_member(
    group_id: int,
    body: GroupMemberAssignRequest,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> GroupMemberOut:
    auth.require_group_permission("group:member_manage", group_id)

    group = (
        await db.execute(
            select(OpsGroup).where(OpsGroup.id == group_id, OpsGroup.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分组不存在")

    user = (
        await db.execute(select(User).where(User.id == body.user_id, User.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    role = (await db.execute(select(Role).where(Role.id == body.role_id))).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="角色不存在")

    if role.code == "super_admin" and not auth.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="非超级管理员不可分配超级管理员角色",
        )

    exists = (
        await db.execute(
            select(UserGroupRole).where(
                UserGroupRole.user_id == body.user_id,
                UserGroupRole.group_id == group_id,
                UserGroupRole.role_id == body.role_id,
            )
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该成员已拥有此分组角色")

    assignment = UserGroupRole(user_id=body.user_id, group_id=group_id, role_id=body.role_id)
    db.add(assignment)
    await db.flush()

    await record_audit(
        db,
        action="assign_member",
        target_type="ops_group",
        target_id=group.id,
        target_label=f"{group.name} <- {user.username} ({role.name})",
        group_id=group.id,
        after={"user_id": user.id, "username": user.username, "role_code": role.code},
        auth=auth,
    )

    return GroupMemberOut(
        assignment_id=assignment.id,
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        is_active=user.is_active,
        role_id=role.id,
        role_code=role.code,
        role_name=role.name,
    )


@router.delete("/{group_id}/members/{assignment_id}", response_model=MessageResponse)
async def remove_group_member(
    group_id: int,
    assignment_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    auth.require_group_permission("group:member_manage", group_id)

    assignment = (
        await db.execute(
            select(UserGroupRole)
            .options(selectinload(UserGroupRole.user), selectinload(UserGroupRole.role))
            .where(UserGroupRole.id == assignment_id, UserGroupRole.group_id == group_id)
        )
    ).scalar_one_or_none()
    if assignment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="成员授权记录不存在")

    username = assignment.user.username if assignment.user else str(assignment.user_id)
    role_code = assignment.role.code if assignment.role else str(assignment.role_id)
    await db.delete(assignment)
    await db.flush()

    await record_audit(
        db,
        action="remove_member",
        target_type="ops_group",
        target_id=group_id,
        target_label=f"group:{group_id} - {username}",
        group_id=group_id,
        before={"user_id": assignment.user_id, "username": username, "role_code": role_code},
        auth=auth,
    )
    return MessageResponse(message="已移除成员权限")
