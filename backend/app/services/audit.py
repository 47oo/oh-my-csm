"""Audit logging and field-level JSON diff helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import AuthContext
from app.models.audit import AuditLog

REDACTED_FIELDS = {"password", "password_hash", "encrypted_secret", "token_hash", "secret"}


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_serialize_value(v) for v in value]
    if isinstance(value, dict):
        return {
            str(k): ("***" if str(k) in REDACTED_FIELDS else _serialize_value(v))
            for k, v in value.items()
        }
    return value


def compute_diff(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Compute minimal field-level diff dictionaries while redacting secret fields."""
    if before is None and after is None:
        return None, None
    if before is None:
        return None, _serialize_value(after)
    if after is None:
        return _serialize_value(before), None

    diff_before: dict[str, Any] = {}
    diff_after: dict[str, Any] = {}
    keys = set(before.keys()) | set(after.keys())
    for key in sorted(keys):
        if key in REDACTED_FIELDS:
            if before.get(key) != after.get(key):
                diff_before[key] = "***"
                diff_after[key] = "***"
            continue
        b_val = _serialize_value(before.get(key))
        a_val = _serialize_value(after.get(key))
        if b_val != a_val:
            diff_before[key] = b_val
            diff_after[key] = a_val

    return (diff_before or None), (diff_after or None)


async def record_audit(
    db: AsyncSession,
    *,
    action: str,
    target_type: str,
    target_id: int | None = None,
    target_label: str | None = None,
    group_id: int | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    auth: AuthContext | None = None,
    actor_id: int | None = None,
    actor_username: str | None = None,
    auth_method: str = "jwt",
    client_ip: str = "unknown",
    import_batch_id: int | None = None,
) -> AuditLog:
    diff_before, diff_after = compute_diff(before, after)
    log = AuditLog(
        actor_id=auth.user.id if auth else actor_id,
        actor_username=auth.user.username if auth else (actor_username or "system"),
        auth_method=auth.auth_method if auth else auth_method,
        client_ip=auth.client_ip if auth else client_ip,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_label=target_label,
        group_id=group_id,
        import_batch_id=import_batch_id,
        diff_before=diff_before,
        diff_after=diff_after,
    )
    db.add(log)
    await db.flush()
    return log
