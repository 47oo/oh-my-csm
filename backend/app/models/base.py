"""Base mixins and cross-dialect column helpers (PostgreSQL native + SQLite test compatibility)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

# Works as BIGINT on PostgreSQL and INTEGER PRIMARY KEY AUTOINCREMENT on SQLite
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")
JsonType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    """Python-side UTC timestamp helper so async ORM flush never expires updated_at."""
    return datetime.now(timezone.utc)


class TimestampSoftDeleteMixin:
    """Common audit timestamps, soft delete marker, and JSONB extra attributes."""

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    extra_attrs: Mapped[dict[str, Any]] = mapped_column(
        JsonType,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=utcnow,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    @declared_attr
    def created_by(cls) -> Mapped[int | None]:
        return mapped_column(
            BigIntPK,
            ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        )

    @declared_attr
    def updated_by(cls) -> Mapped[int | None]:
        return mapped_column(
            BigIntPK,
            ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        )
