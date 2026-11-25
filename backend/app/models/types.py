"""Cross-dialect network column types (PostgreSQL native INET/CIDR/MACADDR, SQLite VARCHAR)."""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import CIDR, INET, MACADDR

InetType = String(64).with_variant(INET(), "postgresql")
CidrType = String(64).with_variant(CIDR(), "postgresql")
MacAddrType = String(32).with_variant(MACADDR(), "postgresql")
