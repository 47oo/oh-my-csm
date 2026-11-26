"""Service & ServiceEndpoint CRUD, panoramic hosting details, and delete protection."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import AuthContext, get_current_auth
from app.core.security import utc_now
from app.models.assets import (
    Cluster,
    Device,
    IPAddress,
    Service,
    ServiceEndpoint,
    UrlEntry,
)
from app.models.audit import AuditLog
from app.schemas.assets import (
    RelatedEntityCard,
    ServiceCreate,
    ServiceDetailOut,
    ServiceEndpointCreate,
    ServiceEndpointOut,
    ServiceEndpointUpdate,
    ServiceOut,
    ServiceUpdate,
    TagBrief,
)
from app.schemas.common import MessageResponse, PageResponse
from app.schemas.system import AuditLogOut
from app.services.assets import (
    format_ip_str,
    load_entity_tags,
    load_group_names,
    sync_entity_tags,
)
from app.services.audit import record_audit
from app.services.scope import (
    ResolvedScope,
    apply_scope_filter,
    is_card_restricted,
    require_entity_permission,
    resolve_scope,
)

router = APIRouter(prefix="/services", tags=["服务管理"])


async def _serialize_services(db: AsyncSession, rows: list[Service]) -> list[ServiceOut]:
    if not rows:
        return []
    ids = [r.id for r in rows]
    group_names = await load_group_names(db, {r.group_id for r in rows if r.group_id})
    tag_map = await load_entity_tags(db, "service", ids)

    ep_counts = dict(
        (
            await db.execute(
                select(ServiceEndpoint.service_id, func.count())
                .where(ServiceEndpoint.service_id.in_(ids))
                .group_by(ServiceEndpoint.service_id)
            )
        ).all()
    )
    url_counts = dict(
        (
            await db.execute(
                select(UrlEntry.service_id, func.count())
                .where(UrlEntry.service_id.in_(ids), UrlEntry.deleted_at.is_(None))
                .group_by(UrlEntry.service_id)
            )
        ).all()
    )

    return [
        ServiceOut(
            id=r.id,
            group_id=r.group_id,
            group_name=group_names.get(r.group_id) if r.group_id else None,
            name=r.name,
            code=r.code,
            service_type=r.service_type,
            env=r.env,
            status=r.status,
            version=r.version,
            owner=r.owner,
            repo_or_doc_url=r.repo_or_doc_url,
            description=r.description,
            extra_attrs=r.extra_attrs or {},
            tags=[TagBrief(**t) for t in tag_map.get(r.id, [])],
            endpoint_count=int(ep_counts.get(r.id, 0)),
            url_count=int(url_counts.get(r.id, 0)),
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


async def _resolve_endpoints(
    db: AsyncSession,
    scope: ResolvedScope,
    endpoints: list[ServiceEndpoint],
) -> tuple[
    list[ServiceEndpointOut],
    list[RelatedEntityCard],
    list[RelatedEntityCard],
    list[RelatedEntityCard],
]:
    cids = {e.cluster_id for e in endpoints if e.cluster_id}
    dids = {e.device_id for e in endpoints if e.device_id}
    iids = {e.ip_id for e in endpoints if e.ip_id}

    clusters = (
        {
            c.id: c
            for c in (
                await db.execute(
                    select(Cluster).where(Cluster.id.in_(cids), Cluster.deleted_at.is_(None))
                )
            )
            .scalars()
            .all()
        }
        if cids
        else {}
    )
    devices = (
        {
            d.id: d
            for d in (
                await db.execute(
                    select(Device).where(Device.id.in_(dids), Device.deleted_at.is_(None))
                )
            )
            .scalars()
            .all()
        }
        if dids
        else {}
    )
    ips = (
        {
            ip_obj.id: ip_obj
            for ip_obj in (
                await db.execute(
                    select(IPAddress).where(IPAddress.id.in_(iids), IPAddress.deleted_at.is_(None))
                )
            )
            .scalars()
            .all()
        }
        if iids
        else {}
    )

    gids = (
        {c.group_id for c in clusters.values() if c.group_id}
        | {d.group_id for d in devices.values() if d.group_id}
        | {ip_obj.group_id for ip_obj in ips.values() if ip_obj.group_id}
    )
    gnames = await load_group_names(db, gids)

    ep_outs: list[ServiceEndpointOut] = []
    for e in endpoints:
        c = clusters.get(e.cluster_id) if e.cluster_id else None
        d = devices.get(e.device_id) if e.device_id else None
        ip_obj = ips.get(e.ip_id) if e.ip_id else None
        cross = (
            (c is not None and is_card_restricted(scope, "cluster", c.id, c.group_id))
            or (d is not None and is_card_restricted(scope, "device", d.id, d.group_id))
            or (ip_obj is not None and is_card_restricted(scope, "ip", ip_obj.id, ip_obj.group_id))
        )
        ep_outs.append(
            ServiceEndpointOut(
                id=e.id,
                service_id=e.service_id,
                cluster_id=e.cluster_id,
                cluster_name=c.name if c else None,
                device_id=e.device_id,
                device_hostname=d.hostname if d else None,
                ip_id=e.ip_id,
                ip_address=format_ip_str(ip_obj.ip_address) if ip_obj else None,
                listen_ip_text=e.listen_ip_text,
                port=e.port,
                protocol=e.protocol,
                endpoint_role=e.endpoint_role,
                description=None if cross else e.description,
                is_cross_group_summary=cross,
            )
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
        for c in clusters.values()
    ]
    device_cards = [
        RelatedEntityCard(
            entity_type="device",
            id=d.id,
            title=d.hostname,
            subtitle=None if is_card_restricted(scope, "device", d.id, d.group_id) else d.device_type,
            status=d.status,
            group_name=gnames.get(d.group_id) if d.group_id else None,
            is_cross_group_summary=is_card_restricted(scope, "device", d.id, d.group_id),
        )
        for d in devices.values()
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
        for ip_obj in ips.values()
    ]
    return ep_outs, cluster_cards, device_cards, ip_cards


@router.get("", response_model=PageResponse[ServiceOut])
async def list_services(
    q: str | None = Query(default=None),
    group_id: int | None = Query(default=None),
    service_type: str | None = Query(default=None),
    env: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> PageResponse[ServiceOut]:
    scope = await resolve_scope(db, auth, "asset:read")
    base = apply_scope_filter(
        select(Service).where(Service.deleted_at.is_(None)), Service, "service", scope
    )
    if group_id is not None:
        base = base.where(Service.group_id == group_id)
    if service_type:
        base = base.where(Service.service_type == service_type)
    if env:
        base = base.where(Service.env == env)
    if status_filter:
        base = base.where(Service.status == status_filter)
    if q:
        pattern = f"%{q.strip()}%"
        base = base.where(
            or_(
                Service.name.ilike(pattern),
                Service.code.ilike(pattern),
                Service.owner.ilike(pattern),
                Service.description.ilike(pattern),
            )
        )

    total = int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one())
    rows = list(
        (
            await db.execute(
                base.order_by(Service.id.desc()).offset((page - 1) * page_size).limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return PageResponse(
        items=await _serialize_services(db, rows),
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=ServiceOut, status_code=status.HTTP_201_CREATED)
async def create_service(
    body: ServiceCreate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> ServiceOut:
    write_scope = await resolve_scope(db, auth, "asset:write")
    unique_cids = sorted(set(body.cluster_ids or []))
    if unique_cids:
        cl_rows = list(
            (
                await db.execute(
                    select(Cluster).where(Cluster.id.in_(unique_cids), Cluster.deleted_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
        if len(cl_rows) != len(unique_cids):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="包含不存在的集群")
        for cl in cl_rows:
            await require_entity_permission(
                db, auth, "cluster", cl.id, cl.group_id, "asset:write", scope=write_scope
            )
    if body.group_id is not None:
        auth.require_group_permission("asset:write", body.group_id)
    if not auth.has_permission_in_group("asset:write", body.group_id) and not unique_cids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅集群授权用户新建服务时必须关联至少一个有写权限的集群",
        )

    name = body.name.strip()
    exists = (
        await db.execute(
            select(Service.id).where(
                Service.name == name,
                Service.env == body.env,
                Service.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"同环境下服务 '{name}' 已存在",
        )

    svc = Service(
        group_id=body.group_id,
        name=name,
        code=body.code.strip() if body.code else None,
        service_type=body.service_type,
        env=body.env,
        status=body.status,
        version=body.version,
        owner=body.owner,
        repo_or_doc_url=body.repo_or_doc_url,
        description=body.description,
        extra_attrs=body.extra_attrs,
        created_by=auth.user.id,
        updated_by=auth.user.id,
    )
    db.add(svc)
    await db.flush()
    if body.cluster_ids:
        for cid in sorted(set(body.cluster_ids)):
            db.add(ServiceEndpoint(service_id=svc.id, cluster_id=cid, protocol="tcp"))
        await db.flush()
    await sync_entity_tags(db, "service", svc.id, body.tag_ids)

    await record_audit(
        db,
        action="create",
        target_type="service",
        target_id=svc.id,
        target_label=svc.name,
        group_id=svc.group_id,
        after={"name": svc.name, "service_type": svc.service_type, "env": svc.env},
        auth=auth,
    )
    return (await _serialize_services(db, [svc]))[0]


@router.get("/{service_id}", response_model=ServiceDetailOut)
async def get_service_detail(
    service_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> ServiceDetailOut:
    svc = (
        await db.execute(
            select(Service).where(Service.id == service_id, Service.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if svc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="服务不存在")
    scope = await require_entity_permission(
        db, auth, "service", svc.id, svc.group_id, "asset:read"
    )

    base_out = (await _serialize_services(db, [svc]))[0]

    endpoints = list(
        (
            await db.execute(
                select(ServiceEndpoint)
                .where(ServiceEndpoint.service_id == svc.id)
                .order_by(ServiceEndpoint.id.asc())
            )
        )
        .scalars()
        .all()
    )
    ep_outs, cluster_cards, device_cards, ip_cards = await _resolve_endpoints(db, scope, endpoints)

    urls = list(
        (
            await db.execute(
                select(UrlEntry).where(UrlEntry.service_id == svc.id, UrlEntry.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    ugnames = await load_group_names(db, {u.group_id for u in urls if u.group_id})
    url_cards = [
        RelatedEntityCard(
            entity_type="url",
            id=u.id,
            title=u.name,
            subtitle=u.full_url,
            status=u.status,
            group_name=ugnames.get(u.group_id) if u.group_id else None,
            extra_badge=u.access_type,
            is_cross_group_summary=is_card_restricted(scope, "url", u.id, u.group_id),
        )
        for u in urls
    ]

    audits = list(
        (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.target_type == "service", AuditLog.target_id == svc.id)
                .order_by(AuditLog.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )

    return ServiceDetailOut(
        **base_out.model_dump(),
        endpoints=ep_outs,
        urls=url_cards,
        clusters=cluster_cards,
        devices=device_cards,
        ips=ip_cards,
        recent_audits=[AuditLogOut.model_validate(a) for a in audits],
    )


@router.put("/{service_id}", response_model=ServiceOut)
async def update_service(
    service_id: int,
    body: ServiceUpdate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> ServiceOut:
    svc = (
        await db.execute(
            select(Service).where(Service.id == service_id, Service.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if svc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="服务不存在")
    await require_entity_permission(db, auth, "service", svc.id, svc.group_id, "asset:write")
    if body.group_id is not None and body.group_id != svc.group_id:
        auth.require_group_permission("asset:write", svc.group_id)
        auth.require_group_permission("asset:write", body.group_id)

    new_name = body.name.strip() if body.name is not None else svc.name
    new_env = body.env if body.env is not None else svc.env
    if new_name != svc.name or new_env != svc.env:
        env_cond = Service.env.is_(None) if new_env is None else (Service.env == new_env)
        dup = (
            await db.execute(
                select(Service.id).where(
                    Service.id != svc.id,
                    Service.name == new_name,
                    env_cond,
                    Service.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if dup is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"同环境下服务 '{new_name}' 已存在",
            )

    before = {
        "name": svc.name,
        "group_id": svc.group_id,
        "service_type": svc.service_type,
        "env": svc.env,
        "status": svc.status,
        "version": svc.version,
        "owner": svc.owner,
    }
    for field in (
        "group_id",
        "code",
        "service_type",
        "env",
        "status",
        "version",
        "owner",
        "repo_or_doc_url",
        "description",
        "extra_attrs",
    ):
        val = getattr(body, field)
        if val is not None:
            setattr(svc, field, val)
    if body.name is not None:
        svc.name = body.name.strip()
    svc.updated_by = auth.user.id
    await db.flush()
    await sync_entity_tags(db, "service", svc.id, body.tag_ids)

    await record_audit(
        db,
        action="update",
        target_type="service",
        target_id=svc.id,
        target_label=svc.name,
        group_id=svc.group_id,
        before=before,
        after={
            "name": svc.name,
            "group_id": svc.group_id,
            "service_type": svc.service_type,
            "env": svc.env,
            "status": svc.status,
            "version": svc.version,
            "owner": svc.owner,
        },
        auth=auth,
    )
    return (await _serialize_services(db, [svc]))[0]


@router.delete("/{service_id}", response_model=MessageResponse)
async def delete_service(
    service_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    svc = (
        await db.execute(
            select(Service).where(Service.id == service_id, Service.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if svc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="服务不存在")
    await require_entity_permission(db, auth, "service", svc.id, svc.group_id, "asset:delete")

    blockers: list[str] = []
    url_cnt = int(
        (
            await db.execute(
                select(func.count())
                .select_from(UrlEntry)
                .where(UrlEntry.service_id == svc.id, UrlEntry.deleted_at.is_(None))
            )
        ).scalar_one()
    )
    if url_cnt:
        blockers.append(f"{url_cnt} 条关联 URL")

    if blockers:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"该服务仍有关联项（{'、'.join(blockers)}），请先解绑后再删除",
        )

    svc.deleted_at = utc_now()
    svc.updated_by = auth.user.id
    await db.flush()
    await record_audit(
        db,
        action="delete",
        target_type="service",
        target_id=svc.id,
        target_label=svc.name,
        group_id=svc.group_id,
        before={"name": svc.name, "env": svc.env},
        auth=auth,
    )
    return MessageResponse(message=f"服务 '{svc.name}' 已移入回收站")


async def _validate_endpoint_targets(
    db: AsyncSession,
    auth: AuthContext,
    *,
    cluster_id: int | None,
    device_id: int | None,
    ip_id: int | None,
) -> None:
    if cluster_id is not None:
        cl = (
            await db.execute(
                select(Cluster).where(Cluster.id == cluster_id, Cluster.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if cl is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="关联集群不存在")
        await require_entity_permission(db, auth, "cluster", cl.id, cl.group_id, "asset:write")
    if device_id is not None:
        dev = (
            await db.execute(
                select(Device).where(Device.id == device_id, Device.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if dev is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="关联设备不存在")
        await require_entity_permission(db, auth, "device", dev.id, dev.group_id, "asset:read")
    if ip_id is not None:
        ip_obj = (
            await db.execute(
                select(IPAddress).where(IPAddress.id == ip_id, IPAddress.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if ip_obj is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="监听 IP 不存在")
        await require_entity_permission(db, auth, "ip", ip_obj.id, ip_obj.group_id, "asset:read")


@router.post("/{service_id}/endpoints", response_model=ServiceEndpointOut, status_code=status.HTTP_201_CREATED)
async def add_service_endpoint(
    service_id: int,
    body: ServiceEndpointCreate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> ServiceEndpointOut:
    svc = (
        await db.execute(
            select(Service).where(Service.id == service_id, Service.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if svc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="服务不存在")
    await require_entity_permission(db, auth, "service", svc.id, svc.group_id, "asset:write")
    await _validate_endpoint_targets(
        db, auth, cluster_id=body.cluster_id, device_id=body.device_id, ip_id=body.ip_id
    )

    ep = ServiceEndpoint(
        service_id=svc.id,
        cluster_id=body.cluster_id,
        device_id=body.device_id,
        ip_id=body.ip_id,
        listen_ip_text=body.listen_ip_text,
        port=body.port,
        protocol=body.protocol,
        endpoint_role=body.endpoint_role,
        description=body.description,
    )
    db.add(ep)
    await db.flush()

    await record_audit(
        db,
        action="add_endpoint",
        target_type="service",
        target_id=svc.id,
        target_label=svc.name,
        group_id=svc.group_id,
        after={
            "endpoint_id": ep.id,
            "cluster_id": ep.cluster_id,
            "device_id": ep.device_id,
            "ip_id": ep.ip_id,
            "port": ep.port,
            "protocol": ep.protocol,
        },
        auth=auth,
    )
    read_scope = await resolve_scope(db, auth, "asset:read")
    ep_outs, _, _, _ = await _resolve_endpoints(db, read_scope, [ep])
    return ep_outs[0]


@router.put("/{service_id}/endpoints/{endpoint_id}", response_model=ServiceEndpointOut)
async def update_service_endpoint(
    service_id: int,
    endpoint_id: int,
    body: ServiceEndpointUpdate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> ServiceEndpointOut:
    svc = (
        await db.execute(
            select(Service).where(Service.id == service_id, Service.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if svc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="服务不存在")
    await require_entity_permission(db, auth, "service", svc.id, svc.group_id, "asset:write")

    ep = (
        await db.execute(
            select(ServiceEndpoint).where(
                ServiceEndpoint.id == endpoint_id, ServiceEndpoint.service_id == svc.id
            )
        )
    ).scalar_one_or_none()
    if ep is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="服务端点不存在")

    if body.cluster_id is not None and ep.cluster_id is not None and body.cluster_id != ep.cluster_id:
        old_cl = await db.get(Cluster, ep.cluster_id)
        await require_entity_permission(
            db, auth, "cluster", ep.cluster_id, old_cl.group_id if old_cl else None, "asset:write"
        )

    await _validate_endpoint_targets(
        db,
        auth,
        cluster_id=body.cluster_id if body.cluster_id != ep.cluster_id else None,
        device_id=body.device_id if body.device_id != ep.device_id else None,
        ip_id=body.ip_id if body.ip_id != ep.ip_id else None,
    )

    for field in (
        "cluster_id",
        "device_id",
        "ip_id",
        "listen_ip_text",
        "port",
        "protocol",
        "endpoint_role",
        "description",
    ):
        val = getattr(body, field)
        if val is not None:
            setattr(ep, field, val)
    await db.flush()

    read_scope = await resolve_scope(db, auth, "asset:read")
    ep_outs, _, _, _ = await _resolve_endpoints(db, read_scope, [ep])
    return ep_outs[0]


@router.delete("/{service_id}/endpoints/{endpoint_id}", response_model=MessageResponse)
async def delete_service_endpoint(
    service_id: int,
    endpoint_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    svc = (
        await db.execute(
            select(Service).where(Service.id == service_id, Service.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if svc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="服务不存在")
    await require_entity_permission(db, auth, "service", svc.id, svc.group_id, "asset:write")

    ep = (
        await db.execute(
            select(ServiceEndpoint).where(
                ServiceEndpoint.id == endpoint_id, ServiceEndpoint.service_id == svc.id
            )
        )
    ).scalar_one_or_none()
    if ep is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="服务端点不存在")
    if ep.cluster_id is not None:
        old_cl = await db.get(Cluster, ep.cluster_id)
        await require_entity_permission(
            db, auth, "cluster", ep.cluster_id, old_cl.group_id if old_cl else None, "asset:write"
        )

    await db.delete(ep)
    await db.flush()
    await record_audit(
        db,
        action="delete_endpoint",
        target_type="service",
        target_id=svc.id,
        target_label=svc.name,
        group_id=svc.group_id,
        before={"endpoint_id": endpoint_id, "port": ep.port, "protocol": ep.protocol},
        auth=auth,
    )
    return MessageResponse(message="服务端点已删除")
