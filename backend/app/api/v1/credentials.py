"""Encrypted credential vault endpoints with strict RBAC and mandatory reveal auditing."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import AuthContext, get_current_auth
from app.core.security import CredentialCipher, utc_now
from app.models.assets import NIC, Cluster, Device, Service, UrlEntry
from app.models.credentials import Credential
from app.schemas.common import MessageResponse
from app.schemas.m3 import (
    CredentialCreate,
    CredentialOut,
    CredentialRevealOut,
    CredentialTargetType,
    CredentialUpdate,
)
from app.services.audit import record_audit
from app.services.scope import require_entity_permission

router = APIRouter(prefix="/credentials", tags=["关联凭据"])


async def _resolve_target_scope(
    db: AsyncSession,
    target_type: str,
    target_id: int,
) -> tuple[int | None, str]:
    """Verify target exists and return (group_id, display_label)."""
    if target_type == "cluster":
        obj = (
            await db.execute(
                select(Cluster).where(Cluster.id == target_id, Cluster.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if obj is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="关联集群不存在")
        return obj.group_id, f"cluster:{obj.name}"

    if target_type == "device":
        obj = (
            await db.execute(
                select(Device).where(Device.id == target_id, Device.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if obj is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="关联设备不存在")
        return obj.group_id, f"device:{obj.hostname}"

    if target_type == "nic":
        nic = (
            await db.execute(select(NIC).where(NIC.id == target_id, NIC.deleted_at.is_(None)))
        ).scalar_one_or_none()
        if nic is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="关联网卡不存在")
        dev = (
            await db.execute(
                select(Device).where(Device.id == nic.device_id, Device.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        return (dev.group_id if dev else None), f"nic:{dev.hostname if dev else nic.device_id}:{nic.name}"

    if target_type == "service":
        obj = (
            await db.execute(
                select(Service).where(Service.id == target_id, Service.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if obj is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="关联服务不存在")
        return obj.group_id, f"service:{obj.name}"

    if target_type == "url":
        obj = (
            await db.execute(
                select(UrlEntry).where(UrlEntry.id == target_id, UrlEntry.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if obj is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="关联 URL 不存在")
        return obj.group_id, f"url:{obj.name}"

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不支持的目标类型")


@router.get("", response_model=list[CredentialOut])
async def list_credentials(
    target_type: CredentialTargetType = Query(...),
    target_id: int = Query(..., ge=1),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[CredentialOut]:
    group_id, _ = await _resolve_target_scope(db, target_type, target_id)
    await require_entity_permission(db, auth, target_type, target_id, group_id, "asset:read")

    rows = list(
        (
            await db.execute(
                select(Credential)
                .where(
                    Credential.target_type == target_type,
                    Credential.target_id == target_id,
                    Credential.deleted_at.is_(None),
                )
                .order_by(Credential.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return [CredentialOut.model_validate(r) for r in rows]


@router.post("", response_model=CredentialOut, status_code=status.HTTP_201_CREATED)
async def create_credential(
    body: CredentialCreate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> CredentialOut:
    group_id, target_label = await _resolve_target_scope(db, body.target_type, body.target_id)
    await require_entity_permission(
        db, auth, body.target_type, body.target_id, group_id, "credential:manage"
    )

    cipher = CredentialCipher.from_settings()
    encrypted, ver = cipher.encrypt(body.secret)

    cred = Credential(
        target_type=body.target_type,
        target_id=body.target_id,
        group_id=group_id,
        purpose=body.purpose.strip(),
        username=body.username.strip() if body.username else None,
        encrypted_secret=encrypted,
        key_version=ver,
        notes=body.notes,
        created_by=auth.user.id,
        updated_by=auth.user.id,
    )
    db.add(cred)
    await db.flush()

    await record_audit(
        db,
        action="create",
        target_type="credential",
        target_id=cred.id,
        target_label=f"{target_label} ({cred.purpose})",
        group_id=cred.group_id,
        after={
            "target_type": cred.target_type,
            "target_id": cred.target_id,
            "purpose": cred.purpose,
            "username": cred.username,
            "secret": "***",
        },
        auth=auth,
    )
    return CredentialOut.model_validate(cred)


@router.put("/{cred_id}", response_model=CredentialOut)
async def update_credential(
    cred_id: int,
    body: CredentialUpdate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> CredentialOut:
    cred = (
        await db.execute(
            select(Credential).where(Credential.id == cred_id, Credential.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if cred is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="凭据不存在")
    await require_entity_permission(
        db, auth, cred.target_type, cred.target_id, cred.group_id, "credential:manage"
    )

    before = {"purpose": cred.purpose, "username": cred.username, "notes": cred.notes}
    if body.purpose is not None:
        cred.purpose = body.purpose.strip()
    if body.username is not None:
        cred.username = body.username.strip() or None
    if body.notes is not None:
        cred.notes = body.notes
    secret_changed = False
    if body.secret is not None:
        cipher = CredentialCipher.from_settings()
        encrypted, ver = cipher.encrypt(body.secret)
        cred.encrypted_secret = encrypted
        cred.key_version = ver
        secret_changed = True
    cred.updated_by = auth.user.id
    await db.flush()

    after = {"purpose": cred.purpose, "username": cred.username, "notes": cred.notes}
    if secret_changed:
        before["secret"] = "***"
        after["secret"] = "*** (updated)"

    await record_audit(
        db,
        action="update",
        target_type="credential",
        target_id=cred.id,
        target_label=f"{cred.target_type}:{cred.target_id} ({cred.purpose})",
        group_id=cred.group_id,
        before=before,
        after=after,
        auth=auth,
    )
    return CredentialOut.model_validate(cred)


@router.delete("/{cred_id}", response_model=MessageResponse)
async def delete_credential(
    cred_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    cred = (
        await db.execute(
            select(Credential).where(Credential.id == cred_id, Credential.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if cred is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="凭据不存在")
    await require_entity_permission(
        db, auth, cred.target_type, cred.target_id, cred.group_id, "credential:manage"
    )

    cred.deleted_at = utc_now()
    cred.updated_by = auth.user.id
    await db.flush()

    await record_audit(
        db,
        action="delete",
        target_type="credential",
        target_id=cred.id,
        target_label=f"{cred.target_type}:{cred.target_id} ({cred.purpose})",
        group_id=cred.group_id,
        before={"purpose": cred.purpose, "username": cred.username},
        auth=auth,
    )
    return MessageResponse(message="凭据已移入回收站")


@router.post("/{cred_id}/reveal", response_model=CredentialRevealOut)
async def reveal_credential(
    cred_id: int,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> CredentialRevealOut:
    """Decrypt and return credential plaintext; requires credential:reveal AND always writes an audit log."""
    cred = (
        await db.execute(
            select(Credential).where(Credential.id == cred_id, Credential.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if cred is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="凭据不存在")

    await require_entity_permission(
        db, auth, cred.target_type, cred.target_id, cred.group_id, "asset:read"
    )
    await require_entity_permission(
        db, auth, cred.target_type, cred.target_id, cred.group_id, "credential:reveal"
    )

    cipher = CredentialCipher.from_settings()
    try:
        plaintext = cipher.decrypt(cred.encrypted_secret)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="凭据解密失败，请检查主密钥配置",
        ) from exc

    # Mandatory audit record on every plaintext reveal
    await record_audit(
        db,
        action="credential_reveal",
        target_type="credential",
        target_id=cred.id,
        target_label=f"{cred.target_type}:{cred.target_id} ({cred.purpose})",
        group_id=cred.group_id,
        after={"purpose": cred.purpose, "username": cred.username, "revealed": True},
        auth=auth,
    )

    return CredentialRevealOut(
        id=cred.id,
        purpose=cred.purpose,
        username=cred.username,
        secret=plaintext,
    )
