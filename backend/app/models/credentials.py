"""Encrypted associated credentials model (polymorphic target, AES-256-GCM ciphertext)."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import BigIntPK, TimestampSoftDeleteMixin


class Credential(TimestampSoftDeleteMixin, Base):
    __tablename__ = "credentials"

    target_type: Mapped[str] = mapped_column(String(32), nullable=False)  # cluster|device|nic|service|url
    target_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    group_id: Mapped[int | None] = mapped_column(
        BigIntPK, ForeignKey("ops_groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    purpose: Mapped[str] = mapped_column(String(128), nullable=False)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    encrypted_secret: Mapped[str] = mapped_column(Text, nullable=False)
    key_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_credentials_target", "target_type", "target_id"),
    )
