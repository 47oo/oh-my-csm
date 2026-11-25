"""IPAddress CRUD, NIC binding, auto LPM subnet matching, VIP multi-target bindings, and panoramic detail."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import AuthContext, get_current_auth
from app.core.security import utc_now
from app.models.assets import (
    NIC,
    Cluster,
    Device,
    IPAddress,
    IPVIPBinding,
    Service,
    ServiceEndpoint,
    Subnet,
    UrlEntry,
)
from app.models.audit import AuditLog
from app.schemas.assets import (
    IpCreate,
    IpDetailOut,
    IpOut,
    IpUpdate,
    RelatedEntityCard,
    TagBrief,
    VipBindingCreate,
    VipBindingOut,
)
from app.schemas.common import MessageResponse, PageResponse
from app.schemas.system import AuditLogOut
from app.services.assets import (
    find_next_free_ip,
    format_ip_str,
    get_default_zone_id,
    get_zone_subnet_ip_map,
    load_entity_tags,
    load_group_names,
    load_zone_names,
    match_subnet_for_ip,
    normalize_ip,
    sync_entity_tags,
    validate_ip_in_subnet,
)
from app.services.audit import record_audit
from app.services.scope import (
    apply_scope_filter,
    is_card_restricted,
    require_entity_permission,
    resolve_scope,
)

router = APIRouter(prefix="/ips", tags=["IP 地址台账"])


async def _serialize_ips(db: AsyncSession, rows: list[IPAddress]) -> list[IpOut]:
    if not rows:
        return []
    ids = [r.id for r in rows]
    zone_names = await load_zone_names(db, {r.network_zone_id for r in rows})
    group_names = await load_group_names(db, {r.group_id for r in rows if r.group_id})
    tag_map = await load_entity_tags(db, "ip", ids)

    subnet_ids = {r.subnet_id for r in rows if r.subnet_id}
    subnet_map: dict[int, str] = {}
    if subnet_ids:
        subnet_map = {
            sid: str(cidr)
            for sid, cidr in (
                await db.execute(select(Subnet.id, Subnet.cidr).where(Subnet.id.in_(subnet_ids)))
            ).all()
        }

    nic_ids = {r.nic_id for r in rows if r.nic_id}
    nic_map: dict[int, tuple[str, int, str]] = {}
    if nic_ids:
        for nid, nname, did, dhost in (
            await db.execute(
                select(NIC.id, NIC.name, Device.id, Device.hostname)
                .join(Device, Device.id == NIC.device_id)
                .where(NIC.id.in_(nic_ids))
            )
        ).all():
            nic_map[nid] = (nname, did, dhost)

    vip_counts = dict(
        (
            await db.execute(
                select(IPVIPBinding.ip_id, func.count())
                .where(IPVIPBinding.ip_id.in_(ids))
                .group_by(IPVIPBinding.ip_id)
            )
        ).all()
    )

    result: list[IpOut] = []
    for r in rows:
        nic_info = nic_map.get(r.nic_id) if r.nic_id else None
        result.append(
            IpOut(
                id=r.id,
                network_zone_id=r.network_zone_id,
                network_zone_name=zone_names.get(r.network_zone_id),
                subnet_id=r.subnet_id,
                subnet_cidr=subnet_map.get(r.subnet_id) if r.subnet_id else None,
                nic_id=r.nic_id,
                nic_name=nic_info[0] if nic_info else None,
                device_id=nic_info[1] if nic_info else None,
                device_hostname=nic_info[2] if nic_info else None,
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
                tags=[TagBrief(**t) for t in tag_map.get(r.id, [])],
                vip_binding_count=int(vip_counts.get(r.id, 0)),
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
        )
    return result


async def _resolve_vip_bindings(db: AsyncSession, ip_id: int) -> list[VipBindingOut]:
    rows = list(
        (
            await db.execute(
                select(IPVIPBinding)
                .where(IPVIPBinding.ip_id == ip_id)
                .order_by(IPVIPBinding.id.asc())
            )
        )
        .scalars()
        .all()
    )
    out: list[VipBindingOut] = []
    for b in rows:
        label = f"{b.target_type}:{b.target_id}"
        if b.target_type == "nic":
            row = (
                await db.execute(
                    select(Device.hostname, NIC.name)
                    .join(NIC, NIC.device_id == Device.id)
                    .where(NIC.id == b.target_id)
                )
            ).first()
            if row:
                label = f"{row[0]}:{row[1]}"
        elif b.target_type == "cluster":
            name = (
                await db.execute(select(Cluster.name).where(Cluster.id == b.target_id))
            ).scalar_one_or_none()
            if name:
                label = name
        elif b.target_type == "service":
            name = (
                await db.execute(select(Service.name).where(Service.id == b.target_id))
            ).scalar_one_or_none()
            if name:
                label = name
        out.append(
            VipBindingOut(
                id=b.id,
                ip_id=b.ip_id,
                target_type=b.target_type,
                target_id=b.target_id,
                target_label=label,
                role=b.role,
                description=b.description,
                created_at=b.created_at,
            )
        )
    return out


@router.get("", response_model=PageResponse[IpOut])
async def list_ips(
    q: str | None = Query(default=None),
    network_zone_id: int | None = Query(default=None),
    subnet_id: int | None = Query(default=None),
    nic_id: int | None = Query(default=None),
    group_id: int | None = Query(default=None),
    ip_type: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> PageResponse[IpOut]:
    scope = await resolve_scope(db, auth, "asset:read")
    base = apply_scope_filter(
        select(IPAddress).where(IPAddress.deleted_at.is_(None)), IPAddress, "ip", scope
    )
    if network_zone_id is not None:
        base = base.where(IPAddress.network_zone_id == network_zone_id)
    if subnet_id is not None:
        base = base.where(IPAddress.subnet_id == subnet_id)
    if nic_id is not None:
        base = base.where(IPAddress.nic_id == nic_id)
    if group_id is not None:
        base = base.where(IPAddress.group_id == group_id)
    if ip_type:
        base = base.where(IPAddress.ip_type == ip_type)
    if status_filter:
        base = base.where(IPAddress.status == status_filter)
    if q:
        pattern = f"%{q.strip()}%"
        base = base.where(
            or_(
                func.cast(IPAddress.ip_address, IPAddress.status.type).ilike(pattern),
                IPAddress.usage.ilike(pattern),
                IPAddress.description.ilike(pattern),
            )
        )

    total = int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one())
    rows = list(
        (
            await db.execute(
                base.order_by(IPAddress.id.desc()).offset((page - 1) * page_size).limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return PageResponse(
        items=await _serialize_ips(db, rows),
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=IpOut, status_code=status.HTTP_201_CREATED)
async def create_ip(
    body: IpCreate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> IpOut:
    raw_ip = (body.ip_address or "").strip()
    auto_allocated = not raw_ip
    if auto_allocated and body.subnet_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="IP 地址与所属网段至少填写一项（留空 IP 时必须选择所属网段以自动分配）",
        )

    nic: NIC | None = None
    nic_device: Device | None = None
    if body.nic_id is not None:
        nic = (
            await db.execute(select(NIC).where(NIC.id == body.nic_id, NIC.deleted_at.is_(None)))
        ).scalar_one_or_none()
        if nic is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="绑定的网卡不存在")
        nic_device = (
            await db.execute(
                select(Device).where(Device.id == nic.device_id, Device.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if nic_device is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="网卡所属设备不存在")
        await require_entity_permission(
            db, auth, "device", nic_device.id, nic_device.group_id, "asset:write"
        )

    target_cluster: Cluster | None = None
    if body.cluster_id is not None:
        target_cluster = (
            await db.execute(
                select(Cluster).where(Cluster.id == body.cluster_id, Cluster.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if target_cluster is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="关联集群不存在")
        await require_entity_permission(
            db, auth, "cluster", target_cluster.id, target_cluster.group_id, "asset:write"
        )

    subnet: Subnet | None = None
    if body.subnet_id is not None:
        sub_stmt = select(Subnet).where(Subnet.id == body.subnet_id, Subnet.deleted_at.is_(None))
        if auto_allocated:
            sub_stmt = sub_stmt.with_for_update()
        subnet = (await db.execute(sub_stmt)).scalar_one_or_none()
        if subnet is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="所属网段不存在")
        if auto_allocated:
            await require_entity_permission(
                db, auth, "subnet", subnet.id, subnet.group_id, "asset:read"
            )

    zone_id = (
        body.network_zone_id
        or (subnet.network_zone_id if subnet else None)
        or (nic_device.network_zone_id if nic_device else None)
        or await get_default_zone_id(db)
    )
    if subnet is not None and subnet.network_zone_id != zone_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="所选网段不属于指定的网络域",
        )

    group_id = (
        body.group_id
        if body.group_id is not None
        else (
            nic_device.group_id
            if nic_device and nic_device.group_id is not None
            else (subnet.group_id if subnet else None)
        )
    )
    if not auth.has_permission_in_group("asset:write", group_id):
        write_scope = await resolve_scope(db, auth, "asset:write")
        allowed_via_nic = nic_device is not None and write_scope.allows(
            "device", nic_device.id, nic_device.group_id
        )
        allowed_via_cluster = (
            body.cluster_id is not None and body.cluster_id in write_scope.cluster_ids
        )
        if not (allowed_via_nic or allowed_via_cluster):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="仅集群授权用户录入 IP 时必须绑定到有写权限的集群网卡或指定集群 VIP",
            )

    ip_obj: IPAddress | None = None
    ip_str = ""
    reused_free = False
    max_attempts = 5 if auto_allocated else 1
    for attempt in range(max_attempts):
        reusable_ip: IPAddress | None = None
        if auto_allocated:
            assert subnet is not None
            zone_ip_map = await get_zone_subnet_ip_map(db, subnet)
            occupied = {ip for ip, obj in zone_ip_map.items() if obj.status != "free"}
            next_ip = find_next_free_ip(subnet, occupied)
            if next_ip is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"网段 {subnet.cidr} 已无可用空闲 IP，请更换网段或释放空闲地址",
                )
            ip_str = next_ip
            reusable_ip = zone_ip_map.get(ip_str)
        else:
            ip_str = normalize_ip(raw_ip)
            if subnet is not None:
                validate_ip_in_subnet(ip_str, str(subnet.cidr))
            else:
                subnet = await match_subnet_for_ip(db, zone_id, ip_str)

            exists = (
                await db.execute(
                    select(IPAddress.id).where(
                        IPAddress.network_zone_id == zone_id,
                        IPAddress.ip_address == ip_str,
                        IPAddress.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if exists is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"该网络域内 IP {ip_str} 已存在，请检查冲突",
                )

        sp = await db.begin_nested()
        try:
            reused_free = reusable_ip is not None
            if reusable_ip is not None:
                reusable_ip.subnet_id = subnet.id if subnet else None
                reusable_ip.nic_id = nic.id if nic else None
                reusable_ip.group_id = group_id
                reusable_ip.ip_type = body.ip_type
                reusable_ip.is_primary = body.is_primary
                reusable_ip.status = body.status
                reusable_ip.usage = body.usage
                reusable_ip.description = body.description
                reusable_ip.extra_attrs = body.extra_attrs
                reusable_ip.updated_by = auth.user.id
                ip_obj = reusable_ip
            else:
                ip_obj = IPAddress(
                    network_zone_id=zone_id,
                    subnet_id=subnet.id if subnet else None,
                    nic_id=nic.id if nic else None,
                    group_id=group_id,
                    ip_address=ip_str,
                    ip_type=body.ip_type,
                    is_primary=body.is_primary,
                    status=body.status,
                    usage=body.usage,
                    description=body.description,
                    extra_attrs=body.extra_attrs,
                    created_by=auth.user.id,
                    updated_by=auth.user.id,
                )
                db.add(ip_obj)
            await db.flush()
            await sp.commit()
            break
        except IntegrityError as exc:
            await sp.rollback()
            if auto_allocated and attempt + 1 < max_attempts:
                continue
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"该网络域内 IP {ip_str} 并发冲突，请重试",
            ) from exc

    assert ip_obj is not None
    if body.cluster_id is not None:
        existing_binding = (
            await db.execute(
                select(IPVIPBinding.id).where(
                    IPVIPBinding.ip_id == ip_obj.id,
                    IPVIPBinding.target_type == "cluster",
                    IPVIPBinding.target_id == body.cluster_id,
                )
            )
        ).scalar_one_or_none()
        if existing_binding is None:
            db.add(
                IPVIPBinding(
                    ip_id=ip_obj.id,
                    target_type="cluster",
                    target_id=body.cluster_id,
                    role="member",
                )
            )
            await db.flush()
    await sync_entity_tags(db, "ip", ip_obj.id, body.tag_ids)

    await record_audit(
        db,
        action="create",
        target_type="ip",
        target_id=ip_obj.id,
        target_label=ip_str,
        group_id=ip_obj.group_id,
        after={
            "ip_address": ip_str,
            "network_zone_id": zone_id,
            "subnet_id": ip_obj.subnet_id,
            "nic_id": ip_obj.nic_id,
            "ip_type": ip_obj.ip_type,
            "status": ip_obj.status,
            "auto_allocated": auto_allocated,
            "reused_free_record": reused_free,
        },
        auth=auth,
    )
    return (await _serialize_ips(db, [ip_obj]))[0]


@router.get("/{ip_id}", response_model=IpDetailOut)
async def get_ip_detail(
    ip_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> IpDetailOut:
    ip_obj = (
        await db.execute(
            select(IPAddress).where(IPAddress.id == ip_id, IPAddress.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if ip_obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="IP 不存在")
    scope = await require_entity_permission(
        db, auth, "ip", ip_obj.id, ip_obj.group_id, "asset:read"
    )

    base_out = (await _serialize_ips(db, [ip_obj]))[0]

    subnet_card = None
    if ip_obj.subnet_id:
        sub = (
            await db.execute(
                select(Subnet).where(Subnet.id == ip_obj.subnet_id, Subnet.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if sub:
            subnet_card = RelatedEntityCard(
                entity_type="subnet",
                id=sub.id,
                title=str(sub.cidr),
                subtitle=sub.name,
                status=sub.status,
                extra_badge=f"VLAN {sub.vlan_id}" if sub.vlan_id else None,
            )

    nic_card = None
    device_card = None
    if ip_obj.nic_id:
        nic = (
            await db.execute(select(NIC).where(NIC.id == ip_obj.nic_id, NIC.deleted_at.is_(None)))
        ).scalar_one_or_none()
        if nic:
            dev = (
                await db.execute(
                    select(Device).where(Device.id == nic.device_id, Device.deleted_at.is_(None))
                )
            ).scalar_one_or_none()
            restricted = (
                is_card_restricted(scope, "device", dev.id, dev.group_id)
                if dev
                else False
            )
            gnames = await load_group_names(db, {dev.group_id} if dev and dev.group_id else set())
            nic_card = RelatedEntityCard(
                entity_type="nic",
                id=nic.id,
                title=nic.name,
                subtitle=None if restricted else (str(nic.mac_address) if nic.mac_address else nic.nic_type),
                status=nic.status,
                extra_badge=f"VLAN {nic.vlan_id}" if nic.vlan_id else None,
                is_cross_group_summary=restricted,
            )
            if dev:
                device_card = RelatedEntityCard(
                    entity_type="device",
                    id=dev.id,
                    title=dev.hostname,
                    subtitle=None if restricted else (dev.spec_summary or dev.device_type),
                    status=dev.status,
                    group_name=gnames.get(dev.group_id) if dev.group_id else None,
                    is_cross_group_summary=restricted,
                )

    vip_bindings = await _resolve_vip_bindings(db, ip_obj.id)

    svc_rows = (
        await db.execute(
            select(Service, ServiceEndpoint.port, ServiceEndpoint.protocol)
            .join(ServiceEndpoint, ServiceEndpoint.service_id == Service.id)
            .where(ServiceEndpoint.ip_id == ip_obj.id, Service.deleted_at.is_(None))
        )
    ).all()
    url_rows = list(
        (
            await db.execute(
                select(UrlEntry).where(
                    UrlEntry.entry_ip_id == ip_obj.id, UrlEntry.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    rel_gids = {s.group_id for s, _, _ in svc_rows if s.group_id} | {
        u.group_id for u in url_rows if u.group_id
    }
    rel_gnames = await load_group_names(db, rel_gids)

    service_cards = [
        RelatedEntityCard(
            entity_type="service",
            id=s.id,
            title=s.name,
            subtitle=f"{proto.upper()}:{port}" if port else proto.upper(),
            status=s.status,
            group_name=rel_gnames.get(s.group_id) if s.group_id else None,
            extra_badge=s.env,
            is_cross_group_summary=is_card_restricted(scope, "service", s.id, s.group_id),
        )
        for s, port, proto in svc_rows
    ]
    url_cards = [
        RelatedEntityCard(
            entity_type="url",
            id=u.id,
            title=u.name,
            subtitle=u.full_url,
            status=u.status,
            group_name=rel_gnames.get(u.group_id) if u.group_id else None,
            extra_badge=u.access_type,
            is_cross_group_summary=is_card_restricted(scope, "url", u.id, u.group_id),
        )
        for u in url_rows
    ]

    audits = list(
        (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.target_type == "ip", AuditLog.target_id == ip_obj.id)
                .order_by(AuditLog.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )

    return IpDetailOut(
        **base_out.model_dump(),
        subnet=subnet_card,
        nic=nic_card,
        device=device_card,
        vip_bindings=vip_bindings,
        services=service_cards,
        urls=url_cards,
        recent_audits=[AuditLogOut.model_validate(a) for a in audits],
    )


@router.put("/{ip_id}", response_model=IpOut)
async def update_ip(
    ip_id: int,
    body: IpUpdate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> IpOut:
    ip_obj = (
        await db.execute(
            select(IPAddress).where(IPAddress.id == ip_id, IPAddress.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if ip_obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="IP 不存在")
    await require_entity_permission(db, auth, "ip", ip_obj.id, ip_obj.group_id, "asset:write")
    if body.group_id is not None and body.group_id != ip_obj.group_id:
        auth.require_group_permission("asset:write", body.group_id)

    ip_str = format_ip_str(ip_obj.ip_address)
    before = {
        "subnet_id": ip_obj.subnet_id,
        "nic_id": ip_obj.nic_id,
        "group_id": ip_obj.group_id,
        "ip_type": ip_obj.ip_type,
        "is_primary": ip_obj.is_primary,
        "status": ip_obj.status,
        "usage": ip_obj.usage,
    }

    if body.subnet_id is not None:
        sub = (
            await db.execute(
                select(Subnet).where(Subnet.id == body.subnet_id, Subnet.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if sub is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="所属网段不存在")
        if body.subnet_id != ip_obj.subnet_id:
            await require_entity_permission(db, auth, "subnet", sub.id, sub.group_id, "asset:read")
        if sub.network_zone_id != ip_obj.network_zone_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="网段与 IP 不在同一个网络域")
        validate_ip_in_subnet(ip_str, str(sub.cidr))
        ip_obj.subnet_id = sub.id

    if body.clear_nic:
        ip_obj.nic_id = None
        ip_obj.is_primary = False
    elif body.nic_id is not None:
        nic = (
            await db.execute(select(NIC).where(NIC.id == body.nic_id, NIC.deleted_at.is_(None)))
        ).scalar_one_or_none()
        if nic is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="绑定的网卡不存在")
        if body.nic_id != ip_obj.nic_id:
            nic_dev = (
                await db.execute(
                    select(Device).where(Device.id == nic.device_id, Device.deleted_at.is_(None))
                )
            ).scalar_one_or_none()
            if nic_dev is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="网卡所属设备不存在")
            await require_entity_permission(
                db, auth, "device", nic_dev.id, nic_dev.group_id, "asset:write"
            )
        ip_obj.nic_id = nic.id

    for field in ("group_id", "ip_type", "is_primary", "status", "usage", "description", "extra_attrs"):
        val = getattr(body, field)
        if val is not None:
            setattr(ip_obj, field, val)
    ip_obj.updated_by = auth.user.id
    await db.flush()
    await sync_entity_tags(db, "ip", ip_obj.id, body.tag_ids)

    await record_audit(
        db,
        action="update",
        target_type="ip",
        target_id=ip_obj.id,
        target_label=ip_str,
        group_id=ip_obj.group_id,
        before=before,
        after={
            "subnet_id": ip_obj.subnet_id,
            "nic_id": ip_obj.nic_id,
            "group_id": ip_obj.group_id,
            "ip_type": ip_obj.ip_type,
            "is_primary": ip_obj.is_primary,
            "status": ip_obj.status,
            "usage": ip_obj.usage,
        },
        auth=auth,
    )
    return (await _serialize_ips(db, [ip_obj]))[0]


@router.delete("/{ip_id}", response_model=MessageResponse)
async def delete_ip(
    ip_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    ip_obj = (
        await db.execute(
            select(IPAddress).where(IPAddress.id == ip_id, IPAddress.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if ip_obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="IP 不存在")
    await require_entity_permission(db, auth, "ip", ip_obj.id, ip_obj.group_id, "asset:delete")

    blockers: list[str] = []
    if ip_obj.nic_id is not None:
        blockers.append("已绑定网卡")

    vip_cnt = int(
        (
            await db.execute(
                select(func.count()).select_from(IPVIPBinding).where(IPVIPBinding.ip_id == ip_obj.id)
            )
        ).scalar_one()
    )
    if vip_cnt:
        blockers.append(f"{vip_cnt} 条 VIP 绑定")

    ep_cnt = int(
        (
            await db.execute(
                select(func.count())
                .select_from(ServiceEndpoint)
                .join(Service, Service.id == ServiceEndpoint.service_id)
                .where(ServiceEndpoint.ip_id == ip_obj.id, Service.deleted_at.is_(None))
            )
        ).scalar_one()
    )
    if ep_cnt:
        blockers.append(f"{ep_cnt} 条服务端点引用")

    url_cnt = int(
        (
            await db.execute(
                select(func.count())
                .select_from(UrlEntry)
                .where(UrlEntry.entry_ip_id == ip_obj.id, UrlEntry.deleted_at.is_(None))
            )
        ).scalar_one()
    )
    if url_cnt:
        blockers.append(f"{url_cnt} 条 URL 入口引用")

    if blockers:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"该 IP 仍被引用（{'、'.join(blockers)}），请先解绑后再删除",
        )

    ip_str = format_ip_str(ip_obj.ip_address)
    ip_obj.deleted_at = utc_now()
    ip_obj.updated_by = auth.user.id
    await db.flush()

    await record_audit(
        db,
        action="delete",
        target_type="ip",
        target_id=ip_obj.id,
        target_label=ip_str,
        group_id=ip_obj.group_id,
        before={"ip_address": ip_str, "ip_type": ip_obj.ip_type},
        auth=auth,
    )
    return MessageResponse(message=f"IP '{ip_str}' 已移入回收站")


@router.post("/{ip_id}/vip-bindings", response_model=VipBindingOut, status_code=status.HTTP_201_CREATED)
async def add_vip_binding(
    ip_id: int,
    body: VipBindingCreate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> VipBindingOut:
    ip_obj = (
        await db.execute(
            select(IPAddress).where(IPAddress.id == ip_id, IPAddress.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if ip_obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="IP 不存在")
    await require_entity_permission(db, auth, "ip", ip_obj.id, ip_obj.group_id, "asset:write")

    # Verify target exists and user holds write permission on it
    model_map = {"nic": NIC, "cluster": Cluster, "service": Service}
    target_model = model_map[body.target_type]
    target = (
        await db.execute(
            select(target_model).where(
                target_model.id == body.target_id,
                target_model.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VIP 绑定的目标对象不存在")
    if body.target_type == "nic":
        nic_dev = (
            await db.execute(
                select(Device).where(Device.id == target.device_id, Device.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if nic_dev is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="目标网卡所属设备不存在")
        await require_entity_permission(
            db, auth, "device", nic_dev.id, nic_dev.group_id, "asset:write"
        )
    else:
        await require_entity_permission(
            db, auth, body.target_type, target.id, target.group_id, "asset:write"
        )

    exists = (
        await db.execute(
            select(IPVIPBinding.id).where(
                IPVIPBinding.ip_id == ip_obj.id,
                IPVIPBinding.target_type == body.target_type,
                IPVIPBinding.target_id == body.target_id,
            )
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该 VIP 已绑定此目标")

    binding = IPVIPBinding(
        ip_id=ip_obj.id,
        target_type=body.target_type,
        target_id=body.target_id,
        role=body.role,
        description=body.description,
    )
    db.add(binding)
    await db.flush()

    await record_audit(
        db,
        action="add_vip_binding",
        target_type="ip",
        target_id=ip_obj.id,
        target_label=format_ip_str(ip_obj.ip_address),
        group_id=ip_obj.group_id,
        after={"target_type": body.target_type, "target_id": body.target_id, "role": body.role},
        auth=auth,
    )
    resolved = await _resolve_vip_bindings(db, ip_obj.id)
    return next(b for b in resolved if b.id == binding.id)


@router.delete("/{ip_id}/vip-bindings/{binding_id}", response_model=MessageResponse)
async def remove_vip_binding(
    ip_id: int,
    binding_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    ip_obj = (
        await db.execute(
            select(IPAddress).where(IPAddress.id == ip_id, IPAddress.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if ip_obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="IP 不存在")
    await require_entity_permission(db, auth, "ip", ip_obj.id, ip_obj.group_id, "asset:write")

    binding = (
        await db.execute(
            select(IPVIPBinding).where(IPVIPBinding.id == binding_id, IPVIPBinding.ip_id == ip_obj.id)
        )
    ).scalar_one_or_none()
    if binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VIP 绑定记录不存在")

    await db.delete(binding)
    await db.flush()
    await record_audit(
        db,
        action="remove_vip_binding",
        target_type="ip",
        target_id=ip_obj.id,
        target_label=format_ip_str(ip_obj.ip_address),
        group_id=ip_obj.group_id,
        before={"target_type": binding.target_type, "target_id": binding.target_id},
        auth=auth,
    )
    return MessageResponse(message="已解除 VIP 绑定")
