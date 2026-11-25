<script setup lang="ts">
import { h, onMounted, reactive, ref } from 'vue'
import { NButton, NPopconfirm, NSpace, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import { clusterApi, deviceApi, type ClusterItem, type DeviceItem } from '@/api/assets'
import { extractErrorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useOptionsStore } from '@/stores/options'
import AssetDetailDrawer from '@/components/AssetDetailDrawer.vue'
import BatchActionBar from '@/components/BatchActionBar.vue'

const auth = useAuthStore()
const options = useOptionsStore()
const message = useMessage()

const loading = ref(false)
const items = ref<DeviceItem[]>([])
const selectedIds = ref<number[]>([])
const clusters = ref<ClusterItem[]>([])
const total = ref(0)
const filters = reactive({
  q: '',
  device_type: null as string | null,
  env: null as string | null,
  group_id: null as number | null,
  page: 1,
})

const modalOpen = ref(false)
const editing = ref<DeviceItem | null>(null)
const form = reactive({
  hostname: '',
  asset_code: '',
  serial_number: '',
  device_type: 'physical',
  group_id: null as number | null,
  network_zone_id: null as number | null,
  env: null as string | null,
  status: 'in_use',
  os_info: '',
  spec_summary: '',
  rack_location: '',
  owner: '',
  description: '',
  cluster_ids: [] as number[],
  tag_ids: [] as number[],
})

const drawerOpen = ref(false)
const inspectId = ref<number | null>(null)

async function loadData() {
  loading.value = true
  try {
    await options.ensureLoaded()
    const [dRes, cRes] = await Promise.all([
      deviceApi.list({
        q: filters.q || undefined,
        device_type: filters.device_type || undefined,
        env: filters.env || undefined,
        group_id: filters.group_id || undefined,
        page: filters.page,
        page_size: 20,
      }),
      clusterApi.list({ page: 1, page_size: 200 }),
    ])
    items.value = dRes.data.items
    total.value = dRes.data.total
    clusters.value = cRes.data.items
  } catch (err) {
    message.error(extractErrorMessage(err, '加载设备失败'))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  const defaultClusters =
    !auth.hasWritableGroup && auth.writableClusterIds.length ? [auth.writableClusterIds[0]] : []
  Object.assign(form, {
    hostname: '',
    asset_code: '',
    serial_number: '',
    device_type: 'physical',
    group_id: null,
    network_zone_id: options.zones.find((z) => z.is_default)?.id ?? null,
    env: null,
    status: 'in_use',
    os_info: '',
    spec_summary: '',
    rack_location: '',
    owner: '',
    description: '',
    cluster_ids: defaultClusters,
    tag_ids: [],
  })
  modalOpen.value = true
}

function openEdit(row: DeviceItem) {
  editing.value = row
  Object.assign(form, {
    hostname: row.hostname,
    asset_code: row.asset_code || '',
    serial_number: row.serial_number || '',
    device_type: row.device_type,
    group_id: row.group_id,
    network_zone_id: row.network_zone_id,
    env: row.env,
    status: row.status,
    os_info: row.os_info || '',
    spec_summary: row.spec_summary || '',
    rack_location: row.rack_location || '',
    owner: row.owner || '',
    description: row.description || '',
    cluster_ids: clusters.value.filter((c) => row.cluster_names.includes(c.name)).map((c) => c.id),
    tag_ids: row.tags.map((t) => t.id),
  })
  modalOpen.value = true
}

async function handleSave() {
  const payload = {
    hostname: form.hostname,
    asset_code: form.asset_code || null,
    serial_number: form.serial_number || null,
    device_type: form.device_type,
    group_id: form.group_id,
    network_zone_id: form.network_zone_id,
    env: form.env,
    status: form.status,
    os_info: form.os_info || null,
    spec_summary: form.spec_summary || null,
    rack_location: form.rack_location || null,
    owner: form.owner || null,
    description: form.description || null,
    cluster_ids: form.cluster_ids,
    tag_ids: form.tag_ids,
  }
  try {
    const res = editing.value
      ? await deviceApi.update(editing.value.id, payload)
      : await deviceApi.create(payload)
    message.success(editing.value ? '设备已更新' : '设备创建成功')
    if (res.data.warnings?.length) {
      message.warning(res.data.warnings.join('；'))
    }
    modalOpen.value = false
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '保存设备失败'))
  }
}

async function handleDelete(id: number) {
  try {
    await deviceApi.remove(id)
    message.success('设备已移入回收站')
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '删除失败'))
  }
}

const columns: DataTableColumns<DeviceItem> = [
  { type: 'selection' },
  {
    title: '主机名 / 设备名',
    key: 'hostname',
    width: 170,
    render: (r) =>
      h(
        NButton,
        { text: true, type: 'primary', onClick: () => { inspectId.value = r.id; drawerOpen.value = true } },
        { default: () => r.hostname },
      ),
  },
  {
    title: '类型',
    key: 'device_type',
    width: 100,
    render: (r) => h(NTag, { size: 'small' }, { default: () => options.dictLabel('device_type', r.device_type) }),
  },
  {
    title: '所属集群',
    key: 'cluster_names',
    width: 160,
    render: (r) =>
      r.cluster_names.length
        ? h(NSpace, { size: 'small', wrap: true }, () => r.cluster_names.map((c) => h(NTag, { size: 'tiny', type: 'info' }, { default: () => c })))
        : '—',
  },
  {
    title: '网卡 / 绑定 IP',
    key: 'primary_ips',
    render: (r) =>
      h(NSpace, { size: 'small', wrap: true }, () => [
        h(NTag, { size: 'tiny' }, { default: () => `${r.nic_count} 网卡` }),
        ...r.primary_ips.map((ip) => h(NTag, { size: 'tiny', type: 'success' }, { default: () => ip })),
      ]),
  },
  { title: '资产编号', key: 'asset_code', width: 120, render: (r) => r.asset_code || '—' },
  { title: '环境', key: 'env', width: 80, render: (r) => (r.env ? options.dictLabel('env', r.env) : '—') },
  { title: '位置', key: 'rack_location', width: 120, ellipsis: { tooltip: true } },
  {
    title: '操作',
    key: 'actions',
    width: 170,
    render: (r) =>
      h(NSpace, { size: 'small' }, () => [
        h(NButton, { size: 'tiny', onClick: () => { inspectId.value = r.id; drawerOpen.value = true } }, { default: () => '全景' }),
        h(NButton, { size: 'tiny', onClick: () => openEdit(r) }, { default: () => '编辑' }),
        h(
          NPopconfirm,
          { onPositiveClick: () => handleDelete(r.id) },
          {
            trigger: () => h(NButton, { size: 'tiny', type: 'error', quaternary: true }, { default: () => '删除' }),
            default: () => `确认删除设备「${r.hostname}」？`,
          },
        ),
      ]),
  },
]

onMounted(loadData)
</script>

<template>
  <n-card title="设备台账">
    <template #header-extra>
      <n-space>
        <n-input v-model:value="filters.q" placeholder="搜索主机名/资产编号/SN/机柜" clearable class="w-200px" @keyup.enter="loadData" />
        <n-select v-model:value="filters.device_type" clearable placeholder="设备类型" class="w-120px" :options="options.dictOptions('device_type')" />
        <n-select v-model:value="filters.env" clearable placeholder="环境" class="w-100px" :options="options.dictOptions('env')" />
        <n-button @click="() => { filters.page = 1; loadData() }">查询</n-button>
        <n-button type="primary" @click="openCreate">新建设备</n-button>
      </n-space>
    </template>

    <BatchActionBar
      entity="devices"
      :selected-ids="selectedIds"
      @clear="selectedIds = []"
      @done="loadData"
    />

    <n-data-table
      :loading="loading"
      :columns="columns"
      :data="items"
      :row-key="(r: DeviceItem) => r.id"
      :checked-row-keys="selectedIds"
      @update:checked-row-keys="(keys: Array<string | number>) => { selectedIds = keys.map(Number) }"
      :bordered="false"
    />
    <div class="flex justify-end mt-4">
      <n-pagination v-model:page="filters.page" :page-size="20" :item-count="total" @update:page="loadData" />
    </div>

    <n-modal v-model:show="modalOpen" preset="card" :title="editing ? '编辑设备' : '新建设备'" class="w-600px">
      <n-form label-placement="left" label-width="85">
        <div class="grid grid-cols-2 gap-x-4">
          <n-form-item label="主机名" required>
            <n-input v-model:value="form.hostname" placeholder="如：prod-db-01" />
          </n-form-item>
          <n-form-item label="设备类型" required>
            <n-select v-model:value="form.device_type" :options="options.dictOptions('device_type')" />
          </n-form-item>
          <n-form-item label="资产编号">
            <n-input v-model:value="form.asset_code" placeholder="全局唯一（可选）" />
          </n-form-item>
          <n-form-item label="序列号 SN">
            <n-input v-model:value="form.serial_number" placeholder="全局唯一（可选）" />
          </n-form-item>
          <n-form-item label="归属分组">
            <n-select v-model:value="form.group_id" clearable :options="options.groups.map((g) => ({ label: g.name, value: g.id }))" />
          </n-form-item>
          <n-form-item label="网络域">
            <n-select v-model:value="form.network_zone_id" clearable :options="options.zones.map((z) => ({ label: z.name, value: z.id }))" />
          </n-form-item>
          <n-form-item label="环境">
            <n-select v-model:value="form.env" clearable :options="options.dictOptions('env')" />
          </n-form-item>
          <n-form-item label="状态">
            <n-select v-model:value="form.status" :options="options.dictOptions('asset_status')" />
          </n-form-item>
          <n-form-item label="操作系统">
            <n-input v-model:value="form.os_info" placeholder="如：Rocky Linux 9.4" />
          </n-form-item>
          <n-form-item label="配置摘要">
            <n-input v-model:value="form.spec_summary" placeholder="如：32C 128G 2T NVMe" />
          </n-form-item>
          <n-form-item label="机房/机柜">
            <n-input v-model:value="form.rack_location" placeholder="如：BJ-A03-U12" />
          </n-form-item>
          <n-form-item label="负责人">
            <n-input v-model:value="form.owner" />
          </n-form-item>
        </div>
        <n-form-item label="所属集群">
          <n-select v-model:value="form.cluster_ids" multiple filterable :options="clusters.map((c) => ({ label: c.name, value: c.id }))" />
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

    <AssetDetailDrawer v-model:show="drawerOpen" entity-type="device" :entity-id="inspectId" />
  </n-card>
</template>
