# Oh-My-CSM

轻量化运维资产与网络拓扑台账管理系统（CMDB-Lite），以 **集群、设备、网卡（含 Bond 层级）、IP / 网段（IPAM）、服务与端点、URL** 为核心实体，解决零散 Excel 表格整合、全链路快速反查、RBAC 分组权限隔离与变更审计问题。

完整架构、ER 图、数据库 Schema 与权限设计详见 [docs/DESIGN.md](docs/DESIGN.md)。

## 核心特性

- **六大核心资产与网段 IPAM 闭环**
  - **设备 → 网卡（支持 Bond `parent_nic_id` 父子层级）→ IP** 精准绑定，同时支持 VIP 多目标绑定（网卡 / 集群 / 服务）与未分配游离 IP。
  - **网络域（NetworkZone）隔离**：默认提供 `default` 域，支持不同隔离网段复用相同私网 IP。
  - **IPAM 格子矩阵**：`≤ /20` 网段平铺可视化色块（空闲/已分配/VIP/预留/冲突/网关），点击空闲格一键分配；超大网段与 IPv6 自动切换分页列表。
  - **全景上下游关联抽屉**：从任意 URL/IP/设备/集群一键反查完整链路，跨组节点自动以只读脱敏摘要呈现。
- **Excel 双向批处理引擎**
  - 内置 **标准 6-Sheet 模板** 与 **老旧单 Sheet 宽表列映射**（支持保存映射预设、未映射列自动收入 `extra_attrs`）。
  - 强制 **Dry-run 差异预览**，支持重复合并策略（跳过 / 仅更新非空 / 全量覆盖）与错误处理（整批回滚 / 跳过错误并下载错误清单 Excel）。
  - 支持按分组/环境条件导出 6-Sheet 完整台账或宽表（严格排除敏感凭据）。
- **权限治理与安全审计**
  - 内置 4 角色（`SuperAdmin` / `GroupAdmin` / `OpsEditor` / `Viewer`）+ 自定义角色 + 运维分组数据隔离。
  - 独立 **AES-256-GCM 关联凭据模块**（外部环境变量注入版本化主密钥、`credential:reveal` 独立权限 + 明文查看强审计 + 15 秒前端自动脱敏 + CLI 密钥轮换）。
  - **个人 API Token（PAT）** 支持运维脚本 / Ansible 调用 `/api/v1` REST API（OpenAPI 文档位于 `/api/v1/docs`）。
  - 全量字段级 Diff 审计日志、软删除级联保护与回收站冲突安全恢复。
- **高频效率体验**：`Ctrl/Cmd + K` 全局聚合秒搜、多选批量操作栏、Dashboard 高占用网段/SSL 证书到期/待补全资产预警看板。

---

## 一键部署（Docker Compose）

```bash
cd deploy
cp .env.example .env
# 修改 .env 中的 POSTGRES_PASSWORD、JWT_SECRET_KEY 与 CSM_SECRET_KEYS
docker compose up -d --build
```

- 默认访问地址：`http://localhost:8080`
- 初始超管账号：`admin` / `Admin123456`（首次登录强制修改密码）

## 内网完全离线部署

1. **在有网环境打包离线交付包**：
   ```bash
   ./deploy/scripts/export-offline.sh v0.1.0
   # 生成 deploy/oh-my-csm-offline-v0.1.0.tar.gz
   ```
2. **拷贝至内网目标服务器解压并一键启动**：
   ```bash
   tar -xzf oh-my-csm-offline-v0.1.0.tar.gz
   cd oh-my-csm-offline
   ./deploy/scripts/import-offline.sh
   ```

## 数据库定时备份与恢复

```bash
# 手动备份（默认保留 30 天）
./deploy/scripts/backup-db.sh /data/backups/oh-my-csm 30

# 恢复备份
./deploy/scripts/restore-db.sh /data/backups/oh-my-csm/oh_my_csm_20261125_023000.dump
```

> **安全提示**：请务必将 `deploy/.env` 中的 `CSM_SECRET_KEYS` 与数据库备份分开妥善保管；如需轮换主密钥，可执行：
> `docker exec oh-my-csm-backend python -m app.cli rotate-credential-keys --to-version 2`

---

## 本地开发与测试

### 后端（Python + FastAPI）
```bash
cd backend
uv sync --extra dev
uv run alembic upgrade head
uv run python -m app.cli seed
uv run pytest -v
uv run uvicorn app.main:app --reload --port 8000
```

### 前端（Vue 3 + TypeScript + Naive UI）
```bash
cd frontend
npm install
npm run dev
# 生产类型检查与打包
npm run build
```

### 运维脚本调用示例（PAT）
在系统右上角「个人 API Token」生成一个 `read_only` Token 后即可直接查询台账：
```bash
curl -H "Authorization: Bearer csm_your_token_here" \
  "http://localhost:8080/api/v1/search?q=10.20.1"
```
