"""Integration tests for M4 Excel engine: 6-sheet template, wide-table column mapping, Dry-run, error export, and scoped export."""

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


@pytest.mark.asyncio
async def test_standard_6sheet_template_preview_commit_and_export(client: AsyncClient, auth_headers):
    tok = await _get_admin_token(client)
    hdr = auth_headers(tok)

    # Pre-create subnet so imported IP 10.20.1.10 auto-matches it via LPM!
    sub = (
        await client.post(
            "/api/v1/subnets",
            json={"name": "模板测试网段", "cidr": "10.20.1.0/24", "gateway": "10.20.1.1"},
            headers=hdr,
        )
    ).json()

    # 1. Download 6-sheet standard template (contains 1 complete example chain!)
    tpl_resp = await client.get("/api/v1/excel/template", headers=hdr)
    assert tpl_resp.status_code == 200
    wb = load_workbook(io.BytesIO(tpl_resp.content))
    assert len(wb.sheetnames) == 6
    assert "03_网卡" in wb.sheetnames

    # 2. Upload the downloaded template directly
    up = await client.post(
        "/api/v1/excel/upload",
        files={"file": ("template.xlsx", tpl_resp.content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=hdr,
    )
    assert up.status_code == 200
    up_data = up.json()
    assert up_data["detected_mode"] == "multi_sheet"
    upload_id = up_data["upload_id"]

    # 3. Dry-run preview must NOT write anything to DB
    preview = (
        await client.post(
            "/api/v1/excel/preview",
            json={"upload_id": upload_id, "mode": "multi_sheet"},
            headers=hdr,
        )
    ).json()
    assert preview["error_count"] == 0
    assert preview["create_count"] == 6
    assert preview["can_commit"] is True

    # Confirm DB still has 0 devices before commit
    devs_before = (await client.get("/api/v1/devices", headers=hdr)).json()
    assert devs_before["total"] == 0

    # 4. Commit import
    committed = (
        await client.post(
            "/api/v1/excel/commit",
            json={"upload_id": upload_id, "mode": "multi_sheet"},
            headers=hdr,
        )
    ).json()
    assert committed["status"] == "completed"
    assert committed["created_count"] == 6
    assert committed["failed_count"] == 0

    # Verify full chain exists and IP auto-matched subnet + NIC!
    ips = (await client.get("/api/v1/ips", headers=hdr)).json()
    assert ips["total"] == 1
    assert ips["items"][0]["ip_address"] == "10.20.1.10"
    assert ips["items"][0]["subnet_id"] == sub["id"]
    assert ips["items"][0]["nic_name"] == "bond0"
    assert ips["items"][0]["device_hostname"] == "prod-node-01"

    # Re-importing with skip strategy skips all 6 cleanly
    re_preview = (
        await client.post(
            "/api/v1/excel/preview",
            json={"upload_id": upload_id, "mode": "multi_sheet", "conflict_strategy": "skip"},
            headers=hdr,
        )
    ).json()
    assert re_preview["create_count"] == 0
    assert re_preview["skip_count"] == 6

    # 5. Export both 6-sheet and wide-table workbooks
    exp6 = await client.post("/api/v1/excel/export", json={"mode": "multi_sheet"}, headers=hdr)
    assert exp6.status_code == 200
    exp6_wb = load_workbook(io.BytesIO(exp6.content))
    assert len(exp6_wb.sheetnames) == 6
    assert exp6_wb["04_IP"].max_row == 3  # 2 headers + 1 IP row

    exp_wide = await client.post("/api/v1/excel/export", json={"mode": "wide_table"}, headers=hdr)
    assert exp_wide.status_code == 200


@pytest.mark.asyncio
async def test_wide_table_column_mapping_and_error_report(client: AsyncClient, auth_headers):
    tok = await _get_admin_token(client)
    hdr = auth_headers(tok)

    # Build a messy legacy single-sheet Excel with custom Chinese headers + 1 invalid IP row + unmapped column
    wb = Workbook()
    ws = wb.active
    ws.title = "老旧运维表"
    ws.append(["业务集群", "服务器名", "网卡口", "内网IP", "端口", "应用名", "访问地址", "机柜保修备注"])
    ws.append(["遗留集群A", "legacy-web-01", "eth0", "10.30.0.15", 8080, "legacy-web", "https://legacy.example.com", "2028过保"])
    ws.append(["遗留集群A", "legacy-web-02", "eth0", "999.999.999.1", 8080, "legacy-web", "https://legacy2.example.com", "坏IP行"])
    buf = io.BytesIO()
    wb.save(buf)

    up = (
        await client.post(
            "/api/v1/excel/upload",
            files={"file": ("legacy.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            headers=hdr,
        )
    ).json()
    assert up["detected_mode"] == "wide_table"
    upload_id = up["upload_id"]

    mapping = {
        "cluster_name": "业务集群",
        "device_hostname": "服务器名",
        "nic_name": "网卡口",
        "ip_address": "内网IP",
        "port": "端口",
        "service_name": "应用名",
        "full_url": "访问地址",
    }

    # All-or-nothing mode rolls back on invalid IP
    rolled = (
        await client.post(
            "/api/v1/excel/commit",
            json={
                "upload_id": upload_id,
                "mode": "wide_table",
                "sheet_name": "老旧运维表",
                "column_mapping": mapping,
                "error_mode": "all_or_nothing",
            },
            headers=hdr,
        )
    ).json()
    assert rolled["status"] == "rolled_back"
    assert rolled["failed_count"] == 1
    assert (await client.get("/api/v1/devices", headers=hdr)).json()["total"] == 0

    # Skip-errors mode imports valid entities AND allows downloading error report xlsx!
    partial = (
        await client.post(
            "/api/v1/excel/commit",
            json={
                "upload_id": upload_id,
                "mode": "wide_table",
                "sheet_name": "老旧运维表",
                "column_mapping": mapping,
                "error_mode": "skip_errors",
                "store_unmapped_to_extra": True,
            },
            headers=hdr,
        )
    ).json()
    assert partial["status"] == "completed_with_errors"
    assert partial["created_count"] >= 5
    assert partial["failed_count"] == 1

    # Verify unmapped column "机柜保修备注" was captured into device extra_attrs!
    devs = (await client.get("/api/v1/devices", params={"q": "legacy-web-01"}, headers=hdr)).json()
    assert devs["items"][0]["extra_attrs"]["机柜保修备注"] == "2028过保"

    # Download error report Excel for this batch
    err_xlsx = await client.get(f"/api/v1/excel/batches/{partial['batch_id']}/errors", headers=hdr)
    assert err_xlsx.status_code == 200
    err_wb = load_workbook(io.BytesIO(err_xlsx.content))
    assert err_wb.active.max_row == 2  # header + 1 error row
