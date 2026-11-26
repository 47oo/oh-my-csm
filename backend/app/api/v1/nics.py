"""NIC CRUD (supports Bond parent_nic_id hierarchy, MAC normalization/warning, delete protection)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import AuthContext, get_current_auth
from app.core.security import utc_now
from app.models.assets import NIC, Device, IPAddress, IPVIPBinding
from app.models.audit import AuditLog
from app.schemas.assets import (
    NicCreate,
    NicDetailOut,
    NicOut,
    NicUpdate,
    RelatedEntityCard,
    TagBrief,
)
from app.schemas.common import MessageResponse, PageResponse
from app.schemas.system import AuditLogOut
from app.services.assets import (
    format_ip_str,
    load_entity_tags,
    load_group_names,
    normalize_mac,
    sync_entity_tags,
)
from app.services.audit import record_audit
from app.services.scope import apply_scope_filter, require_entity_permission, resolve_scope

router = APIRouter(prefix="/nics", tags=["网卡管理"])


async def _serialize_nics(
    db: AsyncSession,
    rows: list[NIC],
    warnings_map: dict[int, list[str]] | None = None,
) -> list[NicOut]:
    if not rows:
        return []
    ids = [r.id for r in rows]
    dev_ids = {r.device_id for r in rows}
    parent_ids = {r.parent_nic_id for r in rows if r.parent_nic_id}

    dev_map = {
        did: (host, gid)
        for did, host, gid in (
            await db.execute(select(Device.id, Device.hostname, Device.group_id).where(Device.id.in_(dev_ids)))
        ).all()
    }
    parent_map: dict[int, str] = {}
    if parent_ids:
        parent_map = dict(
            (await db.execute(select(NIC.id, NIC.name).where(NIC.id.in_(parent_ids)))).all()
        )

    tag_map = await load_entity_tags(db, "nic", ids)

    ip_rows = list(
        (
            await db.execute(
                select(IPAddress)
                .where(IPAddress.nic_id.in_(ids), IPAddress.deleted_at.is_(None))
                .order_by(IPAddress.is_primary.desc(), IPAddress.id.asc())
            )
        )
        .scalars()
        .all()
    )
    ips_by_nic: dict[int, list[str]] = {nid: [] for nid in ids}
    for ip_obj in ip_rows:
        if ip_obj.nic_id is not None:
            ips_by_nic.setdefault(ip_obj.nic_id, []).append(format_ip_str(ip_obj.ip_address))

    wmap = warnings_map or {}
    return [
        NicOut(
            id=r.id,
            device_id=r.device_id,
            device_hostname=dev_map.get(r.device_id, (None, None))[0],
            group_id=dev_map.get(r.device_id, (None, None))[1],
            parent_nic_id=r.parent_nic_id,
            parent_nic_name=parent_map.get(r.parent_nic_id) if r.parent_nic_id else None,
            name=r.name,
            nic_type=r.nic_type,
            mac_address=str(r.mac_address) if r.mac_address else None,
            speed_mbps=r.speed_mbps,
            vlan_id=r.vlan_id,
            bond_mode=r.bond_mode,
            switch_port=r.switch_port,
            status=r.status,
            description=r.description,
            extra_attrs=r.extra_attrs or {},
            tags=[TagBrief(**t) for t in tag_map.get(r.id, [])],
            bound_ips=ips_by_nic.get(r.id, []),
            warnings=wmap.get(r.id, []),
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


async def _check_mac_warnings(
    db: AsyncSession,
    device: Device,
    mac_address: str | None,
    exclude_nic_id: int | None = None,
) -> list[str]:
    if not mac_address:
        return []
    stmt = (
        select(func.count())
        .select_from(NIC)
        .join(Device, Device.id == NIC.device_id)
        .where(
            NIC.mac_address == mac_address,
            NIC.deleted_at.is_(None),
            Device.deleted_at.is_(None),
            Device.network_zone_id == device.network_zone_id,
        )
    )
    if exclude_nic_id is not None:
        stmt = stmt.where(NIC.id != exclude_nic_id)
    dup_cnt = int((await db.execute(stmt)).scalar_one())
    if dup_cnt > 0:
        return [f"同网络域内已有 {dup_cnt} 张网卡使用 MAC {mac_address}（若为 Bond 成员口属正常现象）"]
    return []


@router.get("", response_model=PageResponse[NicOut])
async def list_nics(
    q: str | None = Query(default=None),
    device_id: int | None = Query(default=None),
    nic_type: str | None = Query(default=None),
    vlan_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> PageResponse[NicOut]:
    scope = await resolve_scope(db, auth, "asset:read")
    base = (
        select(NIC)
        .join(Device, Device.id == NIC.device_id)
        .where(NIC.deleted_at.is_(None), Device.deleted_at.is_(None))
    )
    base = apply_scope_filter(base, Device, "device", scope)

    if device_id is not None:
        base = base.where(NIC.device_id == device_id)
    if nic_type:
        base = base.where(NIC.nic_type == nic_type)
    if vlan_id is not None:
        base = base.where(NIC.vlan_id == vlan_id)
    if q:
        pattern = f"%{q.strip()}%"
        base = base.where(
            or_(
                NIC.name.ilike(pattern),
                NIC.switch_port.ilike(pattern),
                Device.hostname.ilike(pattern),
                func.cast(NIC.mac_address, NIC.name.type).ilike(pattern),
            )
        )

    total = int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one())
    rows = list(
        (
            await db.execute(
                base.order_by(NIC.device_id.desc(), NIC.id.asc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return PageResponse(
        items=await _serialize_nics(db, rows),
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=NicOut, status_code=status.HTTP_201_CREATED)
async def create_nic(
    body: NicCreate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> NicOut:
    device = (
        await db.execute(
            select(Device).where(Device.id == body.device_id, Device.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="所属设备不存在")
    await require_entity_permission(db, auth, "device", device.id, device.group_id, "asset:write")

    name = body.name.strip()
    exists = (
        await db.execute(
            select(NIC.id).where(
                NIC.device_id == device.id,
                NIC.name == name,
                NIC.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"设备 '{device.hostname}' 上已存在网卡 '{name}'",
        )

    if body.parent_nic_id is not None:
        parent = (
            await db.execute(
                select(NIC).where(
                    NIC.id == body.parent_nic_id,
                    NIC.device_id == device.id,
                    NIC.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if parent is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="父网卡（聚合口）必须属于同一台设备且存在",
            )

    mac = normalize_mac(body.mac_address)
    warnings = await _check_mac_warnings(db, device, mac)

    nic = NIC(
        device_id=device.id,
        parent_nic_id=body.parent_nic_id,
        name=name,
        nic_type=body.nic_type,
        mac_address=mac,
        speed_mbps=body.speed_mbps,
        vlan_id=body.vlan_id,
        bond_mode=body.bond_mode,
        switch_port=body.switch_port,
        status=body.status,
        description=body.description,
        extra_attrs=body.extra_attrs,
        created_by=auth.user.id,
        updated_by=auth.user.id,
    )
    db.add(nic)
    await db.flush()
    await sync_entity_tags(db, "nic", nic.id, body.tag_ids)

    await record_audit(
        db,
        action="create",
        target_type="nic",
        target_id=nic.id,
        target_label=f"{device.hostname}:{nic.name}",
        group_id=device.group_id,
        after={
            "device_id": device.id,
            "name": nic.name,
            "nic_type": nic.nic_type,
            "mac_address": mac,
            "parent_nic_id": nic.parent_nic_id,
        },
        auth=auth,
    )
    return (await _serialize_nics(db, [nic], {nic.id: warnings}))[0]


@router.get("/{nic_id}", response_model=NicDetailOut)
async def get_nic_detail(
    nic_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> NicDetailOut:
    nic = (
        await db.execute(select(NIC).where(NIC.id == nic_id, NIC.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if nic is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="网卡不存在")
    device = (
        await db.execute(select(Device).where(Device.id == nic.device_id, Device.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="所属设备不存在")
    await require_entity_permission(db, auth, "device", device.id, device.group_id, "asset:read")

    base_out = (await _serialize_nics(db, [nic]))[0]
    gnames = await load_group_names(db, {device.group_id} if device.group_id else set())

    parent_card = None
    if nic.parent_nic_id:
        p = (
            await db.execute(select(NIC).where(NIC.id == nic.parent_nic_id, NIC.deleted_at.is_(None)))
        ).scalar_one_or_none()
        if p:
            parent_card = RelatedEntityCard(
                entity_type="nic",
                id=p.id,
                title=p.name,
                subtitle=p.bond_mode or p.nic_type,
                status=p.status,
            )

    children = list(
        (
            await db.execute(
                select(NIC).where(NIC.parent_nic_id == nic.id, NIC.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    child_cards = [
        RelatedEntityCard(
            entity_type="nic",
            id=c.id,
            title=c.name,
            subtitle=str(c.mac_address) if c.mac_address else c.nic_type,
            status=c.status,
            extra_badge=f"{c.speed_mbps}Mbps" if c.speed_mbps else None,
        )
        for c in children
    ]

    ips = list(
        (
            await db.execute(
                select(IPAddress).where(IPAddress.nic_id == nic.id, IPAddress.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    ip_cards = [
        RelatedEntityCard(
            entity_type="ip",
            id=ip_obj.id,
            title=format_ip_str(ip_obj.ip_address),
            subtitle=ip_obj.usage,
            status=ip_obj.status,
            extra_badge="主IP" if ip_obj.is_primary else ip_obj.ip_type,
        )
        for ip_obj in ips
    ]

    vips = list(
        (
            await db.execute(
                select(IPAddress)
                .join(IPVIPBinding, IPVIPBinding.ip_id == IPAddress.id)
                .where(
                    IPVIPBinding.target_type == "nic",
                    IPVIPBinding.target_id == nic.id,
                    IPAddress.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    vip_cards = [
        RelatedEntityCard(
            entity_type="ip",
            id=v.id,
            title=format_ip_str(v.ip_address),
            subtitle=v.usage,
            status=v.status,
            extra_badge="VIP",
        )
        for v in vips
    ]

    audits = list(
        (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.target_type == "nic", AuditLog.target_id == nic.id)
                .order_by(AuditLog.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )

    return NicDetailOut(
        **base_out.model_dump(),
        device=RelatedEntityCard(
            entity_type="device",
            id=device.id,
            title=device.hostname,
            subtitle=device.device_type,
            status=device.status,
            group_name=gnames.get(device.group_id) if device.group_id else None,
        ),
        parent_nic=parent_card,
        child_nics=child_cards,
        ips=ip_cards,
        vip_ips=vip_cards,
        recent_audits=[AuditLogOut.model_validate(a) for a in audits],
    )


@router.put("/{nic_id}", response_model=NicOut)
async def update_nic(
    nic_id: int,
    body: NicUpdate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> NicOut:
    nic = (
        await db.execute(select(NIC).where(NIC.id == nic_id, NIC.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if nic is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="网卡不存在")
    device = (
        await db.execute(select(Device).where(Device.id == nic.device_id, Device.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="所属设备不存在")
    await require_entity_permission(db, auth, "device", device.id, device.group_id, "asset:write")

    before = {
        "name": nic.name,
        "nic_type": nic.nic_type,
        "mac_address": str(nic.mac_address) if nic.mac_address else None,
        "parent_nic_id": nic.parent_nic_id,
        "vlan_id": nic.vlan_id,
        "status": nic.status,
    }

    if body.parent_nic_id is not None:
        if body.parent_nic_id == nic.id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="父网卡不能指向自身")
        parent = (
            await db.execute(
                select(NIC).where(
                    NIC.id == body.parent_nic_id,
                    NIC.device_id == device.id,
                    NIC.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if parent is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="父网卡必须属于同一台设备且存在",
            )
        nic.parent_nic_id = body.parent_nic_id

    if body.name is not None:
        new_name = body.name.strip()
        conflict = (
            await db.execute(
                select(NIC.id).where(
                    NIC.device_id == device.id,
                    NIC.name == new_name,
                    NIC.id != nic.id,
                    NIC.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if conflict is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"设备 '{device.hostname}' 上已存在网卡 '{new_name}'",
            )
        nic.name = new_name

    warnings: list[str] = []
    if body.mac_address is not None:
        mac = normalize_mac(body.mac_address)
        warnings = await _check_mac_warnings(db, device, mac, exclude_nic_id=nic.id)
        nic.mac_address = mac

    for field in (
        "nic_type",
        "speed_mbps",
        "vlan_id",
        "bond_mode",
        "switch_port",
        "status",
        "description",
        "extra_attrs",
    ):
        val = getattr(body, field)
        if val is not None:
            setattr(nic, field, val)
    nic.updated_by = auth.user.id
    await db.flush()
    await sync_entity_tags(db, "nic", nic.id, body.tag_ids)

    await record_audit(
        db,
        action="update",
        target_type="nic",
        target_id=nic.id,
        target_label=f"{device.hostname}:{nic.name}",
        group_id=device.group_id,
        before=before,
        after={
            "name": nic.name,
            "nic_type": nic.nic_type,
            "mac_address": str(nic.mac_address) if nic.mac_address else None,
            "parent_nic_id": nic.parent_nic_id,
            "vlan_id": nic.vlan_id,
            "status": nic.status,
        },
        auth=auth,
    )
    return (await _serialize_nics(db, [nic], {nic.id: warnings}))[0]


@router.delete("/{nic_id}", response_model=MessageResponse)
async def delete_nic(
    nic_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    nic = (
        await db.execute(select(NIC).where(NIC.id == nic_id, NIC.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if nic is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="网卡不存在")
    device = (
        await db.execute(select(Device).where(Device.id == nic.device_id))
    ).scalar_one_or_none()
    await require_entity_permission(
        db,
        auth,
        "device",
        device.id if device else nic.device_id,
        device.group_id if device else None,
        "asset:delete",
    )

    blockers: list[str] = []
    bound_ips = int(
        (
            await db.execute(
                select(func.count())
                .select_from(IPAddress)
                .where(IPAddress.nic_id == nic.id, IPAddress.deleted_at.is_(None))
            )
        ).scalar_one()
    )
    if bound_ips:
        blockers.append(f"{bound_ips} 个绑定 IP")

    child_nics = int(
        (
            await db.execute(
                select(func.count())
                .select_from(NIC)
                .where(NIC.parent_nic_id == nic.id, NIC.deleted_at.is_(None))
            )
        ).scalar_one()
    )
    if child_nics:
        blockers.append(f"{child_nics} 张成员子网卡")

    vip_cnt = int(
        (
            await db.execute(
                select(func.count())
                .select_from(IPVIPBinding)
                .join(IPAddress, IPAddress.id == IPVIPBinding.ip_id)
                .where(
                    IPVIPBinding.target_type == "nic",
                    IPVIPBinding.target_id == nic.id,
                    IPAddress.deleted_at.is_(None),
                )
            )
        ).scalar_one()
    )
    if vip_cnt:
        blockers.append(f"{vip_cnt} 条 VIP 承载绑定")

    if blockers:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"该网卡仍有关联项（{'、'.join(blockers)}），请先解绑后再删除",
        )

    nic.deleted_at = utc_now()
    nic.updated_by = auth.user.id
    await db.flush()

    await record_audit(
        db,
        action="delete",
        target_type="nic",
        target_id=nic.id,
        target_label=f"{device.hostname if device else nic.device_id}:{nic.name}",
        group_id=device.group_id if device else None,
        before={"name": nic.name, "device_id": nic.device_id},
        auth=auth,
    )
    return MessageResponse(message=f"网卡 '{nic.name}' 已移入回收站")
