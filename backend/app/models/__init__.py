"""Export all SQLAlchemy models for Alembic metadata discovery."""

from app.models.assets import (
    NIC,
    Cluster,
    ClusterDevice,
    Device,
    IPAddress,
    IPVIPBinding,
    Service,
    ServiceEndpoint,
    Subnet,
    UrlEntry,
)
from app.models.audit import AuditLog, ImportBatch
from app.models.auth import ApiToken, ClusterACL, RevokedToken, Role, User, UserGroupRole
from app.models.credentials import Credential
from app.models.system import DictItem, EntityTag, NetworkZone, OpsGroup, Tag

__all__ = [
    "NIC",
    "ApiToken",
    "AuditLog",
    "Cluster",
    "ClusterACL",
    "ClusterDevice",
    "Credential",
    "Device",
    "DictItem",
    "EntityTag",
    "IPAddress",
    "IPVIPBinding",
    "ImportBatch",
    "NetworkZone",
    "OpsGroup",
    "RevokedToken",
    "Role",
    "Service",
    "ServiceEndpoint",
    "Subnet",
    "Tag",
    "UrlEntry",
    "User",
    "UserGroupRole",
]
