"""M3 encrypted credentials table.

Revision ID: 0003_m3_credentials
Revises: 0002_m2_assets
Create Date: 2026-11-25 13:20:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003_m3_credentials"
down_revision: Union[str, None] = "0002_m2_assets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    pk_type = sa.BigInteger() if is_pg else sa.Integer()
    json_type = postgresql.JSONB(astext_type=sa.Text()) if is_pg else sa.JSON()

    op.create_table(
        "credentials",
        sa.Column("id", pk_type, autoincrement=True, nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("group_id", sa.BigInteger(), nullable=True),
        sa.Column("purpose", sa.String(length=128), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=True),
        sa.Column("encrypted_secret", sa.Text(), nullable=False),
        sa.Column("key_version", sa.String(length=16), nullable=False, server_default="1"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("extra_attrs", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["group_id"], ["ops_groups.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_credentials"),
    )
    op.create_index("ix_credentials_target", "credentials", ["target_type", "target_id"])
    op.create_index("ix_credentials_group_id", "credentials", ["group_id"])
    op.create_index("ix_credentials_deleted_at", "credentials", ["deleted_at"])


def downgrade() -> None:
    op.drop_table("credentials")
