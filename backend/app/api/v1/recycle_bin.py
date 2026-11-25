"""Recycle bin endpoints: list soft-deleted assets, conflict-safe restore, and SuperAdmin physical purge."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
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
from app.models.credentials import Credential
from app.models.system import NetworkZone, OpsGroup
from app.schemas.common import MessageResponse
from app.schemas.m3 import RecycleBinItemOut
from app.services.assets import format_ip_str, load_group_names
from app.services.audit import record_audit
from app.services.scope import ResolvedScope, resolve_scope

router = APIRouter(prefix="/recycle-bin", tags=["回收站"])

RECYCLE_MODELS: dict[str, type] = {
    "cluster": Cluster,
    "device": Device,
    "nic": NIC,
    "subnet": Subnet,
    "ip": IPAddress,
    "service": Service,
    "url": UrlEntry,
    "credential": Credential,
    "ops_group": OpsGroup,
    "network_zone": NetworkZone,
}


def _describe_deleted(entity_type: str, obj: Any) -> tuple[str, str | None]:
    if entity_type == "device":
        return str(obj.hostname), obj.asset_code or obj.device_type
    if entity_type == "ip":
        return format_ip_str(obj.ip_address), obj.usage or obj.ip_type
    if entity_type == "subnet":
        return str(obj.cidr), obj.name
    if entity_type == "url":
        return str(obj.name), obj.full_url
    if entity_type == "credential":
        return str(obj.purpose), f"{obj.target_type}:{obj.target_id}"
    if entity_type == "nic":
        return str(obj.name), str(obj.mac_address) if obj.mac_address else obj.nic_type
    return str(getattr(obj, "name", obj.id)), getattr(obj, "code", None)


async def _item_group_id(db: AsyncSession, entity_type: str, obj: Any) -> int | None:
    if entity_type == "nic":
        return (
            await db.execute(select(Device.group_id).where(Device.id == obj.device_id))
        ).scalar_one_or_none()
    if entity_type == "ops_group":
        return obj.id
    return getattr(obj, "group_id", None)


async def _check_restore_conflict(db: AsyncSession, entity_type: str, obj: Any) -> str | None:
    if entity_type == "ip":
        conflict = (
            await db.execute(
                select(IPAddress.id).where(
                    IPAddress.network_zone_id == obj.network_zone_id,
                    IPAddress.ip_address == obj.ip_address,
                    IPAddress.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if conflict is not None:
            return f"同网络域内已存在活跃的 IP {format_ip_str(obj.ip_address)}，无法恢复"

    elif entity_type == "subnet":
        conflict = (
            await db.execute(
                select(Subnet.id).where(
                    Subnet.network_zone_id == obj.network_zone_id,
                    Subnet.cidr == obj.cidr,
                    Subnet.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if conflict is not None:
            return f"同网络域内已存在活跃网段 {obj.cidr}，无法恢复"

    elif entity_type == "device":
        if obj.asset_code:
            c = (
                await db.execute(
                    select(Device.id).where(
                        Device.asset_code == obj.asset_code, Device.deleted_at.is_(None)
                    )
                )
            ).scalar_one_or_none()
            if c is not None:
                return f"资产编号 '{obj.asset_code}' 已被其他活跃设备占用"
        if obj.serial_number:
            c = (
                await db.execute(
                    select(Device.id).where(
                        Device.serial_number == obj.serial_number, Device.deleted_at.is_(None)
                    )
                )
            ).scalar_one_or_none()
            if c is not None:
                return f"序列号 '{obj.serial_number}' 已被其他活跃设备占用"

    elif entity_type == "nic":
        dev = (
            await db.execute(
                select(Device.id).where(Device.id == obj.device_id, Device.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if dev is None:
            return "所属设备处于删除状态，请先恢复设备"
        c = (
            await db.execute(
                select(NIC.id).where(
                    NIC.device_id == obj.device_id,
                    NIC.name == obj.name,
                    NIC.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if c is not None:
            return f"该设备上已存在同名网卡 '{obj.name}'"

    elif entity_type == "cluster":
        c = (
            await db.execute(
                select(Cluster.id).where(
                    Cluster.name == obj.name,
                    Cluster.env == obj.env,
                    Cluster.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if c is not None:
            return f"同环境下已存在同名集群 '{obj.name}'"

    elif entity_type == "service":
        c = (
            await db.execute(
                select(Service.id).where(
                    Service.name == obj.name,
                    Service.env == obj.env,
                    Service.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if c is not None:
            return f"同环境下已存在同名服务 '{obj.name}'"

    return None


def _can_restore_item(
    auth: AuthContext,
    restore_scope: ResolvedScope,
    entity_type: str,
    obj: Any,
    gid: int | None,
) -> bool:
    if auth.is_superadmin or (gid is not None and auth.has_permission_in_group("recycle_bin:restore", gid)):
        return True
    if entity_type in {"cluster", "device", "nic", "ip", "service", "url", "subnet"}:
        return restore_scope.allows(entity_type, obj.id, gid)
    if entity_type == "credential":
        return restore_scope.allows(obj.target_type, obj.target_id, gid)
    return False


@router.get("", response_model=list[RecycleBinItemOut])
async def list_recycle_bin(
    entity_type: str | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[RecycleBinItemOut]:
    if not auth.has_any_permission("recycle_bin:restore") and not auth.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="缺少访问回收站权限")

    if entity_type and entity_type not in RECYCLE_MODELS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无效的实体类型")

    types_to_scan = [entity_type] if entity_type else list(RECYCLE_MODELS.keys())
    raw_items: list[tuple[str, Any, int | None]] = []
    restore_scope = await resolve_scope(db, auth, "recycle_bin:restore", include_deleted=True)

    for etype in types_to_scan:
        model = RECYCLE_MODELS[etype]
        rows = list(
            (
                await db.execute(
                    select(model)
                    .where(model.deleted_at.is_not(None))
                    .order_by(model.deleted_at.desc())
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
        for obj in rows:
            gid = await _item_group_id(db, etype, obj)
            if _can_restore_item(auth, restore_scope, etype, obj, gid):
                raw_items.append((etype, obj, gid))

    gnames = await load_group_names(db, {gid for _, _, gid in raw_items if gid is not None})
    results: list[RecycleBinItemOut] = []
    for etype, obj, gid in raw_items:
        label, subtitle = _describe_deleted(etype, obj)
        results.append(
            RecycleBinItemOut(
                entity_type=etype,
                id=obj.id,
                label=label,
                subtitle=subtitle,
                group_id=gid,
                group_name=gnames.get(gid) if gid else None,
                deleted_at=obj.deleted_at,
            )
        )

    results.sort(key=lambda x: x.deleted_at, reverse=True)
    return results


@router.post("/{entity_type}/{item_id}/restore", response_model=MessageResponse)
async def restore_item(
    entity_type: str,
    item_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    if entity_type not in RECYCLE_MODELS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不支持的实体类型")

    model = RECYCLE_MODELS[entity_type]
    obj = (
        await db.execute(select(model).where(model.id == item_id, model.deleted_at.is_not(None)))
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="回收站中未找到该记录")

    gid = await _item_group_id(db, entity_type, obj)
    restore_scope = await resolve_scope(db, auth, "recycle_bin:restore", include_deleted=True)
    if not _can_restore_item(auth, restore_scope, entity_type, obj, gid):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="缺少恢复该记录的权限: recycle_bin:restore",
        )

    conflict = await _check_restore_conflict(db, entity_type, obj)
    if conflict:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=conflict)

    label, _ = _describe_deleted(entity_type, obj)
    obj.deleted_at = None
    if hasattr(obj, "updated_by"):
        obj.updated_by = auth.user.id
    await db.flush()

    await record_audit(
        db,
        action="restore",
        target_type=entity_type,
        target_id=obj.id,
        target_label=label,
        group_id=gid,
        after={"restored": True},
        auth=auth,
    )
    return MessageResponse(message=f"已恢复 {entity_type}: {label}")


@router.delete("/{entity_type}/{item_id}", response_model=MessageResponse)
async def purge_item(
    entity_type: str,
    item_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    auth.require_global("recycle_bin:purge")
    if entity_type not in RECYCLE_MODELS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不支持的实体类型")

    model = RECYCLE_MODELS[entity_type]
    obj = (
        await db.execute(select(model).where(model.id == item_id, model.deleted_at.is_not(None)))
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="回收站中未找到该记录")

    gid = await _item_group_id(db, entity_type, obj)
    label, _ = _describe_deleted(entity_type, obj)
    await db.delete(obj)
    await db.flush()

    await record_audit(
        db,
        action="purge",
        target_type=entity_type,
        target_id=item_id,
        target_label=label,
        group_id=gid,
        before={"purged": True, "label": label},
        auth=auth,
    )
    return MessageResponse(message=f"已彻底清理 {entity_type}: {label}")
