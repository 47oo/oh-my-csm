"""Unified multi-select batch operations endpoint (tags, group migration, status/env, soft delete)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select
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
    IPVIPBinding,
    Service,
    ServiceEndpoint,
    Subnet,
    UrlEntry,
)
from app.models.system import EntityTag, OpsGroup, Tag
from app.schemas.m3 import (
    BatchEntity,
    BatchFailureItem,
    BatchOperationRequest,
    BatchOperationResponse,
)
from app.services.assets import format_ip_str
from app.services.audit import record_audit
from app.services.scope import resolve_scope

router = APIRouter(prefix="/batch", tags=["批量操作"])

ENTITY_META: dict[str, tuple[type, str]] = {
    "clusters": (Cluster, "cluster"),
    "devices": (Device, "device"),
    "nics": (NIC, "nic"),
    "subnets": (Subnet, "subnet"),
    "ips": (IPAddress, "ip"),
    "services": (Service, "service"),
    "urls": (UrlEntry, "url"),
}


def _entity_label(entity_type: str, obj: Any) -> str:
    if entity_type == "device":
        return str(obj.hostname)
    if entity_type == "ip":
        return format_ip_str(obj.ip_address)
    if entity_type == "subnet":
        return f"{obj.name} ({obj.cidr})"
    return str(getattr(obj, "name", obj.id))


async def _get_row_group_id(db: AsyncSession, entity_type: str, obj: Any) -> int | None:
    if entity_type == "nic":
        return (
            await db.execute(select(Device.group_id).where(Device.id == obj.device_id))
        ).scalar_one_or_none()
    return getattr(obj, "group_id", None)


async def _check_delete_blockers(db: AsyncSession, entity_type: str, obj: Any) -> str | None:
    if entity_type == "subnet":
        cnt = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(IPAddress)
                    .where(
                        IPAddress.subnet_id == obj.id,
                        IPAddress.deleted_at.is_(None),
                        IPAddress.status != "free",
                    )
                )
            ).scalar_one()
        )
        return f"仍有 {cnt} 个已分配/预留 IP" if cnt else None

    if entity_type == "cluster":
        cnt = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(ClusterDevice)
                    .join(Device, Device.id == ClusterDevice.device_id)
                    .where(ClusterDevice.cluster_id == obj.id, Device.deleted_at.is_(None))
                )
            ).scalar_one()
        )
        if cnt:
            return f"仍有 {cnt} 台成员设备"
        ep_cnt = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(ServiceEndpoint)
                    .join(Service, Service.id == ServiceEndpoint.service_id)
                    .where(ServiceEndpoint.cluster_id == obj.id, Service.deleted_at.is_(None))
                )
            ).scalar_one()
        )
        if ep_cnt:
            return f"仍有 {ep_cnt} 条服务端点"
        vip_cnt = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(IPVIPBinding)
                    .join(IPAddress, IPAddress.id == IPVIPBinding.ip_id)
                    .where(
                        IPVIPBinding.target_type == "cluster",
                        IPVIPBinding.target_id == obj.id,
                        IPAddress.deleted_at.is_(None),
                    )
                )
            ).scalar_one()
        )
        return f"仍有 {vip_cnt} 条 VIP 绑定" if vip_cnt else None

    if entity_type == "device":
        nic_cnt = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(NIC)
                    .where(NIC.device_id == obj.id, NIC.deleted_at.is_(None))
                )
            ).scalar_one()
        )
        if nic_cnt:
            return f"仍有 {nic_cnt} 张网卡"
        ep_cnt = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(ServiceEndpoint)
                    .join(Service, Service.id == ServiceEndpoint.service_id)
                    .where(ServiceEndpoint.device_id == obj.id, Service.deleted_at.is_(None))
                )
            ).scalar_one()
        )
        return f"仍有 {ep_cnt} 条服务端点" if ep_cnt else None

    if entity_type == "nic":
        ip_cnt = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(IPAddress)
                    .where(IPAddress.nic_id == obj.id, IPAddress.deleted_at.is_(None))
                )
            ).scalar_one()
        )
        if ip_cnt:
            return f"仍有 {ip_cnt} 个绑定 IP"
        child_cnt = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(NIC)
                    .where(NIC.parent_nic_id == obj.id, NIC.deleted_at.is_(None))
                )
            ).scalar_one()
        )
        if child_cnt:
            return f"仍有 {child_cnt} 张成员网卡"
        vip_cnt = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(IPVIPBinding)
                    .join(IPAddress, IPAddress.id == IPVIPBinding.ip_id)
                    .where(
                        IPVIPBinding.target_type == "nic",
                        IPVIPBinding.target_id == obj.id,
                        IPAddress.deleted_at.is_(None),
                    )
                )
            ).scalar_one()
        )
        return f"仍有 {vip_cnt} 条 VIP 承载绑定" if vip_cnt else None

    if entity_type == "ip":
        if obj.nic_id is not None:
            return "仍绑定在网卡上"
        vip_cnt = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(IPVIPBinding)
                    .where(IPVIPBinding.ip_id == obj.id)
                )
            ).scalar_one()
        )
        if vip_cnt:
            return f"仍有 {vip_cnt} 条 VIP 绑定"
        ep_cnt = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(ServiceEndpoint)
                    .join(Service, Service.id == ServiceEndpoint.service_id)
                    .where(ServiceEndpoint.ip_id == obj.id, Service.deleted_at.is_(None))
                )
            ).scalar_one()
        )
        if ep_cnt:
            return f"仍有 {ep_cnt} 条服务端点引用"
        url_cnt = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(UrlEntry)
                    .where(UrlEntry.entry_ip_id == obj.id, UrlEntry.deleted_at.is_(None))
                )
            ).scalar_one()
        )
        return f"仍有 {url_cnt} 条 URL 入口引用" if url_cnt else None

    if entity_type == "service":
        url_cnt = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(UrlEntry)
                    .where(UrlEntry.service_id == obj.id, UrlEntry.deleted_at.is_(None))
                )
            ).scalar_one()
        )
        return f"仍有 {url_cnt} 条关联 URL" if url_cnt else None

    return None


@router.post("/{entity}", response_model=BatchOperationResponse)
async def execute_batch_operation(
    entity: BatchEntity,
    body: BatchOperationRequest,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> BatchOperationResponse:
    model, target_type = ENTITY_META[entity]

    # Pre-validate action parameters
    if body.action in {"tag_add", "tag_remove"}:
        if not body.tag_ids:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请选择至少一个标签")
        existing_tags = set(
            (await db.execute(select(Tag.id).where(Tag.id.in_(body.tag_ids)))).scalars().all()
        )
        if len(existing_tags) != len(set(body.tag_ids)):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="包含不存在的标签")

    if body.action == "move_group":
        if target_type == "nic":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="网卡归属分组继承自所属设备，请批量迁移设备分组",
            )
        if body.group_id is not None:
            grp = (
                await db.execute(
                    select(OpsGroup.id).where(
                        OpsGroup.id == body.group_id, OpsGroup.deleted_at.is_(None)
                    )
                )
            ).scalar_one_or_none()
            if grp is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="目标分组不存在")
        auth.require_group_permission("asset:write", body.group_id)

    if body.action == "update_status" and body.status is None and body.env is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请至少指定要修改的状态或环境",
        )

    rows = list(
        (
            await db.execute(
                select(model).where(model.id.in_(set(body.ids)), model.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    row_by_id = {r.id: r for r in rows}

    succeeded: list[int] = []
    failed: list[BatchFailureItem] = []
    req_perm = "asset:delete" if body.action == "delete" else "asset:write"
    action_scope = await resolve_scope(db, auth, req_perm)

    for item_id in body.ids:
        obj = row_by_id.get(item_id)
        if obj is None:
            failed.append(BatchFailureItem(id=item_id, label=f"#{item_id}", reason="记录不存在或已删除"))
            continue

        label = _entity_label(target_type, obj)
        row_gid = await _get_row_group_id(db, target_type, obj)
        if not action_scope.allows(target_type, obj.id, row_gid):
            failed.append(BatchFailureItem(id=item_id, label=label, reason=f"无权限 ({req_perm})"))
            continue

        if body.action == "tag_add":
            for tid in set(body.tag_ids or []):
                exists = (
                    await db.execute(
                        select(EntityTag.tag_id).where(
                            EntityTag.target_type == target_type,
                            EntityTag.target_id == obj.id,
                            EntityTag.tag_id == tid,
                        )
                    )
                ).scalar_one_or_none()
                if exists is None:
                    db.add(EntityTag(target_type=target_type, target_id=obj.id, tag_id=tid))
            await record_audit(
                db,
                action="batch_tag_add",
                target_type=target_type,
                target_id=obj.id,
                target_label=label,
                group_id=row_gid,
                after={"added_tag_ids": sorted(set(body.tag_ids or []))},
                auth=auth,
            )
            succeeded.append(obj.id)

        elif body.action == "tag_remove":
            await db.execute(
                delete(EntityTag).where(
                    EntityTag.target_type == target_type,
                    EntityTag.target_id == obj.id,
                    EntityTag.tag_id.in_(set(body.tag_ids or [])),
                )
            )
            await record_audit(
                db,
                action="batch_tag_remove",
                target_type=target_type,
                target_id=obj.id,
                target_label=label,
                group_id=row_gid,
                after={"removed_tag_ids": sorted(set(body.tag_ids or []))},
                auth=auth,
            )
            succeeded.append(obj.id)

        elif body.action == "move_group":
            if body.group_id != row_gid and not (
                auth.has_permission_in_group("asset:write", row_gid)
                and auth.has_permission_in_group("asset:write", body.group_id)
            ):
                failed.append(
                    BatchFailureItem(
                        id=item_id,
                        label=label,
                        reason="跨分组迁移需同时具备源分组与目标分组的 asset:write 权限",
                    )
                )
                continue
            before = {"group_id": row_gid}
            obj.group_id = body.group_id
            obj.updated_by = auth.user.id
            await record_audit(
                db,
                action="batch_move_group",
                target_type=target_type,
                target_id=obj.id,
                target_label=label,
                group_id=body.group_id,
                before=before,
                after={"group_id": body.group_id},
                auth=auth,
            )
            succeeded.append(obj.id)

        elif body.action == "update_status":
            before: dict[str, Any] = {}
            after: dict[str, Any] = {}
            if body.status is not None and hasattr(obj, "status"):
                before["status"] = obj.status
                obj.status = body.status
                after["status"] = body.status
            if body.env is not None and hasattr(obj, "env"):
                before["env"] = obj.env
                obj.env = body.env
                after["env"] = body.env
            obj.updated_by = auth.user.id
            await record_audit(
                db,
                action="batch_update",
                target_type=target_type,
                target_id=obj.id,
                target_label=label,
                group_id=row_gid,
                before=before,
                after=after,
                auth=auth,
            )
            succeeded.append(obj.id)

        elif body.action == "delete":
            blocker = await _check_delete_blockers(db, target_type, obj)
            if blocker:
                failed.append(BatchFailureItem(id=item_id, label=label, reason=blocker))
                continue
            obj.deleted_at = utc_now()
            obj.updated_by = auth.user.id
            await record_audit(
                db,
                action="delete",
                target_type=target_type,
                target_id=obj.id,
                target_label=label,
                group_id=row_gid,
                before={"label": label},
                auth=auth,
            )
            succeeded.append(obj.id)

    await db.flush()
    return BatchOperationResponse(succeeded_ids=succeeded, failed=failed)
