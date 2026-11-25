"""Global aggregated instant search across all 7 asset domains (Ctrl/Cmd + K)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import AuthContext, get_current_auth
from app.models.assets import (
    NIC,
    Cluster,
    Device,
    IPAddress,
    Service,
    Subnet,
    UrlEntry,
)
from app.schemas.m3 import GlobalSearchOut, SearchGroup, SearchHit
from app.services.assets import format_ip_str, load_group_names
from app.services.scope import apply_scope_filter, resolve_scope

router = APIRouter(prefix="/search", tags=["全局秒搜"])


@router.get("", response_model=GlobalSearchOut)
async def global_search(
    q: str = Query(..., min_length=1, max_length=128),
    limit_per_group: int = Query(default=6, ge=1, le=20),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> GlobalSearchOut:
    keyword = q.strip()
    pattern = f"%{keyword}%"
    scope = await resolve_scope(db, auth, "asset:read")

    # 1. IPs
    ip_stmt = apply_scope_filter(
        select(IPAddress).where(
            IPAddress.deleted_at.is_(None),
            or_(
                func.cast(IPAddress.ip_address, IPAddress.status.type).ilike(pattern),
                IPAddress.usage.ilike(pattern),
                IPAddress.description.ilike(pattern),
            ),
        ),
        IPAddress,
        "ip",
        scope,
    ).limit(limit_per_group)
    ip_rows = list((await db.execute(ip_stmt)).scalars().all())

    # 2. URLs
    url_stmt = apply_scope_filter(
        select(UrlEntry).where(
            UrlEntry.deleted_at.is_(None),
            or_(
                UrlEntry.name.ilike(pattern),
                UrlEntry.full_url.ilike(pattern),
                UrlEntry.domain.ilike(pattern),
                UrlEntry.description.ilike(pattern),
            ),
        ),
        UrlEntry,
        "url",
        scope,
    ).limit(limit_per_group)
    url_rows = list((await db.execute(url_stmt)).scalars().all())

    # 3. Devices
    dev_stmt = apply_scope_filter(
        select(Device).where(
            Device.deleted_at.is_(None),
            or_(
                Device.hostname.ilike(pattern),
                Device.asset_code.ilike(pattern),
                Device.serial_number.ilike(pattern),
                Device.rack_location.ilike(pattern),
                Device.owner.ilike(pattern),
                Device.description.ilike(pattern),
            ),
        ),
        Device,
        "device",
        scope,
    ).limit(limit_per_group)
    dev_rows = list((await db.execute(dev_stmt)).scalars().all())

    # 4. Services
    svc_stmt = apply_scope_filter(
        select(Service).where(
            Service.deleted_at.is_(None),
            or_(
                Service.name.ilike(pattern),
                Service.code.ilike(pattern),
                Service.owner.ilike(pattern),
                Service.description.ilike(pattern),
            ),
        ),
        Service,
        "service",
        scope,
    ).limit(limit_per_group)
    svc_rows = list((await db.execute(svc_stmt)).scalars().all())

    # 5. Clusters
    cl_stmt = apply_scope_filter(
        select(Cluster).where(
            Cluster.deleted_at.is_(None),
            or_(
                Cluster.name.ilike(pattern),
                Cluster.code.ilike(pattern),
                Cluster.cluster_type.ilike(pattern),
                Cluster.owner.ilike(pattern),
            ),
        ),
        Cluster,
        "cluster",
        scope,
    ).limit(limit_per_group)
    cl_rows = list((await db.execute(cl_stmt)).scalars().all())

    # 6. NICs
    nic_stmt = (
        select(NIC, Device.hostname, Device.group_id)
        .join(Device, Device.id == NIC.device_id)
        .where(
            NIC.deleted_at.is_(None),
            Device.deleted_at.is_(None),
            or_(
                NIC.name.ilike(pattern),
                NIC.switch_port.ilike(pattern),
                func.cast(NIC.mac_address, NIC.name.type).ilike(pattern),
                Device.hostname.ilike(pattern),
            ),
        )
    )
    nic_stmt = apply_scope_filter(nic_stmt, Device, "device", scope).limit(limit_per_group)
    nic_rows = list((await db.execute(nic_stmt)).all())

    # 7. Subnets
    sub_stmt = apply_scope_filter(
        select(Subnet).where(
            Subnet.deleted_at.is_(None),
            or_(
                Subnet.name.ilike(pattern),
                func.cast(Subnet.cidr, Subnet.name.type).ilike(pattern),
                Subnet.description.ilike(pattern),
            ),
        ),
        Subnet,
        "subnet",
        scope,
    ).limit(limit_per_group)
    sub_rows = list((await db.execute(sub_stmt)).scalars().all())

    all_gids = (
        {r.group_id for r in ip_rows if r.group_id}
        | {r.group_id for r in url_rows if r.group_id}
        | {r.group_id for r in dev_rows if r.group_id}
        | {r.group_id for r in svc_rows if r.group_id}
        | {r.group_id for r in cl_rows if r.group_id}
        | {r.group_id for r in sub_rows if r.group_id}
        | {gid for _, _, gid in nic_rows if gid}
    )
    gnames = await load_group_names(db, all_gids)

    # Resolve NIC -> Device hostname for matched IPs
    ip_nic_ids = {r.nic_id for r in ip_rows if r.nic_id}
    ip_nic_map: dict[int, str] = {}
    if ip_nic_ids:
        for nid, nname, dhost in (
            await db.execute(
                select(NIC.id, NIC.name, Device.hostname)
                .join(Device, Device.id == NIC.device_id)
                .where(NIC.id.in_(ip_nic_ids))
            )
        ).all():
            ip_nic_map[nid] = f"{dhost}:{nname}"

    groups: list[SearchGroup] = []

    if ip_rows:
        groups.append(
            SearchGroup(
                entity_type="ip",
                label="IP 地址",
                items=[
                    SearchHit(
                        entity_type="ip",
                        id=r.id,
                        title=format_ip_str(r.ip_address),
                        subtitle=ip_nic_map.get(r.nic_id) if r.nic_id else (r.usage or "未绑定网卡"),
                        badge=r.ip_type.upper(),
                        status=r.status,
                        group_name=gnames.get(r.group_id) if r.group_id else None,
                    )
                    for r in ip_rows
                ],
            )
        )

    if url_rows:
        groups.append(
            SearchGroup(
                entity_type="url",
                label="URL 入口",
                items=[
                    SearchHit(
                        entity_type="url",
                        id=r.id,
                        title=r.name,
                        subtitle=r.full_url,
                        badge=r.access_type,
                        status=r.status,
                        group_name=gnames.get(r.group_id) if r.group_id else None,
                    )
                    for r in url_rows
                ],
            )
        )

    if dev_rows:
        groups.append(
            SearchGroup(
                entity_type="device",
                label="设备",
                items=[
                    SearchHit(
                        entity_type="device",
                        id=r.id,
                        title=r.hostname,
                        subtitle=r.spec_summary or r.rack_location or r.asset_code,
                        badge=r.device_type,
                        status=r.status,
                        group_name=gnames.get(r.group_id) if r.group_id else None,
                    )
                    for r in dev_rows
                ],
            )
        )

    if svc_rows:
        groups.append(
            SearchGroup(
                entity_type="service",
                label="服务",
                items=[
                    SearchHit(
                        entity_type="service",
                        id=r.id,
                        title=r.name,
                        subtitle=r.description or r.owner,
                        badge=r.env or r.service_type,
                        status=r.status,
                        group_name=gnames.get(r.group_id) if r.group_id else None,
                    )
                    for r in svc_rows
                ],
            )
        )

    if cl_rows:
        groups.append(
            SearchGroup(
                entity_type="cluster",
                label="集群",
                items=[
                    SearchHit(
                        entity_type="cluster",
                        id=r.id,
                        title=r.name,
                        subtitle=r.cluster_type or r.owner,
                        badge=r.env,
                        status=r.status,
                        group_name=gnames.get(r.group_id) if r.group_id else None,
                    )
                    for r in cl_rows
                ],
            )
        )

    if nic_rows:
        groups.append(
            SearchGroup(
                entity_type="nic",
                label="网卡",
                items=[
                    SearchHit(
                        entity_type="nic",
                        id=n.id,
                        title=f"{dhost}:{n.name}",
                        subtitle=str(n.mac_address) if n.mac_address else (n.switch_port or n.nic_type),
                        badge=f"VLAN {n.vlan_id}" if n.vlan_id else n.nic_type,
                        status=n.status,
                        group_name=gnames.get(dgid) if dgid else None,
                    )
                    for n, dhost, dgid in nic_rows
                ],
            )
        )

    if sub_rows:
        groups.append(
            SearchGroup(
                entity_type="subnet",
                label="网段 (IPAM)",
                items=[
                    SearchHit(
                        entity_type="subnet",
                        id=r.id,
                        title=str(r.cidr),
                        subtitle=r.name,
                        badge=f"VLAN {r.vlan_id}" if r.vlan_id else r.env,
                        status=r.status,
                        group_name=gnames.get(r.group_id) if r.group_id else None,
                    )
                    for r in sub_rows
                ],
            )
        )

    total = sum(len(g.items) for g in groups)
    return GlobalSearchOut(query=keyword, total=total, groups=groups)
