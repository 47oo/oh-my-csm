"""Pydantic v2 schemas for M2 assets, IPAM matrix, endpoints, VIP bindings, and panoramic details."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.system import AuditLogOut


class TagBrief(BaseModel):
    id: int
    name: str
    color: str | None = None


class RelatedEntityCard(BaseModel):
    """Unified card for panoramic upstream/downstream drawer with cross-group redaction support."""

    entity_type: Literal["cluster", "device", "nic", "subnet", "ip", "service", "url"]
    id: int
    title: str
    subtitle: str | None = None
    status: str | None = None
    group_name: str | None = None
    role: str | None = None
    extra_badge: str | None = None
    is_cross_group_summary: bool = False


# ---------------- Subnet / IPAM ----------------


class SubnetCreate(BaseModel):
    network_zone_id: int | None = None
    group_id: int | None = None
    name: str = Field(..., min_length=1, max_length=128)
    cidr: str = Field(..., min_length=3, max_length=64)
    gateway: str | None = None
    vlan_id: int | None = Field(default=None, ge=1, le=4094)
    env: str | None = None
    status: str = "in_use"
    description: str | None = None
    extra_attrs: dict[str, Any] = Field(default_factory=dict)
    tag_ids: list[int] | None = None


class SubnetUpdate(BaseModel):
    group_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=128)
    gateway: str | None = None
    vlan_id: int | None = Field(default=None, ge=1, le=4094)
    env: str | None = None
    status: str | None = None
    description: str | None = None
    extra_attrs: dict[str, Any] | None = None
    tag_ids: list[int] | None = None


class SubnetOut(BaseModel):
    id: int
    network_zone_id: int
    network_zone_name: str | None = None
    group_id: int | None = None
    group_name: str | None = None
    name: str
    cidr: str
    gateway: str | None = None
    vlan_id: int | None = None
    env: str | None = None
    status: str
    description: str | None = None
    extra_attrs: dict[str, Any] = Field(default_factory=dict)
    tags: list[TagBrief] = Field(default_factory=list)
    total_capacity: int = 0
    allocated_count: int = 0
    utilization_pct: float = 0.0
    can_render_matrix: bool = True
    created_at: datetime
    updated_at: datetime


class SubnetIpCellOut(BaseModel):
    ip: str
    status: str  # free | allocated | reserved | conflict | deprecated | gateway
    ip_type: str | None = None
    ip_id: int | None = None
    nic_id: int | None = None
    device_id: int | None = None
    device_hostname: str | None = None
    usage: str | None = None
    is_gateway: bool = False


class SubnetMatrixOut(BaseModel):
    subnet: SubnetOut
    mode: Literal["matrix", "list"]
    stats: dict[str, int]
    cells: list[SubnetIpCellOut] = Field(default_factory=list)
    allocated_items: list[IpOut] = Field(default_factory=list)
    total_allocated: int = 0
    page: int = 1
    page_size: int = 50


class NextFreeIpOut(BaseModel):
    subnet_id: int
    cidr: str
    next_free_ip: str | None


# ---------------- Cluster ----------------


class ClusterCreate(BaseModel):
    group_id: int | None = None
    network_zone_id: int | None = None
    name: str = Field(..., min_length=1, max_length=128)
    code: str | None = Field(default=None, max_length=64)
    cluster_type: str | None = Field(default=None, max_length=64)
    env: str | None = None
    status: str = "in_use"
    owner: str | None = Field(default=None, max_length=64)
    description: str | None = None
    extra_attrs: dict[str, Any] = Field(default_factory=dict)
    tag_ids: list[int] | None = None


class ClusterUpdate(BaseModel):
    group_id: int | None = None
    network_zone_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=128)
    code: str | None = None
    cluster_type: str | None = None
    env: str | None = None
    status: str | None = None
    owner: str | None = None
    description: str | None = None
    extra_attrs: dict[str, Any] | None = None
    tag_ids: list[int] | None = None


class ClusterOut(BaseModel):
    id: int
    group_id: int | None = None
    group_name: str | None = None
    network_zone_id: int | None = None
    network_zone_name: str | None = None
    name: str
    code: str | None = None
    cluster_type: str | None = None
    env: str | None = None
    status: str
    owner: str | None = None
    description: str | None = None
    extra_attrs: dict[str, Any] = Field(default_factory=dict)
    tags: list[TagBrief] = Field(default_factory=list)
    device_count: int = 0
    service_count: int = 0
    created_at: datetime
    updated_at: datetime


class ClusterDeviceLinkRequest(BaseModel):
    device_id: int
    node_role: str | None = Field(default=None, max_length=64)


class ClusterMemberAssignRequest(BaseModel):
    user_id: int
    role_id: int


class ClusterMemberOut(BaseModel):
    acl_id: int
    user_id: int
    username: str
    display_name: str
    email: str | None
    is_active: bool
    role_id: int
    role_code: str
    role_name: str


class ClusterDetailOut(ClusterOut):
    devices: list[RelatedEntityCard] = Field(default_factory=list)
    vips: list[RelatedEntityCard] = Field(default_factory=list)
    services: list[RelatedEntityCard] = Field(default_factory=list)
    recent_audits: list[AuditLogOut] = Field(default_factory=list)


# ---------------- Device ----------------


class DeviceCreate(BaseModel):
    group_id: int | None = None
    network_zone_id: int | None = None
    hostname: str = Field(..., min_length=1, max_length=128)
    asset_code: str | None = Field(default=None, max_length=64)
    serial_number: str | None = Field(default=None, max_length=128)
    device_type: str = "physical"
    env: str | None = None
    status: str = "in_use"
    os_info: str | None = Field(default=None, max_length=128)
    spec_summary: str | None = Field(default=None, max_length=128)
    rack_location: str | None = Field(default=None, max_length=128)
    owner: str | None = Field(default=None, max_length=64)
    description: str | None = None
    extra_attrs: dict[str, Any] = Field(default_factory=dict)
    tag_ids: list[int] | None = None
    cluster_ids: list[int] | None = None


class DeviceUpdate(BaseModel):
    group_id: int | None = None
    network_zone_id: int | None = None
    hostname: str | None = Field(default=None, min_length=1, max_length=128)
    asset_code: str | None = None
    serial_number: str | None = None
    device_type: str | None = None
    env: str | None = None
    status: str | None = None
    os_info: str | None = None
    spec_summary: str | None = None
    rack_location: str | None = None
    owner: str | None = None
    description: str | None = None
    extra_attrs: dict[str, Any] | None = None
    tag_ids: list[int] | None = None
    cluster_ids: list[int] | None = None


class DeviceOut(BaseModel):
    id: int
    group_id: int | None = None
    group_name: str | None = None
    network_zone_id: int | None = None
    network_zone_name: str | None = None
    hostname: str
    asset_code: str | None = None
    serial_number: str | None = None
    device_type: str
    env: str | None = None
    status: str
    os_info: str | None = None
    spec_summary: str | None = None
    rack_location: str | None = None
    owner: str | None = None
    health_status: str = "unknown"
    last_checked_at: datetime | None = None
    description: str | None = None
    extra_attrs: dict[str, Any] = Field(default_factory=dict)
    tags: list[TagBrief] = Field(default_factory=list)
    cluster_ids: list[int] = Field(default_factory=list)
    cluster_names: list[str] = Field(default_factory=list)
    nic_count: int = 0
    primary_ips: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class DeviceDetailOut(DeviceOut):
    clusters: list[RelatedEntityCard] = Field(default_factory=list)
    nics: list[NicOut] = Field(default_factory=list)
    ips: list[RelatedEntityCard] = Field(default_factory=list)
    services: list[RelatedEntityCard] = Field(default_factory=list)
    recent_audits: list[AuditLogOut] = Field(default_factory=list)


# ---------------- NIC ----------------


class NicCreate(BaseModel):
    device_id: int
    parent_nic_id: int | None = None
    name: str = Field(..., min_length=1, max_length=64)
    nic_type: str = "business"
    mac_address: str | None = None
    speed_mbps: int | None = Field(default=None, ge=1)
    vlan_id: int | None = Field(default=None, ge=1, le=4094)
    bond_mode: str | None = Field(default=None, max_length=32)
    switch_port: str | None = Field(default=None, max_length=128)
    status: str = "up"
    description: str | None = None
    extra_attrs: dict[str, Any] = Field(default_factory=dict)
    tag_ids: list[int] | None = None


class NicUpdate(BaseModel):
    parent_nic_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=64)
    nic_type: str | None = None
    mac_address: str | None = None
    speed_mbps: int | None = Field(default=None, ge=1)
    vlan_id: int | None = Field(default=None, ge=1, le=4094)
    bond_mode: str | None = None
    switch_port: str | None = None
    status: str | None = None
    description: str | None = None
    extra_attrs: dict[str, Any] | None = None
    tag_ids: list[int] | None = None


class NicOut(BaseModel):
    id: int
    device_id: int
    device_hostname: str | None = None
    group_id: int | None = None
    parent_nic_id: int | None = None
    parent_nic_name: str | None = None
    name: str
    nic_type: str
    mac_address: str | None = None
    speed_mbps: int | None = None
    vlan_id: int | None = None
    bond_mode: str | None = None
    switch_port: str | None = None
    status: str
    description: str | None = None
    extra_attrs: dict[str, Any] = Field(default_factory=dict)
    tags: list[TagBrief] = Field(default_factory=list)
    bound_ips: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class NicDetailOut(NicOut):
    device: RelatedEntityCard | None = None
    parent_nic: RelatedEntityCard | None = None
    child_nics: list[RelatedEntityCard] = Field(default_factory=list)
    ips: list[RelatedEntityCard] = Field(default_factory=list)
    vip_ips: list[RelatedEntityCard] = Field(default_factory=list)
    recent_audits: list[AuditLogOut] = Field(default_factory=list)


# ---------------- IP Address & VIP Binding ----------------


class IpCreate(BaseModel):
    network_zone_id: int | None = None
    subnet_id: int | None = None
    nic_id: int | None = None
    cluster_id: int | None = None  # Optional: auto-bind as cluster VIP on create
    group_id: int | None = None
    ip_address: str | None = Field(default=None, max_length=64)
    ip_type: Literal["host", "vip", "gateway", "floating", "reserved"] = "host"
    is_primary: bool = False
    status: str = "allocated"
    usage: str | None = Field(default=None, max_length=128)
    description: str | None = None
    extra_attrs: dict[str, Any] = Field(default_factory=dict)
    tag_ids: list[int] | None = None


class IpUpdate(BaseModel):
    subnet_id: int | None = None
    nic_id: int | None = None
    clear_nic: bool = False
    group_id: int | None = None
    ip_type: Literal["host", "vip", "gateway", "floating", "reserved"] | None = None
    is_primary: bool | None = None
    status: str | None = None
    usage: str | None = None
    description: str | None = None
    extra_attrs: dict[str, Any] | None = None
    tag_ids: list[int] | None = None


class VipBindingCreate(BaseModel):
    target_type: Literal["nic", "cluster", "service"]
    target_id: int
    role: str | None = Field(default="member", max_length=32)
    description: str | None = Field(default=None, max_length=255)


class VipBindingOut(BaseModel):
    id: int
    ip_id: int
    target_type: str
    target_id: int
    target_label: str
    role: str | None = None
    description: str | None = None
    created_at: datetime


class IpOut(BaseModel):
    id: int
    network_zone_id: int
    network_zone_name: str | None = None
    subnet_id: int | None = None
    subnet_cidr: str | None = None
    nic_id: int | None = None
    nic_name: str | None = None
    device_id: int | None = None
    device_hostname: str | None = None
    group_id: int | None = None
    group_name: str | None = None
    ip_address: str
    ip_type: str
    is_primary: bool
    status: str
    usage: str | None = None
    health_status: str = "unknown"
    last_checked_at: datetime | None = None
    description: str | None = None
    extra_attrs: dict[str, Any] = Field(default_factory=dict)
    tags: list[TagBrief] = Field(default_factory=list)
    vip_binding_count: int = 0
    created_at: datetime
    updated_at: datetime


class IpDetailOut(IpOut):
    subnet: RelatedEntityCard | None = None
    nic: RelatedEntityCard | None = None
    device: RelatedEntityCard | None = None
    vip_bindings: list[VipBindingOut] = Field(default_factory=list)
    services: list[RelatedEntityCard] = Field(default_factory=list)
    urls: list[RelatedEntityCard] = Field(default_factory=list)
    recent_audits: list[AuditLogOut] = Field(default_factory=list)


# ---------------- Service & Endpoint ----------------


class ServiceEndpointCreate(BaseModel):
    cluster_id: int | None = None
    device_id: int | None = None
    ip_id: int | None = None
    listen_ip_text: str | None = Field(default=None, max_length=64)
    port: int | None = Field(default=None, ge=1, le=65535)
    protocol: str = "tcp"
    endpoint_role: str | None = Field(default=None, max_length=32)
    description: str | None = Field(default=None, max_length=255)


class ServiceEndpointUpdate(BaseModel):
    cluster_id: int | None = None
    device_id: int | None = None
    ip_id: int | None = None
    listen_ip_text: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    protocol: str | None = None
    endpoint_role: str | None = None
    description: str | None = None


class ServiceEndpointOut(BaseModel):
    id: int
    service_id: int
    cluster_id: int | None = None
    cluster_name: str | None = None
    device_id: int | None = None
    device_hostname: str | None = None
    ip_id: int | None = None
    ip_address: str | None = None
    listen_ip_text: str | None = None
    port: int | None = None
    protocol: str
    endpoint_role: str | None = None
    description: str | None = None
    is_cross_group_summary: bool = False


class ServiceCreate(BaseModel):
    group_id: int | None = None
    cluster_ids: list[int] | None = None  # Optional: attach initial hosting clusters on create
    name: str = Field(..., min_length=1, max_length=128)
    code: str | None = Field(default=None, max_length=64)
    service_type: str = "application"
    env: str | None = None
    status: str = "in_use"
    version: str | None = Field(default=None, max_length=64)
    owner: str | None = Field(default=None, max_length=64)
    repo_or_doc_url: str | None = Field(default=None, max_length=512)
    description: str | None = None
    extra_attrs: dict[str, Any] = Field(default_factory=dict)
    tag_ids: list[int] | None = None


class ServiceUpdate(BaseModel):
    group_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=128)
    code: str | None = None
    service_type: str | None = None
    env: str | None = None
    status: str | None = None
    version: str | None = None
    owner: str | None = None
    repo_or_doc_url: str | None = None
    description: str | None = None
    extra_attrs: dict[str, Any] | None = None
    tag_ids: list[int] | None = None


class ServiceOut(BaseModel):
    id: int
    group_id: int | None = None
    group_name: str | None = None
    name: str
    code: str | None = None
    service_type: str
    env: str | None = None
    status: str
    version: str | None = None
    owner: str | None = None
    repo_or_doc_url: str | None = None
    description: str | None = None
    extra_attrs: dict[str, Any] = Field(default_factory=dict)
    tags: list[TagBrief] = Field(default_factory=list)
    endpoint_count: int = 0
    url_count: int = 0
    created_at: datetime
    updated_at: datetime


class ServiceDetailOut(ServiceOut):
    endpoints: list[ServiceEndpointOut] = Field(default_factory=list)
    urls: list[RelatedEntityCard] = Field(default_factory=list)
    clusters: list[RelatedEntityCard] = Field(default_factory=list)
    devices: list[RelatedEntityCard] = Field(default_factory=list)
    ips: list[RelatedEntityCard] = Field(default_factory=list)
    recent_audits: list[AuditLogOut] = Field(default_factory=list)


# ---------------- URL ----------------


class UrlCreate(BaseModel):
    group_id: int | None = None
    service_id: int | None = None
    entry_ip_id: int | None = None
    name: str = Field(..., min_length=1, max_length=128)
    full_url: str = Field(..., min_length=3, max_length=1024)
    access_type: Literal["internal", "external", "vpn"] = "internal"
    env: str | None = None
    status: str = "in_use"
    ssl_expires_at: datetime | None = None
    description: str | None = None
    extra_attrs: dict[str, Any] = Field(default_factory=dict)
    tag_ids: list[int] | None = None


class UrlUpdate(BaseModel):
    group_id: int | None = None
    service_id: int | None = None
    clear_service: bool = False
    entry_ip_id: int | None = None
    clear_entry_ip: bool = False
    name: str | None = Field(default=None, min_length=1, max_length=128)
    full_url: str | None = Field(default=None, min_length=3, max_length=1024)
    access_type: Literal["internal", "external", "vpn"] | None = None
    env: str | None = None
    status: str | None = None
    ssl_expires_at: datetime | None = None
    description: str | None = None
    extra_attrs: dict[str, Any] | None = None
    tag_ids: list[int] | None = None


class UrlOut(BaseModel):
    id: int
    group_id: int | None = None
    group_name: str | None = None
    service_id: int | None = None
    service_name: str | None = None
    entry_ip_id: int | None = None
    entry_ip_address: str | None = None
    name: str
    full_url: str
    protocol: str
    domain: str
    port: int | None = None
    path: str | None = None
    access_type: str
    env: str | None = None
    status: str
    is_https: bool
    ssl_expires_at: datetime | None = None
    health_status: str = "unknown"
    last_checked_at: datetime | None = None
    description: str | None = None
    extra_attrs: dict[str, Any] = Field(default_factory=dict)
    tags: list[TagBrief] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class UrlDetailOut(UrlOut):
    service: RelatedEntityCard | None = None
    entry_ip: RelatedEntityCard | None = None
    clusters: list[RelatedEntityCard] = Field(default_factory=list)
    devices: list[RelatedEntityCard] = Field(default_factory=list)
    nics: list[RelatedEntityCard] = Field(default_factory=list)
    ips: list[RelatedEntityCard] = Field(default_factory=list)
    recent_audits: list[AuditLogOut] = Field(default_factory=list)
