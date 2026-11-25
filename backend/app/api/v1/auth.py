"""Authentication, password change, current user profile, and PAT endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth_provider import LocalAuthProvider
from app.core.config import get_settings
from app.core.database import get_db
from app.core.permissions import (
    AuthContext,
    _extract_client_ip,
    bearer_scheme,
    get_current_auth,
    get_current_auth_allow_password_change,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_api_token,
    hash_password,
    utc_now,
    validate_password_strength,
    verify_password,
)
from app.models.auth import ApiToken, ClusterACL, RevokedToken, User, UserGroupRole
from app.schemas.auth import (
    ApiTokenCreate,
    ApiTokenCreatedOut,
    ApiTokenOut,
    ChangePasswordRequest,
    ClusterAssignmentOut,
    CurrentUserOut,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    TokenResponse,
    UserAssignmentOut,
)
from app.schemas.common import MessageResponse
from app.services.audit import record_audit

router = APIRouter(prefix="/auth", tags=["认证与令牌"])
local_provider = LocalAuthProvider()


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    client_ip = _extract_client_ip(request)
    result = await local_provider.authenticate(db, body.username.strip(), body.password)
    if result.user is None:
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=result.error or "用户名或密码错误",
        )

    settings = get_settings()
    access_token, _, _ = create_access_token(result.user.id, result.user.username)
    refresh_token, _, _ = create_refresh_token(result.user.id, result.user.username)

    await record_audit(
        db,
        action="login",
        target_type="user",
        target_id=result.user.id,
        target_label=result.user.username,
        actor_id=result.user.id,
        actor_username=result.user.username,
        auth_method="jwt",
        client_ip=client_ip,
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        must_change_password=result.user.must_change_password,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    try:
        payload = decode_token(body.refresh_token)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="刷新令牌无效或已过期",
        ) from exc

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌类型错误",
        )

    jti = payload.get("jti")
    if jti:
        revoked = (
            await db.execute(select(RevokedToken.id).where(RevokedToken.jti == jti))
        ).scalar_one_or_none()
        if revoked is not None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="刷新令牌已被吊销",
            )
        exp_ts = payload.get("exp")
        exp_dt = (
            datetime.fromtimestamp(exp_ts, tz=timezone.utc)
            if exp_ts
            else utc_now() + timedelta(days=7)
        )
        db.add(RevokedToken(jti=jti, token_type="refresh", expires_at=exp_dt))

    user_id = int(payload["sub"])
    user = (
        await db.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已禁用",
        )

    settings = get_settings()
    access_token, _, _ = create_access_token(user.id, user.username)
    new_refresh, _, _ = create_refresh_token(user.id, user.username)
    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        must_change_password=user.must_change_password,
    )


@router.post("/logout", response_model=MessageResponse)
async def logout(
    body: LogoutRequest | None = None,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    auth: AuthContext = Depends(get_current_auth_allow_password_change),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    tokens_to_revoke: list[str] = []
    if credentials and credentials.credentials and auth.auth_method == "jwt":
        tokens_to_revoke.append(credentials.credentials)
    if body and body.refresh_token:
        tokens_to_revoke.append(body.refresh_token)

    for raw in tokens_to_revoke:
        try:
            payload = decode_token(raw)
        except jwt.PyJWTError:
            continue
        jti = payload.get("jti")
        if not jti:
            continue
        exists = (
            await db.execute(select(RevokedToken.id).where(RevokedToken.jti == jti))
        ).scalar_one_or_none()
        if exists is None:
            exp_ts = payload.get("exp")
            exp_dt = (
                datetime.fromtimestamp(exp_ts, tz=timezone.utc)
                if exp_ts
                else utc_now() + timedelta(hours=1)
            )
            db.add(
                RevokedToken(
                    jti=jti,
                    token_type=str(payload.get("type", "access")),
                    expires_at=exp_dt,
                )
            )

    await record_audit(
        db,
        action="logout",
        target_type="user",
        target_id=auth.user.id,
        target_label=auth.user.username,
        auth=auth,
    )
    return MessageResponse(message="已安全退出登录")


@router.post("/change-password", response_model=MessageResponse)
async def change_password(
    body: ChangePasswordRequest,
    auth: AuthContext = Depends(get_current_auth_allow_password_change),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    user = auth.user
    if user.auth_source != "local":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="外部 SSO 账号不支持在本地修改密码",
        )
    if not user.password_hash or not verify_password(body.old_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="原密码不正确",
        )
    if body.old_password == body.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="新密码不能与原密码相同",
        )
    try:
        validate_password_strength(body.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    await db.flush()

    await record_audit(
        db,
        action="change_password",
        target_type="user",
        target_id=user.id,
        target_label=user.username,
        auth=auth,
    )
    return MessageResponse(message="密码修改成功")


@router.get("/me", response_model=CurrentUserOut)
async def get_me(
    auth: AuthContext = Depends(get_current_auth_allow_password_change),
    db: AsyncSession = Depends(get_db),
) -> CurrentUserOut:
    stmt = (
        select(UserGroupRole)
        .options(selectinload(UserGroupRole.role), selectinload(UserGroupRole.group))
        .where(UserGroupRole.user_id == auth.user.id)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    assignments = [
        UserAssignmentOut(
            id=r.id,
            group_id=r.group_id,
            group_name=r.group.name if r.group else None,
            role_id=r.role_id,
            role_code=r.role.code,
            role_name=r.role.name,
        )
        for r in rows
        if r.role is not None
    ]

    acl_stmt = (
        select(ClusterACL)
        .options(selectinload(ClusterACL.role), selectinload(ClusterACL.cluster))
        .where(ClusterACL.user_id == auth.user.id)
    )
    acl_rows = list((await db.execute(acl_stmt)).scalars().all())
    cluster_assignments = [
        ClusterAssignmentOut(
            id=a.id,
            cluster_id=a.cluster_id,
            cluster_name=a.cluster.name if a.cluster else None,
            role_id=a.role_id,
            role_code=a.role.code,
            role_name=a.role.name,
        )
        for a in acl_rows
        if a.role is not None and (a.cluster is None or a.cluster.deleted_at is None)
    ]

    return CurrentUserOut(
        id=auth.user.id,
        username=auth.user.username,
        display_name=auth.user.display_name,
        email=auth.user.email,
        auth_source=auth.user.auth_source,
        is_active=auth.user.is_active,
        allow_public_assets=auth.allow_public_assets,
        must_change_password=auth.user.must_change_password,
        last_login_at=auth.user.last_login_at,
        is_superadmin=auth.is_superadmin,
        global_permissions=sorted(auth.global_permissions),
        group_permissions={gid: sorted(perms) for gid, perms in auth.group_permissions.items()},
        cluster_permissions={cid: sorted(perms) for cid, perms in auth.cluster_permissions.items()},
        assignments=assignments,
        cluster_assignments=cluster_assignments,
    )


@router.get("/tokens", response_model=list[ApiTokenOut])
async def list_my_tokens(
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[ApiTokenOut]:
    stmt = (
        select(ApiToken)
        .where(ApiToken.user_id == auth.user.id)
        .order_by(ApiToken.created_at.desc())
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return [ApiTokenOut.model_validate(r) for r in rows]


@router.post("/tokens", response_model=ApiTokenCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_my_token(
    body: ApiTokenCreate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> ApiTokenCreatedOut:
    if auth.auth_method == "pat":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="不允许使用 API Token 创建新的 API Token",
        )

    raw_token, prefix, digest = generate_api_token()
    expires_at = (
        utc_now() + timedelta(days=body.expires_in_days)
        if body.expires_in_days is not None
        else None
    )
    token_obj = ApiToken(
        user_id=auth.user.id,
        name=body.name.strip(),
        token_prefix=prefix,
        token_hash=digest,
        scope=body.scope,
        expires_at=expires_at,
    )
    db.add(token_obj)
    await db.flush()

    await record_audit(
        db,
        action="create",
        target_type="api_token",
        target_id=token_obj.id,
        target_label=f"{token_obj.name} ({prefix}...)",
        after={"name": token_obj.name, "scope": token_obj.scope, "expires_at": expires_at},
        auth=auth,
    )

    out = ApiTokenOut.model_validate(token_obj)
    return ApiTokenCreatedOut(**out.model_dump(), token=raw_token)


@router.delete("/tokens/{token_id}", response_model=MessageResponse)
async def revoke_my_token(
    token_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    token_obj = (
        await db.execute(
            select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == auth.user.id)
        )
    ).scalar_one_or_none()
    if token_obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API Token 不存在")

    if token_obj.revoked_at is None:
        token_obj.revoked_at = utc_now()
        await db.flush()
        await record_audit(
            db,
            action="revoke",
            target_type="api_token",
            target_id=token_obj.id,
            target_label=token_obj.name,
            auth=auth,
        )
    return MessageResponse(message="API Token 已吊销")
