"""Integration tests for M1 foundation: auth, lockout, forced pw change, PAT, RBAC, and scope isolation."""

from __future__ import annotations

import base64
import os

import pytest
from httpx import AsyncClient

from app.core.security import CredentialCipher


async def _login_admin_and_change_password(client: AsyncClient) -> str:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "Admin123456"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["must_change_password"] is True
    token = data["access_token"]

    # Blocked from normal endpoints before password change
    blocked = await client.get(
        "/api/v1/groups",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert blocked.status_code == 403

    # Allowed to change password
    changed = await client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "Admin123456", "new_password": "NewAdminPassword2026"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert changed.status_code == 200

    # Re-login with new password
    resp2 = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "NewAdminPassword2026"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["must_change_password"] is False
    return resp2.json()["access_token"]


@pytest.mark.asyncio
async def test_health_and_cipher():
    key1 = base64.b64encode(os.urandom(32)).decode("ascii")
    key2 = base64.b64encode(os.urandom(32)).decode("ascii")
    cipher = CredentialCipher({"1": key1, "2": key2}, active_version="1")
    enc, ver = cipher.encrypt("super-secret-ipmi-pass")
    assert ver == "1"
    assert enc.startswith("v1:")
    assert cipher.decrypt(enc) == "super-secret-ipmi-pass"

    enc2, ver2 = cipher.encrypt("rotated-secret", version="2")
    assert ver2 == "2"
    assert cipher.decrypt(enc2) == "rotated-secret"


@pytest.mark.asyncio
async def test_auth_lockout_refresh_logout_and_pat(client: AsyncClient, auth_headers):
    health = await client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    admin_token = await _login_admin_and_change_password(client)

    # Refresh token rotation
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "NewAdminPassword2026"},
    )
    refresh_token = login_resp.json()["refresh_token"]
    refreshed = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert refreshed.status_code == 200
    # Reusing rotated refresh token must fail
    reused = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert reused.status_code == 401

    # Logout revokes access token
    logout_resp = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": refreshed.json()["refresh_token"]},
        headers=auth_headers(refreshed.json()["access_token"]),
    )
    assert logout_resp.status_code == 200
    after_logout = await client.get(
        "/api/v1/auth/me",
        headers=auth_headers(refreshed.json()["access_token"]),
    )
    assert after_logout.status_code == 401

    # PAT read_only token cannot mutate, can read
    pat_resp = await client.post(
        "/api/v1/auth/tokens",
        json={"name": "ansible-ro", "scope": "read_only", "expires_in_days": 30},
        headers=auth_headers(admin_token),
    )
    assert pat_resp.status_code == 201
    pat_raw = pat_resp.json()["token"]
    assert pat_raw.startswith("csm_")

    assert (await client.get("/api/v1/network-zones", headers=auth_headers(pat_raw))).status_code == 200
    assert (
        await client.post(
            "/api/v1/tags",
            json={"name": "should-fail"},
            headers=auth_headers(pat_raw),
        )
    ).status_code == 403


@pytest.mark.asyncio
async def test_group_scope_isolation_and_system_resources(client: AsyncClient, auth_headers):
    admin_token = await _login_admin_and_change_password(client)

    # Fetch built-in roles
    roles = (await client.get("/api/v1/roles", headers=auth_headers(admin_token))).json()
    role_by_code = {r["code"]: r["id"] for r in roles}
    assert {"super_admin", "group_admin", "ops_editor", "viewer"}.issubset(role_by_code.keys())

    # Create two groups
    g_a = (
        await client.post(
            "/api/v1/groups",
            json={"name": "核心交易运维组", "code": "core-trade"},
            headers=auth_headers(admin_token),
        )
    ).json()
    g_b = (
        await client.post(
            "/api/v1/groups",
            json={"name": "大数据运维组", "code": "big-data"},
            headers=auth_headers(admin_token),
        )
    ).json()

    # Create GroupAdmin A (only Group A) and Viewer B (only Group B)
    u_a = (
        await client.post(
            "/api/v1/users",
            json={
                "username": "alice_ops",
                "display_name": "Alice",
                "password": "AlicePassword123",
                "must_change_password": False,
                "assignments": [{"group_id": g_a["id"], "role_id": role_by_code["group_admin"]}],
            },
            headers=auth_headers(admin_token),
        )
    ).json()
    u_b = (
        await client.post(
            "/api/v1/users",
            json={
                "username": "bob_ops",
                "display_name": "Bob",
                "password": "BobPassword123",
                "must_change_password": False,
                "assignments": [{"group_id": g_b["id"], "role_id": role_by_code["viewer"]}],
            },
            headers=auth_headers(admin_token),
        )
    ).json()

    # Test lockout on bob_ops then admin unlock
    for _ in range(5):
        bad = await client.post(
            "/api/v1/auth/login",
            json={"username": "bob_ops", "password": "WrongPassword1"},
        )
        assert bad.status_code == 401
    locked = await client.post(
        "/api/v1/auth/login",
        json={"username": "bob_ops", "password": "BobPassword123"},
    )
    assert locked.status_code == 401
    assert "锁定" in locked.json()["detail"]

    unlock = await client.post(
        f"/api/v1/users/{u_b['id']}/unlock",
        headers=auth_headers(admin_token),
    )
    assert unlock.status_code == 200

    # Alice logs in: sees ONLY Group A
    alice_token = (
        await client.post(
            "/api/v1/auth/login",
            json={"username": "alice_ops", "password": "AlicePassword123"},
        )
    ).json()["access_token"]

    alice_groups = (await client.get("/api/v1/groups", headers=auth_headers(alice_token))).json()
    assert [g["id"] for g in alice_groups] == [g_a["id"]]

    # Alice CAN add Bob to Group A as ops_editor
    add_ok = await client.post(
        f"/api/v1/groups/{g_a['id']}/members",
        json={"user_id": u_b["id"], "role_id": role_by_code["ops_editor"]},
        headers=auth_headers(alice_token),
    )
    assert add_ok.status_code == 201

    # Alice CANNOT assign super_admin or touch Group B
    escalate = await client.post(
        f"/api/v1/groups/{g_a['id']}/members",
        json={"user_id": u_b["id"], "role_id": role_by_code["super_admin"]},
        headers=auth_headers(alice_token),
    )
    assert escalate.status_code == 403

    cross_group = await client.post(
        f"/api/v1/groups/{g_b['id']}/members",
        json={"user_id": u_a["id"], "role_id": role_by_code["viewer"]},
        headers=auth_headers(alice_token),
    )
    assert cross_group.status_code == 403

    # Built-in network zone & dict protections
    zones = (await client.get("/api/v1/network-zones", headers=auth_headers(admin_token))).json()
    default_zone = next(z for z in zones if z["is_default"])
    del_default = await client.delete(
        f"/api/v1/network-zones/{default_zone['id']}",
        headers=auth_headers(admin_token),
    )
    assert del_default.status_code == 400

    # Dashboard summary and audit logs work
    dash = await client.get("/api/v1/dashboard/summary", headers=auth_headers(admin_token))
    assert dash.status_code == 200
    assert dash.json()["counts"]["groups"] == 2
    assert len(dash.json()["recent_audits"]) > 0
