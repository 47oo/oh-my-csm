"""Global asset tag management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import AuthContext, get_current_auth
from app.models.system import Tag
from app.schemas.common import MessageResponse
from app.schemas.system import TagCreate, TagOut, TagUpdate
from app.services.audit import record_audit

router = APIRouter(prefix="/tags", tags=["标签管理"])


@router.get("", response_model=list[TagOut])
async def list_tags(
    q: str | None = Query(default=None),
    _: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[TagOut]:
    stmt = select(Tag)
    if q:
        stmt = stmt.where(Tag.name.ilike(f"%{q.strip()}%"))
    stmt = stmt.order_by(Tag.name.asc())
    rows = list((await db.execute(stmt)).scalars().all())
    return [TagOut.model_validate(r) for r in rows]


@router.post("", response_model=TagOut, status_code=status.HTTP_201_CREATED)
async def create_tag(
    body: TagCreate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> TagOut:
    if not auth.has_any_permission("asset:write") and not auth.has_global_permission("system:manage"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="缺少创建标签权限")

    name = body.name.strip()
    exists = (await db.execute(select(Tag.id).where(Tag.name == name))).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"标签 '{name}' 已存在")

    tag = Tag(name=name, color=body.color, description=body.description)
    db.add(tag)
    await db.flush()

    await record_audit(
        db,
        action="create",
        target_type="tag",
        target_id=tag.id,
        target_label=tag.name,
        after={"name": tag.name, "color": tag.color},
        auth=auth,
    )
    return TagOut.model_validate(tag)


@router.put("/{tag_id}", response_model=TagOut)
async def update_tag(
    tag_id: int,
    body: TagUpdate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> TagOut:
    auth.require_global("system:manage")

    tag = (await db.execute(select(Tag).where(Tag.id == tag_id))).scalar_one_or_none()
    if tag is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="标签不存在")

    before = {"name": tag.name, "color": tag.color, "description": tag.description}
    if body.name is not None:
        new_name = body.name.strip()
        conflict = (
            await db.execute(select(Tag.id).where(Tag.name == new_name, Tag.id != tag.id))
        ).scalar_one_or_none()
        if conflict is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"标签 '{new_name}' 已存在")
        tag.name = new_name
    if body.color is not None:
        tag.color = body.color
    if body.description is not None:
        tag.description = body.description
    await db.flush()

    await record_audit(
        db,
        action="update",
        target_type="tag",
        target_id=tag.id,
        target_label=tag.name,
        before=before,
        after={"name": tag.name, "color": tag.color, "description": tag.description},
        auth=auth,
    )
    return TagOut.model_validate(tag)


@router.delete("/{tag_id}", response_model=MessageResponse)
async def delete_tag(
    tag_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    auth.require_global("system:manage")

    tag = (await db.execute(select(Tag).where(Tag.id == tag_id))).scalar_one_or_none()
    if tag is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="标签不存在")

    await db.delete(tag)
    await db.flush()
    await record_audit(
        db,
        action="delete",
        target_type="tag",
        target_id=tag_id,
        target_label=tag.name,
        before={"name": tag.name},
        auth=auth,
    )
    return MessageResponse(message=f"标签 '{tag.name}' 已删除")
