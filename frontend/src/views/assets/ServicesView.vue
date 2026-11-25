<script setup lang="ts">
import { h, onMounted, reactive, ref } from 'vue'
import { NButton, NPopconfirm, NSpace, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import {
  clusterApi,
  deviceApi,
  ipApi,
  serviceApi,
  type ClusterItem,
  type DeviceItem,
  type IpItem,
  type ServiceEndpointItem,
  type ServiceItem,
} from '@/api/assets'
import { extractErrorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useOptionsStore } from '@/stores/options'
import AssetDetailDrawer from '@/components/AssetDetailDrawer.vue'
import BatchActionBar from '@/components/BatchActionBar.vue'

const auth = useAuthStore()
const options = useOptionsStore()
const message = useMessage()

const loading = ref(false)
const items = ref<ServiceItem[]>([])
const selectedIds = ref<number[]>([])
const total = ref(0)
const filters = reactive({ q: '', service_type: null as string | null, env: null as string | null, page: 1 })

const modalOpen = ref(false)
const editing = ref<ServiceItem | null>(null)
const form = reactive({
  name: '',
  code: '',
  service_type: 'application',
  group_id: null as number | null,
  env: null as string | null,
  status: 'in_use',
  version: '',
  owner: '',
  repo_or_doc_url: '',
  description: '',
  cluster_ids: [] as number[],
  tag_ids: [] as number[],
})

// Endpoints management drawer
const epDrawerOpen = ref(false)
const activeService = ref<ServiceItem | null>(null)
const endpoints = ref<ServiceEndpointItem[]>([])
const clusters = ref<ClusterItem[]>([])
const devices = ref<DeviceItem[]>([])
const ips = ref<IpItem[]>([])
const epForm = reactive({
  cluster_id: null as number | null,
  device_id: null as number | null,
  ip_id: null as number | null,
  listen_ip_text: '',
  port: null as number | null,
  protocol: 'http',
  endpoint_role: '',
  description: '',
})

const drawerOpen = ref(false)
const inspectId = ref<number | null>(null)

async function loadData() {
  loading.value = true
  try {
    await options.ensureLoaded()
    const [res, cRes] = await Promise.all([
      serviceApi.list({
        q: filters.q || undefined,
        service_type: filters.service_type || undefined,
        env: filters.env || undefined,
        page: filters.page,
        page_size: 20,
      }),
      clusterApi.list({ page: 1, page_size: 200 }),
    ])
    items.value = res.data.items
    total.value = res.data.total
    clusters.value = cRes.data.items
  } catch (err) {
    message.error(extractErrorMessage(err, '加载服务列表失败'))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  const defaultClusters =
    !auth.hasWritableGroup && auth.writableClusterIds.length ? [auth.writableClusterIds[0]] : []
  Object.assign(form, {
    name: '',
    code: '',
    service_type: 'application',
    group_id: null,
    env: null,
    status: 'in_use',
    version: '',
    owner: '',
    repo_or_doc_url: '',
    description: '',
    cluster_ids: defaultClusters,
    tag_ids: [],
  })
  modalOpen.value = true
}

function openEdit(row: ServiceItem) {
  editing.value = row
  Object.assign(form, {
    name: row.name,
    code: row.code || '',
    service_type: row.service_type,
    group_id: row.group_id,
    env: row.env,
    status: row.status,
    version: row.version || '',
    owner: row.owner || '',
    repo_or_doc_url: row.repo_or_doc_url || '',
    description: row.description || '',
    tag_ids: row.tags.map((t) => t.id),
  })
  modalOpen.value = true
}

async function handleSave() {
  const payload = {
    name: form.name,
    code: form.code || null,
    service_type: form.service_type,
    group_id: form.group_id,
    env: form.env,
    status: form.status,
    version: form.version || null,
    owner: form.owner || null,
    repo_or_doc_url: form.repo_or_doc_url || null,
    description: form.description || null,
    tag_ids: form.tag_ids,
  }
  try {
    if (editing.value) {
      await serviceApi.update(editing.value.id, payload)
      message.success('服务已更新')
    } else {
      await serviceApi.create({ ...payload, cluster_ids: form.cluster_ids })
      message.success('服务创建成功')
    }
    modalOpen.value = false
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '保存服务失败'))
  }
}

async function handleDelete(id: number) {
  try {
    await serviceApi.remove(id)
    message.success('服务已移入回收站')
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '删除失败'))
  }
}

async function openEndpoints(row: ServiceItem) {
  activeService.value = row
  epDrawerOpen.value = true
  try {
    const [dRes, cRes, devRes, ipRes] = await Promise.all([
      serviceApi.detail(row.id),
      clusterApi.list({ page: 1, page_size: 200 }),
      deviceApi.list({ page: 1, page_size: 200 }),
      ipApi.list({ page: 1, page_size: 200 }),
    ])
    endpoints.value = dRes.data.endpoints
    clusters.value = cRes.data.items
    devices.value = devRes.data.items
    ips.value = ipRes.data.items
  } catch (err) {
    message.error(extractErrorMessage(err, '加载端点失败'))
  }
}

async function handleAddEndpoint() {
  if (!activeService.value) return
  try {
    await serviceApi.addEndpoint(activeService.value.id, {
      cluster_id: epForm.cluster_id,
      device_id: epForm.device_id,
      ip_id: epForm.ip_id,
      listen_ip_text: epForm.listen_ip_text || null,
      port: epForm.port,
      protocol: epForm.protocol,
      endpoint_role: epForm.endpoint_role || null,
      description: epForm.description || null,
    })
    message.success('端点已添加')
    Object.assign(epForm, { cluster_id: null, device_id: null, ip_id: null, listen_ip_text: '', port: null, endpoint_role: '', description: '' })
    await openEndpoints(activeService.value)
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '添加端点失败'))
  }
}

async function handleDeleteEndpoint(epId: number) {
  if (!activeService.value) return
  try {
    await serviceApi.deleteEndpoint(activeService.value.id, epId)
    message.success('端点已移除')
    await openEndpoints(activeService.value)
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '移除失败'))
  }
}

const columns: DataTableColumns<ServiceItem> = [
  { type: 'selection' },
  {
    title: '服务名称',
    key: 'name',
    render: (r) =>
      h(
        NButton,
        { text: true, type: 'primary', onClick: () => { inspectId.value = r.id; drawerOpen.value = true } },
        { default: () => r.name },
      ),
  },
  {
    title: '类型',
    key: 'service_type',
    width: 110,
    render: (r) => h(NTag, { size: 'small' }, { default: () => options.dictLabel('service_type', r.service_type) }),
  },
  { title: '环境', key: 'env', width: 85, render: (r) => (r.env ? options.dictLabel('env', r.env) : '—') },
  { title: '版本', key: 'version', width: 90, render: (r) => r.version || '—' },
  { title: '端点数', key: 'endpoint_count', width: 80 },
  { title: 'URL 数', key: 'url_count', width: 80 },
  { title: '负责人', key: 'owner', width: 100, render: (r) => r.owner || '—' },
  { title: '归属分组', key: 'group_name', width: 110, render: (r) => r.group_name || '公共' },
  {
    title: '操作',
    key: 'actions',
    width: 240,
    render: (r) =>
      h(NSpace, { size: 'small' }, () => [
        h(NButton, { size: 'tiny', onClick: () => { inspectId.value = r.id; drawerOpen.value = true } }, { default: () => '全景' }),
        h(NButton, { size: 'tiny', type: 'info', secondary: true, onClick: () => openEndpoints(r) }, { default: () => '端点' }),
        h(NButton, { size: 'tiny', onClick: () => openEdit(r) }, { default: () => '编辑' }),
        h(
          NPopconfirm,
          { onPositiveClick: () => handleDelete(r.id) },
          {
            trigger: () => h(NButton, { size: 'tiny', type: 'error', quaternary: true }, { default: () => '删除' }),
            default: () => `确认删除服务「${r.name}」？`,
          },
        ),
      ]),
  },
]

onMounted(loadData)
</script>

<template>
  <n-card title="服务台账">
    <template #header-extra>
      <n-space>
        <n-input v-model:value="filters.q" placeholder="搜索服务名/编码/负责人" clearable class="w-190px" @keyup.enter="loadData" />
        <n-select v-model:value="filters.service_type" clearable placeholder="服务类型" class="w-120px" :options="options.dictOptions('service_type')" />
        <n-select v-model:value="filters.env" clearable placeholder="环境" class="w-100px" :options="options.dictOptions('env')" />
        <n-button @click="() => { filters.page = 1; loadData() }">查询</n-button>
        <n-button type="primary" @click="openCreate">新建服务</n-button>
      </n-space>
    </template>

    <BatchActionBar
      entity="services"
      :selected-ids="selectedIds"
      @clear="selectedIds = []"
      @done="loadData"
    />

    <n-data-table
      :loading="loading"
      :columns="columns"
      :data="items"
      :row-key="(r: ServiceItem) => r.id"
      :checked-row-keys="selectedIds"
      @update:checked-row-keys="(keys: Array<string | number>) => { selectedIds = keys.map(Number) }"
      :bordered="false"
    />
    <div class="flex justify-end mt-4">
      <n-pagination v-model:page="filters.page" :page-size="20" :item-count="total" @update:page="loadData" />
    </div>

    <n-modal v-model:show="modalOpen" preset="card" :title="editing ? '编辑服务' : '新建服务'" class="w-540px">
      <n-form label-placement="left" label-width="85">
        <n-form-item label="服务名称" required>
          <n-input v-model:value="form.name" placeholder="如：order-gateway" />
        </n-form-item>
        <n-form-item label="服务类型">
          <n-select v-model:value="form.service_type" :options="options.dictOptions('service_type')" />
        </n-form-item>
        <n-form-item label="归属分组">
          <n-select v-model:value="form.group_id" clearable :options="options.groups.map((g) => ({ label: g.name, value: g.id }))" />
        </n-form-item>
        <n-form-item v-if="!editing" label="部署集群">
          <n-select
            v-model:value="form.cluster_ids"
            multiple
            filterable
            placeholder="可选，集群级权限用户需关联至少一个有写权限的集群"
            :options="clusters.map((c) => ({ label: c.name, value: c.id }))"
          />
        </n-form-item>
        <n-form-item label="环境">
          <n-select v-model:value="form.env" clearable :options="options.dictOptions('env')" />
        </n-form-item>
        <n-form-item label="状态">
          <n-select v-model:value="form.status" :options="options.dictOptions('asset_status')" />
        </n-form-item>
        <n-form-item label="版本">
          <n-input v-model:value="form.version" placeholder="如：v2.4.1" />
        </n-form-item>
        <n-form-item label="负责人">
          <n-input v-model:value="form.owner" />
        </n-form-item>
        <n-form-item label="文档/仓库">
          <n-input v-model:value="form.repo_or_doc_url" />
        </n-form-item>
        <n-form-item label="标签">
          <n-select v-model:value="form.tag_ids" multiple :options="options.tags.map((t) => ({ label: t.name, value: t.id }))" />
        </n-form-item>
        <n-form-item label="备注">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <template #footer>
        <div class="flex justify-end gap-2">
          <n-button @click="modalOpen = false">取消</n-button>
          <n-button type="primary" @click="handleSave">保存</n-button>
        </div>
      </template>
    </n-modal>

    <n-drawer v-model:show="epDrawerOpen" width="680">
      <n-drawer-content :title="`运行端点管理 · ${activeService?.name || ''}`">
        <div class="p-3 bg-slate-50 rounded border border-slate-200 mb-4 space-y-2">
          <div class="grid grid-cols-3 gap-2">
            <n-select v-model:value="epForm.cluster_id" clearable filterable placeholder="关联集群" :options="clusters.map((c) => ({ label: c.name, value: c.id }))" />
            <n-select v-model:value="epForm.device_id" clearable filterable placeholder="关联设备" :options="devices.map((d) => ({ label: d.hostname, value: d.id }))" />
            <n-select v-model:value="epForm.ip_id" clearable filterable placeholder="监听 IP" :options="ips.map((i) => ({ label: i.ip_address, value: i.id }))" />
          </div>
          <div class="grid grid-cols-4 gap-2">
            <n-select v-model:value="epForm.protocol" :options="options.dictOptions('protocol')" />
            <n-input-number v-model:value="epForm.port" :min="1" :max="65535" placeholder="端口" />
            <n-input v-model:value="epForm.endpoint_role" placeholder="角色(http/metrics)" />
            <n-button type="primary" @click="handleAddEndpoint">添加端点</n-button>
          </div>
        </div>

        <n-data-table
          size="small"
          :data="endpoints"
          :columns="[
            { title: '协议/端口', key: 'port', width: 110, render: (r: ServiceEndpointItem) => `${r.protocol.toUpperCase()}:${r.port ?? '-'}` },
            { title: 'IP', key: 'ip', render: (r: ServiceEndpointItem) => r.ip_address || r.listen_ip_text || '—' },
            { title: '设备', key: 'device_hostname', render: (r: ServiceEndpointItem) => r.device_hostname || '—' },
            { title: '集群', key: 'cluster_name', render: (r: ServiceEndpointItem) => r.cluster_name || '—' },
            { title: '角色', key: 'endpoint_role', width: 90 },
            {
              title: '操作',
              key: 'op',
              width: 75,
              render: (r: ServiceEndpointItem) => h(NButton, { size: 'tiny', type: 'error', quaternary: true, onClick: () => handleDeleteEndpoint(r.id) }, { default: () => '删除' }),
            },
          ]"
        />
      </n-drawer-content>
    </n-drawer>

    <AssetDetailDrawer v-model:show="drawerOpen" entity-type="service" :entity-id="inspectId" />
  </n-card>
</template>
