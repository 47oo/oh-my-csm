"""Subnet & IPAM endpoints (CRUD, IP grid matrix <= /20, list fallback, next free IP)."""

from __future__ import annotations

import ipaddress

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import AuthContext, get_current_auth
from app.core.security import utc_now
from app.models.assets import NIC, Device, IPAddress, Subnet
from app.schemas.assets import (
    IpOut,
    NextFreeIpOut,
    SubnetCreate,
    SubnetIpCellOut,
    SubnetMatrixOut,
    SubnetOut,
    SubnetUpdate,
    TagBrief,
)
from app.schemas.common import MessageResponse, PageResponse
from app.services.assets import (
    can_render_ip_matrix,
    find_next_free_ip,
    format_ip_str,
    get_default_zone_id,
    get_subnet_ip_map,
    get_zone_subnet_ip_map,
    load_entity_tags,
    load_group_names,
    load_zone_names,
    normalize_cidr,
    normalize_ip,
    subnet_capacity,
    sync_entity_tags,
    validate_ip_in_subnet,
)
from app.services.audit import record_audit
from app.services.scope import apply_scope_filter, require_entity_permission, resolve_scope

router = APIRouter(prefix="/subnets", tags=["网段与 IPAM"])


async def _serialize_subnets(db: AsyncSession, rows: list[Subnet]) -> list[SubnetOut]:
    if not rows:
        return []
    ids = [r.id for r in rows]
    group_names = await load_group_names(db, {r.group_id for r in rows if r.group_id})
    zone_names = await load_zone_names(db, {r.network_zone_id for r in rows})
    tag_map = await load_entity_tags(db, "subnet", ids)

    ip_counts = dict(
        (
            await db.execute(
                select(IPAddress.subnet_id, func.count())
                .where(
                    IPAddress.subnet_id.in_(ids),
                    IPAddress.deleted_at.is_(None),
                    IPAddress.status != "free",
                )
                .group_by(IPAddress.subnet_id)
            )
        ).all()
    )

    result: list[SubnetOut] = []
    for r in rows:
        cidr_str = str(r.cidr)
        cap = subnet_capacity(cidr_str)
        used = int(ip_counts.get(r.id, 0))
        pct = round((used / cap) * 100, 1) if cap > 0 else 0.0
        result.append(
            SubnetOut(
                id=r.id,
                network_zone_id=r.network_zone_id,
                network_zone_name=zone_names.get(r.network_zone_id),
                group_id=r.group_id,
                group_name=group_names.get(r.group_id) if r.group_id else None,
                name=r.name,
                cidr=cidr_str,
                gateway=format_ip_str(r.gateway) if r.gateway else None,
                vlan_id=r.vlan_id,
                env=r.env,
                status=r.status,
                description=r.description,
                extra_attrs=r.extra_attrs or {},
                tags=[TagBrief(**t) for t in tag_map.get(r.id, [])],
                total_capacity=cap,
                allocated_count=used,
                utilization_pct=min(pct, 100.0),
                can_render_matrix=can_render_ip_matrix(cidr_str),
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
        )
    return result


@router.get("", response_model=PageResponse[SubnetOut])
async def list_subnets(
    q: str | None = Query(default=None),
    network_zone_id: int | None = Query(default=None),
    group_id: int | None = Query(default=None),
    env: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> PageResponse[SubnetOut]:
    scope = await resolve_scope(db, auth, "asset:read")
    base = apply_scope_filter(
        select(Subnet).where(Subnet.deleted_at.is_(None)), Subnet, "subnet", scope
    )
    if network_zone_id is not None:
        base = base.where(Subnet.network_zone_id == network_zone_id)
    if group_id is not None:
        base = base.where(Subnet.group_id == group_id)
    if env:
        base = base.where(Subnet.env == env)
    if q:
        pattern = f"%{q.strip()}%"
        base = base.where(
            or_(
                Subnet.name.ilike(pattern),
                Subnet.description.ilike(pattern),
                func.cast(Subnet.cidr, Subnet.name.type).ilike(pattern),
            )
        )

    total = int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one())
    rows = list(
        (
            await db.execute(
                base.order_by(Subnet.id.desc()).offset((page - 1) * page_size).limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    items = await _serialize_subnets(db, rows)
    return PageResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=SubnetOut, status_code=status.HTTP_201_CREATED)
async def create_subnet(
    body: SubnetCreate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SubnetOut:
    auth.require_group_permission("asset:write", body.group_id)

    zone_id = body.network_zone_id or await get_default_zone_id(db)
    cidr = normalize_cidr(body.cidr)
    gateway = normalize_ip(body.gateway) if body.gateway else None
    if gateway:
        validate_ip_in_subnet(gateway, cidr)

    exists = (
        await db.execute(
            select(Subnet.id).where(
                Subnet.network_zone_id == zone_id,
                Subnet.cidr == cidr,
                Subnet.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"该网络域内网段 {cidr} 已存在",
        )

    subnet = Subnet(
        network_zone_id=zone_id,
        group_id=body.group_id,
        name=body.name.strip(),
        cidr=cidr,
        gateway=gateway,
        vlan_id=body.vlan_id,
        env=body.env,
        status=body.status,
        description=body.description,
        extra_attrs=body.extra_attrs,
        created_by=auth.user.id,
        updated_by=auth.user.id,
    )
    db.add(subnet)
    await db.flush()
    await sync_entity_tags(db, "subnet", subnet.id, body.tag_ids)

    await record_audit(
        db,
        action="create",
        target_type="subnet",
        target_id=subnet.id,
        target_label=f"{subnet.name} ({cidr})",
        group_id=subnet.group_id,
        after={"name": subnet.name, "cidr": cidr, "gateway": gateway, "vlan_id": subnet.vlan_id},
        auth=auth,
    )
    return (await _serialize_subnets(db, [subnet]))[0]


@router.get("/{subnet_id}", response_model=SubnetOut)
async def get_subnet(
    subnet_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SubnetOut:
    subnet = (
        await db.execute(select(Subnet).where(Subnet.id == subnet_id, Subnet.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if subnet is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="网段不存在")
    await require_entity_permission(db, auth, "subnet", subnet.id, subnet.group_id, "asset:read")
    return (await _serialize_subnets(db, [subnet]))[0]


@router.put("/{subnet_id}", response_model=SubnetOut)
async def update_subnet(
    subnet_id: int,
    body: SubnetUpdate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SubnetOut:
    subnet = (
        await db.execute(select(Subnet).where(Subnet.id == subnet_id, Subnet.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if subnet is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="网段不存在")
    auth.require_group_permission("asset:write", subnet.group_id)
    if body.group_id is not None and body.group_id != subnet.group_id:
        auth.require_group_permission("asset:write", body.group_id)

    before = {
        "name": subnet.name,
        "group_id": subnet.group_id,
        "gateway": format_ip_str(subnet.gateway) if subnet.gateway else None,
        "vlan_id": subnet.vlan_id,
        "env": subnet.env,
        "status": subnet.status,
        "description": subnet.description,
    }

    if body.group_id is not None:
        subnet.group_id = body.group_id
    if body.name is not None:
        subnet.name = body.name.strip()
    if body.gateway is not None:
        gw = normalize_ip(body.gateway) if body.gateway.strip() else None
        if gw:
            validate_ip_in_subnet(gw, str(subnet.cidr))
        subnet.gateway = gw
    if body.vlan_id is not None:
        subnet.vlan_id = body.vlan_id
    if body.env is not None:
        subnet.env = body.env
    if body.status is not None:
        subnet.status = body.status
    if body.description is not None:
        subnet.description = body.description
    if body.extra_attrs is not None:
        subnet.extra_attrs = body.extra_attrs
    subnet.updated_by = auth.user.id
    await db.flush()
    await sync_entity_tags(db, "subnet", subnet.id, body.tag_ids)

    await record_audit(
        db,
        action="update",
        target_type="subnet",
        target_id=subnet.id,
        target_label=f"{subnet.name} ({subnet.cidr})",
        group_id=subnet.group_id,
        before=before,
        after={
            "name": subnet.name,
            "group_id": subnet.group_id,
            "gateway": format_ip_str(subnet.gateway) if subnet.gateway else None,
            "vlan_id": subnet.vlan_id,
            "env": subnet.env,
            "status": subnet.status,
            "description": subnet.description,
        },
        auth=auth,
    )
    return (await _serialize_subnets(db, [subnet]))[0]


@router.delete("/{subnet_id}", response_model=MessageResponse)
async def delete_subnet(
    subnet_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    subnet = (
        await db.execute(select(Subnet).where(Subnet.id == subnet_id, Subnet.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if subnet is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="网段不存在")
    auth.require_group_permission("asset:delete", subnet.group_id)

    active_ips = list(
        (
            await db.execute(
                select(IPAddress.ip_address)
                .where(
                    IPAddress.subnet_id == subnet.id,
                    IPAddress.deleted_at.is_(None),
                    IPAddress.status != "free",
                )
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    if active_ips:
        sample = ", ".join(format_ip_str(ip) for ip in active_ips[:5])
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"该网段下仍有已分配/预留 IP（示例：{sample} 等），请先解绑或删除关联 IP",
        )

    subnet.deleted_at = utc_now()
    subnet.updated_by = auth.user.id
    await db.flush()

    await record_audit(
        db,
        action="delete",
        target_type="subnet",
        target_id=subnet.id,
        target_label=f"{subnet.name} ({subnet.cidr})",
        group_id=subnet.group_id,
        before={"name": subnet.name, "cidr": str(subnet.cidr)},
        auth=auth,
    )
    return MessageResponse(message=f"网段 '{subnet.cidr}' 已移入回收站")


@router.get("/{subnet_id}/next-free-ip", response_model=NextFreeIpOut)
async def get_next_free_ip(
    subnet_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> NextFreeIpOut:
    subnet = (
        await db.execute(select(Subnet).where(Subnet.id == subnet_id, Subnet.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if subnet is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="网段不存在")
    await require_entity_permission(db, auth, "subnet", subnet.id, subnet.group_id, "asset:read")

    ip_map = await get_zone_subnet_ip_map(db, subnet)
    occupied = {ip for ip, obj in ip_map.items() if obj.status != "free"}
    next_ip = find_next_free_ip(subnet, occupied)
    return NextFreeIpOut(subnet_id=subnet.id, cidr=str(subnet.cidr), next_free_ip=next_ip)


@router.get("/{subnet_id}/matrix", response_model=SubnetMatrixOut)
async def get_subnet_matrix(
    subnet_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SubnetMatrixOut:
    subnet = (
        await db.execute(select(Subnet).where(Subnet.id == subnet_id, Subnet.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if subnet is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="网段不存在")
    await require_entity_permission(db, auth, "subnet", subnet.id, subnet.group_id, "asset:read")
    read_scope = await resolve_scope(db, auth, "asset:read")

    subnet_out = (await _serialize_subnets(db, [subnet]))[0]
    ip_map = await get_subnet_ip_map(db, subnet.id)

    # Batch load NIC -> Device hostnames for tooltips
    nic_ids = {obj.nic_id for obj in ip_map.values() if obj.nic_id}
    nic_device_map: dict[int, tuple[int, str, str]] = {}
    if nic_ids:
        rows = (
            await db.execute(
                select(NIC.id, NIC.name, Device.id, Device.hostname)
                .join(Device, Device.id == NIC.device_id)
                .where(NIC.id.in_(nic_ids))
            )
        ).all()
        nic_device_map = {nid: (did, dhost, nname) for nid, nname, did, dhost in rows}

    stats = {
        "total": subnet_out.total_capacity,
        "allocated": 0,
        "vip": 0,
        "reserved": 0,
        "conflict": 0,
        "free": 0,
    }
    for obj in ip_map.values():
        if obj.status == "conflict":
            stats["conflict"] += 1
        elif obj.status == "reserved":
            stats["reserved"] += 1
        elif obj.ip_type == "vip" and obj.status != "free":
            stats["vip"] += 1
        elif obj.status != "free":
            stats["allocated"] += 1

    occupied_total = stats["allocated"] + stats["vip"] + stats["reserved"] + stats["conflict"]
    stats["free"] = max(stats["total"] - occupied_total, 0)

    cidr_str = str(subnet.cidr)
    gateway = format_ip_str(subnet.gateway) if subnet.gateway else None

    if can_render_ip_matrix(cidr_str):
        net = ipaddress.ip_network(cidr_str, strict=False)
        iterator = net.hosts() if (net.version == 4 and net.prefixlen <= 30) else iter(net)
        cells: list[SubnetIpCellOut] = []
        for addr in iterator:
            ip_text = str(addr)
            rec = ip_map.get(ip_text)
            is_gw = gateway is not None and ip_text == gateway
            if rec is not None:
                can_see = read_scope.allows("ip", rec.id, rec.group_id)
                dev_info = nic_device_map.get(rec.nic_id) if (can_see and rec.nic_id) else None
                cell_status = rec.status
                if rec.ip_type == "vip" and rec.status == "allocated":
                    cell_status = "vip"
                cells.append(
                    SubnetIpCellOut(
                        ip=ip_text,
                        status=cell_status,
                        ip_type=rec.ip_type,
                        ip_id=rec.id if can_see else None,
                        nic_id=rec.nic_id if can_see else None,
                        device_id=dev_info[0] if dev_info else None,
                        device_hostname=dev_info[1] if dev_info else (None if can_see else "已占用（无权查看）"),
                        usage=rec.usage if can_see else None,
                        is_gateway=is_gw,
                    )
                )
            else:
                cells.append(
                    SubnetIpCellOut(
                        ip=ip_text,
                        status="gateway" if is_gw else "free",
                        is_gateway=is_gw,
                    )
                )
        return SubnetMatrixOut(
            subnet=subnet_out,
            mode="matrix",
            stats=stats,
            cells=cells,
            total_allocated=len(ip_map),
        )

    # Large subnet (> /20) or IPv6 fallback: paginated allocated IP list (filtered by read scope)
    visible_ips = [r for r in ip_map.values() if read_scope.allows("ip", r.id, r.group_id)]
    sorted_ips = sorted(visible_ips, key=lambda x: ipaddress.ip_address(format_ip_str(x.ip_address)))
    total_allocated = len(sorted_ips)
    sliced = sorted_ips[(page - 1) * page_size : page * page_size]
    zone_names = await load_zone_names(db, {subnet.network_zone_id})
    group_names = await load_group_names(db, {r.group_id for r in sliced if r.group_id})

    allocated_items = []
    for r in sliced:
        dev_info = nic_device_map.get(r.nic_id) if r.nic_id else None
        allocated_items.append(
            IpOut(
                id=r.id,
                network_zone_id=r.network_zone_id,
                network_zone_name=zone_names.get(r.network_zone_id),
                subnet_id=r.subnet_id,
                subnet_cidr=cidr_str,
                nic_id=r.nic_id,
                nic_name=dev_info[2] if dev_info else None,
                device_id=dev_info[0] if dev_info else None,
                device_hostname=dev_info[1] if dev_info else None,
                group_id=r.group_id,
                group_name=group_names.get(r.group_id) if r.group_id else None,
                ip_address=format_ip_str(r.ip_address),
                ip_type=r.ip_type,
                is_primary=r.is_primary,
                status=r.status,
                usage=r.usage,
                health_status=r.health_status,
                last_checked_at=r.last_checked_at,
                description=r.description,
                extra_attrs=r.extra_attrs or {},
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
        )

    return SubnetMatrixOut(
        subnet=subnet_out,
        mode="list",
        stats=stats,
        allocated_items=allocated_items,
        total_allocated=total_allocated,
        page=page,
        page_size=page_size,
    )
