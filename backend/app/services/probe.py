"""Reserved extension seam for Phase-2 active probing (HTTP/SSL/ICMP/TCP checks)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class ProbeResult:
    health_status: str  # "healthy" | "unhealthy" | "degraded" | "unknown"
    checked_at: datetime
    latency_ms: float | None = None
    ssl_expires_at: datetime | None = None
    detail: str | None = None


class ProbeProvider(ABC):
    """Plugin interface for future URL/IP/Device active health checks."""

    provider_name: str

    @abstractmethod
    async def probe_target(self, target_type: str, target_address: str) -> ProbeResult:
        raise NotImplementedError
