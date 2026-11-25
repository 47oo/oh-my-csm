"""URL entry CRUD, domain aggregation filter, SSL expiry filter, and full panoramic chain lookup."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
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
    UrlEntry,
)
from app.models.audit import AuditLog
from app.schemas.assets import (
    RelatedEntityCard,
    TagBrief,
    UrlCreate,
    UrlDetailOut,
    UrlOut,
    UrlUpdate,
)
from app.schemas.common import MessageResponse, PageResponse
from app.schemas.system import AuditLogOut
from app.services.assets import (
    format_ip_str,
    load_entity_tags,
    load_group_names,
    parse_full_url,
    sync_entity_tags,
)
from app.services.audit import record_audit
from app.services.scope import (
    apply_scope_filter,
    is_card_restricted,
    require_entity_permission,
    resolve_scope,
)

router = APIRouter(prefix="/urls", tags=["URL 入口"])


async def _serialize_urls(db: AsyncSession, rows: list[UrlEntry]) -> list[UrlOut]:
    if not rows:
        return []
    ids = [r.id for r in rows]
    group_names = await load_group_names(db, {r.group_id for r in rows if r.group_id})
    tag_map = await load_entity_tags(db, "url", ids)

    svc_ids = {r.service_id for r in rows if r.service_id}
    svc_map: dict[int, str] = {}
    if svc_ids:
        svc_map = dict(
            (await db.execute(select(Service.id, Service.name).where(Service.id.in_(svc_ids)))).all()
        )

    ip_ids = {r.entry_ip_id for r in rows if r.entry_ip_id}
    ip_map: dict[int, str] = {}
    if ip_ids:
        ip_map = {
            iid: format_ip_str(addr)
            for iid, addr in (
                await db.execute(select(IPAddress.id, IPAddress.ip_address).where(IPAddress.id.in_(ip_ids)))
            ).all()
        }

    return [
        UrlOut(
            id=r.id,
            group_id=r.group_id,
            group_name=group_names.get(r.group_id) if r.group_id else None,
            service_id=r.service_id,
            service_name=svc_map.get(r.service_id) if r.service_id else None,
            entry_ip_id=r.entry_ip_id,
            entry_ip_address=ip_map.get(r.entry_ip_id) if r.entry_ip_id else None,
            name=r.name,
            full_url=r.full_url,
            protocol=r.protocol,
            domain=r.domain,
            port=r.port,
            path=r.path,
            access_type=r.access_type,
            env=r.env,
            status=r.status,
            is_https=r.is_https,
            ssl_expires_at=r.ssl_expires_at,
            health_status=r.health_status,
            last_checked_at=r.last_checked_at,
            description=r.description,
            extra_attrs=r.extra_attrs or {},
            tags=[TagBrief(**t) for t in tag_map.get(r.id, [])],
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


@router.get("", response_model=PageResponse[UrlOut])
async def list_urls(
    q: str | None = Query(default=None),
    domain: str | None = Query(default=None),
    access_type: str | None = Query(default=None),
    service_id: int | None = Query(default=None),
    group_id: int | None = Query(default=None),
    env: str | None = Query(default=None),
    expiring_days: int | None = Query(default=None, ge=1, le=365),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> PageResponse[UrlOut]:
    scope = await resolve_scope(db, auth, "asset:read")
    base = apply_scope_filter(
        select(UrlEntry).where(UrlEntry.deleted_at.is_(None)), UrlEntry, "url", scope
    )
    if domain:
        base = base.where(UrlEntry.domain.ilike(f"%{domain.strip()}%"))
    if access_type:
        base = base.where(UrlEntry.access_type == access_type)
    if service_id is not None:
        base = base.where(UrlEntry.service_id == service_id)
    if group_id is not None:
        base = base.where(UrlEntry.group_id == group_id)
    if env:
        base = base.where(UrlEntry.env == env)
    if expiring_days is not None:
        cutoff = utc_now() + timedelta(days=expiring_days)
        base = base.where(UrlEntry.ssl_expires_at.is_not(None), UrlEntry.ssl_expires_at <= cutoff)
    if q:
        pattern = f"%{q.strip()}%"
        base = base.where(
            or_(
                UrlEntry.name.ilike(pattern),
                UrlEntry.full_url.ilike(pattern),
                UrlEntry.domain.ilike(pattern),
                UrlEntry.description.ilike(pattern),
            )
        )

    total = int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one())
    rows = list(
        (
            await db.execute(
                base.order_by(UrlEntry.id.desc()).offset((page - 1) * page_size).limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return PageResponse(
        items=await _serialize_urls(db, rows),
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=UrlOut, status_code=status.HTTP_201_CREATED)
async def create_url(
    body: UrlCreate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> UrlOut:
    if not auth.has_permission_in_group("asset:write", body.group_id):
        write_scope = await resolve_scope(db, auth, "asset:write")
        allowed_via_svc = False
        if body.service_id is not None:
            svc = (
                await db.execute(
                    select(Service).where(Service.id == body.service_id, Service.deleted_at.is_(None))
                )
            ).scalar_one_or_none()
            allowed_via_svc = svc is not None and write_scope.allows("service", svc.id, svc.group_id)
        allowed_via_ip = False
        if body.entry_ip_id is not None:
            eip = (
                await db.execute(
                    select(IPAddress).where(
                        IPAddress.id == body.entry_ip_id, IPAddress.deleted_at.is_(None)
                    )
                )
            ).scalar_one_or_none()
            allowed_via_ip = eip is not None and write_scope.allows("ip", eip.id, eip.group_id)
        if not (allowed_via_svc or allowed_via_ip):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="仅集群授权用户录入 URL 时必须关联有写权限的服务或入口 IP",
            )

    parsed = parse_full_url(body.full_url)
    entry = UrlEntry(
        group_id=body.group_id,
        service_id=body.service_id,
        entry_ip_id=body.entry_ip_id,
        name=body.name.strip(),
        full_url=parsed["full_url"],
        protocol=parsed["protocol"],
        domain=parsed["domain"],
        port=parsed["port"],
        path=parsed["path"],
        access_type=body.access_type,
        env=body.env,
        status=body.status,
        is_https=parsed["is_https"],
        ssl_expires_at=body.ssl_expires_at,
        description=body.description,
        extra_attrs=body.extra_attrs,
        created_by=auth.user.id,
        updated_by=auth.user.id,
    )
    db.add(entry)
    await db.flush()
    await sync_entity_tags(db, "url", entry.id, body.tag_ids)

    await record_audit(
        db,
        action="create",
        target_type="url",
        target_id=entry.id,
        target_label=f"{entry.name} ({entry.domain})",
        group_id=entry.group_id,
        after={
            "name": entry.name,
            "full_url": entry.full_url,
            "service_id": entry.service_id,
            "entry_ip_id": entry.entry_ip_id,
        },
        auth=auth,
    )
    return (await _serialize_urls(db, [entry]))[0]


@router.get("/{url_id}", response_model=UrlDetailOut)
async def get_url_detail(
    url_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> UrlDetailOut:
    """Panoramic reverse trace: URL -> Entry IP -> Service -> Endpoints -> Clusters -> Devices -> NICs -> IPs."""
    entry = (
        await db.execute(
            select(UrlEntry).where(UrlEntry.id == url_id, UrlEntry.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="URL 记录不存在")
    scope = await require_entity_permission(
        db, auth, "url", entry.id, entry.group_id, "asset:read"
    )

    base_out = (await _serialize_urls(db, [entry]))[0]

    service_obj: Service | None = None
    if entry.service_id:
        service_obj = (
            await db.execute(
                select(Service).where(Service.id == entry.service_id, Service.deleted_at.is_(None))
            )
        ).scalar_one_or_none()

    entry_ip_obj: IPAddress | None = None
    if entry.entry_ip_id:
        entry_ip_obj = (
            await db.execute(
                select(IPAddress).where(
                    IPAddress.id == entry.entry_ip_id, IPAddress.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()

    # Traverse service endpoints -> clusters, devices, ips, nics
    cluster_ids: set[int] = set()
    device_ids: set[int] = set()
    ip_ids: set[int] = set()
    if entry_ip_obj:
        ip_ids.add(entry_ip_obj.id)

    if service_obj:
        eps = list(
            (
                await db.execute(
                    select(ServiceEndpoint).where(ServiceEndpoint.service_id == service_obj.id)
                )
            )
            .scalars()
            .all()
        )
        for ep in eps:
            if ep.cluster_id:
                cluster_ids.add(ep.cluster_id)
            if ep.device_id:
                device_ids.add(ep.device_id)
            if ep.ip_id:
                ip_ids.add(ep.ip_id)

    # Expand cluster -> devices
    if cluster_ids:
        c_devs = (
            await db.execute(
                select(ClusterDevice.device_id).where(ClusterDevice.cluster_id.in_(cluster_ids))
            )
        ).scalars().all()
        device_ids.update(c_devs)

    # Expand ips -> nics -> devices
    ip_objs: list[IPAddress] = []
    if ip_ids:
        ip_objs = list(
            (
                await db.execute(
                    select(IPAddress).where(IPAddress.id.in_(ip_ids), IPAddress.deleted_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
    nic_ids = {ip_obj.nic_id for ip_obj in ip_objs if ip_obj.nic_id}

    nic_objs: list[NIC] = []
    if nic_ids or device_ids:
        conds = []
        if nic_ids:
            conds.append(NIC.id.in_(nic_ids))
        if device_ids:
            conds.append(NIC.device_id.in_(device_ids))
        nic_objs = list(
            (await db.execute(select(NIC).where(or_(*conds), NIC.deleted_at.is_(None))))
            .scalars()
            .all()
        )
        device_ids.update(n.device_id for n in nic_objs)

    # Also grab primary IPs of expanded NICs
    all_nic_ids = [n.id for n in nic_objs]
    if all_nic_ids:
        more_ips = list(
            (
                await db.execute(
                    select(IPAddress).where(
                        IPAddress.nic_id.in_(all_nic_ids), IPAddress.deleted_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        seen_ip_ids = {i.id for i in ip_objs}
        for mip in more_ips:
            if mip.id not in seen_ip_ids:
                ip_objs.append(mip)
                seen_ip_ids.add(mip.id)

    clusters = (
        list(
            (
                await db.execute(
                    select(Cluster).where(Cluster.id.in_(cluster_ids), Cluster.deleted_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
        if cluster_ids
        else []
    )
    devices = (
        list(
            (
                await db.execute(
                    select(Device).where(Device.id.in_(device_ids), Device.deleted_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
        if device_ids
        else []
    )
    dev_by_id = {d.id: d for d in devices}

    all_gids = (
        ({service_obj.group_id} if service_obj and service_obj.group_id else set())
        | ({entry_ip_obj.group_id} if entry_ip_obj and entry_ip_obj.group_id else set())
        | {c.group_id for c in clusters if c.group_id}
        | {d.group_id for d in devices if d.group_id}
        | {ip_obj.group_id for ip_obj in ip_objs if ip_obj.group_id}
    )
    gnames = await load_group_names(db, all_gids)

    service_card = (
        RelatedEntityCard(
            entity_type="service",
            id=service_obj.id,
            title=service_obj.name,
            subtitle=None
            if is_card_restricted(scope, "service", service_obj.id, service_obj.group_id)
            else service_obj.service_type,
            status=service_obj.status,
            group_name=gnames.get(service_obj.group_id) if service_obj.group_id else None,
            extra_badge=service_obj.env,
            is_cross_group_summary=is_card_restricted(scope, "service", service_obj.id, service_obj.group_id),
        )
        if service_obj
        else None
    )
    entry_ip_card = (
        RelatedEntityCard(
            entity_type="ip",
            id=entry_ip_obj.id,
            title=format_ip_str(entry_ip_obj.ip_address),
            subtitle=None
            if is_card_restricted(scope, "ip", entry_ip_obj.id, entry_ip_obj.group_id)
            else entry_ip_obj.usage,
            status=entry_ip_obj.status,
            group_name=gnames.get(entry_ip_obj.group_id) if entry_ip_obj.group_id else None,
            extra_badge=entry_ip_obj.ip_type,
            is_cross_group_summary=is_card_restricted(scope, "ip", entry_ip_obj.id, entry_ip_obj.group_id),
        )
        if entry_ip_obj
        else None
    )

    cluster_cards = [
        RelatedEntityCard(
            entity_type="cluster",
            id=c.id,
            title=c.name,
            subtitle=None if is_card_restricted(scope, "cluster", c.id, c.group_id) else c.cluster_type,
            status=c.status,
            group_name=gnames.get(c.group_id) if c.group_id else None,
            extra_badge=c.env,
            is_cross_group_summary=is_card_restricted(scope, "cluster", c.id, c.group_id),
        )
        for c in clusters
    ]
    device_cards = [
        RelatedEntityCard(
            entity_type="device",
            id=d.id,
            title=d.hostname,
            subtitle=None if is_card_restricted(scope, "device", d.id, d.group_id) else (d.spec_summary or d.device_type),
            status=d.status,
            group_name=gnames.get(d.group_id) if d.group_id else None,
            is_cross_group_summary=is_card_restricted(scope, "device", d.id, d.group_id),
        )
        for d in devices
    ]
    nic_cards = [
        RelatedEntityCard(
            entity_type="nic",
            id=n.id,
            title=f"{dev_by_id[n.device_id].hostname}:{n.name}" if n.device_id in dev_by_id else n.name,
            subtitle=None
            if is_card_restricted(
                scope, "nic", n.id, dev_by_id[n.device_id].group_id if n.device_id in dev_by_id else None
            )
            else (str(n.mac_address) if n.mac_address else n.nic_type),
            status=n.status,
            extra_badge=f"VLAN {n.vlan_id}" if n.vlan_id else None,
            is_cross_group_summary=is_card_restricted(
                scope, "nic", n.id, dev_by_id[n.device_id].group_id if n.device_id in dev_by_id else None
            ),
        )
        for n in nic_objs
    ]
    ip_cards = [
        RelatedEntityCard(
            entity_type="ip",
            id=ip_obj.id,
            title=format_ip_str(ip_obj.ip_address),
            subtitle=None if is_card_restricted(scope, "ip", ip_obj.id, ip_obj.group_id) else ip_obj.usage,
            status=ip_obj.status,
            group_name=gnames.get(ip_obj.group_id) if ip_obj.group_id else None,
            extra_badge=ip_obj.ip_type,
            is_cross_group_summary=is_card_restricted(scope, "ip", ip_obj.id, ip_obj.group_id),
        )
        for ip_obj in ip_objs
    ]

    audits = list(
        (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.target_type == "url", AuditLog.target_id == entry.id)
                .order_by(AuditLog.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )

    return UrlDetailOut(
        **base_out.model_dump(),
        service=service_card,
        entry_ip=entry_ip_card,
        clusters=cluster_cards,
        devices=device_cards,
        nics=nic_cards,
        ips=ip_cards,
        recent_audits=[AuditLogOut.model_validate(a) for a in audits],
    )


@router.put("/{url_id}", response_model=UrlOut)
async def update_url(
    url_id: int,
    body: UrlUpdate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> UrlOut:
    entry = (
        await db.execute(
            select(UrlEntry).where(UrlEntry.id == url_id, UrlEntry.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="URL 记录不存在")
    await require_entity_permission(db, auth, "url", entry.id, entry.group_id, "asset:write")
    if body.group_id is not None and body.group_id != entry.group_id:
        auth.require_group_permission("asset:write", body.group_id)

    before = {
        "name": entry.name,
        "full_url": entry.full_url,
        "service_id": entry.service_id,
        "entry_ip_id": entry.entry_ip_id,
        "access_type": entry.access_type,
        "status": entry.status,
        "ssl_expires_at": entry.ssl_expires_at,
    }

    if body.full_url is not None:
        parsed = parse_full_url(body.full_url)
        entry.full_url = parsed["full_url"]
        entry.protocol = parsed["protocol"]
        entry.domain = parsed["domain"]
        entry.port = parsed["port"]
        entry.path = parsed["path"]
        entry.is_https = parsed["is_https"]

    if body.clear_service:
        entry.service_id = None
    elif body.service_id is not None:
        entry.service_id = body.service_id

    if body.clear_entry_ip:
        entry.entry_ip_id = None
    elif body.entry_ip_id is not None:
        entry.entry_ip_id = body.entry_ip_id

    for field in ("group_id", "access_type", "env", "status", "ssl_expires_at", "description", "extra_attrs"):
        val = getattr(body, field)
        if val is not None:
            setattr(entry, field, val)
    if body.name is not None:
        entry.name = body.name.strip()
    entry.updated_by = auth.user.id
    await db.flush()
    await sync_entity_tags(db, "url", entry.id, body.tag_ids)

    await record_audit(
        db,
        action="update",
        target_type="url",
        target_id=entry.id,
        target_label=f"{entry.name} ({entry.domain})",
        group_id=entry.group_id,
        before=before,
        after={
            "name": entry.name,
            "full_url": entry.full_url,
            "service_id": entry.service_id,
            "entry_ip_id": entry.entry_ip_id,
            "access_type": entry.access_type,
            "status": entry.status,
            "ssl_expires_at": entry.ssl_expires_at,
        },
        auth=auth,
    )
    return (await _serialize_urls(db, [entry]))[0]


@router.delete("/{url_id}", response_model=MessageResponse)
async def delete_url(
    url_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    entry = (
        await db.execute(
            select(UrlEntry).where(UrlEntry.id == url_id, UrlEntry.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="URL 记录不存在")
    await require_entity_permission(db, auth, "url", entry.id, entry.group_id, "asset:delete")

    entry.deleted_at = utc_now()
    entry.updated_by = auth.user.id
    await db.flush()
    await record_audit(
        db,
        action="delete",
        target_type="url",
        target_id=entry.id,
        target_label=f"{entry.name} ({entry.domain})",
        group_id=entry.group_id,
        before={"name": entry.name, "full_url": entry.full_url},
        auth=auth,
    )
    return MessageResponse(message=f"URL '{entry.name}' 已移入回收站")
