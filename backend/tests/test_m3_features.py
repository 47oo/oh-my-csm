"""Integration tests for M3: encrypted credentials + reveal audit, global search, batch ops, recycle bin, and dashboard alerts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient


async def _get_admin_token(client: AsyncClient) -> str:
    r = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "Admin123456"},
    )
    tok = r.json()["access_token"]
    await client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "Admin123456", "new_password": "AdminPassword2026"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    r2 = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "AdminPassword2026"},
    )
    return r2.json()["access_token"]


@pytest.mark.asyncio
async def test_credentials_rbac_and_mandatory_reveal_audit(client: AsyncClient, auth_headers):
    tok = await _get_admin_token(client)
    hdr = auth_headers(tok)

    roles = (await client.get("/api/v1/roles", headers=hdr)).json()
    role_by_code = {r["code"]: r["id"] for r in roles}

    grp = (
        await client.post("/api/v1/groups", json={"name": "网络运维组", "code": "net-ops"}, headers=hdr)
    ).json()

    # Create OpsEditor (no credential:reveal by default) and GroupAdmin (has credential:manage & reveal)
    await client.post(
        "/api/v1/users",
        json={
            "username": "editor_net",
            "display_name": "Editor Net",
            "password": "EditorPassword1",
            "must_change_password": False,
            "assignments": [{"group_id": grp["id"], "role_id": role_by_code["ops_editor"]}],
        },
        headers=hdr,
    )
    await client.post(
        "/api/v1/users",
        json={
            "username": "admin_net",
            "display_name": "Admin Net",
            "password": "AdminNetPassword1",
            "must_change_password": False,
            "assignments": [{"group_id": grp["id"], "role_id": role_by_code["group_admin"]}],
        },
        headers=hdr,
    )

    dev = (
        await client.post(
            "/api/v1/devices",
            json={"hostname": "core-sw-01", "device_type": "switch", "group_id": grp["id"]},
            headers=hdr,
        )
    ).json()

    # GroupAdmin creates encrypted credential
    ga_tok = (
        await client.post(
            "/api/v1/auth/login",
            json={"username": "admin_net", "password": "AdminNetPassword1"},
        )
    ).json()["access_token"]
    ga_hdr = auth_headers(ga_tok)

    created = await client.post(
        "/api/v1/credentials",
        json={
            "target_type": "device",
            "target_id": dev["id"],
            "purpose": "IPMI 管理口",
            "username": "ipmiadmin",
            "secret": "UltraSecret#2026",
            "notes": "机房带外管理",
        },
        headers=ga_hdr,
    )
    assert created.status_code == 201
    cred_data = created.json()
    assert "secret" not in cred_data
    assert "encrypted_secret" not in cred_data
    assert cred_data["has_secret"] is True

    # OpsEditor can list metadata but CANNOT reveal plaintext (403)
    ed_tok = (
        await client.post(
            "/api/v1/auth/login",
            json={"username": "editor_net", "password": "EditorPassword1"},
        )
    ).json()["access_token"]
    ed_hdr = auth_headers(ed_tok)

    listed = await client.get(
        "/api/v1/credentials",
        params={"target_type": "device", "target_id": dev["id"]},
        headers=ed_hdr,
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert "secret" not in listed.json()[0]

    denied = await client.post(f"/api/v1/credentials/{cred_data['id']}/reveal", headers=ed_hdr)
    assert denied.status_code == 403

    # GroupAdmin reveals plaintext -> succeeds AND writes credential_reveal audit log!
    revealed = await client.post(f"/api/v1/credentials/{cred_data['id']}/reveal", headers=ga_hdr)
    assert revealed.status_code == 200
    assert revealed.json()["secret"] == "UltraSecret#2026"

    audits = (
        await client.get(
            "/api/v1/audit-logs",
            params={"action": "credential_reveal", "target_type": "credential"},
            headers=hdr,
        )
    ).json()
    assert audits["total"] == 1
    assert audits["items"][0]["actor_username"] == "admin_net"


@pytest.mark.asyncio
async def test_global_search_batch_ops_recycle_bin_and_dashboard_alerts(
    client: AsyncClient, auth_headers
):
    tok = await _get_admin_token(client)
    hdr = auth_headers(tok)

    grp = (
        await client.post("/api/v1/groups", json={"name": "支付运维组", "code": "pay-ops"}, headers=hdr)
    ).json()
    tag = (
        await client.post("/api/v1/tags", json={"name": "核心链路", "color": "#d03050"}, headers=hdr)
    ).json()

    sub = (
        await client.post(
            "/api/v1/subnets",
            json={"name": "支付网段", "cidr": "10.88.1.0/29", "gateway": "10.88.1.1"},
            headers=hdr,
        )
    ).json()
    dev1 = (
        await client.post(
            "/api/v1/devices",
            json={"hostname": "pay-app-01", "env": "prod"},
            headers=hdr,
        )
    ).json()
    dev2 = (
        await client.post(
            "/api/v1/devices",
            json={"hostname": "pay-app-01", "env": "test"},
            headers=hdr,
        )
    ).json()
    ip1 = (
        await client.post(
            "/api/v1/ips",
            json={"ip_address": "10.88.1.2", "usage": "pay-app-01 业务"},
            headers=hdr,
        )
    ).json()
    expiring_ssl = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    url1 = (
        await client.post(
            "/api/v1/urls",
            json={
                "name": "支付后台",
                "full_url": "https://pay-admin.example.com",
                "entry_ip_id": ip1["id"],
                "ssl_expires_at": expiring_ssl,
            },
            headers=hdr,
        )
    ).json()

    # 1. Global search finds device, IP, URL, subnet
    s1 = (await client.get("/api/v1/search", params={"q": "pay"}, headers=hdr)).json()
    assert s1["total"] >= 3
    hit_types = {g["entity_type"] for g in s1["groups"]}
    assert {"device", "url", "ip"}.issubset(hit_types)

    # 2. Dashboard alerts include top subnet, expiring SSL URL, incomplete counts, duplicate hostname
    dash = (await client.get("/api/v1/dashboard/summary", headers=hdr)).json()
    assert len(dash["top_subnets"]) == 1
    assert dash["top_subnets"][0]["id"] == sub["id"]
    assert len(dash["expiring_urls"]) == 1
    assert dash["expiring_urls"][0]["id"] == url1["id"]
    assert dash["incomplete_counts"]["urls_without_service"] == 1
    assert dash["incomplete_counts"]["host_ips_without_nic"] == 1
    assert dash["incomplete_counts"]["devices_without_group"] == 2
    assert dash["duplicate_hostnames"][0]["hostname"] == "pay-app-01"
    assert dash["duplicate_hostnames"][0]["count"] == 2

    # 3. Batch operations: tag_add, move_group, update_status, delete (partial block!)
    b_tag = (
        await client.post(
            "/api/v1/batch/devices",
            json={"ids": [dev1["id"], dev2["id"]], "action": "tag_add", "tag_ids": [tag["id"]]},
            headers=hdr,
        )
    ).json()
    assert sorted(b_tag["succeeded_ids"]) == sorted([dev1["id"], dev2["id"]])

    b_move = (
        await client.post(
            "/api/v1/batch/devices",
            json={"ids": [dev1["id"]], "action": "move_group", "group_id": grp["id"]},
            headers=hdr,
        )
    ).json()
    assert b_move["succeeded_ids"] == [dev1["id"]]

    # Batch delete IP blocked because URL references it as entry_ip_id
    b_del_ip = (
        await client.post(
            "/api/v1/batch/ips",
            json={"ids": [ip1["id"]], "action": "delete"},
            headers=hdr,
        )
    ).json()
    assert b_del_ip["succeeded_ids"] == []
    assert len(b_del_ip["failed"]) == 1
    assert "URL" in b_del_ip["failed"][0]["reason"]

    # Delete dev2 -> verify Recycle Bin list, restore, re-delete, and physical purge
    assert (await client.delete(f"/api/v1/devices/{dev2['id']}", headers=hdr)).status_code == 200
    rb = (await client.get("/api/v1/recycle-bin", headers=hdr)).json()
    assert any(item["entity_type"] == "device" and item["id"] == dev2["id"] for item in rb)

    restored = await client.post(f"/api/v1/recycle-bin/device/{dev2['id']}/restore", headers=hdr)
    assert restored.status_code == 200
    assert (await client.get(f"/api/v1/devices/{dev2['id']}", headers=hdr)).status_code == 200

    assert (await client.delete(f"/api/v1/devices/{dev2['id']}", headers=hdr)).status_code == 200
    purged = await client.delete(f"/api/v1/recycle-bin/device/{dev2['id']}", headers=hdr)
    assert purged.status_code == 200
    rb_after = (await client.get("/api/v1/recycle-bin", headers=hdr)).json()
    assert not any(item["entity_type"] == "device" and item["id"] == dev2["id"] for item in rb_after)
