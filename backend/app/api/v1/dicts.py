"""Configurable asset dictionary endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import AuthContext, get_current_auth
from app.models.system import DictItem
from app.schemas.common import MessageResponse
from app.schemas.system import DictItemCreate, DictItemOut, DictItemUpdate
from app.services.audit import record_audit

router = APIRouter(prefix="/dicts", tags=["数据字典"])


@router.get("", response_model=list[DictItemOut])
async def list_dicts(
    category: str | None = Query(default=None),
    only_enabled: bool = Query(default=False),
    _: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[DictItemOut]:
    stmt = select(DictItem)
    if category:
        stmt = stmt.where(DictItem.category == category)
    if only_enabled:
        stmt = stmt.where(DictItem.is_enabled.is_(True))
    stmt = stmt.order_by(DictItem.category.asc(), DictItem.sort_order.asc(), DictItem.id.asc())
    rows = list((await db.execute(stmt)).scalars().all())
    return [DictItemOut.model_validate(r) for r in rows]


@router.post("", response_model=DictItemOut, status_code=status.HTTP_201_CREATED)
async def create_dict_item(
    body: DictItemCreate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> DictItemOut:
    auth.require_global("system:manage")

    exists = (
        await db.execute(
            select(DictItem.id).where(
                DictItem.category == body.category,
                DictItem.code == body.code,
            )
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"字典项 {body.category}/{body.code} 已存在",
        )

    item = DictItem(
        category=body.category,
        code=body.code,
        label=body.label.strip(),
        color=body.color,
        sort_order=body.sort_order,
        is_builtin=False,
        is_enabled=body.is_enabled,
        description=body.description,
    )
    db.add(item)
    await db.flush()

    await record_audit(
        db,
        action="create",
        target_type="dict_item",
        target_id=item.id,
        target_label=f"{item.category}:{item.code}",
        after={"category": item.category, "code": item.code, "label": item.label},
        auth=auth,
    )
    return DictItemOut.model_validate(item)


@router.put("/{item_id}", response_model=DictItemOut)
async def update_dict_item(
    item_id: int,
    body: DictItemUpdate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> DictItemOut:
    auth.require_global("system:manage")

    item = (await db.execute(select(DictItem).where(DictItem.id == item_id))).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="字典项不存在")

    before = {
        "label": item.label,
        "color": item.color,
        "sort_order": item.sort_order,
        "is_enabled": item.is_enabled,
        "description": item.description,
    }
    if body.label is not None:
        item.label = body.label.strip()
    if body.color is not None:
        item.color = body.color
    if body.sort_order is not None:
        item.sort_order = body.sort_order
    if body.is_enabled is not None:
        item.is_enabled = body.is_enabled
    if body.description is not None:
        item.description = body.description
    await db.flush()

    await record_audit(
        db,
        action="update",
        target_type="dict_item",
        target_id=item.id,
        target_label=f"{item.category}:{item.code}",
        before=before,
        after={
            "label": item.label,
            "color": item.color,
            "sort_order": item.sort_order,
            "is_enabled": item.is_enabled,
            "description": item.description,
        },
        auth=auth,
    )
    return DictItemOut.model_validate(item)


@router.delete("/{item_id}", response_model=MessageResponse)
async def delete_dict_item(
    item_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    auth.require_global("system:manage")

    item = (await db.execute(select(DictItem).where(DictItem.id == item_id))).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="字典项不存在")
    if item.is_builtin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="内置字典项不可删除，可将其设为停用",
        )

    await db.delete(item)
    await db.flush()
    await record_audit(
        db,
        action="delete",
        target_type="dict_item",
        target_id=item_id,
        target_label=f"{item.category}:{item.code}",
        before={"category": item.category, "code": item.code, "label": item.label},
        auth=auth,
    )
    return MessageResponse(message="字典项已删除")
