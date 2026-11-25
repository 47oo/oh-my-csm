"""RBAC permission catalog, built-in roles, auth context (Group + Cluster ACL union), and ScopeFilter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Select, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.security import PAT_PREFIX, decode_token, hash_api_token, utc_now
from app.models.auth import ApiToken, ClusterACL, RevokedToken, User, UserGroupRole

bearer_scheme = HTTPBearer(auto_error=False)

PERMISSION_CATALOG: list[dict[str, str]] = [
    {"code": "asset:read", "label": "查看资产", "category": "资产台账"},
    {"code": "asset:write", "label": "新增与编辑资产", "category": "资产台账"},
    {"code": "asset:delete", "label": "软删除资产", "category": "资产台账"},
    {"code": "excel:import", "label": "Excel 批量导入", "category": "批处理"},
    {"code": "excel:export", "label": "Excel 筛选导出", "category": "批处理"},
    {"code": "credential:manage", "label": "凭据增删改", "category": "敏感凭据"},
    {"code": "credential:reveal", "label": "查看凭据明文", "category": "敏感凭据"},
    {"code": "group:member_manage", "label": "管理本组/本集群成员", "category": "权限治理"},
    {"code": "recycle_bin:restore", "label": "回收站恢复", "category": "系统治理"},
    {"code": "recycle_bin:purge", "label": "回收站彻底清理", "category": "系统治理"},
    {"code": "audit:read", "label": "查看审计日志", "category": "系统治理"},
    {"code": "system:manage", "label": "系统全局配置（用户/角色/字典/网络域）", "category": "系统治理"},
]

ALL_PERMISSION_CODES: set[str] = {item["code"] for item in PERMISSION_CATALOG}

READ_ONLY_PERMISSIONS: set[str] = {
    "asset:read",
    "excel:export",
    "audit:read",
}

BUILTIN_ROLES: list[dict[str, Any]] = [
    {
        "code": "super_admin",
        "name": "超级管理员",
        "description": "全局全部权限，包括系统配置、用户角色管理与回收站彻底清理",
        "permissions": sorted(ALL_PERMISSION_CODES),
    },
    {
        "code": "group_admin",
        "name": "分组/集群管理员",
        "description": "所属分组或授权集群内的全部资产管理、批量导入、成员授权、凭据管理与恢复",
        "permissions": [
            "asset:read",
            "asset:write",
            "asset:delete",
            "excel:import",
            "excel:export",
            "credential:manage",
            "credential:reveal",
            "group:member_manage",
            "recycle_bin:restore",
            "audit:read",
        ],
    },
    {
        "code": "ops_editor",
        "name": "运维编辑",
        "description": "所属分组或授权集群内资产增删改与导入导出，默认不含明文查看凭据权限",
        "permissions": [
            "asset:read",
            "asset:write",
            "asset:delete",
            "excel:import",
            "excel:export",
            "audit:read",
        ],
    },
    {
        "code": "viewer",
        "name": "只读访客",
        "description": "所属分组或授权集群内资产只读查看与条件导出",
        "permissions": [
            "asset:read",
            "excel:export",
        ],
    },
]


@dataclass
class AuthContext:
    user: User
    auth_method: str  # "jwt" | "pat"
    token_scope: str  # "inherit" | "read_only"
    client_ip: str
    is_superadmin: bool = False
    allow_public_assets: bool = False
    global_permissions: set[str] = field(default_factory=set)
    group_permissions: dict[int, set[str]] = field(default_factory=dict)
    cluster_permissions: dict[int, set[str]] = field(default_factory=dict)

    @property
    def readable_group_ids(self) -> set[int]:
        return {
            gid for gid, perms in self.group_permissions.items() if "asset:read" in perms
        }

    @property
    def writable_group_ids(self) -> set[int]:
        return {
            gid for gid, perms in self.group_permissions.items() if "asset:write" in perms
        }

    @property
    def manageable_group_ids(self) -> set[int]:
        return {
            gid
            for gid, perms in self.group_permissions.items()
            if "group:member_manage" in perms
        }

    @property
    def readable_cluster_ids(self) -> set[int]:
        return {
            cid for cid, perms in self.cluster_permissions.items() if "asset:read" in perms
        }

    @property
    def writable_cluster_ids(self) -> set[int]:
        return {
            cid for cid, perms in self.cluster_permissions.items() if "asset:write" in perms
        }

    @property
    def manageable_cluster_ids(self) -> set[int]:
        return {
            cid
            for cid, perms in self.cluster_permissions.items()
            if "group:member_manage" in perms
        }

    def group_ids_with_perm(self, code: str) -> set[int]:
        if self.token_scope == "read_only" and code not in READ_ONLY_PERMISSIONS:
            return set()
        return {gid for gid, perms in self.group_permissions.items() if code in perms}

    def cluster_ids_with_perm(self, code: str) -> set[int]:
        if self.token_scope == "read_only" and code not in READ_ONLY_PERMISSIONS:
            return set()
        return {cid for cid, perms in self.cluster_permissions.items() if code in perms}

    def has_global_permission(self, code: str) -> bool:
        if self.token_scope == "read_only" and code not in READ_ONLY_PERMISSIONS:
            return False
        if self.is_superadmin:
            return True
        return code in self.global_permissions

    def has_permission_in_group(self, code: str, group_id: int | None) -> bool:
        if self.token_scope == "read_only" and code not in READ_ONLY_PERMISSIONS:
            return False
        if self.is_superadmin:
            return True
        if code in self.global_permissions:
            return True
        if group_id is None:
            # Strict least-privilege: public/unassigned resources readable only if allow_public_assets=True
            return self.allow_public_assets and code in {"asset:read", "excel:export"}
        return code in self.group_permissions.get(group_id, set())

    def has_permission_in_cluster(self, code: str, cluster_id: int) -> bool:
        if self.token_scope == "read_only" and code not in READ_ONLY_PERMISSIONS:
            return False
        if self.is_superadmin:
            return True
        if code in self.global_permissions:
            return True
        return code in self.cluster_permissions.get(cluster_id, set())

    def has_any_permission(self, code: str) -> bool:
        if self.has_global_permission(code):
            return True
        if self.group_ids_with_perm(code):
            return True
        return bool(self.cluster_ids_with_perm(code))

    def apply_read_scope(self, stmt: Select[Any], model: Any) -> Select[Any]:
        """Legacy group-only read scope filter (use services.scope.apply_asset_scope for cluster penetration)."""
        if self.is_superadmin or "asset:read" in self.global_permissions:
            return stmt
        group_col = getattr(model, "group_id", None)
        if group_col is None:
            return stmt
        readable = self.readable_group_ids
        conds = []
        if readable:
            conds.append(group_col.in_(readable))
        if self.allow_public_assets:
            conds.append(group_col.is_(None))
        if not conds:
            return stmt.where(false())
        return stmt.where(or_(*conds))

    def require_global(self, code: str) -> None:
        if not self.has_global_permission(code):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"缺少全局权限: {code}",
            )

    def require_group_permission(self, code: str, group_id: int | None) -> None:
        if not self.has_permission_in_group(code, group_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"缺少目标分组的权限: {code}",
            )


def _extract_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


async def _build_auth_context(
    db: AsyncSession,
    user: User,
    auth_method: str,
    token_scope: str,
    client_ip: str,
) -> AuthContext:
    stmt = (
        select(UserGroupRole)
        .options(selectinload(UserGroupRole.role))
        .where(UserGroupRole.user_id == user.id)
    )
    assignments = list((await db.execute(stmt)).scalars().all())

    acl_stmt = (
        select(ClusterACL)
        .options(selectinload(ClusterACL.role))
        .where(ClusterACL.user_id == user.id)
    )
    acls = list((await db.execute(acl_stmt)).scalars().all())

    is_superadmin = False
    global_perms: set[str] = set()
    group_perms: dict[int, set[str]] = {}
    cluster_perms: dict[int, set[str]] = {}

    for assignment in assignments:
        role = assignment.role
        if role is None:
            continue
        perms = set(role.permissions or [])
        if token_scope == "read_only":
            perms = perms.intersection(READ_ONLY_PERMISSIONS)
        if assignment.group_id is None:
            if role.code == "super_admin" and token_scope != "read_only":
                is_superadmin = True
            global_perms.update(perms)
        else:
            group_perms.setdefault(assignment.group_id, set()).update(perms)

    for acl in acls:
        role = acl.role
        if role is None:
            continue
        perms = set(role.permissions or [])
        if token_scope == "read_only":
            perms = perms.intersection(READ_ONLY_PERMISSIONS)
        cluster_perms.setdefault(acl.cluster_id, set()).update(perms)

    return AuthContext(
        user=user,
        auth_method=auth_method,
        token_scope=token_scope,
        client_ip=client_ip,
        is_superadmin=is_superadmin,
        allow_public_assets=bool(user.allow_public_assets),
        global_permissions=global_perms,
        group_permissions=group_perms,
        cluster_permissions=cluster_perms,
    )


async def _authenticate_request(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
    db: AsyncSession,
    allow_must_change_password: bool,
) -> AuthContext:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证令牌",
        )

    raw_token = credentials.credentials.strip()
    client_ip = _extract_client_ip(request)
    now = utc_now()

    if raw_token.startswith(PAT_PREFIX):
        token_hash = hash_api_token(raw_token)
        stmt = (
            select(ApiToken)
            .options(selectinload(ApiToken.user))
            .where(
                ApiToken.token_hash == token_hash,
                ApiToken.revoked_at.is_(None),
            )
        )
        pat = (await db.execute(stmt)).scalar_one_or_none()
        if pat is None or pat.user is None or pat.user.deleted_at is not None or not pat.user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API Token 无效或已失效",
            )
        if pat.expires_at is not None:
            exp = pat.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=now.tzinfo)
            if exp <= now:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="API Token 已过期",
                )
        pat.last_used_at = now
        await db.flush()
        return await _build_auth_context(
            db=db,
            user=pat.user,
            auth_method="pat",
            token_scope=pat.scope,
            client_ip=client_ip,
        )

    try:
        payload = decode_token(raw_token)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录会话已过期，请重新登录",
        ) from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证令牌",
        ) from exc

    if payload.get("type") != "access":
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
                detail="登录会话已注销",
            )

    user_id = int(payload["sub"])
    user = (
        await db.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已禁用",
        )

    if user.must_change_password and not allow_must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="首次登录必须先修改密码",
        )

    return await _build_auth_context(
        db=db,
        user=user,
        auth_method="jwt",
        token_scope="inherit",
        client_ip=client_ip,
    )


async def get_current_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    return await _authenticate_request(
        request=request,
        credentials=credentials,
        db=db,
        allow_must_change_password=False,
    )


async def get_current_auth_allow_password_change(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    return await _authenticate_request(
        request=request,
        credentials=credentials,
        db=db,
        allow_must_change_password=True,
    )
