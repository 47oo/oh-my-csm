"""Cluster-level RBAC upgrade: bind cluster_acls to roles + add users.allow_public_assets.

Revision ID: 0004_cluster_rbac
Revises: 0003_m3_credentials
Create Date: 2026-11-25 14:35:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_cluster_rbac"
down_revision: Union[str, None] = "0003_m3_credentials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    pk_type = sa.BigInteger() if is_pg else sa.Integer()

    op.add_column(
        "users",
        sa.Column(
            "allow_public_assets",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # Upgrade cluster_acls from legacy placeholder (permission_level) to RBAC role_id + cluster FK
    op.drop_table("cluster_acls")
    op.create_table(
        "cluster_acls",
        sa.Column("id", pk_type, autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("cluster_id", sa.BigInteger(), nullable=False),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cluster_id"], ["clusters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_cluster_acls"),
    )
    op.create_index("ix_cluster_acls_user_id", "cluster_acls", ["user_id"])
    op.create_index("ix_cluster_acls_cluster_id", "cluster_acls", ["cluster_id"])
    op.create_index("ix_cluster_acls_role_id", "cluster_acls", ["role_id"])
    op.create_index(
        "uq_cluster_acls_user_cluster_role",
        "cluster_acls",
        ["user_id", "cluster_id", "role_id"],
        unique=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    pk_type = sa.BigInteger() if is_pg else sa.Integer()

    op.drop_table("cluster_acls")
    op.create_table(
        "cluster_acls",
        sa.Column("id", pk_type, autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("cluster_id", sa.BigInteger(), nullable=False),
        sa.Column("permission_level", sa.String(length=16), nullable=False, server_default="read"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_cluster_acls"),
    )
    op.create_index(
        "uq_cluster_acls_user_cluster", "cluster_acls", ["user_id", "cluster_id"], unique=True
    )
    op.drop_column("users", "allow_public_assets")
