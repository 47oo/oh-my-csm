"""Core M2 asset & network topology models: Subnet, Cluster, Device, NIC, IP, VIP, Service, URL."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import BigIntPK, TimestampSoftDeleteMixin, utcnow
from app.models.types import CidrType, InetType, MacAddrType


class Subnet(TimestampSoftDeleteMixin, Base):
    __tablename__ = "subnets"

    network_zone_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("network_zones.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    group_id: Mapped[int | None] = mapped_column(
        BigIntPK, ForeignKey("ops_groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    cidr: Mapped[str] = mapped_column(CidrType, nullable=False)
    gateway: Mapped[str | None] = mapped_column(InetType, nullable=True)
    vlan_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    env: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="in_use")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            "uq_subnets_zone_cidr_active",
            "network_zone_id",
            "cidr",
            unique=True,
            postgresql_where=TimestampSoftDeleteMixin.deleted_at.is_(None),
        ),
    )


class Cluster(TimestampSoftDeleteMixin, Base):
    __tablename__ = "clusters"

    group_id: Mapped[int | None] = mapped_column(
        BigIntPK, ForeignKey("ops_groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    network_zone_id: Mapped[int | None] = mapped_column(
        BigIntPK, ForeignKey("network_zones.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cluster_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    env: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="in_use")
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    device_links: Mapped[list[ClusterDevice]] = relationship(
        "ClusterDevice",
        back_populates="cluster",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index(
            "uq_clusters_name_env_active",
            "name",
            "env",
            unique=True,
            postgresql_where=TimestampSoftDeleteMixin.deleted_at.is_(None),
        ),
    )


class Device(TimestampSoftDeleteMixin, Base):
    __tablename__ = "devices"

    group_id: Mapped[int | None] = mapped_column(
        BigIntPK, ForeignKey("ops_groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    network_zone_id: Mapped[int | None] = mapped_column(
        BigIntPK, ForeignKey("network_zones.id", ondelete="SET NULL"), nullable=True, index=True
    )
    hostname: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    asset_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    device_type: Mapped[str] = mapped_column(String(32), nullable=False, default="physical")
    env: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="in_use")
    os_info: Mapped[str | None] = mapped_column(String(128), nullable=True)
    spec_summary: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rack_location: Mapped[str | None] = mapped_column(String(128), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    health_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    cluster_links: Mapped[list[ClusterDevice]] = relationship(
        "ClusterDevice",
        back_populates="device",
        cascade="all, delete-orphan",
    )
    nics: Mapped[list[NIC]] = relationship("NIC", back_populates="device")

    __table_args__ = (
        Index(
            "uq_devices_asset_code_active",
            "asset_code",
            unique=True,
            postgresql_where=(
                TimestampSoftDeleteMixin.deleted_at.is_(None) & asset_code.is_not(None)
            ),
        ),
        Index(
            "uq_devices_serial_number_active",
            "serial_number",
            unique=True,
            postgresql_where=(
                TimestampSoftDeleteMixin.deleted_at.is_(None) & serial_number.is_not(None)
            ),
        ),
    )


class ClusterDevice(Base):
    __tablename__ = "cluster_devices"

    cluster_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("clusters.id", ondelete="CASCADE"), primary_key=True
    )
    device_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    node_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    cluster: Mapped[Cluster] = relationship("Cluster", back_populates="device_links")
    device: Mapped[Device] = relationship("Device", back_populates="cluster_links")


class NIC(TimestampSoftDeleteMixin, Base):
    __tablename__ = "nics"

    device_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("devices.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    parent_nic_id: Mapped[int | None] = mapped_column(
        BigIntPK, ForeignKey("nics.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    nic_type: Mapped[str] = mapped_column(String(32), nullable=False, default="business")
    mac_address: Mapped[str | None] = mapped_column(MacAddrType, nullable=True, index=True)
    speed_mbps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vlan_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bond_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    switch_port: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="up")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    device: Mapped[Device] = relationship("Device", back_populates="nics")
    ips: Mapped[list[IPAddress]] = relationship("IPAddress", back_populates="nic")

    __table_args__ = (
        Index(
            "uq_nics_device_name_active",
            "device_id",
            "name",
            unique=True,
            postgresql_where=TimestampSoftDeleteMixin.deleted_at.is_(None),
        ),
    )


class IPAddress(TimestampSoftDeleteMixin, Base):
    __tablename__ = "ip_addresses"

    network_zone_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("network_zones.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    subnet_id: Mapped[int | None] = mapped_column(
        BigIntPK, ForeignKey("subnets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    nic_id: Mapped[int | None] = mapped_column(
        BigIntPK, ForeignKey("nics.id", ondelete="SET NULL"), nullable=True, index=True
    )
    group_id: Mapped[int | None] = mapped_column(
        BigIntPK, ForeignKey("ops_groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    ip_address: Mapped[str] = mapped_column(InetType, nullable=False, index=True)
    ip_type: Mapped[str] = mapped_column(String(32), nullable=False, default="host")
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="allocated")
    usage: Mapped[str | None] = mapped_column(String(128), nullable=True)
    health_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    nic: Mapped[NIC | None] = relationship("NIC", back_populates="ips")
    vip_bindings: Mapped[list[IPVIPBinding]] = relationship(
        "IPVIPBinding",
        back_populates="ip",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index(
            "uq_ip_addresses_zone_ip_active",
            "network_zone_id",
            "ip_address",
            unique=True,
            postgresql_where=TimestampSoftDeleteMixin.deleted_at.is_(None),
        ),
    )


class IPVIPBinding(Base):
    __tablename__ = "ip_vip_bindings"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    ip_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("ip_addresses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)  # nic | cluster | service
    target_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)  # master | backup | member
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    ip: Mapped[IPAddress] = relationship("IPAddress", back_populates="vip_bindings")

    __table_args__ = (
        Index(
            "uq_ip_vip_bindings_unique",
            "ip_id",
            "target_type",
            "target_id",
            unique=True,
        ),
        Index("ix_ip_vip_bindings_target", "target_type", "target_id"),
    )


class Service(TimestampSoftDeleteMixin, Base):
    __tablename__ = "services"

    group_id: Mapped[int | None] = mapped_column(
        BigIntPK, ForeignKey("ops_groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    service_type: Mapped[str] = mapped_column(String(32), nullable=False, default="application")
    env: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="in_use")
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    repo_or_doc_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    endpoints: Mapped[list[ServiceEndpoint]] = relationship(
        "ServiceEndpoint",
        back_populates="service",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index(
            "uq_services_name_env_active",
            "name",
            "env",
            unique=True,
            postgresql_where=TimestampSoftDeleteMixin.deleted_at.is_(None),
        ),
    )


class ServiceEndpoint(Base):
    __tablename__ = "service_endpoints"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    service_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cluster_id: Mapped[int | None] = mapped_column(
        BigIntPK, ForeignKey("clusters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    device_id: Mapped[int | None] = mapped_column(
        BigIntPK, ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    ip_id: Mapped[int | None] = mapped_column(
        BigIntPK, ForeignKey("ip_addresses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    listen_ip_text: Mapped[str | None] = mapped_column(String(64), nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    protocol: Mapped[str] = mapped_column(String(16), nullable=False, default="tcp")
    endpoint_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=utcnow
    )

    service: Mapped[Service] = relationship("Service", back_populates="endpoints")

    __table_args__ = (Index("ix_service_endpoints_ip_port", "ip_id", "port"),)


class UrlEntry(TimestampSoftDeleteMixin, Base):
    __tablename__ = "urls"

    group_id: Mapped[int | None] = mapped_column(
        BigIntPK, ForeignKey("ops_groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    service_id: Mapped[int | None] = mapped_column(
        BigIntPK, ForeignKey("services.id", ondelete="SET NULL"), nullable=True, index=True
    )
    entry_ip_id: Mapped[int | None] = mapped_column(
        BigIntPK, ForeignKey("ip_addresses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    full_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    protocol: Mapped[str] = mapped_column(String(16), nullable=False, default="https")
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    access_type: Mapped[str] = mapped_column(String(32), nullable=False, default="internal")
    env: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="in_use")
    is_https: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ssl_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    health_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
