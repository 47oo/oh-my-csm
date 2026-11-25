"""Idempotent database seeder for built-in roles, default zone, dictionaries, and initial admin."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.permissions import BUILTIN_ROLES
from app.core.security import hash_password
from app.models.auth import Role, User, UserGroupRole
from app.models.system import DictItem, NetworkZone

BUILTIN_DICT_ITEMS: list[dict[str, object]] = [
    # device_type
    {"category": "device_type", "code": "physical", "label": "物理服务器", "color": "#2080f0", "sort_order": 10},
    {"category": "device_type", "code": "vm", "label": "虚拟机", "color": "#18a058", "sort_order": 20},
    {"category": "device_type", "code": "cloud_host", "label": "云主机", "color": "#0ea5e9", "sort_order": 30},
    {"category": "device_type", "code": "switch", "label": "交换机", "color": "#f0a020", "sort_order": 40},
    {"category": "device_type", "code": "router", "label": "路由器", "color": "#d97706", "sort_order": 50},
    {"category": "device_type", "code": "firewall", "label": "防火墙", "color": "#d03050", "sort_order": 60},
    {"category": "device_type", "code": "storage", "label": "存储设备", "color": "#8b5cf6", "sort_order": 70},
    {"category": "device_type", "code": "other", "label": "其他设备", "color": "#64748b", "sort_order": 99},
    # env
    {"category": "env", "code": "prod", "label": "生产", "color": "#d03050", "sort_order": 10},
    {"category": "env", "code": "staging", "label": "预发", "color": "#f0a020", "sort_order": 20},
    {"category": "env", "code": "test", "label": "测试", "color": "#2080f0", "sort_order": 30},
    {"category": "env", "code": "dev", "label": "开发", "color": "#18a058", "sort_order": 40},
    {"category": "env", "code": "dr", "label": "灾备", "color": "#8b5cf6", "sort_order": 50},
    # asset_status
    {"category": "asset_status", "code": "in_use", "label": "在用", "color": "#18a058", "sort_order": 10},
    {"category": "asset_status", "code": "standby", "label": "备用", "color": "#2080f0", "sort_order": 20},
    {"category": "asset_status", "code": "maintenance", "label": "维护中", "color": "#f0a020", "sort_order": 30},
    {"category": "asset_status", "code": "decommissioning", "label": "待下线", "color": "#d97706", "sort_order": 40},
    {"category": "asset_status", "code": "offline", "label": "已下线", "color": "#64748b", "sort_order": 50},
    # nic_type
    {"category": "nic_type", "code": "business", "label": "物理业务口", "color": "#2080f0", "sort_order": 10},
    {"category": "nic_type", "code": "ipmi", "label": "管理口 (IPMI/iDRAC)", "color": "#f0a020", "sort_order": 20},
    {"category": "nic_type", "code": "bond", "label": "Bond 聚合口", "color": "#8b5cf6", "sort_order": 30},
    {"category": "nic_type", "code": "virtual", "label": "虚拟网卡", "color": "#18a058", "sort_order": 40},
    # ip_status
    {"category": "ip_status", "code": "free", "label": "空闲", "color": "#18a058", "sort_order": 10},
    {"category": "ip_status", "code": "allocated", "label": "已分配", "color": "#2080f0", "sort_order": 20},
    {"category": "ip_status", "code": "reserved", "label": "预留", "color": "#f0a020", "sort_order": 30},
    {"category": "ip_status", "code": "conflict", "label": "冲突异常", "color": "#d03050", "sort_order": 40},
    {"category": "ip_status", "code": "deprecated", "label": "已废弃", "color": "#64748b", "sort_order": 50},
    # service_type
    {"category": "service_type", "code": "application", "label": "业务应用", "color": "#2080f0", "sort_order": 10},
    {"category": "service_type", "code": "database", "label": "数据库", "color": "#8b5cf6", "sort_order": 20},
    {"category": "service_type", "code": "middleware", "label": "中间件", "color": "#f0a020", "sort_order": 30},
    {"category": "service_type", "code": "gateway", "label": "网关/反代", "color": "#18a058", "sort_order": 40},
    {"category": "service_type", "code": "infra", "label": "基础组件", "color": "#64748b", "sort_order": 50},
    # protocol
    {"category": "protocol", "code": "http", "label": "HTTP", "color": "#2080f0", "sort_order": 10},
    {"category": "protocol", "code": "https", "label": "HTTPS", "color": "#18a058", "sort_order": 20},
    {"category": "protocol", "code": "tcp", "label": "TCP", "color": "#64748b", "sort_order": 30},
    {"category": "protocol", "code": "udp", "label": "UDP", "color": "#f0a020", "sort_order": 40},
    {"category": "protocol", "code": "grpc", "label": "gRPC", "color": "#8b5cf6", "sort_order": 50},
]


async def seed_all(db: AsyncSession, force_reset_admin_password: bool = False) -> dict[str, int]:
    """Idempotently seed built-in roles, default network zone, dictionaries, and initial admin."""
    stats = {"roles": 0, "zones": 0, "dicts": 0, "admins": 0}

    # 1. Roles
    role_map: dict[str, Role] = {}
    for role_def in BUILTIN_ROLES:
        role = (
            await db.execute(select(Role).where(Role.code == role_def["code"]))
        ).scalar_one_or_none()
        if role is None:
            role = Role(
                code=role_def["code"],
                name=role_def["name"],
                description=role_def["description"],
                is_builtin=True,
                permissions=list(role_def["permissions"]),
            )
            db.add(role)
            stats["roles"] += 1
        else:
            role.permissions = list(role_def["permissions"])
        await db.flush()
        role_map[role.code] = role

    # 2. Default NetworkZone
    default_zone = (
        await db.execute(
            select(NetworkZone).where(
                NetworkZone.code == "default",
                NetworkZone.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if default_zone is None:
        db.add(
            NetworkZone(
                code="default",
                name="默认网络域",
                location="主数据中心",
                is_default=True,
                description="系统内置默认网络域，未启用多网络隔离域时自动归属本域",
            )
        )
        stats["zones"] += 1

    # 3. Built-in Dictionary Items
    for item in BUILTIN_DICT_ITEMS:
        existing = (
            await db.execute(
                select(DictItem).where(
                    DictItem.category == item["category"],
                    DictItem.code == item["code"],
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                DictItem(
                    category=str(item["category"]),
                    code=str(item["code"]),
                    label=str(item["label"]),
                    color=str(item["color"]),
                    sort_order=int(item["sort_order"]),
                    is_builtin=True,
                    is_enabled=True,
                )
            )
            stats["dicts"] += 1

    # 4. Initial SuperAdmin user
    settings = get_settings()
    admin = (
        await db.execute(
            select(User).where(
                User.username == settings.initial_admin_username,
                User.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()

    if admin is None:
        admin = User(
            username=settings.initial_admin_username,
            display_name=settings.initial_admin_display_name,
            password_hash=hash_password(settings.initial_admin_password),
            auth_source="local",
            is_active=True,
            must_change_password=True,
        )
        db.add(admin)
        await db.flush()
        stats["admins"] += 1
    elif force_reset_admin_password:
        admin.password_hash = hash_password(settings.initial_admin_password)
        admin.must_change_password = True
        admin.failed_login_attempts = 0
        admin.locked_until = None
        admin.is_active = True

    super_role = role_map["super_admin"]
    existing_binding = (
        await db.execute(
            select(UserGroupRole).where(
                UserGroupRole.user_id == admin.id,
                UserGroupRole.group_id.is_(None),
                UserGroupRole.role_id == super_role.id,
            )
        )
    ).scalar_one_or_none()
    if existing_binding is None:
        db.add(UserGroupRole(user_id=admin.id, group_id=None, role_id=super_role.id))

    await db.flush()
    return stats
