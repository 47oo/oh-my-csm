"""Scoped audit log query endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import AuthContext, get_current_auth
from app.models.audit import AuditLog
from app.schemas.common import PageResponse
from app.schemas.system import AuditLogOut
from app.services.scope import apply_audit_scope_filter, resolve_scope

router = APIRouter(prefix="/audit-logs", tags=["审计日志"])


@router.get("", response_model=PageResponse[AuditLogOut])
async def list_audit_logs(
    action: str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    target_id: int | None = Query(default=None),
    actor_username: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> PageResponse[AuditLogOut]:
    base = select(AuditLog)

    if not auth.has_global_permission("audit:read"):
        audit_scope = await resolve_scope(db, auth, "audit:read")
        base = apply_audit_scope_filter(base, auth, audit_scope)

    if action:
        base = base.where(AuditLog.action == action)
    if target_type:
        base = base.where(AuditLog.target_type == target_type)
    if target_id is not None:
        base = base.where(AuditLog.target_id == target_id)
    if actor_username:
        base = base.where(AuditLog.actor_username.ilike(f"%{actor_username.strip()}%"))

    total = int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one())
    stmt = (
        base.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return PageResponse(
        items=[AuditLogOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )
