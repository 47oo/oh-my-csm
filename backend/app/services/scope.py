"""Unified Group ∪ Cluster-ACL full-chain penetration scope resolver for all assets and modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Select, and_, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import AuthContext
from app.models.assets import (
    NIC,
    Cluster,
    ClusterDevice,
    Device,
    IPAddress,
    IPVIPBinding,
    Service,
    ServiceEndpoint,
    UrlEntry,
)
from app.models.audit import AuditLog


@dataclass
class ResolvedScope:
    """Resolved access scope for a specific permission (e.g., asset:read, asset:write, credential:reveal)."""

    permission: str
    unrestricted: bool = False
    allow_public: bool = False
    group_ids: set[int] = field(default_factory=set)
    cluster_ids: set[int] = field(default_factory=set)
    device_ids: set[int] = field(default_factory=set)
    nic_ids: set[int] = field(default_factory=set)
    ip_ids: set[int] = field(default_factory=set)
    service_ids: set[int] = field(default_factory=set)
    url_ids: set[int] = field(default_factory=set)
    subnet_ids: set[int] = field(default_factory=set)

    def entity_id_sets(self) -> dict[str, set[int]]:
        return {
            "cluster": self.cluster_ids,
            "device": self.device_ids,
            "nic": self.nic_ids,
            "ip": self.ip_ids,
            "service": self.service_ids,
            "url": self.url_ids,
            "subnet": self.subnet_ids,
        }

    def allows(self, entity_type: str, entity_id: int, group_id: int | None) -> bool:
        if self.unrestricted:
            return True
        if group_id is not None and group_id in self.group_ids:
            return True
        if group_id is None and self.allow_public:
            return True
        return entity_id in self.entity_id_sets().get(entity_type, set())


async def resolve_scope(
    db: AsyncSession,
    auth: AuthContext,
    permission: str = "asset:read",
    *,
    include_deleted: bool = False,
) -> ResolvedScope:
    """Resolve full-chain penetration IDs: Cluster -> Device -> NIC -> IP/VIP -> Service/Endpoint -> URL (+ Subnet)."""
    if auth.has_global_permission(permission):
        return ResolvedScope(permission=permission, unrestricted=True, allow_public=True)

    group_ids = auth.group_ids_with_perm(permission)
    cluster_ids = set(auth.cluster_ids_with_perm(permission))
    allow_public = auth.allow_public_assets and permission in {"asset:read", "excel:export"}

    # Include clusters owned by user's permitted groups so group-owned clusters also penetrate cleanly
    if group_ids:
        cl_stmt = select(Cluster.id).where(Cluster.group_id.in_(group_ids))
        if not include_deleted:
            cl_stmt = cl_stmt.where(Cluster.deleted_at.is_(None))
        grp_clusters = (await db.execute(cl_stmt)).scalars().all()
        cluster_ids.update(grp_clusters)

    if not cluster_ids:
        return ResolvedScope(
            permission=permission,
            unrestricted=False,
            allow_public=allow_public,
            group_ids=group_ids,
        )

    # 1. Devices in clusters
    dev_stmt = (
        select(ClusterDevice.device_id)
        .join(Device, Device.id == ClusterDevice.device_id)
        .where(ClusterDevice.cluster_id.in_(cluster_ids))
    )
    if not include_deleted:
        dev_stmt = dev_stmt.where(Device.deleted_at.is_(None))
    device_ids = set((await db.execute(dev_stmt)).scalars().all())

    # 2. NICs on those devices
    nic_ids: set[int] = set()
    if device_ids:
        nic_stmt = select(NIC.id).where(NIC.device_id.in_(device_ids))
        if not include_deleted:
            nic_stmt = nic_stmt.where(NIC.deleted_at.is_(None))
        nic_ids = set((await db.execute(nic_stmt)).scalars().all())

    # 3. IPs bound to those NICs OR VIPs bound to those clusters/NICs OR endpoint IPs
    ip_ids: set[int] = set()
    if nic_ids:
        ip_stmt = select(IPAddress.id).where(IPAddress.nic_id.in_(nic_ids))
        if not include_deleted:
            ip_stmt = ip_stmt.where(IPAddress.deleted_at.is_(None))
        ip_ids.update((await db.execute(ip_stmt)).scalars().all())

    vip_conds = [and_(IPVIPBinding.target_type == "cluster", IPVIPBinding.target_id.in_(cluster_ids))]
    if nic_ids:
        vip_conds.append(and_(IPVIPBinding.target_type == "nic", IPVIPBinding.target_id.in_(nic_ids)))
    vip_stmt = (
        select(IPVIPBinding.ip_id)
        .join(IPAddress, IPAddress.id == IPVIPBinding.ip_id)
        .where(or_(*vip_conds))
    )
    if not include_deleted:
        vip_stmt = vip_stmt.where(IPAddress.deleted_at.is_(None))
    ip_ids.update((await db.execute(vip_stmt)).scalars().all())

    ep_conds = [ServiceEndpoint.cluster_id.in_(cluster_ids)]
    if device_ids:
        ep_conds.append(ServiceEndpoint.device_id.in_(device_ids))
    if ip_ids:
        ep_conds.append(ServiceEndpoint.ip_id.in_(ip_ids))

    ep_stmt = (
        select(ServiceEndpoint.service_id, ServiceEndpoint.ip_id)
        .join(Service, Service.id == ServiceEndpoint.service_id)
        .where(or_(*ep_conds))
    )
    if not include_deleted:
        ep_stmt = ep_stmt.where(Service.deleted_at.is_(None))
    ep_rows = (await db.execute(ep_stmt)).all()
    service_ids: set[int] = set()
    for sid, ep_ip_id in ep_rows:
        service_ids.add(sid)
        if ep_ip_id is not None:
            ip_ids.add(ep_ip_id)

    # Also include VIPs bound directly to those services
    if service_ids:
        svc_vip_stmt = (
            select(IPVIPBinding.ip_id)
            .join(IPAddress, IPAddress.id == IPVIPBinding.ip_id)
            .where(
                IPVIPBinding.target_type == "service",
                IPVIPBinding.target_id.in_(service_ids),
            )
        )
        if not include_deleted:
            svc_vip_stmt = svc_vip_stmt.where(IPAddress.deleted_at.is_(None))
        ip_ids.update((await db.execute(svc_vip_stmt)).scalars().all())

    # 4. URLs linked to those services or entry IPs
    url_ids: set[int] = set()
    url_conds = []
    if service_ids:
        url_conds.append(UrlEntry.service_id.in_(service_ids))
    if ip_ids:
        url_conds.append(UrlEntry.entry_ip_id.in_(ip_ids))
    if url_conds:
        url_stmt = select(UrlEntry.id).where(or_(*url_conds))
        if not include_deleted:
            url_stmt = url_stmt.where(UrlEntry.deleted_at.is_(None))
        url_ids = set((await db.execute(url_stmt)).scalars().all())

    # 5. Subnets containing any penetrated IPs
    subnet_ids: set[int] = set()
    if ip_ids:
        sub_stmt = (
            select(IPAddress.subnet_id)
            .where(IPAddress.id.in_(ip_ids), IPAddress.subnet_id.is_not(None))
            .distinct()
        )
        if not include_deleted:
            sub_stmt = sub_stmt.where(IPAddress.deleted_at.is_(None))
        subnet_ids = set((await db.execute(sub_stmt)).scalars().all())

    return ResolvedScope(
        permission=permission,
        unrestricted=False,
        allow_public=allow_public,
        group_ids=group_ids,
        cluster_ids=cluster_ids,
        device_ids=device_ids,
        nic_ids=nic_ids,
        ip_ids=ip_ids,
        service_ids=service_ids,
        url_ids=url_ids,
        subnet_ids=subnet_ids,
    )


def apply_scope_filter(
    stmt: Select[Any],
    model: Any,
    entity_type: str,
    scope: ResolvedScope,
) -> Select[Any]:
    """Apply Group ∪ Cluster-Penetrated ID filter to any SELECT statement."""
    if scope.unrestricted:
        return stmt

    conds = []
    group_col = getattr(model, "group_id", None)
    if group_col is not None:
        if scope.group_ids:
            conds.append(group_col.in_(scope.group_ids))
        if scope.allow_public:
            conds.append(group_col.is_(None))

    penetrated = scope.entity_id_sets().get(entity_type, set())
    if penetrated:
        conds.append(model.id.in_(penetrated))

    if not conds:
        return stmt.where(false())
    return stmt.where(or_(*conds))


def apply_audit_scope_filter(
    stmt: Select[Any],
    auth: AuthContext,
    audit_scope: ResolvedScope,
) -> Select[Any]:
    """Apply actor ∪ Group ∪ Cluster-Penetrated filter to AuditLog queries."""
    if audit_scope.unrestricted:
        return stmt
    conds: list[Any] = [AuditLog.actor_id == auth.user.id]
    if audit_scope.group_ids:
        conds.append(AuditLog.group_id.in_(audit_scope.group_ids))
    for etype, ids in audit_scope.entity_id_sets().items():
        if ids:
            conds.append(and_(AuditLog.target_type == etype, AuditLog.target_id.in_(ids)))
    return stmt.where(or_(*conds))


async def require_entity_permission(
    db: AsyncSession,
    auth: AuthContext,
    entity_type: str,
    entity_id: int,
    group_id: int | None,
    permission: str,
    scope: ResolvedScope | None = None,
) -> ResolvedScope:
    """Assert current user holds `permission` on the specific entity (via Group OR Cluster ACL)."""
    resolved = scope or await resolve_scope(db, auth, permission)
    if not resolved.allows(entity_type, entity_id, group_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"缺少该{entity_type}的访问/操作权限: {permission}",
        )
    return resolved


def is_card_restricted(
    scope: ResolvedScope,
    entity_type: str,
    entity_id: int,
    group_id: int | None,
) -> bool:
    """True when a related entity is outside the user's read scope and must be redacted to a summary."""
    return not scope.allows(entity_type, entity_id, group_id)
