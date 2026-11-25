"""Integration tests for Cluster-Level RBAC: full-chain penetration, role granularity, delegated member management, M:N shared devices, public toggle, and zero bypass across search/dashboard/export."""

from __future__ import annotations

import io

import pytest
from httpx import AsyncClient
from openpyxl import load_workbook


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
async def test_cluster_level_penetration_isolation_and_delegation(client: AsyncClient, auth_headers):
    admin_tok = await _get_admin_token(client)
    admin_hdr = auth_headers(admin_tok)

    roles = (await client.get("/api/v1/roles", headers=admin_hdr)).json()
    role_by_code = {r["code"]: r["id"] for r in roles}

    # Setup two separate subnets & clusters (A and B) + one unassigned public device
    sub_a = (
        await client.post(
            "/api/v1/subnets",
            json={"name": "集群A网段", "cidr": "10.100.1.0/24", "gateway": "10.100.1.1"},
            headers=admin_hdr,
        )
    ).json()
    sub_b = (
        await client.post(
            "/api/v1/subnets",
            json={"name": "集群B网段", "cidr": "10.100.2.0/24", "gateway": "10.100.2.1"},
            headers=admin_hdr,
        )
    ).json()

    cl_a = (
        await client.post("/api/v1/clusters", json={"name": "Cluster-Alpha", "env": "prod"}, headers=admin_hdr)
    ).json()
    cl_b = (
        await client.post("/api/v1/clusters", json={"name": "Cluster-Beta", "env": "prod"}, headers=admin_hdr)
    ).json()

    # Assets in Cluster A
    dev_a = (
        await client.post(
            "/api/v1/devices",
            json={"hostname": "alpha-node-01", "cluster_ids": [cl_a["id"]]},
            headers=admin_hdr,
        )
    ).json()
    nic_a = (
        await client.post(
            "/api/v1/nics",
            json={"device_id": dev_a["id"], "name": "eth0", "mac_address": "aa:00:00:00:00:01"},
            headers=admin_hdr,
        )
    ).json()
    ip_a = (
        await client.post(
            "/api/v1/ips",
            json={"ip_address": "10.100.1.10", "nic_id": nic_a["id"], "usage": "Alpha host IP"},
            headers=admin_hdr,
        )
    ).json()
    vip_a = (
        await client.post(
            "/api/v1/ips",
            json={"ip_address": "10.100.1.250", "ip_type": "vip", "cluster_id": cl_a["id"], "usage": "Alpha VIP"},
            headers=admin_hdr,
        )
    ).json()
    svc_a = (
        await client.post(
            "/api/v1/services",
            json={"name": "alpha-svc", "cluster_ids": [cl_a["id"]]},
            headers=admin_hdr,
        )
    ).json()
    url_a = (
        await client.post(
            "/api/v1/urls",
            json={"name": "Alpha入口", "full_url": "https://alpha.example.com", "service_id": svc_a["id"], "entry_ip_id": vip_a["id"]},
            headers=admin_hdr,
        )
    ).json()
    cred_a = (
        await client.post(
            "/api/v1/credentials",
            json={"target_type": "device", "target_id": dev_a["id"], "purpose": "IPMI-A", "username": "root", "secret": "AlphaSecret#1"},
            headers=admin_hdr,
        )
    ).json()

    # Assets in Cluster B
    dev_b = (
        await client.post(
            "/api/v1/devices",
            json={"hostname": "beta-node-01", "cluster_ids": [cl_b["id"]]},
            headers=admin_hdr,
        )
    ).json()
    nic_b = (
        await client.post(
            "/api/v1/nics",
            json={"device_id": dev_b["id"], "name": "eth0", "mac_address": "bb:00:00:00:00:01"},
            headers=admin_hdr,
        )
    ).json()
    ip_b = (
        await client.post(
            "/api/v1/ips",
            json={"ip_address": "10.100.2.10", "nic_id": nic_b["id"], "usage": "Beta host IP"},
            headers=admin_hdr,
        )
    ).json()
    svc_b = (
        await client.post(
            "/api/v1/services",
            json={"name": "beta-svc", "cluster_ids": [cl_b["id"]]},
            headers=admin_hdr,
        )
    ).json()
    url_b = (
        await client.post(
            "/api/v1/urls",
            json={"name": "Beta入口", "full_url": "https://beta.example.com", "service_id": svc_b["id"]},
            headers=admin_hdr,
        )
    ).json()
    cred_b = (
        await client.post(
            "/api/v1/credentials",
            json={"target_type": "device", "target_id": dev_b["id"], "purpose": "IPMI-B", "username": "root", "secret": "BetaSecret#1"},
            headers=admin_hdr,
        )
    ).json()

    # Unassigned public device (no cluster, no group)
    public_dev = (
        await client.post("/api/v1/devices", json={"hostname": "orphan-public-01"}, headers=admin_hdr)
    ).json()

    # M:N Shared device in BOTH Cluster A and Cluster B
    shared_dev = (
        await client.post(
            "/api/v1/devices",
            json={"hostname": "shared-node-ab", "cluster_ids": [cl_a["id"], cl_b["id"]]},
            headers=admin_hdr,
        )
    ).json()

    # Create user 'cl_user' with NO group, allow_public_assets=False:
    # - Cluster Alpha: group_admin (read/write/credential reveal/member manage)
    # - Cluster Beta: NOT assigned yet
    cl_user = (
        await client.post(
            "/api/v1/users",
            json={
                "username": "cl_user",
                "display_name": "Cluster User",
                "password": "ClusterUser123",
                "must_change_password": False,
                "allow_public_assets": False,
                "assignments": [],
                "cluster_assignments": [{"cluster_id": cl_a["id"], "role_id": role_by_code["group_admin"]}],
            },
            headers=admin_hdr,
        )
    ).json()

    # Second user to test delegated cluster member management
    helper_user = (
        await client.post(
            "/api/v1/users",
            json={
                "username": "helper_user",
                "display_name": "Helper",
                "password": "HelperUser123",
                "must_change_password": False,
            },
            headers=admin_hdr,
        )
    ).json()

    u_tok = (
        await client.post("/api/v1/auth/login", json={"username": "cl_user", "password": "ClusterUser123"})
    ).json()["access_token"]
    u_hdr = auth_headers(u_tok)

    # 1. Verify full-chain penetration: sees ONLY Cluster Alpha assets + shared_dev; Beta & public hidden!
    cl_list = (await client.get("/api/v1/clusters", headers=u_hdr)).json()
    assert [c["id"] for c in cl_list["items"]] == [cl_a["id"]]

    dev_list = (await client.get("/api/v1/devices", headers=u_hdr)).json()
    assert {d["id"] for d in dev_list["items"]} == {dev_a["id"], shared_dev["id"]}

    nic_list = (await client.get("/api/v1/nics", headers=u_hdr)).json()
    assert [n["id"] for n in nic_list["items"]] == [nic_a["id"]]

    ip_list = (await client.get("/api/v1/ips", headers=u_hdr)).json()
    assert {i["id"] for i in ip_list["items"]} == {ip_a["id"], vip_a["id"]}

    sub_list = (await client.get("/api/v1/subnets", headers=u_hdr)).json()
    assert [s["id"] for s in sub_list["items"]] == [sub_a["id"]]

    svc_list = (await client.get("/api/v1/services", headers=u_hdr)).json()
    assert [s["id"] for s in svc_list["items"]] == [svc_a["id"]]

    url_list = (await client.get("/api/v1/urls", headers=u_hdr)).json()
    assert [u["id"] for u in url_list["items"]] == [url_a["id"]]

    # Direct access to Cluster B or orphan public device -> 403
    assert (await client.get(f"/api/v1/devices/{dev_b['id']}", headers=u_hdr)).status_code == 403
    assert (await client.get(f"/api/v1/devices/{public_dev['id']}", headers=u_hdr)).status_code == 403
    assert (await client.get(f"/api/v1/ips/{ip_b['id']}", headers=u_hdr)).status_code == 403
    assert (await client.get(f"/api/v1/urls/{url_b['id']}", headers=u_hdr)).status_code == 403
    assert (await client.get(f"/api/v1/subnets/{sub_b['id']}", headers=u_hdr)).status_code == 403

    # Global Search, Dashboard, and Excel Export must NOT leak Beta or public assets!
    s_beta = (await client.get("/api/v1/search", params={"q": "beta"}, headers=u_hdr)).json()
    assert s_beta["total"] == 0
    s_alpha = (await client.get("/api/v1/search", params={"q": "alpha"}, headers=u_hdr)).json()
    assert s_alpha["total"] >= 4

    dash = (await client.get("/api/v1/dashboard/summary", headers=u_hdr)).json()
    assert dash["counts"]["clusters"] == 1
    assert dash["counts"]["devices"] == 2  # dev_a + shared_dev
    assert dash["counts"]["urls"] == 1

    exp = await client.post("/api/v1/excel/export", json={"mode": "multi_sheet"}, headers=u_hdr)
    assert exp.status_code == 200
    exp_wb = load_workbook(io.BytesIO(exp.content))
    assert exp_wb["01_集群"].max_row == 3  # 2 headers + 1 cluster (Alpha only!)

    # 2. Credential reveal works on Cluster A device, forbidden on Cluster B device
    rev_a = await client.post(f"/api/v1/credentials/{cred_a['id']}/reveal", headers=u_hdr)
    assert rev_a.status_code == 200
    assert rev_a.json()["secret"] == "AlphaSecret#1"
    assert (await client.post(f"/api/v1/credentials/{cred_b['id']}/reveal", headers=u_hdr)).status_code == 403

    # 3. M:N shared device editable because user has write on Cluster A (Q5=A)
    upd_shared = await client.put(
        f"/api/v1/devices/{shared_dev['id']}",
        json={"rack_location": "A01-U05"},
        headers=u_hdr,
    )
    assert upd_shared.status_code == 200

    # 4. Cluster-only user creating assets: orphan blocked (403), attached to Cluster A allowed (201)
    assert (await client.post("/api/v1/devices", json={"hostname": "unattached-01"}, headers=u_hdr)).status_code == 403
    created_dev = await client.post(
        "/api/v1/devices",
        json={"hostname": "alpha-node-02", "cluster_ids": [cl_a["id"]]},
        headers=u_hdr,
    )
    assert created_dev.status_code == 201

    # 5. Delegated cluster member management: cl_user (group_admin on Alpha) can add helper_user to Alpha, not Beta, not super_admin
    add_mem = await client.post(
        f"/api/v1/clusters/{cl_a['id']}/members",
        json={"user_id": helper_user["id"], "role_id": role_by_code["viewer"]},
        headers=u_hdr,
    )
    assert add_mem.status_code == 201
    assert (
        await client.post(
            f"/api/v1/clusters/{cl_a['id']}/members",
            json={"user_id": helper_user["id"], "role_id": role_by_code["super_admin"]},
            headers=u_hdr,
        )
    ).status_code == 403
    assert (
        await client.post(
            f"/api/v1/clusters/{cl_b['id']}/members",
            json={"user_id": helper_user["id"], "role_id": role_by_code["viewer"]},
            headers=u_hdr,
        )
    ).status_code == 403

    # 6. Grant Cluster Beta as Viewer + enable allow_public_assets -> can read Beta & public, still cannot write/reveal Beta!
    await client.put(
        f"/api/v1/users/{cl_user['id']}",
        json={
            "allow_public_assets": True,
            "cluster_assignments": [
                {"cluster_id": cl_a["id"], "role_id": role_by_code["group_admin"]},
                {"cluster_id": cl_b["id"], "role_id": role_by_code["viewer"]},
            ],
        },
        headers=admin_hdr,
    )
    assert (await client.get(f"/api/v1/devices/{dev_b['id']}", headers=u_hdr)).status_code == 200
    assert (await client.get(f"/api/v1/devices/{public_dev['id']}", headers=u_hdr)).status_code == 200
    assert (await client.put(f"/api/v1/devices/{dev_b['id']}", json={"owner": "hack"}, headers=u_hdr)).status_code == 403
    assert (await client.post(f"/api/v1/credentials/{cred_b['id']}/reveal", headers=u_hdr)).status_code == 403
