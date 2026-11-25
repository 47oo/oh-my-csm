"""API v1 router aggregator."""

from fastapi import APIRouter

from app.api.v1 import (
    audit,
    auth,
    batch,
    clusters,
    credentials,
    dashboard,
    devices,
    dicts,
    excel,
    groups,
    health,
    ips,
    network_zones,
    nics,
    recycle_bin,
    roles,
    search,
    services,
    subnets,
    tags,
    urls,
    users,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(dashboard.router)
api_router.include_router(search.router)
api_router.include_router(batch.router)
api_router.include_router(credentials.router)
api_router.include_router(recycle_bin.router)
api_router.include_router(excel.router)
api_router.include_router(subnets.router)
api_router.include_router(clusters.router)
api_router.include_router(devices.router)
api_router.include_router(nics.router)
api_router.include_router(ips.router)
api_router.include_router(services.router)
api_router.include_router(urls.router)
api_router.include_router(groups.router)
api_router.include_router(users.router)
api_router.include_router(roles.router)
api_router.include_router(dicts.router)
api_router.include_router(tags.router)
api_router.include_router(network_zones.router)
api_router.include_router(audit.router)
