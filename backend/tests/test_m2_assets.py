"""Integration tests for M2: IPAM matrix, NIC-IP & Bond hierarchy, VIP bindings, panoramic chain, cross-group redaction, and delete protection."""

from __future__ import annotations

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
async def test_full_topology_chain_ipam_and_delete_protection(client: AsyncClient, auth_headers):
    tok = await _get_admin_token(client)
    hdr = auth_headers(tok)

    # 1. Create a second isolated NetworkZone to test private IP reuse across zones
    zone_dmz = (
        await client.post(
            "/api/v1/network-zones",
            json={"name": "隔离测试区", "code": "iso-test", "is_default": False},
            headers=hdr,
        )
    ).json()

    # 2. Create a /29 subnet (matrix mode) and a /16 subnet (list fallback mode)
    sub_small = (
        await client.post(
            "/api/v1/subnets",
            json={
                "name": "核心交易网段",
                "cidr": "10.20.1.0/29",
                "gateway": "10.20.1.1",
                "vlan_id": 120,
                "env": "prod",
            },
            headers=hdr,
        )
    ).json()
    assert sub_small["total_capacity"] == 6
    assert sub_small["can_render_matrix"] is True

    sub_large = (
        await client.post(
            "/api/v1/subnets",
            json={"name": "大网段容器池", "cidr": "10.99.0.0/16", "env": "prod"},
            headers=hdr,
        )
    ).json()
    assert sub_large["can_render_matrix"] is False

    # Next free IP skips .1 gateway -> .2
    next_ip = (
        await client.get(f"/api/v1/subnets/{sub_small['id']}/next-free-ip", headers=hdr)
    ).json()
    assert next_ip["next_free_ip"] == "10.20.1.2"

    # 3. Create Cluster & Device (+ test duplicate hostname warning & asset_code conflict)
    cluster = (
        await client.post(
            "/api/v1/clusters",
            json={"name": "交易核心K8s", "cluster_type": "K8s", "env": "prod"},
            headers=hdr,
        )
    ).json()

    dev1 = (
        await client.post(
            "/api/v1/devices",
            json={
                "hostname": "trade-node-01",
                "asset_code": "AST-001",
                "device_type": "physical",
                "env": "prod",
                "cluster_ids": [cluster["id"]],
            },
            headers=hdr,
        )
    ).json()
    assert dev1["warnings"] == []

    # Same asset_code -> 409
    dup_ast = await client.post(
        "/api/v1/devices",
        json={"hostname": "other-node", "asset_code": "AST-001"},
        headers=hdr,
    )
    assert dup_ast.status_code == 409

    # Same hostname allowed with warning
    dev2 = (
        await client.post(
            "/api/v1/devices",
            json={"hostname": "trade-node-01", "env": "test"},
            headers=hdr,
        )
    ).json()
    assert len(dev2["warnings"]) == 1

    # 4. Create Bond NIC (bond0) and child member NICs (eth0, eth1) sharing MAC (warning, not error)
    bond0 = (
        await client.post(
            "/api/v1/nics",
            json={
                "device_id": dev1["id"],
                "name": "bond0",
                "nic_type": "bond",
                "mac_address": "AA-BB-CC-DD-EE-01",
                "bond_mode": "mode4-802.3ad",
                "vlan_id": 120,
            },
            headers=hdr,
        )
    ).json()
    assert bond0["mac_address"] == "aa:bb:cc:dd:ee:01"

    eth0 = (
        await client.post(
            "/api/v1/nics",
            json={
                "device_id": dev1["id"],
                "parent_nic_id": bond0["id"],
                "name": "eth0",
                "nic_type": "business",
                "mac_address": "aa:bb:cc:dd:ee:01",
                "speed_mbps": 10000,
                "switch_port": "SW-01:GE1/0/1",
            },
            headers=hdr,
        )
    ).json()
    assert eth0["parent_nic_id"] == bond0["id"]
    assert len(eth0["warnings"]) == 1

    # 5. Bind Host IP to bond0 (auto-matches sub_small via LPM!) + VIP bound to cluster & bond0
    host_ip = (
        await client.post(
            "/api/v1/ips",
            json={
                "ip_address": "10.20.1.2",
                "nic_id": bond0["id"],
                "is_primary": True,
                "usage": "节点主业务IP",
            },
            headers=hdr,
        )
    ).json()
    assert host_ip["subnet_id"] == sub_small["id"]
    assert host_ip["nic_id"] == bond0["id"]
    assert host_ip["device_hostname"] == "trade-node-01"

    # Same private IP in SAME default zone -> 409
    dup_ip = await client.post("/api/v1/ips", json={"ip_address": "10.20.1.2"}, headers=hdr)
    assert dup_ip.status_code == 409

    # Same private IP in DIFFERENT isolated zone -> 201 OK!
    iso_ip = await client.post(
        "/api/v1/ips",
        json={"ip_address": "10.20.1.2", "network_zone_id": zone_dmz["id"]},
        headers=hdr,
    )
    assert iso_ip.status_code == 201

    # VIP (nic_id is NULL) bound to cluster and bond0
    vip = (
        await client.post(
            "/api/v1/ips",
            json={"ip_address": "10.20.1.5", "ip_type": "vip", "usage": "Keepalived VIP"},
            headers=hdr,
        )
    ).json()
    assert vip["subnet_id"] == sub_small["id"]
    assert vip["nic_id"] is None

    vb1 = await client.post(
        f"/api/v1/ips/{vip['id']}/vip-bindings",
        json={"target_type": "cluster", "target_id": cluster["id"], "role": "master"},
        headers=hdr,
    )
    assert vb1.status_code == 201
    vb2 = await client.post(
        f"/api/v1/ips/{vip['id']}/vip-bindings",
        json={"target_type": "nic", "target_id": bond0["id"], "role": "master"},
        headers=hdr,
    )
    assert vb2.status_code == 201

    # Check IP matrix for /29 and list fallback for /16
    mat_small = (
        await client.get(f"/api/v1/subnets/{sub_small['id']}/matrix", headers=hdr)
    ).json()
    assert mat_small["mode"] == "matrix"
    assert len(mat_small["cells"]) == 6
    cell_by_ip = {c["ip"]: c for c in mat_small["cells"]}
    assert cell_by_ip["10.20.1.1"]["status"] == "gateway"
    assert cell_by_ip["10.20.1.2"]["device_hostname"] == "trade-node-01"
    assert cell_by_ip["10.20.1.5"]["status"] == "vip"
    assert cell_by_ip["10.20.1.3"]["status"] == "free"

    mat_large = (
        await client.get(f"/api/v1/subnets/{sub_large['id']}/matrix", headers=hdr)
    ).json()
    assert mat_large["mode"] == "list"

    # 6. Create Service + Endpoint + URL (auto-parsed) and verify full panoramic reverse trace!
    svc = (
        await client.post(
            "/api/v1/services",
            json={"name": "order-gateway", "service_type": "gateway", "env": "prod"},
            headers=hdr,
        )
    ).json()

    ep = (
        await client.post(
            f"/api/v1/services/{svc['id']}/endpoints",
            json={
                "cluster_id": cluster["id"],
                "device_id": dev1["id"],
                "ip_id": host_ip["id"],
                "port": 8443,
                "protocol": "https",
                "endpoint_role": "api",
            },
            headers=hdr,
        )
    ).json()
    assert ep["ip_address"] == "10.20.1.2"

    url_entry = (
        await client.post(
            "/api/v1/urls",
            json={
                "name": "订单网关生产入口",
                "full_url": "https://trade.example.com:8443/api/orders",
                "service_id": svc["id"],
                "entry_ip_id": vip["id"],
                "access_type": "external",
                "env": "prod",
            },
            headers=hdr,
        )
    ).json()
    assert url_entry["protocol"] == "https"
    assert url_entry["domain"] == "trade.example.com"
    assert url_entry["port"] == 8443
    assert url_entry["path"] == "/api/orders"
    assert url_entry["is_https"] is True

    # Panoramic trace from URL -> Entry IP -> Service -> Cluster -> Device -> NIC -> IP!
    url_detail = (await client.get(f"/api/v1/urls/{url_entry['id']}", headers=hdr)).json()
    assert url_detail["service"]["title"] == "order-gateway"
    assert url_detail["entry_ip"]["title"] == "10.20.1.5"
    assert any(c["title"] == "交易核心K8s" for c in url_detail["clusters"])
    assert any(d["title"] == "trade-node-01" for d in url_detail["devices"])
    assert any("bond0" in n["title"] for n in url_detail["nics"])
    assert any(i["title"] == "10.20.1.2" for i in url_detail["ips"])

    # 7. Delete protections must block cascading deletions
    assert (await client.delete(f"/api/v1/subnets/{sub_small['id']}", headers=hdr)).status_code == 409
    assert (await client.delete(f"/api/v1/devices/{dev1['id']}", headers=hdr)).status_code == 409
    assert (await client.delete(f"/api/v1/nics/{bond0['id']}", headers=hdr)).status_code == 409
    assert (await client.delete(f"/api/v1/ips/{host_ip['id']}", headers=hdr)).status_code == 409
    assert (await client.delete(f"/api/v1/services/{svc['id']}", headers=hdr)).status_code == 409
    assert (await client.delete(f"/api/v1/clusters/{cluster['id']}", headers=hdr)).status_code == 409
    assert (await client.delete(f"/api/v1/network-zones/{zone_dmz['id']}", headers=hdr)).status_code == 409


@pytest.mark.asyncio
async def test_cross_group_scope_isolation_and_redacted_summary(client: AsyncClient, auth_headers):
    tok = await _get_admin_token(client)
    hdr = auth_headers(tok)

    roles = (await client.get("/api/v1/roles", headers=hdr)).json()
    role_by_code = {r["code"]: r["id"] for r in roles}

    g_app = (
        await client.post("/api/v1/groups", json={"name": "应用组", "code": "app-team"}, headers=hdr)
    ).json()
    g_infra = (
        await client.post("/api/v1/groups", json={"name": "基础架构组", "code": "infra-team"}, headers=hdr)
    ).json()

    await client.post(
        "/api/v1/users",
        json={
            "username": "app_ops",
            "display_name": "App Ops",
            "password": "AppPassword123",
            "must_change_password": False,
            "assignments": [{"group_id": g_app["id"], "role_id": role_by_code["ops_editor"]}],
        },
        headers=hdr,
    )

    # Infra owns a device/NIC/IP; App owns a service/URL pointing to Infra's device/IP
    infra_dev = (
        await client.post(
            "/api/v1/devices",
            json={
                "hostname": "infra-host-01",
                "group_id": g_infra["id"],
                "spec_summary": "SECRET-INTERNAL-SPEC",
            },
            headers=hdr,
        )
    ).json()
    infra_nic = (
        await client.post(
            "/api/v1/nics",
            json={"device_id": infra_dev["id"], "name": "eth0", "mac_address": "11:22:33:44:55:66"},
            headers=hdr,
        )
    ).json()
    infra_ip = (
        await client.post(
            "/api/v1/ips",
            json={
                "ip_address": "10.50.0.10",
                "nic_id": infra_nic["id"],
                "group_id": g_infra["id"],
                "usage": "CONFIDENTIAL-USAGE",
            },
            headers=hdr,
        )
    ).json()
    app_svc = (
        await client.post(
            "/api/v1/services",
            json={"name": "checkout-svc", "group_id": g_app["id"]},
            headers=hdr,
        )
    ).json()
    await client.post(
        f"/api/v1/services/{app_svc['id']}/endpoints",
        json={"device_id": infra_dev["id"], "ip_id": infra_ip["id"], "port": 8080},
        headers=hdr,
    )
    app_url = (
        await client.post(
            "/api/v1/urls",
            json={
                "name": "结账入口",
                "full_url": "https://checkout.example.com",
                "service_id": app_svc["id"],
                "entry_ip_id": infra_ip["id"],
                "group_id": g_app["id"],
            },
            headers=hdr,
        )
    ).json()

    # Login as app_ops
    app_tok = (
        await client.post(
            "/api/v1/auth/login",
            json={"username": "app_ops", "password": "AppPassword123"},
        )
    ).json()["access_token"]
    app_hdr = auth_headers(app_tok)

    # app_ops CANNOT list or directly view infra_dev
    dev_list = (await client.get("/api/v1/devices", headers=app_hdr)).json()
    assert dev_list["total"] == 0
    assert (await client.get(f"/api/v1/devices/{infra_dev['id']}", headers=app_hdr)).status_code == 403

    # app_ops CAN view their own URL detail and sees cross-group redacted summary for infra_dev/infra_ip!
    url_detail = (await client.get(f"/api/v1/urls/{app_url['id']}", headers=app_hdr)).json()
    assert url_detail["entry_ip"]["title"] == "10.50.0.10"
    assert url_detail["entry_ip"]["is_cross_group_summary"] is True
    assert url_detail["entry_ip"]["subtitle"] is None  # usage redacted!
    assert url_detail["devices"][0]["title"] == "infra-host-01"
    assert url_detail["devices"][0]["is_cross_group_summary"] is True
    assert url_detail["devices"][0]["subtitle"] is None  # spec_summary redacted!


@pytest.mark.asyncio
async def test_auto_ip_allocation_reuse_and_exhaustion(client: AsyncClient, auth_headers):
    tok = await _get_admin_token(client)
    hdr = auth_headers(tok)

    # 1. Both ip_address and subnet_id missing -> 400
    resp_empty = await client.post("/api/v1/ips", json={"usage": "missing both"}, headers=hdr)
    assert resp_empty.status_code == 400

    # 2. Small /29 subnet (6 usable hosts: .1~.6), gateway=.1 -> available: .2, .3, .4, .5, .6
    sub = (
        await client.post(
            "/api/v1/subnets",
            json={"name": "自动分配测试网段", "cidr": "10.220.0.0/29", "gateway": "10.220.0.1"},
            headers=hdr,
        )
    ).json()

    # Pre-occupy .2 manually (without subnet_id, matched by LPM)
    ip2 = (
        await client.post(
            "/api/v1/ips",
            json={"ip_address": "10.220.0.2", "usage": "manual .2"},
            headers=hdr,
        )
    ).json()
    assert ip2["subnet_id"] == sub["id"]

    # Next-free-ip preview should return .3
    nxt = (await client.get(f"/api/v1/subnets/{sub['id']}/next-free-ip", headers=hdr)).json()
    assert nxt["next_free_ip"] == "10.220.0.3"

    # Auto-allocate with ip_address omitted -> .3
    auto3 = await client.post(
        "/api/v1/ips",
        json={"subnet_id": sub["id"], "usage": "auto .3"},
        headers=hdr,
    )
    assert auto3.status_code == 201
    ip3 = auto3.json()
    assert ip3["ip_address"] == "10.220.0.3"
    assert ip3["subnet_id"] == sub["id"]

    # Auto-allocate with blank whitespace ip_address -> .4
    auto4 = await client.post(
        "/api/v1/ips",
        json={"subnet_id": sub["id"], "ip_address": "   ", "usage": "auto .4"},
        headers=hdr,
    )
    assert auto4.status_code == 201
    assert auto4.json()["ip_address"] == "10.220.0.4"

    # Mark .3 as 'free' -> next auto-allocation must reuse .3 (same record ID, updated usage/status)
    upd = await client.put(
        f"/api/v1/ips/{ip3['id']}",
        json={"status": "free"},
        headers=hdr,
    )
    assert upd.status_code == 200
    reused3 = await client.post(
        "/api/v1/ips",
        json={"subnet_id": sub["id"], "usage": "reused .3", "status": "allocated"},
        headers=hdr,
    )
    assert reused3.status_code == 201
    reused3_data = reused3.json()
    assert reused3_data["id"] == ip3["id"]
    assert reused3_data["ip_address"] == "10.220.0.3"
    assert reused3_data["usage"] == "reused .3"
    assert reused3_data["status"] == "allocated"

    # Allocate remaining .5 and .6
    auto5 = (await client.post("/api/v1/ips", json={"subnet_id": sub["id"]}, headers=hdr)).json()
    auto6 = (await client.post("/api/v1/ips", json={"subnet_id": sub["id"]}, headers=hdr)).json()
    assert [auto5["ip_address"], auto6["ip_address"]] == ["10.220.0.5", "10.220.0.6"]

    # Subnet exhausted -> 409 Conflict
    exhausted = await client.post("/api/v1/ips", json={"subnet_id": sub["id"]}, headers=hdr)
    assert exhausted.status_code == 409
    assert "已无可用空闲 IP" in exhausted.json()["detail"]

    # Audit log should record auto_allocated & reused_free_record
    audits = (
        await client.get("/api/v1/audit-logs", params={"target_type": "ip", "action": "create"}, headers=hdr)
    ).json()["items"]
    assert any(
        a["target_label"] == "10.220.0.3"
        and (a["diff_after"] or {}).get("auto_allocated") is True
        and (a["diff_after"] or {}).get("reused_free_record") is True
        for a in audits
    )
