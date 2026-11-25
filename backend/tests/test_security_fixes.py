"""Security regression tests covering Code Review findings (Spec c.1-c.5)."""

from __future__ import annotations

import io

import pytest
from httpx import AsyncClient
from openpyxl import Workbook, load_workbook


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


def _make_workbook_bytes(sheets: dict[str, list[list[str]]]) -> bytes:
    wb = Workbook()
    first = True
    for name, rows in sheets.items():
        ws = wb.active if first else wb.create_sheet(title=name)
        ws.title = name
        first = False
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_security_cross_boundary_attachment_excel_recycle_and_role_guards(
    client: AsyncClient, auth_headers
):
    admin_tok = await _get_admin_token(client)
    admin_hdr = auth_headers(admin_tok)

    roles = (await client.get("/api/v1/roles", headers=admin_hdr)).json()
    role_by_code = {r["code"]: r["id"] for r in roles}

    # Custom role with asset:read ONLY (no excel:export) to test export scope separation
    read_only_role = (
        await client.post(
            "/api/v1/roles",
            json={
                "code": "custom_reader_no_export",
                "name": "纯只读不可导出",
                "permissions": ["asset:read"],
            },
            headers=admin_hdr,
        )
    ).json()

    # Setup Cluster A & Cluster B + devices, NICs, IPs, services, URLs
    cl_a = (
        await client.post("/api/v1/clusters", json={"name": "Sec-Cluster-A", "env": "prod"}, headers=admin_hdr)
    ).json()
    cl_b = (
        await client.post("/api/v1/clusters", json={"name": "Sec-Cluster-B", "env": "prod"}, headers=admin_hdr)
    ).json()

    dev_a = (
        await client.post(
            "/api/v1/devices",
            json={"hostname": "sec-node-a", "cluster_ids": [cl_a["id"]]},
            headers=admin_hdr,
        )
    ).json()
    nic_a = (
        await client.post(
            "/api/v1/nics",
            json={"device_id": dev_a["id"], "name": "eth0"},
            headers=admin_hdr,
        )
    ).json()
    ip_a = (
        await client.post(
            "/api/v1/ips",
            json={"ip_address": "10.230.1.10", "nic_id": nic_a["id"]},
            headers=admin_hdr,
        )
    ).json()
    vip_a = (
        await client.post(
            "/api/v1/ips",
            json={"ip_address": "10.230.1.250", "ip_type": "vip", "cluster_id": cl_a["id"]},
            headers=admin_hdr,
        )
    ).json()
    svc_a = (
        await client.post(
            "/api/v1/services",
            json={"name": "sec-svc-a", "cluster_ids": [cl_a["id"]]},
            headers=admin_hdr,
        )
    ).json()
    url_a = (
        await client.post(
            "/api/v1/urls",
            json={"name": "Sec-URL-A", "full_url": "https://seca.example.com", "service_id": svc_a["id"]},
            headers=admin_hdr,
        )
    ).json()

    dev_b = (
        await client.post(
            "/api/v1/devices",
            json={"hostname": "sec-node-b", "cluster_ids": [cl_b["id"]]},
            headers=admin_hdr,
        )
    ).json()
    nic_b = (
        await client.post(
            "/api/v1/nics",
            json={"device_id": dev_b["id"], "name": "eth0"},
            headers=admin_hdr,
        )
    ).json()
    ip_b = (
        await client.post(
            "/api/v1/ips",
            json={"ip_address": "10.230.2.10", "nic_id": nic_b["id"]},
            headers=admin_hdr,
        )
    ).json()
    svc_b = (
        await client.post(
            "/api/v1/services",
            json={"name": "sec-svc-b", "cluster_ids": [cl_b["id"]]},
            headers=admin_hdr,
        )
    ).json()

    shared_dev = (
        await client.post(
            "/api/v1/devices",
            json={"hostname": "sec-shared-ab", "cluster_ids": [cl_a["id"], cl_b["id"]]},
            headers=admin_hdr,
        )
    ).json()

    # Create Cluster A admin user (NO group perms, ONLY Cluster A group_admin)
    await client.post(
        "/api/v1/users",
        json={
            "username": "sec_cl_a",
            "display_name": "Sec Cluster A Admin",
            "password": "SecUser123",
            "must_change_password": False,
            "allow_public_assets": False,
            "assignments": [],
            "cluster_assignments": [{"cluster_id": cl_a["id"], "role_id": role_by_code["group_admin"]}],
        },
        headers=admin_hdr,
    )
    a_tok = (
        await client.post("/api/v1/auth/login", json={"username": "sec_cl_a", "password": "SecUser123"})
    ).json()["access_token"]
    a_hdr = auth_headers(a_tok)

    # 1. Cross-boundary attachment privilege escalation must ALL return 403!
    assert (
        await client.post(
            f"/api/v1/clusters/{cl_a['id']}/devices",
            json={"device_id": dev_b["id"]},
            headers=a_hdr,
        )
    ).status_code == 403

    assert (
        await client.put(
            f"/api/v1/devices/{dev_a['id']}",
            json={"cluster_ids": [cl_a["id"], cl_b["id"]]},
            headers=a_hdr,
        )
    ).status_code == 403

    # Cannot strip Cluster B from a shared device
    assert (
        await client.put(
            f"/api/v1/devices/{shared_dev['id']}",
            json={"cluster_ids": [cl_a["id"]]},
            headers=a_hdr,
        )
    ).status_code == 403

    # Cannot bind Cluster B / dev_b / ip_b onto svc_a endpoint
    assert (
        await client.post(
            f"/api/v1/services/{svc_a['id']}/endpoints",
            json={"cluster_id": cl_b["id"], "port": 8080},
            headers=a_hdr,
        )
    ).status_code == 403
    assert (
        await client.post(
            f"/api/v1/services/{svc_a['id']}/endpoints",
            json={"device_id": dev_b["id"], "port": 8080},
            headers=a_hdr,
        )
    ).status_code == 403
    assert (
        await client.post(
            f"/api/v1/services/{svc_a['id']}/endpoints",
            json={"ip_id": ip_b["id"], "port": 8080},
            headers=a_hdr,
        )
    ).status_code == 403

    # Cannot rebind ip_a to nic_b, nor bind vip_a to cl_b / svc_b
    assert (
        await client.put(f"/api/v1/ips/{ip_a['id']}", json={"nic_id": nic_b["id"]}, headers=a_hdr)
    ).status_code == 403
    assert (
        await client.post(
            f"/api/v1/ips/{vip_a['id']}/vip-bindings",
            json={"target_type": "cluster", "target_id": cl_b["id"]},
            headers=a_hdr,
        )
    ).status_code == 403
    assert (
        await client.post(
            "/api/v1/urls",
            json={"name": "bad-url", "full_url": "https://hijack.example.com", "service_id": svc_b["id"]},
            headers=a_hdr,
        )
    ).status_code == 403

    # 2. Excel path traversal blocked (400)
    assert (
        await client.post(
            "/api/v1/excel/preview",
            json={"upload_id": "../../etc/passwd", "mode": "multi_sheet"},
            headers=a_hdr,
        )
    ).status_code == 400

    # 3. Excel cross-cluster overwrite blocked + cluster-only NIC/IP import works!
    xlsx = _make_workbook_bytes(
        {
            "02_设备": [
                ["hostname", "device_type", "cluster_names", "description"],
                ["sec-node-b", "physical", "Sec-Cluster-A", "HACKED"],  # unauthorized overwrite!
            ],
            "03_网卡": [
                ["device_hostname", "name", "nic_type"],
                ["sec-node-a", "eth1", "business"],  # authorized cluster-only NIC import!
            ],
            "04_IP": [
                ["ip_address", "ip_type", "device_hostname", "nic_name", "usage"],
                ["10.230.1.11", "host", "sec-node-a", "eth1", "Cluster A imported IP"],
            ],
        }
    )
    up = await client.post(
        "/api/v1/excel/upload",
        files={"file": ("sec.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=a_hdr,
    )
    assert up.status_code == 200
    commit = await client.post(
        "/api/v1/excel/commit",
        json={
            "upload_id": up.json()["upload_id"],
            "mode": "multi_sheet",
            "conflict_strategy": "overwrite",
            "error_mode": "skip_errors",
        },
        headers=a_hdr,
    )
    assert commit.status_code == 200
    c_data = commit.json()
    assert c_data["failed_count"] == 1  # sec-node-b rejected!
    assert c_data["created_count"] == 2  # eth1 + 10.230.1.11 succeeded!
    # Verify sec-node-b was NOT modified
    dev_b_check = (await client.get(f"/api/v1/devices/{dev_b['id']}", headers=admin_hdr)).json()
    assert dev_b_check["description"] != "HACKED"

    # 4. Excel export must enforce excel:export scope (not asset:read!)
    await client.post(
        "/api/v1/users",
        json={
            "username": "split_exporter",
            "display_name": "Split Exporter",
            "password": "SplitUser123",
            "must_change_password": False,
            "assignments": [],
            "cluster_assignments": [
                {"cluster_id": cl_a["id"], "role_id": read_only_role["id"]},  # asset:read ONLY
                {"cluster_id": cl_b["id"], "role_id": role_by_code["viewer"]},  # asset:read + excel:export
            ],
        },
        headers=admin_hdr,
    )
    exp_tok = (
        await client.post("/api/v1/auth/login", json={"username": "split_exporter", "password": "SplitUser123"})
    ).json()["access_token"]
    exp_res = await client.post(
        "/api/v1/excel/export",
        json={"mode": "multi_sheet"},
        headers=auth_headers(exp_tok),
    )
    assert exp_res.status_code == 200
    exp_wb = load_workbook(io.BytesIO(exp_res.content))
    exported_clusters = [
        exp_wb["01_集群"].cell(r, 1).value for r in range(3, exp_wb["01_集群"].max_row + 1)
    ]
    assert exported_clusters == ["Sec-Cluster-B"]  # Cluster A MUST NOT be exported!

    # 5. Role deletion blocked when referenced by ClusterACL
    assert (await client.delete(f"/api/v1/roles/{read_only_role['id']}", headers=admin_hdr)).status_code == 409

    # 6. Cluster-only admin can list & restore soft-deleted Cluster A URL in recycle bin + sees cluster audits on dashboard
    del_url = await client.delete(f"/api/v1/urls/{url_a['id']}", headers=a_hdr)
    assert del_url.status_code == 200
    rb_items = (await client.get("/api/v1/recycle-bin", headers=a_hdr)).json()
    assert any(item["entity_type"] == "url" and item["id"] == url_a["id"] for item in rb_items)
    restore_res = await client.post(f"/api/v1/recycle-bin/url/{url_a['id']}/restore", headers=a_hdr)
    assert restore_res.status_code == 200

    # Admin updates Cluster A -> Cluster-only admin sees another actor's Cluster A audit on dashboard
    await client.put(
        f"/api/v1/clusters/{cl_a['id']}",
        json={"description": "updated-by-admin"},
        headers=admin_hdr,
    )
    dash = (await client.get("/api/v1/dashboard/summary", headers=a_hdr)).json()
    assert any(
        a["target_type"] == "cluster"
        and a["target_id"] == cl_a["id"]
        and a["actor_username"] == "admin"
        for a in dash["recent_audits"]
    )

    # 7. Batch delete cluster blocked when VIP binding exists
    cl_vip_only = (
        await client.post("/api/v1/clusters", json={"name": "VIP-Only-Cluster"}, headers=admin_hdr)
    ).json()
    await client.post(
        "/api/v1/ips",
        json={"ip_address": "10.230.9.99", "ip_type": "vip", "cluster_id": cl_vip_only["id"]},
        headers=admin_hdr,
    )
    batch_del = (
        await client.post(
            "/api/v1/batch/clusters",
            json={"ids": [cl_vip_only["id"]], "action": "delete"},
            headers=admin_hdr,
        )
    ).json()
    assert batch_del["succeeded_ids"] == []
    assert len(batch_del["failed"]) == 1
    assert "VIP" in batch_del["failed"][0]["reason"]
