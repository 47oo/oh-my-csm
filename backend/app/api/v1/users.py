"""User management endpoints with Group + Cluster ACL assignments and public-asset visibility flag."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.permissions import AuthContext, get_current_auth
from app.core.security import hash_password, utc_now, validate_password_strength
from app.models.assets import Cluster
from app.models.auth import ClusterACL, Role, User, UserGroupRole
from app.models.system import OpsGroup
from app.schemas.auth import ClusterAssignmentOut, UserAssignmentOut
from app.schemas.common import MessageResponse, PageResponse
from app.schemas.system import (
    ClusterAssignmentInput,
    UserAssignmentInput,
    UserCreate,
    UserOut,
    UserResetPasswordRequest,
    UserUpdate,
)
from app.services.audit import record_audit

router = APIRouter(prefix="/users", tags=["用户管理"])


def _serialize_user(user: User) -> UserOut:
    assignments = [
        UserAssignmentOut(
            id=a.id,
            group_id=a.group_id,
            group_name=a.group.name if a.group else None,
            role_id=a.role_id,
            role_code=a.role.code,
            role_name=a.role.name,
        )
        for a in (user.assignments or [])
        if a.role is not None
    ]
    cluster_assignments = [
        ClusterAssignmentOut(
            id=c.id,
            cluster_id=c.cluster_id,
            cluster_name=c.cluster.name if c.cluster else None,
            role_id=c.role_id,
            role_code=c.role.code,
            role_name=c.role.name,
        )
        for c in (user.cluster_acls or [])
        if c.role is not None and (c.cluster is None or c.cluster.deleted_at is None)
    ]
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        auth_source=user.auth_source,
        is_active=user.is_active,
        allow_public_assets=bool(user.allow_public_assets),
        must_change_password=user.must_change_password,
        failed_login_attempts=user.failed_login_attempts,
        locked_until=user.locked_until,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        assignments=assignments,
        cluster_assignments=cluster_assignments,
    )


async def _load_user_full(db: AsyncSession, user_id: int) -> User:
    return (
        await db.execute(
            select(User)
            .options(
                selectinload(User.assignments).selectinload(UserGroupRole.role),
                selectinload(User.assignments).selectinload(UserGroupRole.group),
                selectinload(User.cluster_acls).selectinload(ClusterACL.role),
                selectinload(User.cluster_acls).selectinload(ClusterACL.cluster),
            )
            .where(User.id == user_id)
        )
    ).scalar_one()


async def _replace_assignments(
    db: AsyncSession,
    user_id: int,
    items: list[UserAssignmentInput],
) -> None:
    existing = list(
        (await db.execute(select(UserGroupRole).where(UserGroupRole.user_id == user_id)))
        .scalars()
        .all()
    )
    for row in existing:
        await db.delete(row)
    await db.flush()

    seen: set[tuple[int | None, int]] = set()
    for item in items:
        key = (item.group_id, item.role_id)
        if key in seen:
            continue
        seen.add(key)
        role = (await db.execute(select(Role).where(Role.id == item.role_id))).scalar_one_or_none()
        if role is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"角色 ID={item.role_id} 不存在",
            )
        if item.group_id is not None:
            group = (
                await db.execute(
                    select(OpsGroup).where(
                        OpsGroup.id == item.group_id,
                        OpsGroup.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if group is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"分组 ID={item.group_id} 不存在",
                )
        db.add(UserGroupRole(user_id=user_id, group_id=item.group_id, role_id=item.role_id))
    await db.flush()


async def _replace_cluster_assignments(
    db: AsyncSession,
    user_id: int,
    items: list[ClusterAssignmentInput],
) -> None:
    existing = list(
        (await db.execute(select(ClusterACL).where(ClusterACL.user_id == user_id))).scalars().all()
    )
    for row in existing:
        await db.delete(row)
    await db.flush()

    seen: set[tuple[int, int]] = set()
    for item in items:
        key = (item.cluster_id, item.role_id)
        if key in seen:
            continue
        seen.add(key)
        cluster = (
            await db.execute(
                select(Cluster.id).where(
                    Cluster.id == item.cluster_id, Cluster.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()
        if cluster is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"集群 ID={item.cluster_id} 不存在",
            )
        role = (await db.execute(select(Role).where(Role.id == item.role_id))).scalar_one_or_none()
        if role is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"角色 ID={item.role_id} 不存在",
            )
        db.add(ClusterACL(user_id=user_id, cluster_id=item.cluster_id, role_id=item.role_id))
    await db.flush()


@router.get("", response_model=PageResponse[UserOut])
async def list_users(
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> PageResponse[UserOut]:
    if (
        not auth.has_global_permission("system:manage")
        and not auth.manageable_group_ids
        and not auth.manageable_cluster_ids
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="缺少查看用户列表权限",
        )

    base = select(User).where(User.deleted_at.is_(None))
    if q:
        pattern = f"%{q.strip()}%"
        base = base.where(
            or_(
                User.username.ilike(pattern),
                User.display_name.ilike(pattern),
                User.email.ilike(pattern),
            )
        )

    total = int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one())
    stmt = (
        base.options(
            selectinload(User.assignments).selectinload(UserGroupRole.role),
            selectinload(User.assignments).selectinload(UserGroupRole.group),
            selectinload(User.cluster_acls).selectinload(ClusterACL.role),
            selectinload(User.cluster_acls).selectinload(ClusterACL.cluster),
        )
        .order_by(User.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return PageResponse(
        items=[_serialize_user(u) for u in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    auth.require_global("system:manage")

    username = body.username.strip()
    exists = (
        await db.execute(
            select(User.id).where(User.username == username, User.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"用户名 '{username}' 已存在",
        )

    try:
        validate_password_strength(body.password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    user = User(
        username=username,
        display_name=body.display_name.strip(),
        email=body.email.strip() if body.email else None,
        password_hash=hash_password(body.password),
        auth_source="local",
        is_active=body.is_active,
        allow_public_assets=body.allow_public_assets,
        must_change_password=body.must_change_password,
    )
    db.add(user)
    await db.flush()

    if body.assignments:
        await _replace_assignments(db, user.id, body.assignments)
    if body.cluster_assignments:
        await _replace_cluster_assignments(db, user.id, body.cluster_assignments)

    loaded = await _load_user_full(db, user.id)

    await record_audit(
        db,
        action="create",
        target_type="user",
        target_id=loaded.id,
        target_label=loaded.username,
        after={
            "username": loaded.username,
            "display_name": loaded.display_name,
            "email": loaded.email,
            "is_active": loaded.is_active,
            "allow_public_assets": loaded.allow_public_assets,
            "assignments": [
                {"group_id": a.group_id, "role_id": a.role_id} for a in loaded.assignments
            ],
            "cluster_assignments": [
                {"cluster_id": c.cluster_id, "role_id": c.role_id} for c in loaded.cluster_acls
            ],
        },
        auth=auth,
    )
    return _serialize_user(loaded)


@router.put("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    body: UserUpdate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    auth.require_global("system:manage")

    user = (
        await db.execute(
            select(User)
            .options(
                selectinload(User.assignments).selectinload(UserGroupRole.role),
                selectinload(User.assignments).selectinload(UserGroupRole.group),
                selectinload(User.cluster_acls).selectinload(ClusterACL.role),
                selectinload(User.cluster_acls).selectinload(ClusterACL.cluster),
            )
            .where(User.id == user_id, User.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    before = {
        "display_name": user.display_name,
        "email": user.email,
        "is_active": user.is_active,
        "allow_public_assets": user.allow_public_assets,
        "must_change_password": user.must_change_password,
        "assignments": [{"group_id": a.group_id, "role_id": a.role_id} for a in user.assignments],
        "cluster_assignments": [
            {"cluster_id": c.cluster_id, "role_id": c.role_id} for c in user.cluster_acls
        ],
    }

    if body.is_active is False and user.id == auth.user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能禁用当前登录账号")

    if body.display_name is not None:
        user.display_name = body.display_name.strip()
    if body.email is not None:
        user.email = body.email.strip() or None
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.allow_public_assets is not None:
        user.allow_public_assets = body.allow_public_assets
    if body.must_change_password is not None:
        user.must_change_password = body.must_change_password
    await db.flush()

    if body.assignments is not None:
        await _replace_assignments(db, user.id, body.assignments)
    if body.cluster_assignments is not None:
        await _replace_cluster_assignments(db, user.id, body.cluster_assignments)

    db.expire(user)
    loaded = await _load_user_full(db, user_id)

    after = {
        "display_name": loaded.display_name,
        "email": loaded.email,
        "is_active": loaded.is_active,
        "allow_public_assets": loaded.allow_public_assets,
        "must_change_password": loaded.must_change_password,
        "assignments": [{"group_id": a.group_id, "role_id": a.role_id} for a in loaded.assignments],
        "cluster_assignments": [
            {"cluster_id": c.cluster_id, "role_id": c.role_id} for c in loaded.cluster_acls
        ],
    }

    await record_audit(
        db,
        action="update",
        target_type="user",
        target_id=loaded.id,
        target_label=loaded.username,
        before=before,
        after=after,
        auth=auth,
    )
    return _serialize_user(loaded)


@router.post("/{user_id}/unlock", response_model=MessageResponse)
async def unlock_user(
    user_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    auth.require_global("system:manage")
    user = (
        await db.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    user.failed_login_attempts = 0
    user.locked_until = None
    await db.flush()
    await record_audit(
        db,
        action="unlock",
        target_type="user",
        target_id=user.id,
        target_label=user.username,
        auth=auth,
    )
    return MessageResponse(message=f"账号 '{user.username}' 已解锁")


@router.post("/{user_id}/reset-password", response_model=MessageResponse)
async def reset_user_password(
    user_id: int,
    body: UserResetPasswordRequest,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    auth.require_global("system:manage")
    user = (
        await db.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    try:
        validate_password_strength(body.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    user.password_hash = hash_password(body.new_password)
    user.must_change_password = body.must_change_password
    user.failed_login_attempts = 0
    user.locked_until = None
    await db.flush()

    await record_audit(
        db,
        action="reset_password",
        target_type="user",
        target_id=user.id,
        target_label=user.username,
        auth=auth,
    )
    return MessageResponse(message=f"账号 '{user.username}' 密码已重置")


@router.delete("/{user_id}", response_model=MessageResponse)
async def delete_user(
    user_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    auth.require_global("system:manage")
    if user_id == auth.user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能删除当前登录账号")

    user = (
        await db.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    user.deleted_at = utc_now()
    user.is_active = False
    await db.flush()

    await record_audit(
        db,
        action="delete",
        target_type="user",
        target_id=user.id,
        target_label=user.username,
        before={"username": user.username, "display_name": user.display_name},
        auth=auth,
    )
    return MessageResponse(message=f"用户 '{user.username}' 已删除")
