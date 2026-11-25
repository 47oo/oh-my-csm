"""Excel bidirectional import/export engine: 6-sheet standard template, wide-table column mapping, Dry-run, and error report."""

from __future__ import annotations

import io
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import AuthContext
from app.models.assets import (
    NIC,
    Cluster,
    ClusterDevice,
    Device,
    IPAddress,
    Service,
    ServiceEndpoint,
    Subnet,
    UrlEntry,
)
from app.models.audit import ImportBatch
from app.models.system import NetworkZone, OpsGroup
from app.schemas.excel import (
    ExcelCommitOut,
    ExcelPreviewOut,
    ExcelPreviewRequest,
    ExcelRowResult,
    ExcelUploadOut,
    UploadedSheetInfo,
    WideFieldMeta,
)
from app.services.assets import (
    format_ip_str,
    get_default_zone_id,
    match_subnet_for_ip,
    normalize_cidr,
    normalize_ip,
    normalize_mac,
    parse_full_url,
    validate_ip_in_subnet,
)
from app.services.audit import record_audit
from app.services.scope import apply_scope_filter, resolve_scope

UPLOAD_STAGING_DIR = Path("/tmp/oh_my_csm_uploads")
UPLOAD_STAGING_DIR.mkdir(parents=True, exist_ok=True)

SHEET_DEFINITIONS: list[tuple[str, list[tuple[str, str]], list[Any]]] = [
    (
        "01_集群",
        [
            ("name", "集群名称*"),
            ("code", "集群编码"),
            ("cluster_type", "集群类型"),
            ("env", "环境(prod/test/dev)"),
            ("status", "状态(in_use/standby)"),
            ("owner", "负责人"),
            ("group_code", "归属分组编码"),
            ("network_zone_code", "网络域编码"),
            ("description", "备注"),
        ],
        ["生产核心K8s", "prod-k8s-01", "K8s", "prod", "in_use", "张三", "", "default", "示例集群"],
    ),
    (
        "02_设备",
        [
            ("hostname", "主机名*"),
            ("asset_code", "资产编号"),
            ("serial_number", "序列号SN"),
            ("device_type", "设备类型(physical/vm/switch)"),
            ("env", "环境"),
            ("status", "状态"),
            ("os_info", "操作系统"),
            ("spec_summary", "配置摘要"),
            ("rack_location", "机房机柜"),
            ("owner", "负责人"),
            ("cluster_names", "所属集群(逗号分隔)"),
            ("group_code", "归属分组编码"),
            ("network_zone_code", "网络域编码"),
            ("description", "备注"),
        ],
        [
            "prod-node-01",
            "AST-2026-001",
            "SN0001",
            "physical",
            "prod",
            "in_use",
            "Rocky Linux 9.4",
            "32C 128G",
            "BJ-A01-U10",
            "张三",
            "生产核心K8s",
            "",
            "default",
            "示例物理机",
        ],
    ),
    (
        "03_网卡",
        [
            ("device_hostname", "所属设备主机名*"),
            ("name", "网卡名*"),
            ("nic_type", "网卡类型(business/ipmi/bond/virtual)"),
            ("parent_nic_name", "上级Bond网卡名"),
            ("mac_address", "MAC地址"),
            ("speed_mbps", "速率Mbps"),
            ("vlan_id", "VLAN"),
            ("bond_mode", "Bond模式"),
            ("switch_port", "上联交换机口"),
            ("status", "状态(up/down)"),
            ("description", "备注"),
        ],
        ["prod-node-01", "bond0", "bond", "", "aa:bb:cc:dd:ee:01", 10000, 120, "mode4-802.3ad", "SW01:GE1/0/1", "up", "示例聚合口"],
    ),
    (
        "04_IP",
        [
            ("ip_address", "IP地址*"),
            ("ip_type", "IP类型(host/vip/reserved/gateway)"),
            ("status", "状态(allocated/reserved/free)"),
            ("network_zone_code", "网络域编码"),
            ("subnet_cidr", "所属网段CIDR(可自动匹配)"),
            ("device_hostname", "绑定设备主机名"),
            ("nic_name", "绑定网卡名"),
            ("is_primary", "是否主IP(true/false)"),
            ("usage", "用途"),
            ("group_code", "归属分组编码"),
            ("description", "备注"),
        ],
        ["10.20.1.10", "host", "allocated", "default", "", "prod-node-01", "bond0", "true", "节点业务主IP", "", ""],
    ),
    (
        "05_服务与端点",
        [
            ("service_name", "服务名称*"),
            ("service_type", "服务类型(application/database/middleware/gateway)"),
            ("env", "环境"),
            ("status", "状态"),
            ("version", "版本"),
            ("owner", "负责人"),
            ("group_code", "归属分组编码"),
            ("cluster_name", "运行集群名"),
            ("device_hostname", "运行设备主机名"),
            ("ip_address", "监听IP"),
            ("port", "监听端口"),
            ("protocol", "协议(http/https/tcp/grpc)"),
            ("endpoint_role", "端点角色"),
            ("description", "备注"),
        ],
        ["order-api", "application", "prod", "in_use", "v1.2.0", "李四", "", "生产核心K8s", "prod-node-01", "10.20.1.10", 8080, "http", "api", ""],
    ),
    (
        "06_URL",
        [
            ("name", "URL入口名称*"),
            ("full_url", "完整URL*"),
            ("access_type", "访问类型(internal/external/vpn)"),
            ("service_name", "关联服务名称"),
            ("entry_ip", "入口IP"),
            ("env", "环境"),
            ("status", "状态"),
            ("ssl_expires_at", "SSL到期日(YYYY-MM-DD)"),
            ("group_code", "归属分组编码"),
            ("description", "备注"),
        ],
        ["订单生产入口", "https://order.example.com/api", "external", "order-api", "10.20.1.10", "prod", "in_use", "2027-06-30", "", ""],
    ),
]

STANDARD_SHEET_NAMES = {s[0] for s in SHEET_DEFINITIONS}

WIDE_FIELDS: list[WideFieldMeta] = [
    WideFieldMeta(key="cluster_name", label="集群名称"),
    WideFieldMeta(key="device_hostname", label="设备主机名"),
    WideFieldMeta(key="asset_code", label="资产编号"),
    WideFieldMeta(key="device_type", label="设备类型"),
    WideFieldMeta(key="nic_name", label="网卡名称"),
    WideFieldMeta(key="mac_address", label="MAC 地址"),
    WideFieldMeta(key="vlan_id", label="VLAN ID"),
    WideFieldMeta(key="ip_address", label="IP 地址"),
    WideFieldMeta(key="ip_type", label="IP 类型"),
    WideFieldMeta(key="service_name", label="服务名称"),
    WideFieldMeta(key="port", label="服务端口"),
    WideFieldMeta(key="protocol", label="协议"),
    WideFieldMeta(key="url_name", label="URL 入口名"),
    WideFieldMeta(key="full_url", label="完整 URL"),
    WideFieldMeta(key="access_type", label="内外网类型"),
    WideFieldMeta(key="env", label="环境"),
    WideFieldMeta(key="owner", label="负责人"),
    WideFieldMeta(key="group_code", label="归属分组编码"),
    WideFieldMeta(key="description", label="备注"),
]

WIDE_HEADER_HINTS: dict[str, list[str]] = {
    "cluster_name": ["集群", "cluster"],
    "device_hostname": ["主机", "设备", "hostname", "服务器"],
    "asset_code": ["资产编号", "asset"],
    "device_type": ["设备类型", "device_type"],
    "nic_name": ["网卡", "nic", "接口"],
    "mac_address": ["mac"],
    "vlan_id": ["vlan"],
    "ip_address": ["ip地址", "ip", "地址"],
    "ip_type": ["ip类型", "vip"],
    "service_name": ["服务", "service", "应用"],
    "port": ["端口", "port"],
    "protocol": ["协议", "protocol"],
    "url_name": ["入口名", "站点名", "url名称"],
    "full_url": ["url", "域名", "访问地址", "链接"],
    "access_type": ["内外网", "访问类型", "access"],
    "env": ["环境", "env"],
    "owner": ["负责人", "owner", "维护人"],
    "group_code": ["分组", "group", "业务组"],
    "description": ["备注", "说明", "desc"],
}


def _clean_cell(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    return str(val).strip()


def _parse_int(val: Any) -> int | None:
    s = _clean_cell(val)
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _parse_bool(val: Any) -> bool:
    return _clean_cell(val).lower() in {"1", "true", "yes", "y", "是"}


def _parse_date(val: Any) -> datetime | None:
    if isinstance(val, datetime):
        return val
    s = _clean_cell(val)
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def generate_standard_template_bytes() -> bytes:
    wb = Workbook()
    # Remove default sheet
    wb.remove(wb.active)

    header_fill = PatternFill(start_color="1677FF", end_color="1677FF", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    sub_fill = PatternFill(start_color="E6F4FF", end_color="E6F4FF", fill_type="solid")

    for sheet_name, cols, example in SHEET_DEFINITIONS:
        ws = wb.create_sheet(title=sheet_name)
        ws.append([c[0] for c in cols])
        ws.append([c[1] for c in cols])
        ws.append(example)
        ws.freeze_panes = "A3"
        for col_idx in range(1, len(cols) + 1):
            cell1 = ws.cell(row=1, column=col_idx)
            cell1.fill = header_fill
            cell1.font = header_font
            cell2 = ws.cell(row=2, column=col_idx)
            cell2.fill = sub_fill
            ws.column_dimensions[cell1.column_letter].width = 20

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def generate_error_report_bytes(errors: list[dict[str, Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "导入错误清单"
    ws.append(["Sheet", "行号", "资产类型", "自然键/标识", "错误原因"])
    header_fill = PatternFill(start_color="D03050", end_color="D03050", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    for idx in range(1, 6):
        c = ws.cell(row=1, column=idx)
        c.fill = header_fill
        c.font = header_font
        ws.column_dimensions[c.column_letter].width = 24
    ws.column_dimensions["E"].width = 48

    for err in errors:
        ws.append(
            [
                err.get("sheet", ""),
                err.get("row_num", ""),
                err.get("entity_type", ""),
                err.get("natural_key", ""),
                err.get("reason", ""),
            ]
        )

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def save_and_inspect_upload(filename: str, content: bytes) -> ExcelUploadOut:
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无法解析 Excel 文件（请上传标准 .xlsx 文件）: {exc}",
        ) from exc

    upload_id = uuid.uuid4().hex
    target_path = UPLOAD_STAGING_DIR / f"{upload_id}.xlsx"
    meta_path = UPLOAD_STAGING_DIR / f"{upload_id}.name"
    target_path.write_bytes(content)
    meta_path.write_text(filename, encoding="utf-8")

    sheets: list[UploadedSheetInfo] = []
    for sname in wb.sheetnames:
        ws = wb[sname]
        rows_iter = ws.iter_rows(values_only=True)
        first = next(rows_iter, None)
        headers = [_clean_cell(c) for c in (first or [])]
        non_empty_headers = [h for h in headers if h]
        samples: list[dict[str, Any]] = []
        row_count = 0
        for row in rows_iter:
            vals = [_clean_cell(c) for c in row]
            if not any(vals):
                continue
            row_count += 1
            if len(samples) < 5:
                samples.append({headers[i]: vals[i] for i in range(min(len(headers), len(vals))) if headers[i]})
        sheets.append(
            UploadedSheetInfo(
                name=sname,
                headers=non_empty_headers,
                row_count=row_count,
                sample_rows=samples,
            )
        )
    wb.close()

    detected_mode = "multi_sheet" if any(s.name in STANDARD_SHEET_NAMES for s in sheets) else "wide_table"

    # Suggest column mapping for the first sheet if wide_table
    suggested: dict[str, str] = {}
    if sheets:
        first_headers = sheets[0].headers
        used_headers: set[str] = set()
        for field_key, hints in WIDE_HEADER_HINTS.items():
            for h in first_headers:
                if h in used_headers:
                    continue
                low = h.lower()
                if any(hint in low for hint in hints):
                    suggested[field_key] = h
                    used_headers.add(h)
                    break

    return ExcelUploadOut(
        upload_id=upload_id,
        filename=filename,
        detected_mode=detected_mode,
        sheets=sheets,
        wide_fields=WIDE_FIELDS,
        suggested_mapping=suggested,
    )


def _read_sheet_dicts(ws: Any) -> list[tuple[int, dict[str, str]]]:
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [_clean_cell(c) for c in rows[0]]
    start_idx = 1
    # Standard template has human-readable Chinese subtitle in row 2
    if len(rows) > 1:
        row2 = [_clean_cell(c) for c in rows[1]]
        if any("*" in x for x in row2):
            start_idx = 2
    out: list[tuple[int, dict[str, str]]] = []
    for idx in range(start_idx, len(rows)):
        vals = [_clean_cell(c) for c in rows[idx]]
        if not any(vals):
            continue
        rec = {headers[i]: vals[i] for i in range(min(len(headers), len(vals))) if headers[i]}
        out.append((idx + 1, rec))
    return out


def _apply_field_update(
    obj: Any,
    updates: dict[str, Any],
    strategy: str,
) -> bool:
    changed = False
    for k, v in updates.items():
        if strategy == "merge_non_empty" and (v is None or v == ""):
            continue
        if getattr(obj, k) != v:
            setattr(obj, k, v)
            changed = True
    return changed


async def run_excel_import(
    db: AsyncSession,
    auth: AuthContext,
    req: ExcelPreviewRequest,
    *,
    commit_mode: bool,
) -> ExcelPreviewOut | ExcelCommitOut:
    xlsx_path = UPLOAD_STAGING_DIR / f"{req.upload_id}.xlsx"
    name_path = UPLOAD_STAGING_DIR / f"{req.upload_id}.name"
    if not xlsx_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="上传会话已过期或不存在，请重新上传 Excel 文件",
        )
    filename = name_path.read_text(encoding="utf-8") if name_path.exists() else "upload.xlsx"

    wb = load_workbook(xlsx_path, read_only=True, data_only=True)

    # Pre-load lookup maps
    groups_by_code = {
        g.code: g.id
        for g in (
            await db.execute(select(OpsGroup).where(OpsGroup.deleted_at.is_(None)))
        )
        .scalars()
        .all()
    }
    zones_by_code = {
        z.code: z.id
        for z in (
            await db.execute(select(NetworkZone).where(NetworkZone.deleted_at.is_(None)))
        )
        .scalars()
        .all()
    }
    default_zone_id = await get_default_zone_id(db)

    # Normalize input into 6 ordered lists of (sheet, row_num, payload_dict)
    stage_rows: dict[str, list[tuple[str, int, dict[str, Any]]]] = {
        "cluster": [],
        "device": [],
        "nic": [],
        "ip": [],
        "service": [],
        "url": [],
    }

    if req.mode == "multi_sheet":
        sheet_to_stage = {
            "01_集群": "cluster",
            "02_设备": "device",
            "03_网卡": "nic",
            "04_IP": "ip",
            "05_服务与端点": "service",
            "06_URL": "url",
        }
        for sname, stage_key in sheet_to_stage.items():
            if sname in wb.sheetnames:
                for row_num, rec in _read_sheet_dicts(wb[sname]):
                    stage_rows[stage_key].append((sname, row_num, rec))
    else:
        sname = req.sheet_name or wb.sheetnames[0]
        if sname not in wb.sheetnames:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Sheet '{sname}' 不存在")
        mapped_sources = set(req.column_mapping.values())
        for row_num, raw_rec in _read_sheet_dicts(wb[sname]):
            mapped: dict[str, str] = {}
            for target_k, source_h in req.column_mapping.items():
                if source_h:
                    mapped[target_k] = raw_rec.get(source_h, "")
            extra_kv: dict[str, Any] = {}
            if req.store_unmapped_to_extra:
                for k, v in raw_rec.items():
                    if k not in mapped_sources and v != "":
                        extra_kv[k] = v

            if mapped.get("cluster_name"):
                stage_rows["cluster"].append(
                    (
                        sname,
                        row_num,
                        {
                            "name": mapped["cluster_name"],
                            "env": mapped.get("env") or None,
                            "owner": mapped.get("owner") or None,
                            "group_code": mapped.get("group_code") or "",
                        },
                    )
                )
            if mapped.get("device_hostname"):
                stage_rows["device"].append(
                    (
                        sname,
                        row_num,
                        {
                            "hostname": mapped["device_hostname"],
                            "asset_code": mapped.get("asset_code") or "",
                            "device_type": mapped.get("device_type") or "physical",
                            "env": mapped.get("env") or "",
                            "owner": mapped.get("owner") or "",
                            "cluster_names": mapped.get("cluster_name") or "",
                            "group_code": mapped.get("group_code") or "",
                            "description": mapped.get("description") or "",
                            "_extra_attrs": extra_kv,
                        },
                    )
                )
            if mapped.get("device_hostname") and mapped.get("nic_name"):
                stage_rows["nic"].append(
                    (
                        sname,
                        row_num,
                        {
                            "device_hostname": mapped["device_hostname"],
                            "name": mapped["nic_name"],
                            "mac_address": mapped.get("mac_address") or "",
                            "vlan_id": mapped.get("vlan_id") or "",
                        },
                    )
                )
            if mapped.get("ip_address"):
                stage_rows["ip"].append(
                    (
                        sname,
                        row_num,
                        {
                            "ip_address": mapped["ip_address"],
                            "ip_type": mapped.get("ip_type") or "host",
                            "device_hostname": mapped.get("device_hostname") or "",
                            "nic_name": mapped.get("nic_name") or "",
                            "group_code": mapped.get("group_code") or "",
                            "usage": mapped.get("description") or "",
                        },
                    )
                )
            if mapped.get("service_name"):
                stage_rows["service"].append(
                    (
                        sname,
                        row_num,
                        {
                            "service_name": mapped["service_name"],
                            "env": mapped.get("env") or "",
                            "owner": mapped.get("owner") or "",
                            "group_code": mapped.get("group_code") or "",
                            "cluster_name": mapped.get("cluster_name") or "",
                            "device_hostname": mapped.get("device_hostname") or "",
                            "ip_address": mapped.get("ip_address") or "",
                            "port": mapped.get("port") or "",
                            "protocol": mapped.get("protocol") or "tcp",
                        },
                    )
                )
            if mapped.get("full_url"):
                stage_rows["url"].append(
                    (
                        sname,
                        row_num,
                        {
                            "name": mapped.get("url_name") or mapped.get("service_name") or mapped["full_url"],
                            "full_url": mapped["full_url"],
                            "access_type": mapped.get("access_type") or "internal",
                            "service_name": mapped.get("service_name") or "",
                            "entry_ip": mapped.get("ip_address") or "",
                            "env": mapped.get("env") or "",
                            "group_code": mapped.get("group_code") or "",
                            "description": mapped.get("description") or "",
                        },
                    )
                )
    wb.close()

    def _resolve_group(code: str) -> int | None:
        if code:
            if code not in groups_by_code:
                raise ValueError(f"归属分组编码 '{code}' 不存在")
            return groups_by_code[code]
        return req.default_group_id

    def _resolve_zone(code: str) -> int:
        if code:
            if code not in zones_by_code:
                raise ValueError(f"网络域编码 '{code}' 不存在")
            return zones_by_code[code]
        return default_zone_id

    def _check_import_perm(
        group_id: int | None,
        cluster_ids: list[int] | None = None,
    ) -> None:
        if auth.has_permission_in_group("excel:import", group_id) and auth.has_permission_in_group(
            "asset:write", group_id
        ):
            return
        if cluster_ids and all(
            auth.has_permission_in_cluster("excel:import", cid)
            and auth.has_permission_in_cluster("asset:write", cid)
            for cid in cluster_ids
        ):
            return
        raise ValueError("缺少目标分组或集群的 excel:import / asset:write 权限")

    results: list[ExcelRowResult] = []
    batch_obj: ImportBatch | None = None

    # Use a savepoint so preview mode rolls back cleanly without leaving any writes!
    outer_sp = await db.begin_nested()
    try:
        if commit_mode:
            batch_obj = ImportBatch(
                user_id=auth.user.id,
                filename=filename,
                mode=req.mode,
                conflict_strategy=req.conflict_strategy,
                error_mode=req.error_mode,
                status="running",
            )
            db.add(batch_obj)
            await db.flush()

        # Store plain scalar IDs (never ORM instances) so nested savepoint rollbacks never expire cached lookups
        cluster_id_cache: dict[str, int] = {
            name: cid
            for cid, name in (
                await db.execute(select(Cluster.id, Cluster.name).where(Cluster.deleted_at.is_(None)))
            ).all()
        }
        device_id_cache: dict[str, tuple[int, int | None]] = {
            host: (did, gid)
            for did, host, gid in (
                await db.execute(
                    select(Device.id, Device.hostname, Device.group_id).where(Device.deleted_at.is_(None))
                )
            ).all()
        }
        nic_id_cache: dict[tuple[int, str], int] = {
            (did, nname): nid
            for nid, did, nname in (
                await db.execute(select(NIC.id, NIC.device_id, NIC.name).where(NIC.deleted_at.is_(None)))
            ).all()
        }
        ip_id_cache: dict[tuple[int, str], int] = {
            (zid, format_ip_str(addr)): iid
            for iid, zid, addr in (
                await db.execute(
                    select(IPAddress.id, IPAddress.network_zone_id, IPAddress.ip_address).where(
                        IPAddress.deleted_at.is_(None)
                    )
                )
            ).all()
        }
        service_id_cache: dict[str, int] = {
            sname: sid
            for sid, sname in (
                await db.execute(select(Service.id, Service.name).where(Service.deleted_at.is_(None)))
            ).all()
        }

        def _try_lookup_ip_id(raw_ip_text: str) -> int | None:
            cleaned = raw_ip_text.strip()
            if not cleaned:
                return None
            try:
                norm = normalize_ip(cleaned)
            except HTTPException:
                return None
            return ip_id_cache.get((default_zone_id, norm))

        async def _audit_if_commit(action: str, etype: str, obj_id: int, label: str, gid: int | None):
            if commit_mode and batch_obj is not None:
                await record_audit(
                    db,
                    action=f"import_{action}",
                    target_type=etype,
                    target_id=obj_id,
                    target_label=label,
                    group_id=gid,
                    import_batch_id=batch_obj.id,
                    auth=auth,
                )

        # ---- Stage 1: Clusters ----
        for sname, row_num, rec in stage_rows["cluster"]:
            name = (rec.get("name") or "").strip()
            key = name or f"row-{row_num}"
            row_sp = await db.begin_nested()
            try:
                if not name:
                    raise ValueError("集群名称不能为空")
                gid = _resolve_group(rec.get("group_code") or "")
                zid = _resolve_zone(rec.get("network_zone_code") or "")
                env = rec.get("env") or None
                existing_id = cluster_id_cache.get(name)
                _check_import_perm(gid, [existing_id] if existing_id is not None else None)
                if existing_id is not None:
                    if req.conflict_strategy == "skip":
                        results.append(ExcelRowResult(sheet=sname, row_num=row_num, entity_type="cluster", natural_key=key, operation="skip", reason="已存在"))
                    else:
                        existing = await db.get(Cluster, existing_id)
                        assert existing is not None
                        changed = _apply_field_update(
                            existing,
                            {
                                "code": rec.get("code") or None,
                                "cluster_type": rec.get("cluster_type") or None,
                                "env": env,
                                "status": rec.get("status") or None,
                                "owner": rec.get("owner") or None,
                                "description": rec.get("description") or None,
                            },
                            req.conflict_strategy,
                        )
                        existing.updated_by = auth.user.id
                        await db.flush()
                        op_type = "update" if changed else "skip"
                        if changed:
                            await _audit_if_commit("update", "cluster", existing.id, existing.name, existing.group_id)
                        results.append(ExcelRowResult(sheet=sname, row_num=row_num, entity_type="cluster", natural_key=key, operation=op_type))
                else:
                    obj = Cluster(
                        name=name,
                        code=rec.get("code") or None,
                        cluster_type=rec.get("cluster_type") or None,
                        env=env,
                        status=rec.get("status") or "in_use",
                        owner=rec.get("owner") or None,
                        group_id=gid,
                        network_zone_id=zid,
                        description=rec.get("description") or None,
                        created_by=auth.user.id,
                        updated_by=auth.user.id,
                    )
                    db.add(obj)
                    await db.flush()
                    cluster_id_cache[name] = obj.id
                    await _audit_if_commit("create", "cluster", obj.id, obj.name, obj.group_id)
                    results.append(ExcelRowResult(sheet=sname, row_num=row_num, entity_type="cluster", natural_key=key, operation="create"))
                await row_sp.commit()
            except (ValueError, HTTPException, SQLAlchemyError) as exc:
                await row_sp.rollback()
                results.append(ExcelRowResult(sheet=sname, row_num=row_num, entity_type="cluster", natural_key=key, operation="error", reason=str(exc)))

        # ---- Stage 2: Devices ----
        for sname, row_num, rec in stage_rows["device"]:
            hostname = (rec.get("hostname") or "").strip()
            key = hostname or f"row-{row_num}"
            row_sp = await db.begin_nested()
            try:
                if not hostname:
                    raise ValueError("设备主机名不能为空")
                gid = _resolve_group(rec.get("group_code") or "")
                cl_str_pre = rec.get("cluster_names") or ""
                target_cids = [
                    cluster_id_cache[cname]
                    for cname in [c.strip() for c in cl_str_pre.replace("，", ",").split(",") if c.strip()]
                    if cname in cluster_id_cache
                ]
                _check_import_perm(gid, target_cids or None)
                zid = _resolve_zone(rec.get("network_zone_code") or "")
                extra_kv = rec.get("_extra_attrs") or {}

                existing_dev_info = device_id_cache.get(hostname)
                if existing_dev_info is not None:
                    target_dev_id = existing_dev_info[0]
                    if req.conflict_strategy == "skip":
                        results.append(ExcelRowResult(sheet=sname, row_num=row_num, entity_type="device", natural_key=key, operation="skip", reason="已存在"))
                    else:
                        existing = await db.get(Device, target_dev_id)
                        assert existing is not None
                        merged_extra = {**(existing.extra_attrs or {}), **extra_kv} if extra_kv else existing.extra_attrs
                        changed = _apply_field_update(
                            existing,
                            {
                                "asset_code": rec.get("asset_code") or None,
                                "serial_number": rec.get("serial_number") or None,
                                "device_type": rec.get("device_type") or None,
                                "env": rec.get("env") or None,
                                "status": rec.get("status") or None,
                                "os_info": rec.get("os_info") or None,
                                "spec_summary": rec.get("spec_summary") or None,
                                "rack_location": rec.get("rack_location") or None,
                                "owner": rec.get("owner") or None,
                                "description": rec.get("description") or None,
                                "extra_attrs": merged_extra,
                            },
                            req.conflict_strategy,
                        )
                        existing.updated_by = auth.user.id
                        await db.flush()
                        op_type = "update" if changed else "skip"
                        if changed:
                            await _audit_if_commit("update", "device", existing.id, existing.hostname, existing.group_id)
                        results.append(ExcelRowResult(sheet=sname, row_num=row_num, entity_type="device", natural_key=key, operation=op_type))
                else:
                    target_dev = Device(
                        hostname=hostname,
                        asset_code=rec.get("asset_code") or None,
                        serial_number=rec.get("serial_number") or None,
                        device_type=rec.get("device_type") or "physical",
                        env=rec.get("env") or None,
                        status=rec.get("status") or "in_use",
                        os_info=rec.get("os_info") or None,
                        spec_summary=rec.get("spec_summary") or None,
                        rack_location=rec.get("rack_location") or None,
                        owner=rec.get("owner") or None,
                        group_id=gid,
                        network_zone_id=zid,
                        description=rec.get("description") or None,
                        extra_attrs=extra_kv,
                        created_by=auth.user.id,
                        updated_by=auth.user.id,
                    )
                    db.add(target_dev)
                    await db.flush()
                    target_dev_id = target_dev.id
                    device_id_cache[hostname] = (target_dev_id, gid)
                    await _audit_if_commit("create", "device", target_dev_id, hostname, gid)
                    results.append(ExcelRowResult(sheet=sname, row_num=row_num, entity_type="device", natural_key=key, operation="create"))

                # Link clusters if provided
                cl_str = rec.get("cluster_names") or ""
                for cname in [c.strip() for c in cl_str.replace("，", ",").split(",") if c.strip()]:
                    cl_id = cluster_id_cache.get(cname)
                    if cl_id is None:
                        raise ValueError(f"引用的集群 '{cname}' 不存在")
                    link_exists = (
                        await db.execute(
                            select(ClusterDevice.cluster_id).where(
                                ClusterDevice.cluster_id == cl_id,
                                ClusterDevice.device_id == target_dev_id,
                            )
                        )
                    ).scalar_one_or_none()
                    if link_exists is None:
                        db.add(ClusterDevice(cluster_id=cl_id, device_id=target_dev_id))
                await db.flush()
                await row_sp.commit()
            except (ValueError, HTTPException, SQLAlchemyError) as exc:
                await row_sp.rollback()
                results.append(ExcelRowResult(sheet=sname, row_num=row_num, entity_type="device", natural_key=key, operation="error", reason=str(exc)))

        # ---- Stage 3: NICs (support Bond parent_nic_name) ----
        for sname, row_num, rec in stage_rows["nic"]:
            dhost = (rec.get("device_hostname") or "").strip()
            nname = (rec.get("name") or "").strip()
            key = f"{dhost}:{nname}"
            row_sp = await db.begin_nested()
            try:
                if not dhost or not nname:
                    raise ValueError("所属设备主机名与网卡名均不能为空")
                dev_info = device_id_cache.get(dhost)
                if dev_info is None:
                    raise ValueError(f"所属设备 '{dhost}' 不存在")
                dev_id, dev_gid = dev_info
                _check_import_perm(dev_gid)
                mac = normalize_mac(rec.get("mac_address") or None)
                parent_name = (rec.get("parent_nic_name") or "").strip()
                parent_id: int | None = None
                if parent_name:
                    parent_id = nic_id_cache.get((dev_id, parent_name))
                    if parent_id is None:
                        raise ValueError(f"上级聚合网卡 '{parent_name}' 在设备 '{dhost}' 上不存在（请将聚合口放在成员口之前）")

                existing_nic_id = nic_id_cache.get((dev_id, nname))
                if existing_nic_id is not None:
                    if req.conflict_strategy == "skip":
                        results.append(ExcelRowResult(sheet=sname, row_num=row_num, entity_type="nic", natural_key=key, operation="skip", reason="已存在"))
                    else:
                        existing = await db.get(NIC, existing_nic_id)
                        assert existing is not None
                        changed = _apply_field_update(
                            existing,
                            {
                                "nic_type": rec.get("nic_type") or None,
                                "parent_nic_id": parent_id,
                                "mac_address": mac,
                                "speed_mbps": _parse_int(rec.get("speed_mbps")),
                                "vlan_id": _parse_int(rec.get("vlan_id")),
                                "bond_mode": rec.get("bond_mode") or None,
                                "switch_port": rec.get("switch_port") or None,
                                "status": rec.get("status") or None,
                                "description": rec.get("description") or None,
                            },
                            req.conflict_strategy,
                        )
                        existing.updated_by = auth.user.id
                        await db.flush()
                        op_type = "update" if changed else "skip"
                        if changed:
                            await _audit_if_commit("update", "nic", existing.id, key, dev_gid)
                        results.append(ExcelRowResult(sheet=sname, row_num=row_num, entity_type="nic", natural_key=key, operation=op_type))
                else:
                    obj = NIC(
                        device_id=dev_id,
                        parent_nic_id=parent_id,
                        name=nname,
                        nic_type=rec.get("nic_type") or "business",
                        mac_address=mac,
                        speed_mbps=_parse_int(rec.get("speed_mbps")),
                        vlan_id=_parse_int(rec.get("vlan_id")),
                        bond_mode=rec.get("bond_mode") or None,
                        switch_port=rec.get("switch_port") or None,
                        status=rec.get("status") or "up",
                        description=rec.get("description") or None,
                        created_by=auth.user.id,
                        updated_by=auth.user.id,
                    )
                    db.add(obj)
                    await db.flush()
                    nic_id_cache[(dev_id, nname)] = obj.id
                    await _audit_if_commit("create", "nic", obj.id, key, dev_gid)
                    results.append(ExcelRowResult(sheet=sname, row_num=row_num, entity_type="nic", natural_key=key, operation="create"))
                await row_sp.commit()
            except (ValueError, HTTPException, SQLAlchemyError) as exc:
                await row_sp.rollback()
                reason = exc.detail if isinstance(exc, HTTPException) else str(exc)
                results.append(ExcelRowResult(sheet=sname, row_num=row_num, entity_type="nic", natural_key=key, operation="error", reason=str(reason)))

        # ---- Stage 4: IPs ----
        for sname, row_num, rec in stage_rows["ip"]:
            raw_ip = (rec.get("ip_address") or "").strip()
            key = raw_ip or f"row-{row_num}"
            row_sp = await db.begin_nested()
            try:
                if not raw_ip:
                    raise ValueError("IP 地址不能为空")
                ip_str = normalize_ip(raw_ip)
                zid = _resolve_zone(rec.get("network_zone_code") or "")
                gid = _resolve_group(rec.get("group_code") or "")

                nic_id: int | None = None
                dhost = (rec.get("device_hostname") or "").strip()
                nname = (rec.get("nic_name") or "").strip()
                if dhost and nname:
                    dev_info = device_id_cache.get(dhost)
                    if dev_info is None:
                        raise ValueError(f"绑定设备 '{dhost}' 不存在")
                    dev_id, dev_gid = dev_info
                    nic_id = nic_id_cache.get((dev_id, nname))
                    if nic_id is None:
                        raise ValueError(f"绑定网卡 '{dhost}:{nname}' 不存在")
                    if gid is None:
                        gid = dev_gid

                _check_import_perm(gid)

                subnet_id: int | None = None
                cidr_raw = (rec.get("subnet_cidr") or "").strip()
                if cidr_raw:
                    cidr_norm = normalize_cidr(cidr_raw)
                    validate_ip_in_subnet(ip_str, cidr_norm)
                    sub = (
                        await db.execute(
                            select(Subnet).where(
                                Subnet.network_zone_id == zid,
                                Subnet.cidr == cidr_norm,
                                Subnet.deleted_at.is_(None),
                            )
                        )
                    ).scalar_one_or_none()
                    if sub is None:
                        raise ValueError(f"指定网段 '{cidr_norm}' 在目标网络域不存在")
                    subnet_id = sub.id
                else:
                    matched = await match_subnet_for_ip(db, zid, ip_str)
                    subnet_id = matched.id if matched else None

                existing_ip_id = ip_id_cache.get((zid, ip_str))
                if existing_ip_id is not None:
                    if req.conflict_strategy == "skip":
                        results.append(ExcelRowResult(sheet=sname, row_num=row_num, entity_type="ip", natural_key=ip_str, operation="skip", reason="已存在"))
                    else:
                        existing = await db.get(IPAddress, existing_ip_id)
                        assert existing is not None
                        changed = _apply_field_update(
                            existing,
                            {
                                "subnet_id": subnet_id,
                                "nic_id": nic_id,
                                "ip_type": rec.get("ip_type") or None,
                                "status": rec.get("status") or None,
                                "usage": rec.get("usage") or None,
                                "description": rec.get("description") or None,
                            },
                            req.conflict_strategy,
                        )
                        existing.updated_by = auth.user.id
                        await db.flush()
                        op_type = "update" if changed else "skip"
                        if changed:
                            await _audit_if_commit("update", "ip", existing.id, ip_str, existing.group_id)
                        results.append(ExcelRowResult(sheet=sname, row_num=row_num, entity_type="ip", natural_key=ip_str, operation=op_type))
                else:
                    obj = IPAddress(
                        network_zone_id=zid,
                        subnet_id=subnet_id,
                        nic_id=nic_id,
                        group_id=gid,
                        ip_address=ip_str,
                        ip_type=rec.get("ip_type") or "host",
                        is_primary=_parse_bool(rec.get("is_primary")),
                        status=rec.get("status") or "allocated",
                        usage=rec.get("usage") or None,
                        description=rec.get("description") or None,
                        created_by=auth.user.id,
                        updated_by=auth.user.id,
                    )
                    db.add(obj)
                    await db.flush()
                    ip_id_cache[(zid, ip_str)] = obj.id
                    await _audit_if_commit("create", "ip", obj.id, ip_str, gid)
                    results.append(ExcelRowResult(sheet=sname, row_num=row_num, entity_type="ip", natural_key=ip_str, operation="create"))
                await row_sp.commit()
            except (ValueError, HTTPException, SQLAlchemyError) as exc:
                await row_sp.rollback()
                reason = exc.detail if isinstance(exc, HTTPException) else str(exc)
                results.append(ExcelRowResult(sheet=sname, row_num=row_num, entity_type="ip", natural_key=key, operation="error", reason=str(reason)))

        # ---- Stage 5: Services & Endpoints ----
        for sname, row_num, rec in stage_rows["service"]:
            svc_name = (rec.get("service_name") or "").strip()
            key = svc_name or f"row-{row_num}"
            row_sp = await db.begin_nested()
            try:
                if not svc_name:
                    raise ValueError("服务名称不能为空")
                gid = _resolve_group(rec.get("group_code") or "")
                cname_single = (rec.get("cluster_name") or "").strip()
                target_cids = [cluster_id_cache[cname_single]] if cname_single in cluster_id_cache else None
                _check_import_perm(gid, target_cids)

                existing_svc_id = service_id_cache.get(svc_name)
                if existing_svc_id is not None:
                    svc_id = existing_svc_id
                    if req.conflict_strategy == "skip":
                        op_type: Any = "skip"
                    else:
                        existing = await db.get(Service, existing_svc_id)
                        assert existing is not None
                        changed = _apply_field_update(
                            existing,
                            {
                                "service_type": rec.get("service_type") or None,
                                "env": rec.get("env") or None,
                                "status": rec.get("status") or None,
                                "version": rec.get("version") or None,
                                "owner": rec.get("owner") or None,
                                "description": rec.get("description") or None,
                            },
                            req.conflict_strategy,
                        )
                        existing.updated_by = auth.user.id
                        op_type = "update" if changed else "skip"
                        if changed:
                            await _audit_if_commit("update", "service", existing.id, existing.name, existing.group_id)
                else:
                    svc_obj = Service(
                        name=svc_name,
                        service_type=rec.get("service_type") or "application",
                        env=rec.get("env") or None,
                        status=rec.get("status") or "in_use",
                        version=rec.get("version") or None,
                        owner=rec.get("owner") or None,
                        group_id=gid,
                        description=rec.get("description") or None,
                        created_by=auth.user.id,
                        updated_by=auth.user.id,
                    )
                    db.add(svc_obj)
                    await db.flush()
                    svc_id = svc_obj.id
                    service_id_cache[svc_name] = svc_id
                    await _audit_if_commit("create", "service", svc_id, svc_name, gid)
                    op_type = "create"

                # Optional endpoint row attached to service
                ep_port = _parse_int(rec.get("port"))
                ep_cluster_id = cluster_id_cache.get((rec.get("cluster_name") or "").strip())
                ep_dev_info = device_id_cache.get((rec.get("device_hostname") or "").strip())
                ep_device_id = ep_dev_info[0] if ep_dev_info else None
                ep_ip_str = (rec.get("ip_address") or "").strip()
                ep_ip_id = _try_lookup_ip_id(ep_ip_str)
                if ep_port or ep_cluster_id or ep_device_id or ep_ip_id:
                    ep_exists = (
                        await db.execute(
                            select(ServiceEndpoint.id).where(
                                ServiceEndpoint.service_id == svc_id,
                                ServiceEndpoint.cluster_id == ep_cluster_id,
                                ServiceEndpoint.device_id == ep_device_id,
                                ServiceEndpoint.ip_id == ep_ip_id,
                                ServiceEndpoint.port == ep_port,
                            )
                        )
                    ).scalar_one_or_none()
                    if ep_exists is None:
                        db.add(
                            ServiceEndpoint(
                                service_id=svc_id,
                                cluster_id=ep_cluster_id,
                                device_id=ep_device_id,
                                ip_id=ep_ip_id,
                                listen_ip_text=ep_ip_str if (ep_ip_str and ep_ip_id is None) else None,
                                port=ep_port,
                                protocol=(rec.get("protocol") or "tcp").lower(),
                                endpoint_role=rec.get("endpoint_role") or None,
                            )
                        )
                        await db.flush()
                        if op_type == "skip":
                            op_type = "update"

                results.append(ExcelRowResult(sheet=sname, row_num=row_num, entity_type="service", natural_key=key, operation=op_type))
                await row_sp.commit()
            except (ValueError, HTTPException, SQLAlchemyError) as exc:
                await row_sp.rollback()
                reason = exc.detail if isinstance(exc, HTTPException) else str(exc)
                results.append(ExcelRowResult(sheet=sname, row_num=row_num, entity_type="service", natural_key=key, operation="error", reason=str(reason)))

        # ---- Stage 6: URLs ----
        url_id_cache: dict[str, int] = {
            f_url: uid
            for uid, f_url in (
                await db.execute(select(UrlEntry.id, UrlEntry.full_url).where(UrlEntry.deleted_at.is_(None)))
            ).all()
        }
        for sname, row_num, rec in stage_rows["url"]:
            raw_url = (rec.get("full_url") or "").strip()
            key = raw_url or f"row-{row_num}"
            row_sp = await db.begin_nested()
            try:
                if not raw_url:
                    raise ValueError("完整 URL 不能为空")
                parsed = parse_full_url(raw_url)
                gid = _resolve_group(rec.get("group_code") or "")
                svc_name = (rec.get("service_name") or "").strip()
                svc_id = service_id_cache.get(svc_name) if svc_name else None
                entry_ip_id = _try_lookup_ip_id(rec.get("entry_ip") or "")

                if not (
                    auth.has_permission_in_group("excel:import", gid)
                    and auth.has_permission_in_group("asset:write", gid)
                ):
                    import_scope = await resolve_scope(db, auth, "asset:write")
                    ok_via_svc = svc_id is not None and import_scope.allows("service", svc_id, gid)
                    ok_via_ip = entry_ip_id is not None and import_scope.allows("ip", entry_ip_id, gid)
                    if not (ok_via_svc or ok_via_ip):
                        raise ValueError("缺少目标分组或关联集群的 excel:import / asset:write 权限")
                ssl_dt = _parse_date(rec.get("ssl_expires_at"))

                existing_url_id = url_id_cache.get(parsed["full_url"])
                if existing_url_id is not None:
                    if req.conflict_strategy == "skip":
                        results.append(ExcelRowResult(sheet=sname, row_num=row_num, entity_type="url", natural_key=parsed["full_url"], operation="skip", reason="已存在"))
                    else:
                        existing = await db.get(UrlEntry, existing_url_id)
                        assert existing is not None
                        changed = _apply_field_update(
                            existing,
                            {
                                "name": (rec.get("name") or "").strip() or None,
                                "access_type": rec.get("access_type") or None,
                                "service_id": svc_id,
                                "entry_ip_id": entry_ip_id,
                                "env": rec.get("env") or None,
                                "status": rec.get("status") or None,
                                "ssl_expires_at": ssl_dt,
                                "description": rec.get("description") or None,
                            },
                            req.conflict_strategy,
                        )
                        existing.updated_by = auth.user.id
                        await db.flush()
                        op_type = "update" if changed else "skip"
                        if changed:
                            await _audit_if_commit("update", "url", existing.id, existing.name, existing.group_id)
                        results.append(ExcelRowResult(sheet=sname, row_num=row_num, entity_type="url", natural_key=parsed["full_url"], operation=op_type))
                else:
                    obj = UrlEntry(
                        name=(rec.get("name") or parsed["domain"]).strip(),
                        full_url=parsed["full_url"],
                        protocol=parsed["protocol"],
                        domain=parsed["domain"],
                        port=parsed["port"],
                        path=parsed["path"],
                        is_https=parsed["is_https"],
                        access_type=rec.get("access_type") or "internal",
                        service_id=svc_id,
                        entry_ip_id=entry_ip_id,
                        env=rec.get("env") or None,
                        status=rec.get("status") or "in_use",
                        ssl_expires_at=ssl_dt,
                        group_id=gid,
                        description=rec.get("description") or None,
                        created_by=auth.user.id,
                        updated_by=auth.user.id,
                    )
                    db.add(obj)
                    await db.flush()
                    url_id_cache[parsed["full_url"]] = obj.id
                    await _audit_if_commit("create", "url", obj.id, obj.name, gid)
                    results.append(ExcelRowResult(sheet=sname, row_num=row_num, entity_type="url", natural_key=parsed["full_url"], operation="create"))
                await row_sp.commit()
            except (ValueError, HTTPException, SQLAlchemyError) as exc:
                await row_sp.rollback()
                reason = exc.detail if isinstance(exc, HTTPException) else str(exc)
                results.append(ExcelRowResult(sheet=sname, row_num=row_num, entity_type="url", natural_key=key, operation="error", reason=str(reason)))

        create_cnt = sum(1 for r in results if r.operation == "create")
        update_cnt = sum(1 for r in results if r.operation == "update")
        skip_cnt = sum(1 for r in results if r.operation == "skip")
        errors = [r for r in results if r.operation == "error"]

        if not commit_mode:
            await outer_sp.rollback()
            can_commit = (len(errors) == 0) or (req.error_mode == "skip_errors")
            return ExcelPreviewOut(
                upload_id=req.upload_id,
                mode=req.mode,
                total_rows=len(results),
                create_count=create_cnt,
                update_count=update_cnt,
                skip_count=skip_cnt,
                error_count=len(errors),
                can_commit=can_commit,
                rows=results[:200],
                errors=errors,
            )

        # Commit mode
        if errors and req.error_mode == "all_or_nothing":
            await outer_sp.rollback()
            failed_batch = ImportBatch(
                user_id=auth.user.id,
                filename=filename,
                mode=req.mode,
                conflict_strategy=req.conflict_strategy,
                error_mode=req.error_mode,
                total_rows=len(results),
                created_count=0,
                updated_count=0,
                skipped_count=0,
                failed_count=len(errors),
                status="rolled_back",
                error_report_json=[e.model_dump() for e in errors],
            )
            db.add(failed_batch)
            await db.flush()
            return ExcelCommitOut(
                batch_id=failed_batch.id,
                status="rolled_back",
                total_rows=len(results),
                created_count=0,
                updated_count=0,
                skipped_count=0,
                failed_count=len(errors),
                errors=errors,
            )

        assert batch_obj is not None
        batch_obj.total_rows = len(results)
        batch_obj.created_count = create_cnt
        batch_obj.updated_count = update_cnt
        batch_obj.skipped_count = skip_cnt
        batch_obj.failed_count = len(errors)
        batch_obj.status = "completed_with_errors" if errors else "completed"
        batch_obj.error_report_json = [e.model_dump() for e in errors] if errors else None
        await db.flush()
        await outer_sp.commit()

        return ExcelCommitOut(
            batch_id=batch_obj.id,
            status=batch_obj.status,
            total_rows=len(results),
            created_count=create_cnt,
            updated_count=update_cnt,
            skipped_count=skip_cnt,
            failed_count=len(errors),
            errors=errors,
        )
    except Exception:
        await outer_sp.rollback()
        raise


async def export_assets_workbook(
    db: AsyncSession,
    auth: AuthContext,
    *,
    mode: str,
    group_id: int | None,
    env: str | None,
) -> bytes:
    """Export scoped assets to 6-sheet standard workbook or flattened wide-table (never exports credentials)."""
    if not auth.has_any_permission("excel:export") and not auth.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="缺少 excel:export 导出权限")

    group_codes = {
        g.id: g.code
        for g in (await db.execute(select(OpsGroup).where(OpsGroup.deleted_at.is_(None)))).scalars().all()
    }
    zone_codes = {
        z.id: z.code
        for z in (await db.execute(select(NetworkZone).where(NetworkZone.deleted_at.is_(None)))).scalars().all()
    }

    scope = await resolve_scope(db, auth, "asset:read")

    def _scope_filter(model: Any, entity_type: str, has_env: bool = True):
        stmt = apply_scope_filter(
            select(model).where(model.deleted_at.is_(None)), model, entity_type, scope
        )
        if group_id is not None and hasattr(model, "group_id"):
            stmt = stmt.where(model.group_id == group_id)
        if env and has_env and hasattr(model, "env"):
            stmt = stmt.where(model.env == env)
        return stmt

    clusters = list((await db.execute(_scope_filter(Cluster, "cluster"))).scalars().all())
    devices = list((await db.execute(_scope_filter(Device, "device"))).scalars().all())
    dev_by_id = {d.id: d for d in devices}
    cl_by_id = {c.id: c for c in clusters}

    nics = (
        list(
            (
                await db.execute(
                    select(NIC).where(
                        NIC.device_id.in_(list(dev_by_id.keys())), NIC.deleted_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        if dev_by_id
        else []
    )
    nic_by_id = {n.id: n for n in nics}

    ips = list((await db.execute(_scope_filter(IPAddress, "ip", has_env=False))).scalars().all())
    ip_by_id = {i.id: i for i in ips}
    subnets_by_id = {
        s.id: str(s.cidr)
        for s in (await db.execute(select(Subnet).where(Subnet.deleted_at.is_(None)))).scalars().all()
    }

    services = list((await db.execute(_scope_filter(Service, "service"))).scalars().all())
    svc_by_id = {s.id: s for s in services}
    endpoints = (
        list(
            (
                await db.execute(
                    select(ServiceEndpoint).where(ServiceEndpoint.service_id.in_(list(svc_by_id.keys())))
                )
            )
            .scalars()
            .all()
        )
        if svc_by_id
        else []
    )
    urls = list((await db.execute(_scope_filter(UrlEntry, "url"))).scalars().all())

    wb = Workbook()
    wb.remove(wb.active)
    header_fill = PatternFill(start_color="1677FF", end_color="1677FF", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)

    if mode == "multi_sheet":
        # Cluster -> Device map
        cd_rows = (
            await db.execute(
                select(ClusterDevice.device_id, ClusterDevice.cluster_id).where(
                    ClusterDevice.device_id.in_(list(dev_by_id.keys()))
                )
            )
        ).all() if dev_by_id else []
        dev_clusters: dict[int, list[str]] = {}
        for did, cid in cd_rows:
            if cid in cl_by_id:
                dev_clusters.setdefault(did, []).append(cl_by_id[cid].name)

        def _make_sheet(name: str, cols: list[tuple[str, str]], data_rows: list[list[Any]]):
            ws = wb.create_sheet(title=name)
            ws.append([c[0] for c in cols])
            ws.append([c[1] for c in cols])
            for r in data_rows:
                ws.append(r)
            ws.freeze_panes = "A3"
            for idx in range(1, len(cols) + 1):
                c = ws.cell(row=1, column=idx)
                c.fill = header_fill
                c.font = header_font
                ws.column_dimensions[c.column_letter].width = 20

        _make_sheet(
            "01_集群",
            SHEET_DEFINITIONS[0][1],
            [
                [
                    c.name,
                    c.code or "",
                    c.cluster_type or "",
                    c.env or "",
                    c.status,
                    c.owner or "",
                    group_codes.get(c.group_id, "") if c.group_id else "",
                    zone_codes.get(c.network_zone_id, "") if c.network_zone_id else "",
                    c.description or "",
                ]
                for c in clusters
            ],
        )
        _make_sheet(
            "02_设备",
            SHEET_DEFINITIONS[1][1],
            [
                [
                    d.hostname,
                    d.asset_code or "",
                    d.serial_number or "",
                    d.device_type,
                    d.env or "",
                    d.status,
                    d.os_info or "",
                    d.spec_summary or "",
                    d.rack_location or "",
                    d.owner or "",
                    ",".join(dev_clusters.get(d.id, [])),
                    group_codes.get(d.group_id, "") if d.group_id else "",
                    zone_codes.get(d.network_zone_id, "") if d.network_zone_id else "",
                    d.description or "",
                ]
                for d in devices
            ],
        )
        _make_sheet(
            "03_网卡",
            SHEET_DEFINITIONS[2][1],
            [
                [
                    dev_by_id[n.device_id].hostname if n.device_id in dev_by_id else "",
                    n.name,
                    n.nic_type,
                    nic_by_id[n.parent_nic_id].name if n.parent_nic_id in nic_by_id else "",
                    str(n.mac_address) if n.mac_address else "",
                    n.speed_mbps or "",
                    n.vlan_id or "",
                    n.bond_mode or "",
                    n.switch_port or "",
                    n.status,
                    n.description or "",
                ]
                for n in nics
            ],
        )
        _make_sheet(
            "04_IP",
            SHEET_DEFINITIONS[3][1],
            [
                [
                    format_ip_str(i.ip_address),
                    i.ip_type,
                    i.status,
                    zone_codes.get(i.network_zone_id, "default"),
                    subnets_by_id.get(i.subnet_id, "") if i.subnet_id else "",
                    dev_by_id[nic_by_id[i.nic_id].device_id].hostname
                    if (i.nic_id in nic_by_id and nic_by_id[i.nic_id].device_id in dev_by_id)
                    else "",
                    nic_by_id[i.nic_id].name if i.nic_id in nic_by_id else "",
                    "true" if i.is_primary else "false",
                    i.usage or "",
                    group_codes.get(i.group_id, "") if i.group_id else "",
                    i.description or "",
                ]
                for i in ips
            ],
        )
        svc_rows: list[list[Any]] = []
        eps_by_svc: dict[int, list[ServiceEndpoint]] = {}
        for ep in endpoints:
            eps_by_svc.setdefault(ep.service_id, []).append(ep)
        for s in services:
            s_eps = eps_by_svc.get(s.id) or [None]
            for ep in s_eps:
                svc_rows.append(
                    [
                        s.name,
                        s.service_type,
                        s.env or "",
                        s.status,
                        s.version or "",
                        s.owner or "",
                        group_codes.get(s.group_id, "") if s.group_id else "",
                        cl_by_id[ep.cluster_id].name if (ep and ep.cluster_id in cl_by_id) else "",
                        dev_by_id[ep.device_id].hostname if (ep and ep.device_id in dev_by_id) else "",
                        format_ip_str(ip_by_id[ep.ip_id].ip_address)
                        if (ep and ep.ip_id in ip_by_id)
                        else (ep.listen_ip_text if ep and ep.listen_ip_text else ""),
                        ep.port if ep and ep.port else "",
                        ep.protocol if ep else "",
                        ep.endpoint_role if ep and ep.endpoint_role else "",
                        s.description or "",
                    ]
                )
        _make_sheet("05_服务与端点", SHEET_DEFINITIONS[4][1], svc_rows)

        _make_sheet(
            "06_URL",
            SHEET_DEFINITIONS[5][1],
            [
                [
                    u.name,
                    u.full_url,
                    u.access_type,
                    svc_by_id[u.service_id].name if u.service_id in svc_by_id else "",
                    format_ip_str(ip_by_id[u.entry_ip_id].ip_address)
                    if u.entry_ip_id in ip_by_id
                    else "",
                    u.env or "",
                    u.status,
                    u.ssl_expires_at.strftime("%Y-%m-%d") if u.ssl_expires_at else "",
                    group_codes.get(u.group_id, "") if u.group_id else "",
                    u.description or "",
                ]
                for u in urls
            ],
        )
    else:
        # Flattened wide-table overview
        ws = wb.create_sheet(title="运维台账宽表")
        headers = [
            "资产类型",
            "主名称/标识",
            "IP地址",
            "网卡/MAC",
            "所属设备/集群",
            "服务/URL",
            "环境",
            "状态",
            "归属分组",
            "备注",
        ]
        ws.append(headers)
        for idx in range(1, len(headers) + 1):
            c = ws.cell(row=1, column=idx)
            c.fill = header_fill
            c.font = header_font
            ws.column_dimensions[c.column_letter].width = 20

        for i in ips:
            nic = nic_by_id.get(i.nic_id) if i.nic_id else None
            dev = dev_by_id.get(nic.device_id) if nic else None
            ws.append(
                [
                    f"IP({i.ip_type})",
                    format_ip_str(i.ip_address),
                    format_ip_str(i.ip_address),
                    f"{nic.name} ({nic.mac_address or '-'})" if nic else "",
                    dev.hostname if dev else "",
                    "",
                    dev.env if dev and dev.env else "",
                    i.status,
                    group_codes.get(i.group_id, "") if i.group_id else "",
                    i.usage or i.description or "",
                ]
            )
        for u in urls:
            ws.append(
                [
                    f"URL({u.access_type})",
                    u.name,
                    format_ip_str(ip_by_id[u.entry_ip_id].ip_address) if u.entry_ip_id in ip_by_id else "",
                    "",
                    "",
                    f"{svc_by_id[u.service_id].name if u.service_id in svc_by_id else '-'} | {u.full_url}",
                    u.env or "",
                    u.status,
                    group_codes.get(u.group_id, "") if u.group_id else "",
                    u.description or "",
                ]
            )

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
