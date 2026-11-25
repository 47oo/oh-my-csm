"""NetworkZone management endpoints (supports isolated network domains with default zone)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import AuthContext, get_current_auth
from app.core.security import utc_now
from app.models.assets import Device, IPAddress, Subnet
from app.models.system import NetworkZone
from app.schemas.common import MessageResponse
from app.schemas.system import NetworkZoneCreate, NetworkZoneOut, NetworkZoneUpdate
from app.services.audit import record_audit

router = APIRouter(prefix="/network-zones", tags=["网络域"])


@router.get("", response_model=list[NetworkZoneOut])
async def list_network_zones(
    q: str | None = Query(default=None),
    _: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[NetworkZoneOut]:
    stmt = select(NetworkZone).where(NetworkZone.deleted_at.is_(None))
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                NetworkZone.name.ilike(pattern),
                NetworkZone.code.ilike(pattern),
                NetworkZone.location.ilike(pattern),
            )
        )
    stmt = stmt.order_by(NetworkZone.is_default.desc(), NetworkZone.id.asc())
    rows = list((await db.execute(stmt)).scalars().all())
    return [NetworkZoneOut.model_validate(r) for r in rows]


@router.post("", response_model=NetworkZoneOut, status_code=status.HTTP_201_CREATED)
async def create_network_zone(
    body: NetworkZoneCreate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> NetworkZoneOut:
    auth.require_global("system:manage")

    code = body.code.strip()
    exists = (
        await db.execute(
            select(NetworkZone.id).where(
                NetworkZone.code == code,
                NetworkZone.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"网络域编码 '{code}' 已存在",
        )

    if body.is_default:
        await db.execute(
            update(NetworkZone)
            .where(NetworkZone.deleted_at.is_(None))
            .values(is_default=False)
        )

    zone = NetworkZone(
        name=body.name.strip(),
        code=code,
        location=body.location,
        is_default=body.is_default,
        description=body.description,
        extra_attrs=body.extra_attrs,
        created_by=auth.user.id,
        updated_by=auth.user.id,
    )
    db.add(zone)
    await db.flush()

    await record_audit(
        db,
        action="create",
        target_type="network_zone",
        target_id=zone.id,
        target_label=zone.name,
        after={
            "name": zone.name,
            "code": zone.code,
            "location": zone.location,
            "is_default": zone.is_default,
        },
        auth=auth,
    )
    return NetworkZoneOut.model_validate(zone)


@router.put("/{zone_id}", response_model=NetworkZoneOut)
async def update_network_zone(
    zone_id: int,
    body: NetworkZoneUpdate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> NetworkZoneOut:
    auth.require_global("system:manage")

    zone = (
        await db.execute(
            select(NetworkZone).where(
                NetworkZone.id == zone_id,
                NetworkZone.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if zone is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="网络域不存在")

    before = {
        "name": zone.name,
        "location": zone.location,
        "is_default": zone.is_default,
        "description": zone.description,
        "extra_attrs": zone.extra_attrs,
    }

    if body.is_default is True and not zone.is_default:
        await db.execute(
            update(NetworkZone)
            .where(NetworkZone.deleted_at.is_(None), NetworkZone.id != zone.id)
            .values(is_default=False)
        )
        zone.is_default = True
    elif body.is_default is False and zone.is_default:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="系统必须保留一个默认网络域，请将其他网络域设为默认",
        )

    if body.name is not None:
        zone.name = body.name.strip()
    if body.location is not None:
        zone.location = body.location
    if body.description is not None:
        zone.description = body.description
    if body.extra_attrs is not None:
        zone.extra_attrs = body.extra_attrs
    zone.updated_by = auth.user.id
    await db.flush()

    await record_audit(
        db,
        action="update",
        target_type="network_zone",
        target_id=zone.id,
        target_label=zone.name,
        before=before,
        after={
            "name": zone.name,
            "location": zone.location,
            "is_default": zone.is_default,
            "description": zone.description,
            "extra_attrs": zone.extra_attrs,
        },
        auth=auth,
    )
    return NetworkZoneOut.model_validate(zone)


@router.delete("/{zone_id}", response_model=MessageResponse)
async def delete_network_zone(
    zone_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    auth.require_global("system:manage")

    zone = (
        await db.execute(
            select(NetworkZone).where(
                NetworkZone.id == zone_id,
                NetworkZone.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if zone is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="网络域不存在")
    if zone.is_default:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="默认网络域不可删除")

    blockers: list[str] = []
    sub_cnt = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Subnet)
                .where(Subnet.network_zone_id == zone.id, Subnet.deleted_at.is_(None))
            )
        ).scalar_one()
    )
    if sub_cnt:
        blockers.append(f"{sub_cnt} 个网段")
    dev_cnt = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Device)
                .where(Device.network_zone_id == zone.id, Device.deleted_at.is_(None))
            )
        ).scalar_one()
    )
    if dev_cnt:
        blockers.append(f"{dev_cnt} 台设备")
    ip_cnt = int(
        (
            await db.execute(
                select(func.count())
                .select_from(IPAddress)
                .where(IPAddress.network_zone_id == zone.id, IPAddress.deleted_at.is_(None))
            )
        ).scalar_one()
    )
    if ip_cnt:
        blockers.append(f"{ip_cnt} 个 IP")
    if blockers:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"该网络域下仍有资产（{'、'.join(blockers)}），无法删除",
        )

    zone.deleted_at = utc_now()
    zone.updated_by = auth.user.id
    await db.flush()

    await record_audit(
        db,
        action="delete",
        target_type="network_zone",
        target_id=zone.id,
        target_label=zone.name,
        before={"code": zone.code, "name": zone.name},
        auth=auth,
    )
    return MessageResponse(message=f"网络域 '{zone.name}' 已删除")
