"""Device CRUD, hostname duplicate warning, NIC/IP panoramic detail, and delete protection."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import AuthContext, get_current_auth
from app.core.security import utc_now
from app.models.assets import (
    NIC,
    Cluster,
    ClusterDevice,
    Device,
    IPAddress,
    Service,
    ServiceEndpoint,
)
from app.models.audit import AuditLog
from app.schemas.assets import (
    DeviceCreate,
    DeviceDetailOut,
    DeviceOut,
    DeviceUpdate,
    NicOut,
    RelatedEntityCard,
    TagBrief,
)
from app.schemas.common import MessageResponse, PageResponse
from app.schemas.system import AuditLogOut
from app.services.assets import (
    format_ip_str,
    get_default_zone_id,
    load_entity_tags,
    load_group_names,
    load_zone_names,
    sync_entity_tags,
)
from app.services.audit import record_audit
from app.services.scope import (
    apply_scope_filter,
    is_card_restricted,
    require_entity_permission,
    resolve_scope,
)

router = APIRouter(prefix="/devices", tags=["设备管理"])


async def _serialize_devices(
    db: AsyncSession,
    rows: list[Device],
    warnings_map: dict[int, list[str]] | None = None,
) -> list[DeviceOut]:
    if not rows:
        return []
    ids = [r.id for r in rows]
    group_names = await load_group_names(db, {r.group_id for r in rows if r.group_id})
    zone_names = await load_zone_names(db, {r.network_zone_id for r in rows if r.network_zone_id})
    tag_map = await load_entity_tags(db, "device", ids)

    # Cluster names
    cl_rows = (
        await db.execute(
            select(ClusterDevice.device_id, Cluster.name)
            .join(Cluster, Cluster.id == ClusterDevice.cluster_id)
            .where(ClusterDevice.device_id.in_(ids), Cluster.deleted_at.is_(None))
        )
    ).all()
    cl_map: dict[int, list[str]] = {did: [] for did in ids}
    for did, cname in cl_rows:
        cl_map.setdefault(did, []).append(cname)

    # NIC counts & bound IPs
    nic_rows = list(
        (
            await db.execute(
                select(NIC).where(NIC.device_id.in_(ids), NIC.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    nic_counts: dict[int, int] = {did: 0 for did in ids}
    nic_to_device: dict[int, int] = {}
    for n in nic_rows:
        nic_counts[n.device_id] = nic_counts.get(n.device_id, 0) + 1
        nic_to_device[n.id] = n.device_id

    ip_map: dict[int, list[str]] = {did: [] for did in ids}
    if nic_to_device:
        ip_rows = list(
            (
                await db.execute(
                    select(IPAddress)
                    .where(
                        IPAddress.nic_id.in_(list(nic_to_device.keys())),
                        IPAddress.deleted_at.is_(None),
                    )
                    .order_by(IPAddress.is_primary.desc(), IPAddress.id.asc())
                )
            )
            .scalars()
            .all()
        )
        for ip_obj in ip_rows:
            if ip_obj.nic_id is not None:
                did = nic_to_device[ip_obj.nic_id]
                ip_map.setdefault(did, []).append(format_ip_str(ip_obj.ip_address))

    wmap = warnings_map or {}
    return [
        DeviceOut(
            id=r.id,
            group_id=r.group_id,
            group_name=group_names.get(r.group_id) if r.group_id else None,
            network_zone_id=r.network_zone_id,
            network_zone_name=zone_names.get(r.network_zone_id) if r.network_zone_id else None,
            hostname=r.hostname,
            asset_code=r.asset_code,
            serial_number=r.serial_number,
            device_type=r.device_type,
            env=r.env,
            status=r.status,
            os_info=r.os_info,
            spec_summary=r.spec_summary,
            rack_location=r.rack_location,
            owner=r.owner,
            health_status=r.health_status,
            last_checked_at=r.last_checked_at,
            description=r.description,
            extra_attrs=r.extra_attrs or {},
            tags=[TagBrief(**t) for t in tag_map.get(r.id, [])],
            cluster_names=cl_map.get(r.id, []),
            nic_count=nic_counts.get(r.id, 0),
            primary_ips=ip_map.get(r.id, [])[:6],
            warnings=wmap.get(r.id, []),
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


async def _check_device_uniqueness_and_warnings(
    db: AsyncSession,
    *,
    hostname: str,
    asset_code: str | None,
    serial_number: str | None,
    exclude_id: int | None = None,
) -> list[str]:
    if asset_code:
        q = select(Device.id).where(Device.asset_code == asset_code, Device.deleted_at.is_(None))
        if exclude_id is not None:
            q = q.where(Device.id != exclude_id)
        if (await db.execute(q)).scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"资产编号 '{asset_code}' 已被其他设备占用",
            )

    if serial_number:
        q = select(Device.id).where(
            Device.serial_number == serial_number, Device.deleted_at.is_(None)
        )
        if exclude_id is not None:
            q = q.where(Device.id != exclude_id)
        if (await db.execute(q)).scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"设备序列号 SN '{serial_number}' 已被其他设备占用",
            )

    warnings: list[str] = []
    hq = select(func.count()).select_from(Device).where(
        Device.hostname == hostname, Device.deleted_at.is_(None)
    )
    if exclude_id is not None:
        hq = hq.where(Device.id != exclude_id)
    dup_count = int((await db.execute(hq)).scalar_one())
    if dup_count > 0:
        warnings.append(f"存在 {dup_count} 台同名主机 '{hostname}'（允许跨环境重名，请注意区分）")
    return warnings


@router.get("", response_model=PageResponse[DeviceOut])
async def list_devices(
    q: str | None = Query(default=None),
    group_id: int | None = Query(default=None),
    cluster_id: int | None = Query(default=None),
    device_type: str | None = Query(default=None),
    env: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> PageResponse[DeviceOut]:
    scope = await resolve_scope(db, auth, "asset:read")
    base = apply_scope_filter(
        select(Device).where(Device.deleted_at.is_(None)), Device, "device", scope
    )
    if group_id is not None:
        base = base.where(Device.group_id == group_id)
    if cluster_id is not None:
        base = base.join(ClusterDevice, ClusterDevice.device_id == Device.id).where(
            ClusterDevice.cluster_id == cluster_id
        )
    if device_type:
        base = base.where(Device.device_type == device_type)
    if env:
        base = base.where(Device.env == env)
    if status_filter:
        base = base.where(Device.status == status_filter)
    if q:
        pattern = f"%{q.strip()}%"
        base = base.where(
            or_(
                Device.hostname.ilike(pattern),
                Device.asset_code.ilike(pattern),
                Device.serial_number.ilike(pattern),
                Device.rack_location.ilike(pattern),
                Device.owner.ilike(pattern),
            )
        )

    total = int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one())
    rows = list(
        (
            await db.execute(
                base.order_by(Device.id.desc()).offset((page - 1) * page_size).limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return PageResponse(
        items=await _serialize_devices(db, rows),
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
async def create_device(
    body: DeviceCreate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> DeviceOut:
    if not auth.has_permission_in_group("asset:write", body.group_id):
        write_scope = await resolve_scope(db, auth, "asset:write")
        if not body.cluster_ids or not all(cid in write_scope.cluster_ids for cid in body.cluster_ids):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="仅集群授权用户新建设备时必须关联至少一个有写权限的集群",
            )

    hostname = body.hostname.strip()
    asset_code = body.asset_code.strip() if body.asset_code and body.asset_code.strip() else None
    serial_number = (
        body.serial_number.strip() if body.serial_number and body.serial_number.strip() else None
    )
    warnings = await _check_device_uniqueness_and_warnings(
        db,
        hostname=hostname,
        asset_code=asset_code,
        serial_number=serial_number,
    )

    zone_id = body.network_zone_id or await get_default_zone_id(db)
    device = Device(
        group_id=body.group_id,
        network_zone_id=zone_id,
        hostname=hostname,
        asset_code=asset_code,
        serial_number=serial_number,
        device_type=body.device_type,
        env=body.env,
        status=body.status,
        os_info=body.os_info,
        spec_summary=body.spec_summary,
        rack_location=body.rack_location,
        owner=body.owner,
        description=body.description,
        extra_attrs=body.extra_attrs,
        created_by=auth.user.id,
        updated_by=auth.user.id,
    )
    db.add(device)
    await db.flush()

    if body.cluster_ids:
        for cid in sorted(set(body.cluster_ids)):
            db.add(ClusterDevice(cluster_id=cid, device_id=device.id))
        await db.flush()

    await sync_entity_tags(db, "device", device.id, body.tag_ids)

    await record_audit(
        db,
        action="create",
        target_type="device",
        target_id=device.id,
        target_label=device.hostname,
        group_id=device.group_id,
        after={
            "hostname": device.hostname,
            "asset_code": device.asset_code,
            "device_type": device.device_type,
            "env": device.env,
        },
        auth=auth,
    )
    return (await _serialize_devices(db, [device], {device.id: warnings}))[0]


@router.get("/{device_id}", response_model=DeviceDetailOut)
async def get_device_detail(
    device_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> DeviceDetailOut:
    device = (
        await db.execute(select(Device).where(Device.id == device_id, Device.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="设备不存在")
    scope = await require_entity_permission(
        db, auth, "device", device.id, device.group_id, "asset:read"
    )

    base_out = (await _serialize_devices(db, [device]))[0]

    # Clusters
    cl_rows = (
        await db.execute(
            select(Cluster, ClusterDevice.node_role)
            .join(ClusterDevice, ClusterDevice.cluster_id == Cluster.id)
            .where(ClusterDevice.device_id == device.id, Cluster.deleted_at.is_(None))
        )
    ).all()

    # NICs + bound IPs
    nic_rows = list(
        (
            await db.execute(
                select(NIC)
                .where(NIC.device_id == device.id, NIC.deleted_at.is_(None))
                .order_by(NIC.parent_nic_id.asc().nullsfirst(), NIC.name.asc())
            )
        )
        .scalars()
        .all()
    )
    nic_name_by_id = {n.id: n.name for n in nic_rows}
    nic_ids = [n.id for n in nic_rows]
    ip_rows: list[IPAddress] = []
    if nic_ids:
        ip_rows = list(
            (
                await db.execute(
                    select(IPAddress)
                    .where(IPAddress.nic_id.in_(nic_ids), IPAddress.deleted_at.is_(None))
                    .order_by(IPAddress.is_primary.desc(), IPAddress.id.asc())
                )
            )
            .scalars()
            .all()
        )
    ips_by_nic: dict[int, list[str]] = {}
    for ip_obj in ip_rows:
        if ip_obj.nic_id is not None:
            ips_by_nic.setdefault(ip_obj.nic_id, []).append(format_ip_str(ip_obj.ip_address))

    nic_outs = [
        NicOut(
            id=n.id,
            device_id=n.device_id,
            device_hostname=device.hostname,
            group_id=device.group_id,
            parent_nic_id=n.parent_nic_id,
            parent_nic_name=nic_name_by_id.get(n.parent_nic_id) if n.parent_nic_id else None,
            name=n.name,
            nic_type=n.nic_type,
            mac_address=str(n.mac_address) if n.mac_address else None,
            speed_mbps=n.speed_mbps,
            vlan_id=n.vlan_id,
            bond_mode=n.bond_mode,
            switch_port=n.switch_port,
            status=n.status,
            description=n.description,
            extra_attrs=n.extra_attrs or {},
            bound_ips=ips_by_nic.get(n.id, []),
            created_at=n.created_at,
            updated_at=n.updated_at,
        )
        for n in nic_rows
    ]

    # Services running on this device or on any of its IPs
    ip_ids = [ip_obj.id for ip_obj in ip_rows]
    svc_cond = ServiceEndpoint.device_id == device.id
    if ip_ids:
        svc_cond = or_(svc_cond, ServiceEndpoint.ip_id.in_(ip_ids))
    svc_rows = list(
        (
            await db.execute(
                select(Service)
                .join(ServiceEndpoint, ServiceEndpoint.service_id == Service.id)
                .where(svc_cond, Service.deleted_at.is_(None))
                .distinct()
            )
        )
        .scalars()
        .all()
    )

    gids = (
        {c.group_id for c, _ in cl_rows if c.group_id}
        | {ip_obj.group_id for ip_obj in ip_rows if ip_obj.group_id}
        | {s.group_id for s in svc_rows if s.group_id}
    )
    gnames = await load_group_names(db, gids)

    cluster_cards = [
        RelatedEntityCard(
            entity_type="cluster",
            id=c.id,
            title=c.name,
            subtitle=None if is_card_restricted(scope, "cluster", c.id, c.group_id) else c.cluster_type,
            status=c.status,
            group_name=gnames.get(c.group_id) if c.group_id else None,
            role=node_role,
            extra_badge=c.env,
            is_cross_group_summary=is_card_restricted(scope, "cluster", c.id, c.group_id),
        )
        for c, node_role in cl_rows
    ]
    ip_cards = [
        RelatedEntityCard(
            entity_type="ip",
            id=ip_obj.id,
            title=format_ip_str(ip_obj.ip_address),
            subtitle=nic_name_by_id.get(ip_obj.nic_id) if ip_obj.nic_id else None,
            status=ip_obj.status,
            group_name=gnames.get(ip_obj.group_id) if ip_obj.group_id else None,
            extra_badge="主IP" if ip_obj.is_primary else ip_obj.ip_type,
            is_cross_group_summary=is_card_restricted(scope, "ip", ip_obj.id, ip_obj.group_id),
        )
        for ip_obj in ip_rows
    ]
    service_cards = [
        RelatedEntityCard(
            entity_type="service",
            id=s.id,
            title=s.name,
            subtitle=None if is_card_restricted(scope, "service", s.id, s.group_id) else s.service_type,
            status=s.status,
            group_name=gnames.get(s.group_id) if s.group_id else None,
            extra_badge=s.env,
            is_cross_group_summary=is_card_restricted(scope, "service", s.id, s.group_id),
        )
        for s in svc_rows
    ]

    audits = list(
        (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.target_type == "device", AuditLog.target_id == device.id)
                .order_by(AuditLog.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )

    return DeviceDetailOut(
        **base_out.model_dump(),
        clusters=cluster_cards,
        nics=nic_outs,
        ips=ip_cards,
        services=service_cards,
        recent_audits=[AuditLogOut.model_validate(a) for a in audits],
    )


@router.put("/{device_id}", response_model=DeviceOut)
async def update_device(
    device_id: int,
    body: DeviceUpdate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> DeviceOut:
    device = (
        await db.execute(select(Device).where(Device.id == device_id, Device.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="设备不存在")
    await require_entity_permission(db, auth, "device", device.id, device.group_id, "asset:write")
    if body.group_id is not None and body.group_id != device.group_id:
        auth.require_group_permission("asset:write", body.group_id)

    before = {
        "hostname": device.hostname,
        "asset_code": device.asset_code,
        "serial_number": device.serial_number,
        "device_type": device.device_type,
        "env": device.env,
        "status": device.status,
        "group_id": device.group_id,
    }

    new_hostname = body.hostname.strip() if body.hostname is not None else device.hostname
    new_asset_code = (
        (body.asset_code.strip() or None) if body.asset_code is not None else device.asset_code
    )
    new_sn = (
        (body.serial_number.strip() or None)
        if body.serial_number is not None
        else device.serial_number
    )
    warnings = await _check_device_uniqueness_and_warnings(
        db,
        hostname=new_hostname,
        asset_code=new_asset_code,
        serial_number=new_sn,
        exclude_id=device.id,
    )

    device.hostname = new_hostname
    device.asset_code = new_asset_code
    device.serial_number = new_sn
    for field in (
        "group_id",
        "network_zone_id",
        "device_type",
        "env",
        "status",
        "os_info",
        "spec_summary",
        "rack_location",
        "owner",
        "description",
        "extra_attrs",
    ):
        val = getattr(body, field)
        if val is not None:
            setattr(device, field, val)
    device.updated_by = auth.user.id
    await db.flush()

    if body.cluster_ids is not None:
        await db.execute(delete(ClusterDevice).where(ClusterDevice.device_id == device.id))
        for cid in sorted(set(body.cluster_ids)):
            db.add(ClusterDevice(cluster_id=cid, device_id=device.id))
        await db.flush()

    await sync_entity_tags(db, "device", device.id, body.tag_ids)

    await record_audit(
        db,
        action="update",
        target_type="device",
        target_id=device.id,
        target_label=device.hostname,
        group_id=device.group_id,
        before=before,
        after={
            "hostname": device.hostname,
            "asset_code": device.asset_code,
            "serial_number": device.serial_number,
            "device_type": device.device_type,
            "env": device.env,
            "status": device.status,
            "group_id": device.group_id,
        },
        auth=auth,
    )
    return (await _serialize_devices(db, [device], {device.id: warnings}))[0]


@router.delete("/{device_id}", response_model=MessageResponse)
async def delete_device(
    device_id: int,
    force_unbind: bool = Query(default=False),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    device = (
        await db.execute(select(Device).where(Device.id == device_id, Device.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="设备不存在")
    await require_entity_permission(db, auth, "device", device.id, device.group_id, "asset:delete")

    nics = list(
        (await db.execute(select(NIC).where(NIC.device_id == device.id, NIC.deleted_at.is_(None))))
        .scalars()
        .all()
    )
    nic_ids = [n.id for n in nics]
    ip_cnt = 0
    if nic_ids:
        ip_cnt = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(IPAddress)
                    .where(IPAddress.nic_id.in_(nic_ids), IPAddress.deleted_at.is_(None))
                )
            ).scalar_one()
        )
    ep_cnt = int(
        (
            await db.execute(
                select(func.count())
                .select_from(ServiceEndpoint)
                .join(Service, Service.id == ServiceEndpoint.service_id)
                .where(ServiceEndpoint.device_id == device.id, Service.deleted_at.is_(None))
            )
        ).scalar_one()
    )

    if (nics or ip_cnt or ep_cnt) and not (force_unbind and auth.is_superadmin):
        parts = []
        if nics:
            parts.append(f"{len(nics)} 张网卡")
        if ip_cnt:
            parts.append(f"{ip_cnt} 个绑定 IP")
        if ep_cnt:
            parts.append(f"{ep_cnt} 条服务端点")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"该设备仍有关联项（{'、'.join(parts)}），请先解绑后再删除",
        )

    now = utc_now()
    if force_unbind and auth.is_superadmin:
        if nic_ids:
            ips = list(
                (
                    await db.execute(
                        select(IPAddress).where(
                            IPAddress.nic_id.in_(nic_ids), IPAddress.deleted_at.is_(None)
                        )
                    )
                )
                .scalars()
                .all()
            )
            for ip_obj in ips:
                ip_obj.nic_id = None
                ip_obj.is_primary = False
        for n in nics:
            n.deleted_at = now
            n.updated_by = auth.user.id
        eps = list(
            (
                await db.execute(
                    select(ServiceEndpoint).where(ServiceEndpoint.device_id == device.id)
                )
            )
            .scalars()
            .all()
        )
        for ep in eps:
            ep.device_id = None

    device.deleted_at = now
    device.updated_by = auth.user.id
    await db.flush()

    await record_audit(
        db,
        action="delete",
        target_type="device",
        target_id=device.id,
        target_label=device.hostname,
        group_id=device.group_id,
        before={"hostname": device.hostname, "force_unbind": force_unbind},
        auth=auth,
    )
    return MessageResponse(message=f"设备 '{device.hostname}' 已移入回收站")
