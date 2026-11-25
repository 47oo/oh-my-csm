"""Shared domain helpers for IPAM/CIDR, URL parsing, tag syncing, and cross-group summaries."""

from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import AuthContext
from app.models.assets import IPAddress, Subnet
from app.models.system import EntityTag, NetworkZone, OpsGroup, Tag

MAC_RE = re.compile(r"^([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}$")
MAX_MATRIX_HOSTS = 4096  # <= /20 for IPv4


def normalize_cidr(raw_cidr: str) -> str:
    try:
        net = ipaddress.ip_network(raw_cidr.strip(), strict=False)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无效的 CIDR 网段格式: {raw_cidr}",
        ) from exc
    return str(net)


def normalize_ip(raw_ip: str) -> str:
    try:
        addr = ipaddress.ip_address(raw_ip.strip().split("/")[0])
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无效的 IP 地址格式: {raw_ip}",
        ) from exc
    return str(addr)


def normalize_mac(raw_mac: str | None) -> str | None:
    if raw_mac is None or not raw_mac.strip():
        return None
    cleaned = raw_mac.strip().lower().replace("-", ":")
    if not MAC_RE.match(cleaned):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无效的 MAC 地址格式（示例 aa:bb:cc:dd:ee:ff）: {raw_mac}",
        )
    return cleaned


def parse_full_url(full_url: str) -> dict[str, Any]:
    raw = full_url.strip()
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlsplit(raw)
    if not parsed.hostname:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无法从 URL 解析域名/主机名: {full_url}",
        )
    protocol = (parsed.scheme or "https").lower()
    return {
        "full_url": raw,
        "protocol": protocol,
        "domain": parsed.hostname.lower(),
        "port": parsed.port,
        "path": parsed.path or "/",
        "is_https": protocol in {"https", "wss"},
    }


def subnet_capacity(cidr_str: str) -> int:
    net = ipaddress.ip_network(cidr_str, strict=False)
    if net.version == 4 and net.prefixlen <= 30:
        return max(net.num_addresses - 2, 0)
    return int(net.num_addresses)


async def get_default_zone_id(db: AsyncSession) -> int:
    zone = (
        await db.execute(
            select(NetworkZone).where(
                NetworkZone.is_default.is_(True),
                NetworkZone.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if zone is None:
        zone = (
            await db.execute(
                select(NetworkZone)
                .where(NetworkZone.deleted_at.is_(None))
                .order_by(NetworkZone.id.asc())
            )
        ).scalars().first()
    if zone is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="未配置可用网络域")
    return zone.id


async def match_subnet_for_ip(
    db: AsyncSession,
    network_zone_id: int,
    ip_str: str,
) -> Subnet | None:
    """Longest-prefix-match (LPM) subnet lookup within the same network zone."""
    addr = ipaddress.ip_address(ip_str)
    subnets = list(
        (
            await db.execute(
                select(Subnet).where(
                    Subnet.network_zone_id == network_zone_id,
                    Subnet.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    best_subnet: Subnet | None = None
    best_prefix = -1
    for sub in subnets:
        net = ipaddress.ip_network(str(sub.cidr), strict=False)
        if net.version == addr.version and addr in net and net.prefixlen > best_prefix:
            best_prefix = net.prefixlen
            best_subnet = sub
    return best_subnet


def validate_ip_in_subnet(ip_str: str, cidr_str: str) -> None:
    addr = ipaddress.ip_address(ip_str)
    net = ipaddress.ip_network(cidr_str, strict=False)
    if addr.version != net.version or addr not in net:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"IP 地址 {ip_str} 不在所选网段 {cidr_str} 范围内",
        )


def find_next_free_ip(subnet: Subnet, existing_ips: set[str]) -> str | None:
    net = ipaddress.ip_network(str(subnet.cidr), strict=False)
    gateway = str(subnet.gateway).split("/")[0] if subnet.gateway else None
    iterator = net.hosts() if (net.version == 4 and net.prefixlen <= 30) else iter(net)
    for addr in iterator:
        text = str(addr)
        if text == gateway:
            continue
        if text not in existing_ips:
            return text
    return None


def can_render_ip_matrix(cidr_str: str) -> bool:
    net = ipaddress.ip_network(cidr_str, strict=False)
    return net.version == 4 and net.num_addresses <= MAX_MATRIX_HOSTS


async def load_group_names(db: AsyncSession, group_ids: set[int]) -> dict[int, str]:
    if not group_ids:
        return {}
    rows = (
        await db.execute(select(OpsGroup.id, OpsGroup.name).where(OpsGroup.id.in_(group_ids)))
    ).all()
    return {gid: name for gid, name in rows}


async def load_zone_names(db: AsyncSession, zone_ids: set[int]) -> dict[int, str]:
    if not zone_ids:
        return {}
    rows = (
        await db.execute(select(NetworkZone.id, NetworkZone.name).where(NetworkZone.id.in_(zone_ids)))
    ).all()
    return {zid: name for zid, name in rows}


async def load_entity_tags(
    db: AsyncSession,
    target_type: str,
    target_ids: list[int],
) -> dict[int, list[dict[str, Any]]]:
    if not target_ids:
        return {}
    stmt = (
        select(EntityTag.target_id, Tag.id, Tag.name, Tag.color)
        .join(Tag, Tag.id == EntityTag.tag_id)
        .where(
            EntityTag.target_type == target_type,
            EntityTag.target_id.in_(target_ids),
        )
        .order_by(Tag.name.asc())
    )
    mapping: dict[int, list[dict[str, Any]]] = {tid: [] for tid in target_ids}
    for tid, tag_id, tag_name, tag_color in (await db.execute(stmt)).all():
        mapping.setdefault(tid, []).append({"id": tag_id, "name": tag_name, "color": tag_color})
    return mapping


async def sync_entity_tags(
    db: AsyncSession,
    target_type: str,
    target_id: int,
    tag_ids: list[int] | None,
) -> None:
    if tag_ids is None:
        return
    await db.execute(
        delete(EntityTag).where(
            EntityTag.target_type == target_type,
            EntityTag.target_id == target_id,
        )
    )
    unique_ids = sorted(set(tag_ids))
    if unique_ids:
        existing_ids = set(
            (await db.execute(select(Tag.id).where(Tag.id.in_(unique_ids)))).scalars().all()
        )
        missing = set(unique_ids) - existing_ids
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"标签 ID 不存在: {sorted(missing)}",
            )
        for tid in unique_ids:
            db.add(EntityTag(target_type=target_type, target_id=target_id, tag_id=tid))
    await db.flush()


def is_cross_group_restricted(auth: AuthContext, group_id: int | None) -> bool:
    """True when an asset belongs to a group outside the current user's read scope."""
    if group_id is None:
        return False
    return not auth.has_permission_in_group("asset:read", group_id)


def format_ip_str(value: Any) -> str:
    return str(value).split("/")[0] if value is not None else ""


async def get_subnet_ip_map(db: AsyncSession, subnet_id: int) -> dict[str, IPAddress]:
    rows = list(
        (
            await db.execute(
                select(IPAddress).where(
                    IPAddress.subnet_id == subnet_id,
                    IPAddress.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return {format_ip_str(r.ip_address): r for r in rows}


async def get_zone_subnet_ip_map(db: AsyncSession, subnet: Subnet) -> dict[str, IPAddress]:
    """Return all active IPs in the same network zone that belong to or fall within subnet.cidr."""
    net = ipaddress.ip_network(str(subnet.cidr), strict=False)
    rows = list(
        (
            await db.execute(
                select(IPAddress).where(
                    IPAddress.network_zone_id == subnet.network_zone_id,
                    IPAddress.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    result: dict[str, IPAddress] = {}
    for row in rows:
        ip_text = format_ip_str(row.ip_address)
        if row.subnet_id == subnet.id:
            result[ip_text] = row
            continue
        try:
            addr = ipaddress.ip_address(ip_text)
        except ValueError:
            continue
        if addr.version == net.version and addr in net:
            result[ip_text] = row
    return result
