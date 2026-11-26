"""Cluster CRUD, member device binding, panoramic detail, and delete protection."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.permissions import AuthContext, get_current_auth
from app.core.security import utc_now
from app.models.assets import (
    Cluster,
    ClusterDevice,
    Device,
    IPAddress,
    IPVIPBinding,
    Service,
    ServiceEndpoint,
)
from app.models.audit import AuditLog
from app.models.auth import ClusterACL, Role, User
from app.schemas.assets import (
    ClusterCreate,
    ClusterDetailOut,
    ClusterDeviceLinkRequest,
    ClusterMemberAssignRequest,
    ClusterMemberOut,
    ClusterOut,
    ClusterUpdate,
    RelatedEntityCard,
    TagBrief,
)
from app.schemas.common import MessageResponse, PageResponse
from app.schemas.system import AuditLogOut
from app.services.assets import (
    format_ip_str,
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

router = APIRouter(prefix="/clusters", tags=["集群管理"])


async def _serialize_clusters(db: AsyncSession, rows: list[Cluster]) -> list[ClusterOut]:
    if not rows:
        return []
    ids = [r.id for r in rows]
    group_names = await load_group_names(db, {r.group_id for r in rows if r.group_id})
    zone_names = await load_zone_names(db, {r.network_zone_id for r in rows if r.network_zone_id})
    tag_map = await load_entity_tags(db, "cluster", ids)

    dev_counts = dict(
        (
            await db.execute(
                select(ClusterDevice.cluster_id, func.count())
                .join(Device, Device.id == ClusterDevice.device_id)
                .where(ClusterDevice.cluster_id.in_(ids), Device.deleted_at.is_(None))
                .group_by(ClusterDevice.cluster_id)
            )
        ).all()
    )
    svc_counts = dict(
        (
            await db.execute(
                select(ServiceEndpoint.cluster_id, func.count(func.distinct(ServiceEndpoint.service_id)))
                .join(Service, Service.id == ServiceEndpoint.service_id)
                .where(ServiceEndpoint.cluster_id.in_(ids), Service.deleted_at.is_(None))
                .group_by(ServiceEndpoint.cluster_id)
            )
        ).all()
    )

    return [
        ClusterOut(
            id=r.id,
            group_id=r.group_id,
            group_name=group_names.get(r.group_id) if r.group_id else None,
            network_zone_id=r.network_zone_id,
            network_zone_name=zone_names.get(r.network_zone_id) if r.network_zone_id else None,
            name=r.name,
            code=r.code,
            cluster_type=r.cluster_type,
            env=r.env,
            status=r.status,
            owner=r.owner,
            description=r.description,
            extra_attrs=r.extra_attrs or {},
            tags=[TagBrief(**t) for t in tag_map.get(r.id, [])],
            device_count=int(dev_counts.get(r.id, 0)),
            service_count=int(svc_counts.get(r.id, 0)),
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


@router.get("", response_model=PageResponse[ClusterOut])
async def list_clusters(
    q: str | None = Query(default=None),
    group_id: int | None = Query(default=None),
    env: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> PageResponse[ClusterOut]:
    scope = await resolve_scope(db, auth, "asset:read")
    base = apply_scope_filter(
        select(Cluster).where(Cluster.deleted_at.is_(None)), Cluster, "cluster", scope
    )
    if group_id is not None:
        base = base.where(Cluster.group_id == group_id)
    if env:
        base = base.where(Cluster.env == env)
    if status_filter:
        base = base.where(Cluster.status == status_filter)
    if q:
        pattern = f"%{q.strip()}%"
        base = base.where(
            or_(
                Cluster.name.ilike(pattern),
                Cluster.code.ilike(pattern),
                Cluster.owner.ilike(pattern),
                Cluster.description.ilike(pattern),
            )
        )

    total = int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one())
    rows = list(
        (
            await db.execute(
                base.order_by(Cluster.id.desc()).offset((page - 1) * page_size).limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return PageResponse(
        items=await _serialize_clusters(db, rows),
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=ClusterOut, status_code=status.HTTP_201_CREATED)
async def create_cluster(
    body: ClusterCreate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> ClusterOut:
    auth.require_group_permission("asset:write", body.group_id)

    name = body.name.strip()
    exists = (
        await db.execute(
            select(Cluster.id).where(
                Cluster.name == name,
                Cluster.env == body.env,
                Cluster.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"同环境下已存在集群 '{name}'",
        )

    cluster = Cluster(
        group_id=body.group_id,
        network_zone_id=body.network_zone_id,
        name=name,
        code=body.code.strip() if body.code else None,
        cluster_type=body.cluster_type,
        env=body.env,
        status=body.status,
        owner=body.owner,
        description=body.description,
        extra_attrs=body.extra_attrs,
        created_by=auth.user.id,
        updated_by=auth.user.id,
    )
    db.add(cluster)
    await db.flush()
    await sync_entity_tags(db, "cluster", cluster.id, body.tag_ids)

    await record_audit(
        db,
        action="create",
        target_type="cluster",
        target_id=cluster.id,
        target_label=cluster.name,
        group_id=cluster.group_id,
        after={"name": cluster.name, "env": cluster.env, "cluster_type": cluster.cluster_type},
        auth=auth,
    )
    return (await _serialize_clusters(db, [cluster]))[0]


@router.get("/{cluster_id}", response_model=ClusterDetailOut)
async def get_cluster_detail(
    cluster_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> ClusterDetailOut:
    cluster = (
        await db.execute(
            select(Cluster).where(Cluster.id == cluster_id, Cluster.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="集群不存在")
    scope = await require_entity_permission(
        db, auth, "cluster", cluster.id, cluster.group_id, "asset:read"
    )

    base_out = (await _serialize_clusters(db, [cluster]))[0]

    # Devices
    dev_rows = (
        await db.execute(
            select(Device, ClusterDevice.node_role)
            .join(ClusterDevice, ClusterDevice.device_id == Device.id)
            .where(ClusterDevice.cluster_id == cluster.id, Device.deleted_at.is_(None))
            .order_by(Device.hostname.asc())
        )
    ).all()

    # VIPs
    vip_rows = list(
        (
            await db.execute(
                select(IPAddress)
                .join(IPVIPBinding, IPVIPBinding.ip_id == IPAddress.id)
                .where(
                    IPVIPBinding.target_type == "cluster",
                    IPVIPBinding.target_id == cluster.id,
                    IPAddress.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    # Services via ServiceEndpoint
    svc_rows = list(
        (
            await db.execute(
                select(Service)
                .join(ServiceEndpoint, ServiceEndpoint.service_id == Service.id)
                .where(ServiceEndpoint.cluster_id == cluster.id, Service.deleted_at.is_(None))
                .distinct()
            )
        )
        .scalars()
        .all()
    )

    all_gids = (
        {d.group_id for d, _ in dev_rows if d.group_id}
        | {v.group_id for v in vip_rows if v.group_id}
        | {s.group_id for s in svc_rows if s.group_id}
    )
    gnames = await load_group_names(db, all_gids)

    device_cards = [
        RelatedEntityCard(
            entity_type="device",
            id=d.id,
            title=d.hostname,
            subtitle=None if is_card_restricted(scope, "device", d.id, d.group_id) else (d.spec_summary or d.device_type),
            status=d.status,
            group_name=gnames.get(d.group_id) if d.group_id else None,
            role=node_role,
            is_cross_group_summary=is_card_restricted(scope, "device", d.id, d.group_id),
        )
        for d, node_role in dev_rows
    ]
    vip_cards = [
        RelatedEntityCard(
            entity_type="ip",
            id=v.id,
            title=format_ip_str(v.ip_address),
            subtitle=None if is_card_restricted(scope, "ip", v.id, v.group_id) else v.usage,
            status=v.status,
            group_name=gnames.get(v.group_id) if v.group_id else None,
            extra_badge="VIP",
            is_cross_group_summary=is_card_restricted(scope, "ip", v.id, v.group_id),
        )
        for v in vip_rows
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
                .where(AuditLog.target_type == "cluster", AuditLog.target_id == cluster.id)
                .order_by(AuditLog.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )

    return ClusterDetailOut(
        **base_out.model_dump(),
        devices=device_cards,
        vips=vip_cards,
        services=service_cards,
        recent_audits=[AuditLogOut.model_validate(a) for a in audits],
    )


@router.put("/{cluster_id}", response_model=ClusterOut)
async def update_cluster(
    cluster_id: int,
    body: ClusterUpdate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> ClusterOut:
    cluster = (
        await db.execute(
            select(Cluster).where(Cluster.id == cluster_id, Cluster.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="集群不存在")
    await require_entity_permission(db, auth, "cluster", cluster.id, cluster.group_id, "asset:write")
    if body.group_id is not None and body.group_id != cluster.group_id:
        auth.require_group_permission("asset:write", cluster.group_id)
        auth.require_group_permission("asset:write", body.group_id)

    new_name = body.name.strip() if body.name is not None else cluster.name
    new_env = body.env if body.env is not None else cluster.env
    if new_name != cluster.name or new_env != cluster.env:
        env_cond = Cluster.env.is_(None) if new_env is None else (Cluster.env == new_env)
        dup = (
            await db.execute(
                select(Cluster.id).where(
                    Cluster.id != cluster.id,
                    Cluster.name == new_name,
                    env_cond,
                    Cluster.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if dup is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"同环境下集群 '{new_name}' 已存在",
            )

    before = {
        "name": cluster.name,
        "group_id": cluster.group_id,
        "cluster_type": cluster.cluster_type,
        "env": cluster.env,
        "status": cluster.status,
        "owner": cluster.owner,
        "description": cluster.description,
    }
    for field in (
        "group_id",
        "network_zone_id",
        "code",
        "cluster_type",
        "env",
        "status",
        "owner",
        "description",
        "extra_attrs",
    ):
        val = getattr(body, field)
        if val is not None:
            setattr(cluster, field, val)
    if body.name is not None:
        cluster.name = body.name.strip()
    cluster.updated_by = auth.user.id
    await db.flush()
    await sync_entity_tags(db, "cluster", cluster.id, body.tag_ids)

    await record_audit(
        db,
        action="update",
        target_type="cluster",
        target_id=cluster.id,
        target_label=cluster.name,
        group_id=cluster.group_id,
        before=before,
        after={
            "name": cluster.name,
            "group_id": cluster.group_id,
            "cluster_type": cluster.cluster_type,
            "env": cluster.env,
            "status": cluster.status,
            "owner": cluster.owner,
            "description": cluster.description,
        },
        auth=auth,
    )
    return (await _serialize_clusters(db, [cluster]))[0]


@router.delete("/{cluster_id}", response_model=MessageResponse)
async def delete_cluster(
    cluster_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    cluster = (
        await db.execute(
            select(Cluster).where(Cluster.id == cluster_id, Cluster.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="集群不存在")
    await require_entity_permission(db, auth, "cluster", cluster.id, cluster.group_id, "asset:delete")

    blockers: list[str] = []
    dev_cnt = int(
        (
            await db.execute(
                select(func.count())
                .select_from(ClusterDevice)
                .join(Device, Device.id == ClusterDevice.device_id)
                .where(ClusterDevice.cluster_id == cluster.id, Device.deleted_at.is_(None))
            )
        ).scalar_one()
    )
    if dev_cnt:
        blockers.append(f"{dev_cnt} 台成员设备")

    ep_cnt = int(
        (
            await db.execute(
                select(func.count())
                .select_from(ServiceEndpoint)
                .join(Service, Service.id == ServiceEndpoint.service_id)
                .where(ServiceEndpoint.cluster_id == cluster.id, Service.deleted_at.is_(None))
            )
        ).scalar_one()
    )
    if ep_cnt:
        blockers.append(f"{ep_cnt} 条服务端点")

    vip_cnt = int(
        (
            await db.execute(
                select(func.count())
                .select_from(IPVIPBinding)
                .join(IPAddress, IPAddress.id == IPVIPBinding.ip_id)
                .where(
                    IPVIPBinding.target_type == "cluster",
                    IPVIPBinding.target_id == cluster.id,
                    IPAddress.deleted_at.is_(None),
                )
            )
        ).scalar_one()
    )
    if vip_cnt:
        blockers.append(f"{vip_cnt} 条 VIP 绑定")

    if blockers:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"该集群仍有关联对象（{'、'.join(blockers)}），请先解除关联后再删除",
        )

    cluster.deleted_at = utc_now()
    cluster.updated_by = auth.user.id
    await db.flush()
    await record_audit(
        db,
        action="delete",
        target_type="cluster",
        target_id=cluster.id,
        target_label=cluster.name,
        group_id=cluster.group_id,
        before={"name": cluster.name, "env": cluster.env},
        auth=auth,
    )
    return MessageResponse(message=f"集群 '{cluster.name}' 已移入回收站")


@router.post("/{cluster_id}/devices", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def attach_device_to_cluster(
    cluster_id: int,
    body: ClusterDeviceLinkRequest,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    cluster = (
        await db.execute(
            select(Cluster).where(Cluster.id == cluster_id, Cluster.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="集群不存在")
    await require_entity_permission(db, auth, "cluster", cluster.id, cluster.group_id, "asset:write")

    device = (
        await db.execute(
            select(Device).where(Device.id == body.device_id, Device.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="设备不存在")
    await require_entity_permission(db, auth, "device", device.id, device.group_id, "asset:write")

    existing = (
        await db.execute(
            select(ClusterDevice).where(
                ClusterDevice.cluster_id == cluster.id,
                ClusterDevice.device_id == device.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.node_role = body.node_role
    else:
        db.add(
            ClusterDevice(
                cluster_id=cluster.id,
                device_id=device.id,
                node_role=body.node_role,
            )
        )
    await db.flush()

    await record_audit(
        db,
        action="attach_device",
        target_type="cluster",
        target_id=cluster.id,
        target_label=f"{cluster.name} <- {device.hostname}",
        group_id=cluster.group_id,
        after={"device_id": device.id, "hostname": device.hostname, "node_role": body.node_role},
        auth=auth,
    )
    return MessageResponse(message="设备已关联至集群")


@router.delete("/{cluster_id}/devices/{device_id}", response_model=MessageResponse)
async def detach_device_from_cluster(
    cluster_id: int,
    device_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    cluster = (
        await db.execute(
            select(Cluster).where(Cluster.id == cluster_id, Cluster.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="集群不存在")
    await require_entity_permission(db, auth, "cluster", cluster.id, cluster.group_id, "asset:write")

    link = (
        await db.execute(
            select(ClusterDevice).where(
                ClusterDevice.cluster_id == cluster.id,
                ClusterDevice.device_id == device_id,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="该设备未关联此集群")

    await db.delete(link)
    await db.flush()
    await record_audit(
        db,
        action="detach_device",
        target_type="cluster",
        target_id=cluster.id,
        target_label=f"{cluster.name} - device:{device_id}",
        group_id=cluster.group_id,
        before={"device_id": device_id},
        auth=auth,
    )
    return MessageResponse(message="已解除设备与集群的关联")


@router.get("/{cluster_id}/members", response_model=list[ClusterMemberOut])
async def list_cluster_members(
    cluster_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[ClusterMemberOut]:
    cluster = (
        await db.execute(
            select(Cluster).where(Cluster.id == cluster_id, Cluster.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="集群不存在")
    await require_entity_permission(db, auth, "cluster", cluster.id, cluster.group_id, "asset:read")

    rows = list(
        (
            await db.execute(
                select(ClusterACL)
                .options(selectinload(ClusterACL.user), selectinload(ClusterACL.role))
                .where(ClusterACL.cluster_id == cluster.id)
                .order_by(ClusterACL.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        ClusterMemberOut(
            acl_id=r.id,
            user_id=r.user.id,
            username=r.user.username,
            display_name=r.user.display_name,
            email=r.user.email,
            is_active=r.user.is_active,
            role_id=r.role.id,
            role_code=r.role.code,
            role_name=r.role.name,
        )
        for r in rows
        if r.user is not None and r.user.deleted_at is None and r.role is not None
    ]


@router.post(
    "/{cluster_id}/members",
    response_model=ClusterMemberOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_cluster_member(
    cluster_id: int,
    body: ClusterMemberAssignRequest,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> ClusterMemberOut:
    cluster = (
        await db.execute(
            select(Cluster).where(Cluster.id == cluster_id, Cluster.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="集群不存在")

    if not auth.has_global_permission("system:manage"):
        await require_entity_permission(
            db, auth, "cluster", cluster.id, cluster.group_id, "group:member_manage"
        )

    user = (
        await db.execute(select(User).where(User.id == body.user_id, User.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    role = (await db.execute(select(Role).where(Role.id == body.role_id))).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="角色不存在")
    if role.code == "super_admin" and not auth.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="非超级管理员不可分配超级管理员角色",
        )

    exists = (
        await db.execute(
            select(ClusterACL.id).where(
                ClusterACL.user_id == body.user_id,
                ClusterACL.cluster_id == cluster.id,
                ClusterACL.role_id == body.role_id,
            )
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该成员已拥有此集群角色")

    acl = ClusterACL(user_id=user.id, cluster_id=cluster.id, role_id=role.id)
    db.add(acl)
    await db.flush()

    await record_audit(
        db,
        action="assign_cluster_member",
        target_type="cluster",
        target_id=cluster.id,
        target_label=f"{cluster.name} <- {user.username} ({role.name})",
        group_id=cluster.group_id,
        after={"user_id": user.id, "username": user.username, "role_code": role.code},
        auth=auth,
    )
    return ClusterMemberOut(
        acl_id=acl.id,
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        is_active=user.is_active,
        role_id=role.id,
        role_code=role.code,
        role_name=role.name,
    )


@router.delete("/{cluster_id}/members/{acl_id}", response_model=MessageResponse)
async def remove_cluster_member(
    cluster_id: int,
    acl_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    cluster = (
        await db.execute(
            select(Cluster).where(Cluster.id == cluster_id, Cluster.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="集群不存在")

    if not auth.has_global_permission("system:manage"):
        await require_entity_permission(
            db, auth, "cluster", cluster.id, cluster.group_id, "group:member_manage"
        )

    acl = (
        await db.execute(
            select(ClusterACL)
            .options(selectinload(ClusterACL.user), selectinload(ClusterACL.role))
            .where(ClusterACL.id == acl_id, ClusterACL.cluster_id == cluster.id)
        )
    ).scalar_one_or_none()
    if acl is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="集群成员授权记录不存在")

    username = acl.user.username if acl.user else str(acl.user_id)
    role_code = acl.role.code if acl.role else str(acl.role_id)
    await db.delete(acl)
    await db.flush()

    await record_audit(
        db,
        action="remove_cluster_member",
        target_type="cluster",
        target_id=cluster.id,
        target_label=f"{cluster.name} - {username}",
        group_id=cluster.group_id,
        before={"user_id": acl.user_id, "username": username, "role_code": role_code},
        auth=auth,
    )
    return MessageResponse(message="已移除集群成员权限")
