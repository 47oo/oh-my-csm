"""Enhanced Dashboard summary endpoint with top subnet utilization, SSL expiry, incomplete & duplicate alerts."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import AuthContext, get_current_auth
from app.core.security import utc_now
from app.models.assets import NIC, Cluster, Device, IPAddress, Service, Subnet, UrlEntry
from app.models.audit import AuditLog
from app.models.auth import Role, User
from app.models.system import DictItem, NetworkZone, OpsGroup, Tag
from app.schemas.m3 import (
    DashboardEnhancedOut,
    DuplicateHostnameAlert,
    ExpiringUrlAlert,
    TopSubnetAlert,
)
from app.schemas.system import AuditLogOut
from app.services.assets import subnet_capacity
from app.services.scope import ResolvedScope, apply_scope_filter, resolve_scope

router = APIRouter(prefix="/dashboard", tags=["总览看板"])


async def _scoped_count(
    db: AsyncSession, scope: ResolvedScope, model: type, entity_type: str
) -> int:
    stmt = apply_scope_filter(select(model).where(model.deleted_at.is_(None)), model, entity_type, scope)
    return int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one())


@router.get("/summary", response_model=DashboardEnhancedOut)
async def get_dashboard_summary(
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> DashboardEnhancedOut:
    scope = await resolve_scope(db, auth, "asset:read")

    group_stmt = select(func.count()).select_from(OpsGroup).where(OpsGroup.deleted_at.is_(None))
    if not auth.has_global_permission("system:manage") and not auth.has_global_permission("asset:read"):
        group_count = len(set(auth.group_permissions.keys()))
    else:
        group_count = int((await db.execute(group_stmt)).scalar_one())

    nic_stmt = (
        select(NIC)
        .join(Device, Device.id == NIC.device_id)
        .where(NIC.deleted_at.is_(None), Device.deleted_at.is_(None))
    )
    nic_stmt = apply_scope_filter(nic_stmt, Device, "device", scope)
    nic_count = int((await db.execute(select(func.count()).select_from(nic_stmt.subquery()))).scalar_one())

    counts = {
        "clusters": await _scoped_count(db, scope, Cluster, "cluster"),
        "devices": await _scoped_count(db, scope, Device, "device"),
        "nics": nic_count,
        "subnets": await _scoped_count(db, scope, Subnet, "subnet"),
        "ips": await _scoped_count(db, scope, IPAddress, "ip"),
        "services": await _scoped_count(db, scope, Service, "service"),
        "urls": await _scoped_count(db, scope, UrlEntry, "url"),
        "groups": group_count,
        "users": int(
            (await db.execute(select(func.count()).select_from(User).where(User.deleted_at.is_(None)))).scalar_one()
        ),
        "roles": int((await db.execute(select(func.count()).select_from(Role))).scalar_one()),
        "network_zones": int(
            (
                await db.execute(
                    select(func.count()).select_from(NetworkZone).where(NetworkZone.deleted_at.is_(None))
                )
            ).scalar_one()
        ),
        "dicts": int((await db.execute(select(func.count()).select_from(DictItem))).scalar_one()),
        "tags": int((await db.execute(select(func.count()).select_from(Tag))).scalar_one()),
    }

    # 1. Top 5 Subnets by utilization
    subnets = list(
        (
            await db.execute(
                apply_scope_filter(
                    select(Subnet).where(Subnet.deleted_at.is_(None)), Subnet, "subnet", scope
                )
            )
        )
        .scalars()
        .all()
    )
    top_subnets: list[TopSubnetAlert] = []
    if subnets:
        sub_ids = [s.id for s in subnets]
        used_map = dict(
            (
                await db.execute(
                    select(IPAddress.subnet_id, func.count())
                    .where(
                        IPAddress.subnet_id.in_(sub_ids),
                        IPAddress.deleted_at.is_(None),
                        IPAddress.status != "free",
                    )
                    .group_by(IPAddress.subnet_id)
                )
            ).all()
        )
        for s in subnets:
            cap = subnet_capacity(str(s.cidr))
            used = int(used_map.get(s.id, 0))
            pct = round((used / cap) * 100, 1) if cap > 0 else 0.0
            top_subnets.append(
                TopSubnetAlert(
                    id=s.id,
                    name=s.name,
                    cidr=str(s.cidr),
                    allocated_count=used,
                    total_capacity=cap,
                    utilization_pct=min(pct, 100.0),
                )
            )
        top_subnets.sort(key=lambda x: (x.utilization_pct, x.allocated_count), reverse=True)
        top_subnets = top_subnets[:5]

    # 2. Expiring HTTPS URLs (expired or <= 30 days)
    now = utc_now()
    cutoff = now + timedelta(days=30)
    exp_stmt = apply_scope_filter(
        select(UrlEntry).where(
            UrlEntry.deleted_at.is_(None),
            UrlEntry.ssl_expires_at.is_not(None),
            UrlEntry.ssl_expires_at <= cutoff,
        ),
        UrlEntry,
        "url",
        scope,
    ).order_by(UrlEntry.ssl_expires_at.asc()).limit(10)
    exp_rows = list((await db.execute(exp_stmt)).scalars().all())
    expiring_urls: list[ExpiringUrlAlert] = []
    for u in exp_rows:
        exp_dt = u.ssl_expires_at
        if exp_dt is None:
            continue
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=now.tzinfo)
        days_left = int((exp_dt - now).total_seconds() // 86400)
        expiring_urls.append(
            ExpiringUrlAlert(
                id=u.id,
                name=u.name,
                domain=u.domain,
                full_url=u.full_url,
                ssl_expires_at=exp_dt,
                days_remaining=days_left,
            )
        )

    # 3. Incomplete asset counts (for gradual completion tracking)
    urls_no_svc = int(
        (
            await db.execute(
                select(func.count()).select_from(
                    apply_scope_filter(
                        select(UrlEntry).where(
                            UrlEntry.deleted_at.is_(None), UrlEntry.service_id.is_(None)
                        ),
                        UrlEntry,
                        "url",
                        scope,
                    ).subquery()
                )
            )
        ).scalar_one()
    )
    host_ips_no_nic = int(
        (
            await db.execute(
                select(func.count()).select_from(
                    apply_scope_filter(
                        select(IPAddress).where(
                            IPAddress.deleted_at.is_(None),
                            IPAddress.ip_type == "host",
                            IPAddress.nic_id.is_(None),
                        ),
                        IPAddress,
                        "ip",
                        scope,
                    ).subquery()
                )
            )
        ).scalar_one()
    )
    devices_no_group = int(
        (
            await db.execute(
                select(func.count()).select_from(
                    apply_scope_filter(
                        select(Device).where(
                            Device.deleted_at.is_(None), Device.group_id.is_(None)
                        ),
                        Device,
                        "device",
                        scope,
                    ).subquery()
                )
            )
        ).scalar_one()
    )
    incomplete_counts = {
        "urls_without_service": urls_no_svc,
        "host_ips_without_nic": host_ips_no_nic,
        "devices_without_group": devices_no_group,
    }

    # 4. Duplicate hostnames alert
    dev_scoped = apply_scope_filter(
        select(Device.hostname).where(Device.deleted_at.is_(None)), Device, "device", scope
    ).subquery()
    dup_rows = (
        await db.execute(
            select(dev_scoped.c.hostname, func.count().label("cnt"))
            .group_by(dev_scoped.c.hostname)
            .having(func.count() > 1)
            .order_by(func.count().desc())
            .limit(10)
        )
    ).all()
    duplicate_hostnames = [
        DuplicateHostnameAlert(hostname=h, count=int(c)) for h, c in dup_rows
    ]

    # 5. Recent scoped audits
    audit_stmt = select(AuditLog)
    if not auth.has_global_permission("audit:read"):
        allowed_groups = {
            gid for gid, perms in auth.group_permissions.items() if "audit:read" in perms
        }
        if allowed_groups:
            audit_stmt = audit_stmt.where(
                or_(AuditLog.actor_id == auth.user.id, AuditLog.group_id.in_(allowed_groups))
            )
        else:
            audit_stmt = audit_stmt.where(AuditLog.actor_id == auth.user.id)

    recent_audits = list(
        (
            await db.execute(
                audit_stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(10)
            )
        )
        .scalars()
        .all()
    )

    return DashboardEnhancedOut(
        counts=counts,
        top_subnets=top_subnets,
        expiring_urls=expiring_urls,
        incomplete_counts=incomplete_counts,
        duplicate_hostnames=duplicate_hostnames,
        recent_audits=[AuditLogOut.model_validate(a) for a in recent_audits],
    )
