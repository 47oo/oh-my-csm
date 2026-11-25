"""M2 core asset & IPAM tables: subnets, clusters, devices, nics, ips, vip bindings, services, endpoints, urls.

Revision ID: 0002_m2_assets
Revises: 0001_m1_foundation
Create Date: 2026-11-25 12:30:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002_m2_assets"
down_revision: Union[str, None] = "0001_m1_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    pk_type = sa.BigInteger() if is_pg else sa.Integer()
    json_type = postgresql.JSONB(astext_type=sa.Text()) if is_pg else sa.JSON()
    inet_type = postgresql.INET() if is_pg else sa.String(length=64)
    cidr_type = postgresql.CIDR() if is_pg else sa.String(length=64)
    mac_type = postgresql.MACADDR() if is_pg else sa.String(length=32)

    op.create_table(
        "subnets",
        sa.Column("id", pk_type, autoincrement=True, nullable=False),
        sa.Column("network_zone_id", sa.BigInteger(), nullable=False),
        sa.Column("group_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("cidr", cidr_type, nullable=False),
        sa.Column("gateway", inet_type, nullable=True),
        sa.Column("vlan_id", sa.Integer(), nullable=True),
        sa.Column("env", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="in_use"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("extra_attrs", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["network_zone_id"], ["network_zones.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["group_id"], ["ops_groups.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_subnets"),
    )
    op.create_index("ix_subnets_network_zone_id", "subnets", ["network_zone_id"])
    op.create_index("ix_subnets_group_id", "subnets", ["group_id"])
    op.create_index("ix_subnets_deleted_at", "subnets", ["deleted_at"])
    op.create_index(
        "uq_subnets_zone_cidr_active",
        "subnets",
        ["network_zone_id", "cidr"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "clusters",
        sa.Column("id", pk_type, autoincrement=True, nullable=False),
        sa.Column("group_id", sa.BigInteger(), nullable=True),
        sa.Column("network_zone_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=True),
        sa.Column("cluster_type", sa.String(length=64), nullable=True),
        sa.Column("env", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="in_use"),
        sa.Column("owner", sa.String(length=64), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("extra_attrs", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["group_id"], ["ops_groups.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["network_zone_id"], ["network_zones.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_clusters"),
    )
    op.create_index("ix_clusters_group_id", "clusters", ["group_id"])
    op.create_index("ix_clusters_deleted_at", "clusters", ["deleted_at"])
    op.create_index(
        "uq_clusters_name_env_active",
        "clusters",
        ["name", "env"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "devices",
        sa.Column("id", pk_type, autoincrement=True, nullable=False),
        sa.Column("group_id", sa.BigInteger(), nullable=True),
        sa.Column("network_zone_id", sa.BigInteger(), nullable=True),
        sa.Column("hostname", sa.String(length=128), nullable=False),
        sa.Column("asset_code", sa.String(length=64), nullable=True),
        sa.Column("serial_number", sa.String(length=128), nullable=True),
        sa.Column("device_type", sa.String(length=32), nullable=False, server_default="physical"),
        sa.Column("env", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="in_use"),
        sa.Column("os_info", sa.String(length=128), nullable=True),
        sa.Column("spec_summary", sa.String(length=128), nullable=True),
        sa.Column("rack_location", sa.String(length=128), nullable=True),
        sa.Column("owner", sa.String(length=64), nullable=True),
        sa.Column("health_status", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("extra_attrs", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["group_id"], ["ops_groups.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["network_zone_id"], ["network_zones.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_devices"),
    )
    op.create_index("ix_devices_group_id", "devices", ["group_id"])
    op.create_index("ix_devices_hostname", "devices", ["hostname"])
    op.create_index("ix_devices_deleted_at", "devices", ["deleted_at"])
    op.create_index(
        "uq_devices_asset_code_active",
        "devices",
        ["asset_code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND asset_code IS NOT NULL"),
    )
    op.create_index(
        "uq_devices_serial_number_active",
        "devices",
        ["serial_number"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND serial_number IS NOT NULL"),
    )

    op.create_table(
        "cluster_devices",
        sa.Column("cluster_id", sa.BigInteger(), nullable=False),
        sa.Column("device_id", sa.BigInteger(), nullable=False),
        sa.Column("node_role", sa.String(length=64), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["cluster_id"], ["clusters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("cluster_id", "device_id", name="pk_cluster_devices"),
    )

    op.create_table(
        "nics",
        sa.Column("id", pk_type, autoincrement=True, nullable=False),
        sa.Column("device_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_nic_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("nic_type", sa.String(length=32), nullable=False, server_default="business"),
        sa.Column("mac_address", mac_type, nullable=True),
        sa.Column("speed_mbps", sa.Integer(), nullable=True),
        sa.Column("vlan_id", sa.Integer(), nullable=True),
        sa.Column("bond_mode", sa.String(length=32), nullable=True),
        sa.Column("switch_port", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="up"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("extra_attrs", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["parent_nic_id"], ["nics.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_nics"),
    )
    op.create_index("ix_nics_device_id", "nics", ["device_id"])
    op.create_index("ix_nics_parent_nic_id", "nics", ["parent_nic_id"])
    op.create_index("ix_nics_mac_address", "nics", ["mac_address"])
    op.create_index("ix_nics_deleted_at", "nics", ["deleted_at"])
    op.create_index(
        "uq_nics_device_name_active",
        "nics",
        ["device_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "ip_addresses",
        sa.Column("id", pk_type, autoincrement=True, nullable=False),
        sa.Column("network_zone_id", sa.BigInteger(), nullable=False),
        sa.Column("subnet_id", sa.BigInteger(), nullable=True),
        sa.Column("nic_id", sa.BigInteger(), nullable=True),
        sa.Column("group_id", sa.BigInteger(), nullable=True),
        sa.Column("ip_address", inet_type, nullable=False),
        sa.Column("ip_type", sa.String(length=32), nullable=False, server_default="host"),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="allocated"),
        sa.Column("usage", sa.String(length=128), nullable=True),
        sa.Column("health_status", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("extra_attrs", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["network_zone_id"], ["network_zones.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["subnet_id"], ["subnets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["nic_id"], ["nics.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["group_id"], ["ops_groups.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_ip_addresses"),
    )
    op.create_index("ix_ip_addresses_network_zone_id", "ip_addresses", ["network_zone_id"])
    op.create_index("ix_ip_addresses_subnet_id", "ip_addresses", ["subnet_id"])
    op.create_index("ix_ip_addresses_nic_id", "ip_addresses", ["nic_id"])
    op.create_index("ix_ip_addresses_group_id", "ip_addresses", ["group_id"])
    op.create_index("ix_ip_addresses_ip_address", "ip_addresses", ["ip_address"])
    op.create_index("ix_ip_addresses_deleted_at", "ip_addresses", ["deleted_at"])
    op.create_index(
        "uq_ip_addresses_zone_ip_active",
        "ip_addresses",
        ["network_zone_id", "ip_address"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "ip_vip_bindings",
        sa.Column("id", pk_type, autoincrement=True, nullable=False),
        sa.Column("ip_id", sa.BigInteger(), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=True),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["ip_id"], ["ip_addresses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_ip_vip_bindings"),
    )
    op.create_index("uq_ip_vip_bindings_unique", "ip_vip_bindings", ["ip_id", "target_type", "target_id"], unique=True)
    op.create_index("ix_ip_vip_bindings_target", "ip_vip_bindings", ["target_type", "target_id"])

    op.create_table(
        "services",
        sa.Column("id", pk_type, autoincrement=True, nullable=False),
        sa.Column("group_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=True),
        sa.Column("service_type", sa.String(length=32), nullable=False, server_default="application"),
        sa.Column("env", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="in_use"),
        sa.Column("version", sa.String(length=64), nullable=True),
        sa.Column("owner", sa.String(length=64), nullable=True),
        sa.Column("repo_or_doc_url", sa.String(length=512), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("extra_attrs", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["group_id"], ["ops_groups.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_services"),
    )
    op.create_index("ix_services_group_id", "services", ["group_id"])
    op.create_index("ix_services_name", "services", ["name"])
    op.create_index("ix_services_deleted_at", "services", ["deleted_at"])
    op.create_index(
        "uq_services_name_env_active",
        "services",
        ["name", "env"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "service_endpoints",
        sa.Column("id", pk_type, autoincrement=True, nullable=False),
        sa.Column("service_id", sa.BigInteger(), nullable=False),
        sa.Column("cluster_id", sa.BigInteger(), nullable=True),
        sa.Column("device_id", sa.BigInteger(), nullable=True),
        sa.Column("ip_id", sa.BigInteger(), nullable=True),
        sa.Column("listen_ip_text", sa.String(length=64), nullable=True),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("protocol", sa.String(length=16), nullable=False, server_default="tcp"),
        sa.Column("endpoint_role", sa.String(length=32), nullable=True),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cluster_id"], ["clusters.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ip_id"], ["ip_addresses.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_service_endpoints"),
    )
    op.create_index("ix_service_endpoints_service_id", "service_endpoints", ["service_id"])
    op.create_index("ix_service_endpoints_cluster_id", "service_endpoints", ["cluster_id"])
    op.create_index("ix_service_endpoints_device_id", "service_endpoints", ["device_id"])
    op.create_index("ix_service_endpoints_ip_port", "service_endpoints", ["ip_id", "port"])

    op.create_table(
        "urls",
        sa.Column("id", pk_type, autoincrement=True, nullable=False),
        sa.Column("group_id", sa.BigInteger(), nullable=True),
        sa.Column("service_id", sa.BigInteger(), nullable=True),
        sa.Column("entry_ip_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("full_url", sa.String(length=1024), nullable=False),
        sa.Column("protocol", sa.String(length=16), nullable=False, server_default="https"),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("path", sa.String(length=512), nullable=True),
        sa.Column("access_type", sa.String(length=32), nullable=False, server_default="internal"),
        sa.Column("env", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="in_use"),
        sa.Column("is_https", sa.Boolean(), nullable=False, default=True, server_default=sa.true()),
        sa.Column("ssl_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("health_status", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("extra_attrs", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["group_id"], ["ops_groups.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["entry_ip_id"], ["ip_addresses.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_urls"),
    )
    op.create_index("ix_urls_group_id", "urls", ["group_id"])
    op.create_index("ix_urls_service_id", "urls", ["service_id"])
    op.create_index("ix_urls_entry_ip_id", "urls", ["entry_ip_id"])
    op.create_index("ix_urls_domain", "urls", ["domain"])
    op.create_index("ix_urls_ssl_expires_at", "urls", ["ssl_expires_at"])
    op.create_index("ix_urls_deleted_at", "urls", ["deleted_at"])

    if is_pg:
        op.execute("CREATE INDEX ix_subnets_cidr_gist ON subnets USING gist (cidr inet_ops)")
        op.execute("CREATE INDEX ix_devices_hostname_trgm ON devices USING gin (hostname gin_trgm_ops)")
        op.execute("CREATE INDEX ix_urls_full_url_trgm ON urls USING gin (full_url gin_trgm_ops)")


def downgrade() -> None:
    op.drop_table("urls")
    op.drop_table("service_endpoints")
    op.drop_table("services")
    op.drop_table("ip_vip_bindings")
    op.drop_table("ip_addresses")
    op.drop_table("nics")
    op.drop_table("cluster_devices")
    op.drop_table("devices")
    op.drop_table("clusters")
    op.drop_table("subnets")
