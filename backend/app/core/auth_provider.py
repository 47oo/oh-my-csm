"""Pluggable authentication provider interface (Local implemented; SSO OIDC/LDAP ready)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import utc_now, verify_password
from app.models.auth import User


@dataclass
class AuthResult:
    user: User | None
    error: str | None = None
    locked_until_iso: str | None = None


class AuthProvider(ABC):
    """Extension seam for local accounts and future enterprise SSO (OIDC/LDAP)."""

    source_name: str

    @abstractmethod
    async def authenticate(
        self,
        db: AsyncSession,
        username: str,
        password: str,
    ) -> AuthResult:
        raise NotImplementedError


class LocalAuthProvider(AuthProvider):
    source_name = "local"

    async def authenticate(
        self,
        db: AsyncSession,
        username: str,
        password: str,
    ) -> AuthResult:
        settings = get_settings()
        now = utc_now()

        stmt = select(User).where(User.username == username, User.deleted_at.is_(None))
        user = (await db.execute(stmt)).scalar_one_or_none()
        if user is None:
            return AuthResult(user=None, error="用户名或密码错误")

        if not user.is_active:
            return AuthResult(user=None, error="账号已被禁用，请联系管理员")

        if user.auth_source != "local":
            return AuthResult(user=None, error=f"该账号需通过 {user.auth_source} 登录")

        locked_until = user.locked_until
        if locked_until is not None:
            if locked_until.tzinfo is None:
                locked_until = locked_until.replace(tzinfo=now.tzinfo)
            if locked_until > now:
                return AuthResult(
                    user=None,
                    error="连续登录失败次数过多，账号已暂时锁定，请稍后再试或联系管理员解锁",
                    locked_until_iso=locked_until.isoformat(),
                )
            # Lock expired
            user.locked_until = None
            user.failed_login_attempts = 0

        if not user.password_hash or not verify_password(password, user.password_hash):
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= settings.max_failed_login_attempts:
                user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
                await db.flush()
                return AuthResult(
                    user=None,
                    error=(
                        f"密码错误已达 {settings.max_failed_login_attempts} 次，"
                        f"账号已锁定 {settings.lockout_minutes} 分钟"
                    ),
                    locked_until_iso=user.locked_until.isoformat(),
                )
            remaining = settings.max_failed_login_attempts - user.failed_login_attempts
            await db.flush()
            return AuthResult(user=None, error=f"用户名或密码错误（剩余 {remaining} 次尝试）")

        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login_at = now
        await db.flush()
        return AuthResult(user=user)
